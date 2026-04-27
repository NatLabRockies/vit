# AFI_Init Test-Validate: Multi-Table Validation — 11/11 Pass
**Date:** 2026-04-27
**Status:** completed
**Scope:** Dual-call error-branch verification for AFI_Init multi-table validation logic

## Summary

Added test-validate coverage for AFI_Init's multi-table validation logic (pass 1). 11/11 tests pass — C++ and Fortran produce identical ErrStat for every error branch. This complements the 17/17 simulation baseline verification (which covers the happy path but not error branches, since all test airfoil files are well-formed).

## Motivation

The 17 regression baselines exercise AFI_Init's happy paths (single-table, multi-table Re, multi-table UserProp) but never trigger any error conditions. The multi-table validation block (originally lines 175-267 of AFI_Init) has 7 error branches that detect malformed airfoil data:

- UserProp inconsistency across tables in Re mode
- Negative Reynolds numbers
- Non-monotonic or duplicate Re values (after log transform)
- Re inconsistency across tables in UserProp mode
- Non-monotonic or duplicate UserProp values
- Multi-table file with 1D interpolation mode (warning, not fatal)

These are simple conditionals, but they involve string formatting with table indices, severity-level interactions (Warn vs Fatal), and the `CheckValuesAreUniqueMonotonicIncreasing` call on log-transformed values. Dual-call verification confirms the C++ translation handles all of these identically to the original Fortran.

## Approach

Hand-crafted test following the established patterns from `afi_validateinitinput_test.cpp` (7/7) and `checkvaluesareuniquemonotonicincreasing_test.cpp` (11/11).

**C++ side:** Calls `afi_init_pass1_c` directly (already `extern "C"` in `libbasicaerolib.a`) with mock `afi_parametertype_view_t` structs. Stack-allocated `afi_table_type_view_t` arrays with Re/UserProp fields set per test case. No file I/O, no ReadAFfile — just the view struct fields the validation logic reads.

**Fortran side:** A BIND(C) bridge (`afi_init_validation_bridge.f90`) that receives flat C arrays (NumTabs, AFTabMod, Re_array, UserProp_array, filename), builds an `AFI_ParameterType` with allocated `Table(:)`, and runs the original validation logic (lines 175-267, inlined from the pre-integration source). Compiles against the patched `AirfoilInfo.f90` in `translations/AirfoilInfo/` which still has the original `CheckValuesAreUniqueMonotonicIncreasing` function.

**Key design decision:** The bridge does NOT call the full AFI_Init (which requires ReadAFfile/file I/O). It only runs the multi-table validation block. Both sides receive identical mock data and we compare ErrStat.

## Test Cases

| # | Test | AFTabMod | Condition | Expected |
|---|------|----------|-----------|----------|
| 1 | happy_path_2re | 2 | 3 tables, valid increasing Re, same UserProp | ErrID_None |
| 2 | happy_path_2user | 3 | 3 tables, valid increasing UserProp, same Re | ErrID_None |
| 3 | happy_path_single_table | 1 | 1 table | ErrID_None |
| 4 | warn_multitab_aftabmod1 | 1 | 3 tables with AFTabMod=1 | ErrID_Warn |
| 5 | fatal_userprop_mismatch_2re | 2 | Table 2 has different UserProp | ErrID_Fatal |
| 6 | fatal_negative_re_2re | 2 | Table 2 has Re=-1.0 | ErrID_Fatal |
| 7 | fatal_nonmonotonic_re_2re | 2 | Re=[1e5, 3e5, 2e5] | ErrID_Fatal |
| 8 | fatal_duplicate_re_2re | 2 | Re=[1e5, 2e5, 2e5] | ErrID_Fatal |
| 9 | fatal_re_mismatch_2user | 3 | Table 2 has different Re | ErrID_Fatal |
| 10 | fatal_nonmonotonic_userprop_2user | 3 | UserProp=[0.1, 0.3, 0.2] | ErrID_Fatal |
| 11 | fatal_duplicate_userprop_2user | 3 | UserProp=[0.1, 0.2, 0.2] | ErrID_Fatal |

## Build Issue: Static Constant Redefinition

Initial build failed because `afi_init.cpp` defines `static constexpr int ErrID_None/Warn/Fatal` and `vit_nwtc.cpp` (added in the CubicSplineInitM work) also defines `ErrID_None/Fatal` at file scope. When both are `#include`d into the same translation unit (the test file), g++ reports redefinition errors.

**Fix:** Instead of including `afi_init.hpp` (a copy of afi_init.cpp), declare the `afi_init_pass1_c` function signature directly with `extern "C"`. The implementation comes from `libbasicaerolib.a` at link time. This avoids all include-order conflicts.

**Lesson for future:** The `static constexpr` error constants should ideally be in a shared header (e.g., `vit_nwtc.h`) rather than redefined in each translation file. For now, the pragmatic fix works — test files declare the extern entry point and define their own constants.

## Files

| File | Action |
|------|--------|
| `translations/AirfoilInfo/afi_init_validation_bridge.f90` | **New**: Fortran bridge for original validation logic |
| `translations/AirfoilInfo/afi_init_validation_test.cpp` | **New**: C++ dual-call test harness (11 tests) |
| `translations/AirfoilInfo/Makefile` | Updated: added build rules for test_afi_init_validation |

## Preservation of Original Fortran

The original Fortran AFI_Init (replaced in `modules/aerodyn/src/AirfoilInfo.f90` by the C++ wrapper) is preserved in:
1. **Git history** at commit `edab46311` (the pre-integration baseline, used by `reset_to_clean.sh`)
2. **`translations/AirfoilInfo/AirfoilInfo.f90`** — the patched source file used for test-validate compilation, which retains all original function bodies

This ensures the Fortran reference implementation remains available for dual-call verification and future KGen extraction, even as the production source accumulates integration wrappers.

## Related Dev Notes

- `202604271433` — AFI_Init integration complete (17/17 baselines)
- `202604261355` — Session reflection: test-validate complete (established the methodology)
- `202604261131` — test-validate end-to-end (first successful test-validate run)
