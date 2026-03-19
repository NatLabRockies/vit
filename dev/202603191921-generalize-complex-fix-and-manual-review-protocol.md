# Generalizing --fix complex and the EXIT_MANUAL Protocol
**Date:** 2026-03-19
**Status:** completed
**Scope:** analyze_types.py, cli.py, confirm.py — generalize COMPLEX fix, add EXIT_MANUAL (11), add --rewrite CLI arg

## Background

Phase 4C implemented four `--fix` modes for `vit analyze-types` to make LocalVariables C-compatible. The initial implementation worked perfectly on ROSCO — all 4 fixes applied, byte-identical simulation output. But a post-implementation review revealed two design issues:

1. **`--fix character_array` was redundant** — it was nearly identical to `--fix character`, just matching a slightly different declaration pattern (`CHARACTER, DIMENSION(:), ALLOCATABLE` vs `CHARACTER(:), ALLOCATABLE`).

2. **`--fix complex` was ROSCO-specific** — the Euler formula regex (`EXP(complexI * (expr))` → `COS`/`SIN`) was hardcoded to match ROSCO's Active Wake Control code. A different codebase using COMPLEX for signal processing, FFTs, or matrix operations would hit patterns this regex doesn't handle.

## The Design Conversation

### First question: "Are the regexes ROSCO-specific?"

The user asked whether the Phase 4C regexes were general or project-specific. Analysis showed:

- **LOGICAL fix: genuinely general.** The patterns (`.True.`/`.False.` assignments, IF conditions, `.NOT.`, function arguments, binary I/O) are the *only* contexts a LOGICAL field can appear in Fortran. No project-specific knowledge needed.
- **CHARACTER fixes: general.** Both scalar and array forms are standard Fortran patterns.
- **BIND(C) fix: general.** Pure syntax transformation.
- **COMPLEX fix: ROSCO-specific.** The Euler formula regex matches one exact pattern from Controllers.f90. Other codebases would need different decompositions.

### First fix: merge character_array into character

Straightforward — `fix_character_allocatable` now tries the more specific array pattern first (requires `DIMENSION` keyword), falls back to the scalar pattern. Removed `--fix character_array` as a CLI option and deleted the duplicate `fix_character_array_allocatable` and `infer_character_array_length` functions. Net -103 lines.

A subtle bug surfaced: the scalar pattern (`CHARACTER` + `ALLOCATABLE`) was too broad and matched the array form too. Fixed by trying array-first and requiring `DIMENSION` in the array matcher.

### Second fix: generalize --fix complex

The first approach was to make `fix_complex_field` handle safe patterns automatically (REAL() extraction, AIMAG(), zero init, I/O split, simple `_re` substitution) and print `[MANUAL]` warnings for anything with complex intrinsics (EXP, CMPLX, CONJG, etc.). The warnings were just strings in `result.changes`.

**Problem discovered during ROSCO testing:** VIT rewrote the type definition (COMPLEX → two REALs) and the safe references, but left the EXP line untouched. The build failed because that line still referenced `%AWC_complexangle` which no longer existed — only `%AWC_complexangle_re` and `%AWC_complexangle_im`.

### Attempt 1: Apply `_re` fallback on manual lines

The first idea was to apply a `_re` substitution on manual lines as a placeholder so the code compiles, then flag them for manual review. This was wrong — the code would compile but produce incorrect results (the imaginary component would be silently dropped). Users shouldn't have to distinguish "this compiled but is semantically wrong" from "this compiled and is correct."

### Attempt 2: Don't write anything until manual lines are resolved (scan-first)

Better idea: VIT does a dry-run scan first. If manual lines exist, it exits 11 (`EXIT_MANUAL`) *before writing anything* to disk. The `[VIT:MANUAL]` block tells the caller exactly which lines need attention. Once the caller provides rewrites, VIT re-runs and applies everything atomically.

### The user's key insight: "Should we handle this like assumption confirmation?"

The user pointed out that EXIT_MANUAL should follow the same pattern as EXIT_CONFIRM:
- EXIT_CONFIRM (10): VIT says "I inferred X" → caller re-runs with `--length 1024`
- EXIT_MANUAL (11): VIT says "I can't rewrite this line" → caller re-runs with `--rewrite 'file:line=replacement'`

### Rejected: Claude edits the file directly

The initial proposal was: VIT exits 11, Claude proposes the rewrite, user approves, Claude edits the source file with the Edit tool. The user rejected this — "You shouldn't edit the file directly. You should give the input to a VIT command." This keeps VIT as the single authority for source modifications, maintaining auditability and consistency.

### Rejected: stdin-based interactive mode

Another idea was VIT reading replacement lines from stdin during the same run. Rejected because:
- Docker exec stdin piping is awkward
- Breaks the stateless CLI pattern
- Not inspectable/reproducible

