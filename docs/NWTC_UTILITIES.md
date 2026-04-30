# NWTC Utility Library Reference (`vit_nwtc.h` / `vit_nwtc.cpp`)

C++ translations of NWTC_Library functions used by AeroDyn translations. Located at `modules/aerodyn/src/`. All translations `#include "vit_nwtc.h"`. VIT automatically copies both files to kernel directories during verification.

## Design Rationale

NWTC_Library stays Fortran as a module, but individual utility functions needed by AeroDyn are translated to C++ for the pure-C++ end state. Compiled as a separate unit (not header-only inline) so implementations exist once regardless of how many translations include the header. KGen kernel verification transitively validates these against the Fortran originals. `reset_to_clean.sh` preserves `vit_nwtc.cpp` (shared utility, not a per-function wrapper stub).

## Constants (header, `static constexpr`)

`Pi`, `TwoPi`, `PiBy2`, `D2R`, `R2D`, `ErrMsgLen`, `ErrID_None`, `ErrID_Warn`, `ErrID_Severe`, `ErrID_Fatal`, `AbortErrLev`

## Functions (13 total)

- **`SetErrStat(errStatLcl, errMsgLcl, errStat*, errMsg*, routineName)`** — accumulate errors from callee calls (from NWTC_Base.f90). Keeps highest severity, concatenates messages with routine name prefix. Has `const char*` and `std::string` overloads. See TRANSLATION_TIPS.md for usage pattern.

- **`EqualRealNos(a, b)`** — epsilon-based relative tolerance comparison (from `EqualRealNos8` in NWTC_Num.f90)

- **`fZeros(x, f, n, roots, roots_size, nZeros, has_period, period)`** — zero-crossing finder with optional periodic wraparound (from `fZero_R8`). Arrays are 0-based. `has_period`/`period` replace the Fortran OPTIONAL parameter.

- **`InterpExtrapStp(XVal, XAry, YAry, Ind, AryLen)`** — linear interpolation/extrapolation with stepping index search (from NWTC_Num.f90). `Ind` is **1-based** (Fortran convention) — caller initializes to 1. Arrays are 0-based.

- **`InterpStp(XVal, XAry, YAry, Ind, AryLen)`** — linear interpolation with stepping (no extrapolation, clamps to endpoint values). Same calling convention as InterpExtrapStp.

- **`kernelSmoothing(x, f, n, bw, kernelType, xs, fs, ns)`** — kernel density smoothing with configurable kernel type (Epanechnikov, Quartic, Biweight, Triweight, Tricube, Gaussian). Constants: `kernelType_EPANECHINIKOV` through `kernelType_GAUSSIAN`.

- **`MPi2Pi(angle)`** — normalize angle to [-Pi, Pi]. Inline (header-only). Uses `fmod` + conditional adjustment to match Fortran `MODULO` semantics.

- **`LocateBin(XVal, XAry, AryLen)`** — binary search returning 1-based lower-bound index. Returns 0 if below range, AryLen if above. Arrays are 0-based in C++.

- **`CubicSplineInterpM(X, XAry, YAry, Coef, Res, NumPts, nCols, nCoefRows)`** — multi-column cubic spline interpolation. `nCoefRows` is the first dimension of the Coef array — typically `NumPts-1`. Column-major layout for both YAry and Coef. **Critical:** always pass the actual Coef dimension, not NumPts.

- **`CubicSplineInitM(XAry, YAry, Coef, NumPts, NumCrvs, errStat*, errMsg*)`** — compute natural cubic spline coefficients for irregularly-spaced data with multiple curves. Custom Gaussian elimination on tridiagonal system. Output Coef: `[nCoefRows x NumCrvs x 4]` where nCoefRows = NumPts-1.

- **`CubicLinSplineInitM(XAry, YAry, Coef, NumPts, NumCrvs, errStat*, errMsg*)`** — compute linear spline coefficients in cubic format (c2=c3=0). Same array layouts as CubicSplineInitM.

- **`AddOrSub2Pi(OldAngle, NewAngle*)`** — adjusts NewAngle to within Pi of OldAngle by adding/subtracting multiples of 2*Pi. Coarse jump via `int(delta/2Pi)`, then iterative ±2Pi fine adjustment (max 10 steps).

- **`Angles_ExtrapInterp1(Angle1, Angle2, tin[2], Angle_out*, tin_out)`** — angle-aware linear interpolation between two angles. Calls AddOrSub2Pi to adjust Angle2 within Pi of Angle1 before interpolating. Short-circuits if angles are equal. Used by AFI_UA_BL_Type_ExtrapInterp1 for 8 angle fields.

## Array Convention

All arrays are 0-based in C++. Index-tracking parameters (`Ind` in InterpStp/InterpExtrapStp, return from LocateBin) are 1-based to match Fortran convention.
