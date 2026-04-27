# AFI_Init Integration Complete — 17th AirfoilInfo Function
**Date:** 2026-04-27
**Status:** completed
**Scope:** AFI_Init translation + CubicSplineInitM/CubicLinSplineInitM in vit_nwtc

## Summary

AFI_Init — the AirfoilInfo module initialization orchestrator — has been translated to C++ and integrated. 17/17 baseline regression tests pass bit-identical. This is the 17th AirfoilInfo function integrated, and the last structural function in the module (remaining: 3 debug-output `AFI_Wr*` functions).

## What AFI_Init Does

AFI_Init (originally AirfoilInfo.f90 lines 85-342, ~258 lines) orchestrates the entire airfoil initialization pipeline:

1. Validates initialization inputs (delegates to AFI_ValidateInitInput, already C++)
2. Sets column indices for aerodynamic coefficient storage (ColCl, ColCd, ColCm, ColCpmin)
3. Reads the airfoil data file (delegates to ReadAFfile, already C++)
4. Validates multi-table consistency:
   - AFITable_2Re mode: checks UserProp consistency across tables, validates Re >= 0, clamps Re to 0.001 minimum, computes `secondVals = log(Re)`
   - AFITable_2User mode: checks Re consistency, sets `secondVals = UserProp`
   - Verifies secondVals are unique and monotonically increasing
5. Computes cubic spline coefficients per table (CubicSplineInitM for InterpOrd=3, CubicLinSplineInitM for InterpOrd=1)

## Architecture: Two-Pass with Fortran Allocation Wrapper

Follows the same two-pass pattern proven by ReadAFfile (dev note `202604270333`):

**Fortran wrapper** (AirfoilInfo.f90):
1. Packs InitInput into BIND(C) struct
2. Calls AFI_ValidateInitInput (existing)
3. Handles OPTIONAL UnEcho parameter (echo file write)
4. Sets column indices and calls ReadAFfile (existing two-pass wrapper)
5. Populates view struct from Fortran `p`
6. Calls `afi_init_pass1_c` — returns allocation sizes
7. Allocates `p%secondVals` and per-table `p%Table(i)%SplineCoefs`
8. Re-populates view struct (now with SplineCoefs pointers)
9. Calls `afi_init_pass2_c` — fills secondVals and computes spline coefficients

**C++ pass 1** (`afi_init_pass1_c`):
- Multi-table validation logic (lines 175-267 of original)
- Computes secondVals into a temp buffer
- Returns: n_secondVals, secondVals_buf, per-table SplineCoefs dimensions (dim1, dim2)
- ConstData tables get dim1=0 (no spline needed)

**C++ pass 2** (`afi_init_pass2_c`):
- Copies secondVals from temp buffer to allocated array
- Runs CubicSplineInitM or CubicLinSplineInitM per table
- Validates InterpOrd is 1 or 3

## New NWTC Utility Functions: CubicSplineInitM and CubicLinSplineInitM

Both added to `vit_nwtc.h` / `vit_nwtc.cpp`, alongside the existing `CubicSplineInterpM`.

**CubicSplineInitM** (translated from NWTC_Num.f90 lines 742-903):
- Computes natural cubic spline coefficients for irregularly-spaced data
- Handles multiple curves sharing the same X values (vectorized across columns)
- Algorithm: custom Gaussian elimination on tridiagonal system — NOT LAPACK
- Steps: compute spacing (DelX) and slopes, forward elimination, back-substitution for 4 polynomial coefficients per segment
- Output: 3D column-major array `[nCoefRows x NumCrvs x 4]` where nCoefRows = NumPts-1
- Same layout consumed by the already-translated `CubicSplineInterpM`
- Error checks: `EqualRealNos(DelX, 0)` for non-unique X, `EqualRealNos(U, 0)` for singular system
- Uses `std::vector<double>` for 6 temporary arrays (DelX, Slope, U, V, ZHi, ZLo)

**CubicLinSplineInitM** (translated from NWTC_Num.f90 lines 907-957):
- Linear spline coefficients in cubic format: c0 = value, c1 = slope, c2 = c3 = 0
- Same output layout as CubicSplineInitM
- Enables CubicSplineInterpM to handle both cubic and linear interpolation transparently

These are the 12th and 13th functions in vit_nwtc. The full set is now: EqualRealNos, fZeros, InterpExtrapStp, InterpStp, kernelSmoothing, MPi2Pi, LocateBin, CubicSplineInterpM, CubicSplineInitM, CubicLinSplineInitM, AddOrSub2Pi, Angles_ExtrapInterp1.

## Bug Found and Fixed: ColUAf Overwrite

The first build attempt passed 15/17 baselines. The 2 failing cases were both UA-enabled (`ad_BAR_SineMotion_UA4_DBEMT3`, `ad_MHK_RM1_Fixed_UA`), with large differences (max_abs up to 1.99e+07) — clearly not a subtle floating-point issue.