### Rejected: JSON file for rewrites

Write manual edits to a JSON file, fill it in between runs, re-run with `--rewrites file.json`. Workable but over-engineered for the common case of 1-3 manual lines. The simpler `--rewrite 'file:line=replacement'` flag (repeatable) is sufficient and matches the `--length` pattern.

### Final design: scan-first + --rewrite

The implemented design:

1. **First run** (no `--rewrite`): VIT scans all `%field` references, classifies each as auto or manual. If any manual lines exist → exit 11 with `[VIT:MANUAL]` block. Nothing written.

2. **Claude reads the `[VIT:MANUAL]` block**, understands the context, proposes a rewrite (e.g., "This is an Euler formula, decompose with COS/SIN"), presents it to the user.

3. **User approves** (or adjusts).

4. **Second run** with `--rewrite`: VIT re-scans, finds no unresolved manual lines (all covered by `--rewrite` args), applies everything atomically: type definition rewrite + all auto-rewrites + user-provided rewrites. Exit 0.

The `--rewrite` format is `'filename:line=replacement_code'`. Multiple replacement lines use `\n` as separator within the value. The arg is repeatable (`--rewrite 'A:1=...' --rewrite 'B:2=...'`).

## Implementation Details

### confirm.py additions

- `EXIT_MANUAL = 11` — new exit code
- `ManualEdit` dataclass: `file`, `line`, `reason`, `source_text`, `context`
- `ManualReview` dataclass: `fix_type`, `summary`, `edits[]`, `changes_applied`
- `print_manual_review()` — emits `[VIT:MANUAL]` block to stdout
- `parse_manual_reviews()` — parses blocks from output (for tests)
- Refactored `_parse_block_fields()` shared between assumption and manual parsers

### fix_complex_field restructure

The function now has two phases:

**Phase 1 — Scan:** Iterate all `%field` references. For each line with a complex intrinsic, check if `rewrites` dict has a key for `"filename:line"`. If any are uncovered → populate `result.manual_edits` and return immediately (nothing written).

**Phase 2 — Apply:** Only reached when all manual lines are resolved. Rewrites the type definition, applies all auto-patterns, applies user-provided rewrites (with multi-line support via `\n` splitting), removes complexI declaration.

The complex intrinsic detection regex was also tightened — removed SIN, COS, TAN, ATAN from the "needs manual review" list since these are real-valued functions that appear legitimately in non-complex contexts. Only EXP, CMPLX, CONJG, ABS, SQRT, LOG trigger manual review.

### CLI changes

- Added `--rewrite` arg (repeatable) to `analyze-types` subparser
- Complex dispatch parses `--rewrite` args into a dict, passes to `fix_complex_field`
- Manual edits check moved before the apply/build section — if manual edits exist, emit `[VIT:MANUAL]` and exit 11 before any changes are written

## ROSCO Test Results

Full clean re-application of all Phase 4C fixes with generalized code:

```
vit analyze-types --fix logical --type LocalVariables --field restart --apply         → exit 0
vit analyze-types --fix logical --type LocalVariables --field WriteThisStep --apply   → exit 0
vit analyze-types --fix complex --type LocalVariables --field AWC_complexangle --apply → exit 11 (EXP at Controllers.f90:759)
vit analyze-types --fix complex ... --apply --rewrite 'Controllers.f90:759=..COS/SIN..' → exit 0
vit analyze-types --fix character --type LocalVariables --field ACC_INFILE --length 1024 --apply → exit 0
vit analyze-types --fix bind_c --type LocalVariables --apply                          → exit 1 (cascade)
vit analyze-types --fix bind_c --type WE/FilterParameters/piParams/resParams/rlParams → exit 0
```

Final rebuild: **byte-identical simulation output**. Only 2 lines differ from original Phase 4C commit (minor whitespace from the `--rewrite` formatting).

## Protocol Summary

| Exit code | Meaning | Caller action |
|-----------|---------|---------------|
| 0 | Success | Done |
| 1 | Error | Fix the error |
| 10 (EXIT_CONFIRM) | VIT inferred a value | Confirm or override with CLI flag, re-run |
| 11 (EXIT_MANUAL) | VIT can't auto-rewrite some lines | Propose rewrite, get approval, re-run with `--rewrite` |

The key principle: **VIT never writes incorrect code.** It either writes correct code (exit 0), asks for confirmation of an inference (exit 10), or asks for help with code it can't transform (exit 11). In all cases, the human is in the loop before anything semantically significant happens.

## Related Notes
- [202603191827-phase4c-localvariables-c-compatible.md](202603191827-phase4c-localvariables-c-compatible.md) — Phase 4C initial implementation
