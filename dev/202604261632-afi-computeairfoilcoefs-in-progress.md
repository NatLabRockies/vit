# AFI_ComputeAirfoilCoefs: Translation In Progress — Spline Indexing Bug
**Date:** 2026-04-26
**Status:** completed
**Scope:** AFI_ComputeAirfoilCoefs translation + VIT infrastructure for multi-function translations

## What Was Done

### Infrastructure (completed)

1. **View populator LBOUND fix:** Changed `C_LOC(src%field(1,1,1))` to `C_LOC(src%field(LBOUND(...)))` for all array ranks. This fixes a bug where non-default lower bounds (e.g., `SplineCoefs(:,:,0:3)` with 0-based third dimension) caused the pointer to skip the first slice.

2. **Same-file callee skip:** When translating multiple functions in one `.cpp` file (e.g., 1D + 2D + dispatcher), VIT now skips bridge generation for callees whose `cpp_file` in `vit.yaml` matches the target file. Previously, VIT generated unnecessary Fortran bridges for internal helpers.

3. **Shared-file callee skip:** Callees whose `cpp_file` is in the `shared_files` list (e.g., `CubicSplineInterpM` in `vit_nwtc.cpp`) are also skipped — they have C++ implementations, not Fortran originals. Without this, VIT tried to generate a bridge for `CubicSplineInterpM` and failed on the rank-3 array parameter.

4. **Helper callee detection:** When same-file functions are detected, VIT now also scans their Fortran bodies for callees. `AFI_ComputeAirfoilCoefs2D` calls `FindBoundingTables` — without this scan, the bridge for FindBoundingTables wasn't generated.

### NWTC Utilities (completed)

Added to `vit_nwtc.h/.cpp`:
- **`MPi2Pi(angle)`** — inline, normalizes angle to [-Pi, Pi] via fmod + conditional
- **`LocateBin(XVal, XAry, AryLen)`** — binary search returning 1-based lower-bound index
- **`CubicSplineInterpM(X, XAry, YAry, Coef, Res, NumPts, nCols)`** — multi-column cubic spline interpolation

### Translation (in progress)

All three functions translated in one `.cpp` file:
- `AFI_ComputeAirfoilCoefs` — 3-way dispatcher (AFTabMod 1/2/3)
- `AFI_ComputeAirfoilCoefs1D` — single-table spline lookup
- `AFI_ComputeAirfoilCoefs2D` — two-table interpolation via FindBoundingTables + 1D + linear interp
- `AFI_Output_ExtrapInterp1` — inline linear interpolation on AFI_OutputType fields

### Kernel Extraction (completed)

Extracted from `BEMT.f90:1373` with `ad_BAR_SineMotion`, 20 cases captured.

## Current Status: 1/20 Kernel Pass

Case 1 passes (ConstData path — constant coefficients, no spline). Cases 2-20 fail with moderate differences in Cl, Cd, Cm, fullySeparate, fullyAttached. The pattern is consistent with a 1-based/0-based indexing error in the spline lookup — the interpolation is close but shifted.

Cd0 and Cm0 (from UA_BL scalars) are IDENTICAL across all cases, confirming the Table array marshalling and view struct access are correct. The bug is specifically in the CubicSplineInterpM or LocateBin indexing.

## Suspected Root Cause

The CubicSplineInterpM C++ implementation uses 0-based indexing throughout, but the Fortran original uses 1-based `ILo` from LocateBin. The C++ `LocateBin` returns a 1-based index, and `CubicSplineInterpM` converts it: `int iLo0 = ILo - 1`. The array access `Coef[k * slice + c * NumPts + iLo0]` should be correct for column-major layout. Need to verify the actual values by adding diagnostic output.

## VIT Changes (3 files)

- `vit/vit/view_populator.py` — LBOUND fix for all array ranks
- `vit/vit/kernel_verify.py` — same-file callee skip, shared-file callee skip, helper callee detection, `target_cpp_file` and `shared_files` parameters to `_generate_callee_bridges`
- `vit/vit/derived_type_parser.py` — (unchanged from previous commit)

## OpenFAST Changes

- `modules/aerodyn/src/vit_nwtc.h` — MPi2Pi, LocateBin, CubicSplineInterpM declarations
- `modules/aerodyn/src/vit_nwtc.cpp` — LocateBin, CubicSplineInterpM implementations
- `translations/AirfoilInfo/afi_computeairfoilcoefs.cpp` — full translation (3 functions)
- `vit.yaml` — added 1D, 2D, ExtrapInterp1, CubicSplineInterpM, MPi2Pi to translations registry
- `kernel/AFI_ComputeAirfoilCoefs/` — extracted kernel (20 cases)

## Resolution

The spline indexing bug was in `CubicSplineInterpM`: the SplineCoefs array has `NumAlf-1` rows (one fewer than the knot points), not `NumAlf`. The C++ was using `NumPts` (= `NumAlf`) as the column stride for the coefficient array, reading past the intended data. Fixed by adding a `nCoefRows` parameter to `CubicSplineInterpM` and passing `tab->n_SplineCoefs_dim1` (which is `NumAlf-1`).

Also encountered a production build issue: `reset_to_clean.sh` for extraction replaced all `.cpp` files with stubs, and they weren't restored before integration. Required manual `git checkout HEAD -- modules/aerodyn/src/*.cpp` to restore.

**Final result:** 20/20 kernel IDENTICAL + 16/16 baselines PASS. AFI_ComputeAirfoilCoefs integrated as 13th AirfoilInfo function.
