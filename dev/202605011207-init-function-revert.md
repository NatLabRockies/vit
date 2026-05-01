# Reverting Init Functions to Fortran: Why Translation Was the Wrong Move
**Date:** 2026-05-01
**Status:** completed
**Scope:** OpenFAST (DBEMT_Init, AFI_Init), VIT infrastructure (--auto-allocate)

## Picking Up From Dev Notes 202605010708 and 202605010945

Note 0708 flagged the concern: DBEMT_Init's hand-written Fortran wrapper duplicated 60 lines of allocation logic to work around INTENT(OUT) types with ALLOCATABLE fields. Note 0945 documented the fix: VIT's `--auto-allocate` flag was wired up to extract ALLOCATE statements from Fortran source and emit them in the wrapper automatically, eliminating the hand-written wrapper.

That fix worked (25/25 integrate, 17/17 baselines), but a code audit exposed a fundamental problem.

## The Audit Finding

`--auto-allocate` works by regex-matching imperative code patterns:
- ALLOCATE followed by IF/error-call/RETURN/END IF
- DO loops wrapping Registry Copy calls
- Specific error variable naming (`ErrStat2`, `ErrMsg2`)
- Guard conditions via backward IF/END IF depth tracking

These patterns are **coding-style-specific**, not **Fortran-language-specific**. A different codebase (or even a different developer on the same codebase) would structure init functions differently, and the extraction would fail silently or produce wrong output.

Five additional issues were identified:
1. OpenFAST-specific error handling (`SetErrStat(ErrID_Fatal, ...)`, `ErrMsgLen`)
2. ELSE/ELSE IF guard inversion (backward walk returns wrong condition)
3. Magic numbers (5-line lookahead, 3-line lookback)
4. Cosmetic indentation inconsistencies
5. `ErrMsg2` declared even when unused

A plan was created to fix all five, but during that work the deeper question surfaced: **should init functions be translated at all during incremental translation?**

## The Realization

Init functions are fundamentally different from runtime functions:

| Property | Runtime functions | Init functions |
|----------|-------------------|----------------|
| Call frequency | Thousands–millions per simulation | Once at startup |
| Performance benefit from C++ | Yes (hot path) | Zero |
| Content | Computation, math, loops | Allocation + scalar setup |
| INTENT(OUT) with ALLOCATABLEs | Rare (callers pre-allocate) | Universal (init creates types) |
| C++ translation complexity | Straightforward (views work) | Requires allocation workarounds |

The C++ "translation" of DBEMT_Init was 25 lines of computation. The wrapper to make it work was 60+ lines. Net negative in every dimension: more code, more fragility, more maintenance, zero performance gain.

AFI_Init was worse — a hand-written two-pass pattern where C++ pass 1 returns allocation sizes via output pointers, Fortran allocates, then C++ pass 2 fills the arrays. ~190 lines of wrapper for ~100 lines of computation.

## The Decision

**Init functions stay in Fortran during incremental translation.** They get translated directly to pure C++ only when the entire module converts — at that point, ALLOCATABLE becomes `std::vector`/`new`/`malloc`, no Fortran wrapper exists, and the allocation problem disappears.

This is consistent with how init functions already work: DBEMT_Init calls DBEMT_ValidateInitInp (translated, runs via Fortran wrapper) and DBEMT_ReInit (translated, runs via Fortran wrapper). The init function orchestrates; the functions it calls are already in C++. No performance is left on the table.

## What Was Reverted

### DBEMT_Init
- Removed `dbemt_init_c` INTERFACE block from DBEMT.f90
- Replaced auto-allocate C++ wrapper with original Fortran body (127 lines)
- Removed `dbemt_init.cpp` from `modules/aerodyn/src/` and CMakeLists.txt
- Removed `--auto-allocate` integration line from integrate_all.sh
- Removed entry from vit.yaml

### AFI_Init
- Replaced hand-written two-pass C++ wrapper (~190 lines) with original Fortran body (~260 lines)
- Removed `afi_init.cpp` from `modules/aerodyn/src/` and CMakeLists.txt
- Removed entry from vit.yaml
- Updated integrate_all.sh comment and Step 3 restore list

### VIT
- Discarded uncommitted broken changes to `allocate_extract.py` and `interface_gen.py` (verbatim error block extraction that caused variable redeclaration errors)
- `--auto-allocate` infrastructure remains in VIT (it works for its intended purpose) but is not used for DBEMT

## Verification

Both reverts verified independently:
- DBEMT_Init revert: build clean, 17/17 baselines PASS
- AFI_Init revert: build clean, 17/17 baselines PASS

Final commit: `5aee72f9e`

## Translation Count Impact

- **AirfoilInfo:** 20 → 19 functions in C++ (AFI_Init reverted; ReadAFfile, WrHeader, WrData, WrTables remain as hand-integrated C++ wrappers)
- **DBEMT:** 14 → 13 functions in C++ (DBEMT_Init reverted; all Phase A-D runtime functions remain)

## What Remains

Two items from INIT_FUNCTION_ISSUE.md are still open:

1. **Document init function policy** — Update CLAUDE.md and PROJECT_PLAN_AERODYN.md to codify: init functions stay Fortran during incremental translation.
2. **Build caller dependency tracking** — Track which Fortran functions call each translated function, so we know when wrappers can be removed. `query_coverage.py --call-sites` already has the raw data; needs cross-referencing against vit.yaml translations.

## Lesson

The right question isn't "can we make this work?" — it's "should we be doing this at all?" `--auto-allocate` answered the first question (yes, with effort). The audit answered the second (no — the effort-to-value ratio is inverted for init functions). Translating init functions during the incremental phase solves a problem that doesn't exist (init performance) while creating problems that do (allocation bridging, wrapper fragility, maintenance burden).