**Root cause:** Pass 1 was setting `p->ColUAf = 0` as part of column index initialization (lines 140-144 of the original Fortran). In the original Fortran, this runs BEFORE ReadAFfile, and ReadAFfile subsequently sets ColUAf to the correct non-zero value. In the new architecture, column indices are already set by the Fortran wrapper before ReadAFfile runs, and ReadAFfile correctly updates ColUAf. But pass 1 (which runs AFTER ReadAFfile) was overwriting ColUAf back to 0.

**Effect:** With ColUAf=0, the unsteady aerodynamics coefficient column is lost. Non-UA test cases don't use this column so they passed. UA-enabled test cases rely on it for separation function and attached-flow coefficients, so they produced completely wrong results.

**Fix:** Removed all column-index-setting from pass 1. The Fortran wrapper already handles this before ReadAFfile, and ReadAFfile's scalar copy-back propagates the correct ColUAf value. Pass 1's responsibility is multi-table validation and SplineCoefs sizing only.

**Lesson:** When splitting an orchestrator into a Fortran wrapper + C++ passes, be precise about which code runs before vs. after each callee. Setting state that a callee depends on must happen in the wrapper (before the callee), not in the C++ pass (after the callee). The original Fortran's linear flow masks these ordering dependencies; the two-pass architecture makes them explicit.

## Files Modified

| File | Change |
|------|--------|
| `modules/aerodyn/src/vit_nwtc.h` | Added CubicSplineInitM, CubicLinSplineInitM declarations |
| `modules/aerodyn/src/vit_nwtc.cpp` | Added implementations (~80 lines math + ~20 lines error handling) |
| `translations/AirfoilInfo/afi_init.cpp` | New: C++ translation (pass1 + pass2, ~130 lines) |
| `modules/aerodyn/src/afi_init.cpp` | New: integrated copy |
| `modules/aerodyn/src/AirfoilInfo.f90` | Replaced AFI_Init body with Fortran two-pass wrapper |
| `modules/aerodyn/src/vit_translated.h` | Added afi_init_pass1_c, afi_init_pass2_c declarations |
| `modules/aerodyn/CMakeLists.txt` | Added src/afi_init.cpp to basicaerolib |
| `openfast/vit.yaml` | Added AFI_Init entry (verification: simulation) |
| `vit/CLAUDE.md` | Updated function count, next steps, vit_nwtc contents |

## Verification

- **Build:** Clean compile, no warnings
- **Baseline regression:** 17/17 pass bit-identical (`verify_aerodyn_baselines.sh all`)
- **No KGen extraction:** AFI_Init is an initialization function called once at startup, not in KGen's per-timestep capture window. Simulation-level verification is the correct approach (same as ReadAFfile).

## Design Decisions

1. **CubicSplineInitM in vit_nwtc, not inline:** The spline init functions are general NWTC utilities that will be needed by other modules. They share the exact 3D coefficient layout with the already-translated CubicSplineInterpM. Placing them in vit_nwtc keeps the utility library cohesive.

2. **Column indices stay in Fortran wrapper:** The column-setting logic (ColCl=1, ColCd=2, etc.) is trivial and runs before ReadAFfile. Keeping it in the Fortran wrapper preserves the correct ordering relative to ReadAFfile. The ColUAf bug proved this was the right call.

3. **Dead code omitted:** The original Fortran declared `REAL(ReKi), ALLOCATABLE :: Coef(:)` (line 105) which was never allocated — the `Cleanup()` subroutine checked `ALLOCATED(Coef)` before deallocating, which was always a no-op. Omitted entirely from the C++ translation.

4. **CheckValuesAreUniqueMonotonicIncreasing inlined:** Rather than adding a build dependency on the separately-translated .cpp file (which isn't in CMakeLists), the 6-line function was inlined as a static helper in afi_init.cpp.

5. **Preprocessor hardcoded:** `#ifndef AFI_USE_LINEAR_RE` is never defined anywhere in the OpenFAST codebase. All existing translations (afi_computeairfoilcoefs.cpp, afi_computeuacoefs.cpp) hardcode the `log(Re)` path. AFI_Init follows the same convention.

## AirfoilInfo Module Status

17 of ~20 functions integrated. Remaining:

| Function | Lines | Priority | Notes |
|----------|-------|----------|-------|
| AFI_WrTables | ~128 | Low | Debug output orchestrator, called from UA_Dvr_Subs.f90 only |
| AFI_WrHeader | ~102 | Low | Writes column headers to output file |
| AFI_WrData | ~73 | Low | Writes data rows to output file |

The Wr* functions are debug-only output (commented out in production AeroDyn.f90). They can be deferred — the entire production initialization path (file read -> validation -> spline computation -> parameter setup) is now in C++. DBEMT (14 procedures, mostly leaf math) is the natural next sub-module.

## Related Dev Notes

- `202604270354` — ReadAFfile reflection and path forward (identified AFI_Init as next target)
- `202604270333` — ReadAFfile integration complete (established two-pass pattern)
- `202604270026` — Remaining AirfoilInfo inventory (assessed AFI_Init complexity)
- `202604262231` — AFI_ComputeUACoefs complete (2D UA interpolation)
