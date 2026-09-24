# Assumed-Size Array Handling: From Manual Guess to Unified Auto-Detection
**Date:** 2026-03-27
**Status:** completed
**Scope:** VIT extraction pipeline, assumed-size array sizing, confirm protocol

## Origin: A Vague Recollection

This change started with the user recalling a Phase 7B issue: "I remember in our work for Phase 7B, we identified an issue where we incorrectly sized an allocatable array, and that caused a bug. We handled that with a confirm loop, but the implementation was adjacent and complementary to the original process that sought user input to size the allocatable initially, and that seemed clunky and redundant."

This prompted an investigation into how the two assumed-size array mechanisms worked, why they felt redundant, and whether they could be unified.

## What We Found

### The Two Systems

**System 1: Configuration (Phase 7B, dev note 202603240139)**
When DISCON.F90 extraction was first attempted, KGen's `DIMENSION(:)` write functions couldn't accept assumed-size `(*)` arrays. VIT was enhanced to patch `kw_*` call sites with explicit sections: `avrswap(1:2000)`. The size came from `vit.yaml`:

```yaml
kgen:
  assumed_size_arrays:
    avrSWAP: 2000
```

The value 2000 was a manual guess based on `ExtControl.f90`'s `max_avr_entries = 2000` — the standard Bladed DLL protocol's swap array size.

**System 2: Validation (dev note 202603251411)**
The guess of 2000 was wrong. ROSCO's CableControl writes to `avrSWAP(2601+)` and StructuralControl to `avrSWAP(2801+)` — OpenFAST extensions beyond the standard Bladed range. This caused silent heap corruption: the kernel allocated 2000 elements, the function wrote beyond that, corrupting adjacent ALLOCATABLE array data. Three functions showed mysterious state file errors (zeros, float32 truncation) that turned out to be a single root cause.

After fixing the size to 3000, VIT was enhanced with `infer_assumed_size_arrays()` — a source scanner that detects literal index accesses (`avrSWAP(2601)`) and bounds checks (`IF (idx >= 2801)`) and warns if the configured size is insufficient. This used the confirm protocol (exit code 10) to signal Claude.

### The Redundancy

The validation scanner had all the information needed to PROPOSE the right size, but it was designed only to VALIDATE a user-provided size. The flow was:

1. User guesses size → puts in vit.yaml
2. VIT scans source → finds evidence of larger indices
3. VIT warns: "your guess is too small" → EXIT_CONFIRM
4. User re-runs with `--assumed-size avrSWAP=3000`
5. VIT scans source AGAIN (identical scan) → confirms 3000 is adequate
6. Extraction proceeds

The second scan was pure redundancy. And the user had to guess the initial number — the scanner could have proposed it from the start.

### Why 2000 Was Wrong in the First Place

The answer to "why didn't the first scan catch this?" was simple: **there was no scan**. The scanner was built AFTER the heap corruption bug was discovered. System 2 was a retrospective safety net, not part of the original design. The initial value was a human guess that happened to be wrong.

## The Unification Plan

Promote the scanner from "validator" to "proposer." VIT should:

1. **Auto-detect** assumed-size `(*)` arrays from the function's enclosing subroutine signature
2. **Scan source** to infer the appropriate size from index evidence
3. **Propose** the inferred size via EXIT_CONFIRM (not just validate a pre-configured one)
4. **Skip validation** when the user explicitly confirms via `--assumed-size` (no redundant second scan)
5. **Keep config optional** — `assumed_size_arrays` in vit.yaml works as a known-good override that skips confirmation

## Implementation

### Change 1: `fortran_parser.py` — `is_assumed_size` property

Added `FortranArg.is_assumed_size` to detect `(*)` arrays. The parser already stored `array_dims="*"` for these; the property just checks for `"*"` in dims. Complements the existing `is_assumed_shape` (which checks for `":"` in dims).

### Change 2: `extract.py` — `detect_assumed_size_from_source()`

New function that reads the source file, finds the enclosing SUBROUTINE/FUNCTION above the call site line, and returns the names of any `(*)` parameters. Handles both inline `var(*)` and `DIMENSION(*)` attribute declarations. Correctly ignores `CHARACTER(*)` (not an array).

