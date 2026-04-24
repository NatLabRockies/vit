# First Fully Verified AeroDyn Function — Calculate_Cn 20/20 IDENTICAL
**Date:** 2026-04-24
**Status:** completed
**Scope:** Phase 1 proof-of-concept — full `vit verify` with field log comparison on AeroDyn

## Summary

Calculate_Cn is the first AeroDyn function to pass full VIT verification: 20/20 cases IDENTICAL, with Fortran↔C++ field log comparison enabled (no `--no-field-log` workaround). This required fixing the Fortran reference run to selectively restore only the wrapper function body while preserving field logging patches.

## The Problem

VIT's two-run verification (Fortran reference vs C++ translation) failed because the Fortran reference run restored the entire target file to clean KGen state, stripping the field logging patches along with the wrapper. The Fortran run produced no `[VIT_FIELD]`/`[VIT_ARRAY]` output, causing the comparison to report "present in C++ but not Fortran."

### Why AeroDyn Differs from ROSCO

In ROSCO, the call site and target function were in **different files** (e.g., call site in Controllers.f90, definition in Functions.f90). KGen's verification subroutines went in the caller's file. VIT's logging patches went in the Types file. The target file only had the function body replacement. Restoring it for the Fortran reference run preserved all logging.

In AeroDyn, the call site (CalculateUACoeffs) and target function (Calculate_Cn) are **module procedures in the same file** (AirfoilInfo.f90). KGen's verification subroutines, VIT's logging patches, and the wrapper replacement all land in the same file. Restoring the file strips everything.

This is a general pattern in AeroDyn — module procedures that call each other are typically in the same source file.

## The Fix

Changed `_run_fortran_reference()` in kernel_verify.py to selectively restore only the wrapper function body, not the entire file.

**Before:** restore entire file from `.kgen` backup → loses logging patches.

**After:**
1. Extract the original function body from the `.kgen` backup (bounded by `FUNCTION Calculate_Cn(...)` to `END FUNCTION Calculate_Cn`)
2. Find the wrapper body in the VIT-patched file (same boundaries)
3. Splice: replace only the wrapper lines with the original Fortran lines, keeping everything else

Added `_extract_function_body()` helper that finds a function/subroutine by name and returns its line boundaries and content. Uses the same boundary-detection logic as `_replace_function_body()`.

The save/restore sandwich is preserved — after the Fortran reference run, the file returns to its fully-patched state for the C++ run.

## Result

```
✓ VERIFICATION PASSED: 20/20 passed
  Field log: kernel/Calculate_Cn/verify_fields.csv (20 entries)
```

All 20 entries: `IDENTICAL`, 200 values each, zero differences.

## Full Chain Summary

This session resolved every issue in the extract→verify chain for AeroDyn:

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| KGen can't find NWTC Library | `source_dir` too narrow | Broadened to `modules/` |
| CMakeLists sed matches wrong line | Pattern hit `generate_f90_types()` | Anchored to standalone source entries |
| kgen_utils.f90 wrong directory | Placed in `source_dir` not cmake src/ | Derive from `cmake_file` path |
| cmake skips recompilation | Docker timestamp issue | Touch source files before build |
| KGen resolves wrong `#ifdef` branch | No `-D` flags passed to KGen | Added `preprocessor_defines` config + `-D` flags |
| Kernel missing ISO_FORTRAN_ENV | KGen strips intrinsic module USE | Inject `USE ISO_FORTRAN_ENV` during patch |
| Wrapper uses undefined ReKi | Project kind outside module scope | Resolve kind aliases (`REAL(ReKi)` → `REAL(8)`) |
| Parser truncates nested parens | `[^)]*` regex | Balanced-paren matching |
| PUBLIC removed at module level | Global removal pattern | Scope to after CONTAINS only |
| Unnecessary REAL() conversion | Same-size kind names differ | Compare byte sizes not names |
| IN_TOL not IDENTICAL | FMA mismatch (capture vs verify) | Rebuild with `-ffp-contract=off` |
| Fortran reference has no field log | Entire file restored for reference | Selective function body restore |

## Related

- Dev note `202604241337` — FMA hypothesis confirmed
- Dev note `202604241122` — first verification (IN_TOL, workaround)
- Dev note `202604241051` — VIT adaptation issues catalog
- Dev note `202604241026` — kernel precision mismatch
- Dev note `202604241004` — first extraction
