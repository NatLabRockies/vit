# `vit_nwtc`: NWTC Library Routines in C++ (AeroDyn)

Part of the AeroDyn case study. `vit_nwtc.h` and `vit_nwtc.cpp`, in the OpenFAST fork's
`modules/aerodyn/src/`, hold C++ translations of the NWTC Library routines that translated AeroDyn
code calls.

## Why it exists

The NWTC Library stays Fortran: it is shared by all of OpenFAST, and AeroDyn crosses into it
through C interop (key decision KD5 in the AeroDyn plan). But a C++ translation that called a small
NWTC routine, such as an interpolation or an error-status update, would cross back into Fortran for
each call. So the routines that translated code calls are translated once, here, and shared.

Small routines are `inline` in the header; larger ones are declared in the header and implemented in
`vit_nwtc.cpp`, so each exists once however many translations include it. As of the fork's
`db0796299`, 31 of the 39 translation sources include the header.

## How VIT uses it

- **Listed in `vit.yaml` `shared_files`,** so VIT copies both files into each kernel directory for
  verification. They already live in the directory the integrated build compiles.
  `reset_to_clean.sh` leaves `vit_nwtc.cpp` in place: it is shared code, not a per-function file to
  stub.
- **Read into every translation prompt.** `vit translate` reads the header's declarations
  (`vit/header_api.py`) and lists them under "Shared functions available to call", so a translation
  calls them instead of reimplementing them inline. The `//` comment line immediately above a
  declaration is carried along, which is where a function's conventions belong.
- **Verified through its callers.** The routines are exercised by the kernels, test-validate
  harnesses and simulation baselines of the translations that call them.

## Reference

**The header is the reference.** Each declaration names the NWTC routine it translates, with its file
and line, and describes its array layouts and index conventions. This note does not repeat the
signatures: an earlier version did, and drifted from the header in a function count and a signature.

Contents as of 2026-09-30, with the NWTC routine each translates:

| Purpose | Functions |
|---|---|
| Error handling | `SetErrStat` (NWTC_Base `SetErrStat`; `const char*` and `std::string` overloads), `setErrMsg` (pads and fills an error-message buffer), `num2lstr` (as `Num2LStr`, for messages) |
| Comparison | `EqualRealNos` (`EqualRealNos8`) |
| Interpolation | `InterpExtrapStp`, `InterpStp` (`InterpStpReal8`), `LocateBin`, `CubicSplineInitM`, `CubicLinSplineInitM`, `CubicSplineInterpM` |
| Angles | `MPi2Pi` (`MPi2Pi_R8`), `AddOrSub2Pi` (`AddOrSub2Pi_R8`), `Angles_ExtrapInterp1` (`Angles_ExtrapInterp1_R8`) |
| Other | `fZeros` (`fZero_R8`), `kernelSmoothing` |

Constants: `Pi`, `TwoPi`, `PiBy2`, `D2R`, `R2D`; `ErrMsgLen`, `ErrID_None`, `ErrID_Warn`,
`ErrID_Severe`, `ErrID_Fatal`, `AbortErrLev`; and the `kernelType_*` selectors for
`kernelSmoothing`.

## Conventions

- **Arrays are 0-based.** Index-tracking arguments are not: the `Ind` argument of `InterpStp` and
  `InterpExtrapStp`, and the value `LocateBin` returns, are 1-based, as in Fortran. Callers start
  `Ind` at 1.
- **Multi-dimensional arrays are column-major**, as in Fortran. The spline coefficient array is
  `[nCoefRows × curves × 4]` with `nCoefRows = NumPts - 1`; pass its actual first dimension.
- **A Fortran `OPTIONAL` argument becomes a flag and a value**, as `has_period` and `period` in
  `fZeros`.
- **The constants are compile-time here and run-time in Fortran.** `Pi`, `D2R` and `R2D` are
  `constexpr` in the header, but in the NWTC Library they are set by `NWTC_Init`. A Fortran test
  bridge must call `NWTC_Init()` first (see the AeroDyn operating manual's pitfalls).

## Adding a function

Translate the NWTC routine once, here, rather than inline in a translation. If it is small (under
about 15 lines, no complex loops), make it `inline` in the header; otherwise declare it in the header
and implement it in `vit_nwtc.cpp`. Put a one-line comment directly above the declaration stating
its convention: that is the line VIT shows the next translation.