### Change 3: `extract.py` — Modified `infer_assumed_size_arrays()`

Extended to handle size=0 (auto-detect mode):
- `configured_size > 0 and >= inferred_min`: pass silently (existing)
- `configured_size > 0 and < inferred_min`: EXIT_CONFIRM (existing)
- `configured_size == 0 and inferred_min > 0`: EXIT_CONFIRM with proposed size (NEW)
- `configured_size == 0 and inferred_min == 0`: hard error (NEW — see below)

### Change 4: `cli.py` — Auto-detect and skip-on-override

Before validation, `cmd_extract()` now calls `detect_assumed_size_from_source()` to find `(*)` arrays in the enclosing subroutine. Any detected array not already in the config is added to the dict with size 0 (triggering inference). Arrays provided via `--assumed-size` on the CLI are tracked in `cli_overridden` and excluded from validation — the user has already confirmed.

### Initial Bug: Silent Size-0 Failure (Scenario 4)

After implementing the initial change, we walked through hypothetical scenarios to validate the design. Scenario 4 exposed a critical flaw:

> A project has `buffer(*)` but never uses literal indices — all accesses are through variables like `buffer(offset + i)`. VIT detects `buffer(*)`, scans source, finds no evidence. Inferred minimum is 0. Since `inferred_min == 0` and `configured_size == 0`, the `needs_confirm` check was `inferred_min > 0` → `False`. VIT silently proceeded with size 0, generating `buffer(1:0)` — an empty array section that captures no state.

This was a silent failure. The extraction would "succeed" but the kernel would have no data for the array.

### Fix: Hard Error on No Evidence

When an assumed-size array is auto-detected but no source evidence is found, VIT now emits a hard error (exit 1) instead of silently proceeding:

```
ERROR: Detected assumed-size array buffer(*) but found no index evidence
in source. Cannot infer size automatically.
  Provide the size explicitly with --assumed-size buffer=<SIZE> or
  configure kgen.assumed_size_arrays in vit.yaml.
```

This follows VIT's existing principle (documented in CLAUDE.md): "No evidence, arbitrary fallback → hard error. VIT doesn't have enough information to proceed. Guessing would silently produce wrong code."

## Final Design: Four Cases

| Situation | Source | Behavior |
|-----------|--------|----------|
| Config provides adequate size | Config ≥ inferred min | Proceed silently |
| Config provides undersized value | Config < inferred min | EXIT_CONFIRM with evidence |
| Auto-detected, source has evidence | No config, evidence found | EXIT_CONFIRM with proposed size |
| Auto-detected, no evidence | No config, no evidence | Hard error (exit 1) |

## Tests

9 new tests across 2 files (553 total, all passing):
- `test_fortran_parser.py`: `test_assumed_size_array` — detects `(*)` vs `(:)` vs scalar
- `test_extract.py`: `TestDetectAssumedSizeFromSource` (4 tests) — inline `(*)`, `DIMENSION(*)`, no assumed-size, ignores `CHARACTER(*)`
- `test_extract.py`: `TestInferAssumedSizeArrays` (4 tests) — adequate size, undersized, zero-size with evidence, zero-size with no evidence (error)

## Commits

| Hash | Description |
|------|-------------|
| `9c36b0f` | Unify assumed-size array handling: auto-detect and infer from source |
| `13ed376` | Update CLAUDE.md: assumed_size_arrays is now optional |
| `9cc0faf` | Hard error when assumed-size array has no source evidence |

## Lesson

The assumed-size array system evolved through three stages:
1. **Manual guess** (2000) — simple, wrong
2. **Validator** (scan + warn) — caught the bug retroactively, but clunky
3. **Proposer** (detect + infer + confirm) — unified, with hard error for the no-evidence edge case

The progression from validator to proposer follows a pattern we've seen throughout VIT: infrastructure that starts as a safety net eventually has enough information to be the primary mechanism. The scanner was always capable of proposing sizes — it just wasn't designed that way initially.

The Scenario 4 bug (silent size-0 failure) was caught during design review, before any real extraction could hit it. This validates the practice of walking through hypothetical scenarios before declaring an implementation complete.
