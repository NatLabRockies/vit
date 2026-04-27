# Remaining AirfoilInfo Functions — Inventory and Assessment
**Date:** 2026-04-27
**Status:** completed
**Scope:** Inventory of untranslated AirfoilInfo functions, assessment for completion milestone

## Context

14 of ~20 AirfoilInfo functions are integrated. The question: what remains to complete the module, and what does it involve?

## Corrected Inventory

An initial automated scan identified 11 "untranslated" functions, but this overcounted. Several were already translated as static helpers inside existing translation files:

- **CalculateUACoeffs** — function #11, already integrated (20/20 kernel)
- **ComputeUA360_CnOffset** — function #4, already integrated
- **AFI_ComputeAirfoilCoefs1D** — static helper inside `afi_computeairfoilcoefs.cpp`
- **AFI_ComputeAirfoilCoefs2D** — static helper inside `afi_computeairfoilcoefs.cpp`
- **AFI_ComputeUACoefs2D** — static helper inside `afi_computeuacoefs.cpp`

## Actually Untranslated: 6 Functions (~1,027 lines)

### Critical Path: AFI_Init → ReadAFfile

| Function | Lines | Visibility | Description |
|----------|-------|-----------|-------------|
| **AFI_Init** | ~246 | PUBLIC | Module initialization entry point. Reads airfoil files, validates inputs (calls AFI_ValidateInitInput, already in C++), generates spline coefficients via LAPACK. Called from AeroDyn.f90 during AD_Init. |
| **ReadAFfile** | ~459 | PRIVATE | Core file parser. Opens airfoil data files, parses header/table structure line by line, allocates Table/Alpha/Coefs/SplineCoefs arrays, remaps coefficient columns, calls CalculateUACoeffs (already in C++) for UA parameter computation. Called from AFI_Init. |
| **CheckValuesAreUniqueMonotonicIncreasing** | ~19 | PRIVATE | Validates a real array is sorted with no duplicates. Uses EqualRealNos. Called from AFI_Init and ReadAFfile. Trivial. |

### Debugging Output: AFI_Wr* Family

| Function | Lines | Visibility | Description |
|----------|-------|-----------|-------------|
| **AFI_WrTables** | ~128 | PUBLIC | Orchestrator: writes complete airfoil data tables to file for debugging. Calls AFI_WrHeader and AFI_WrData. Called from UA_Dvr_Subs.f90 (commented out in AeroDyn.f90). |
| **AFI_WrHeader** | ~102 | PUBLIC | Writes column headers to output file. File I/O with format string construction. |
| **AFI_WrData** | ~73 | PUBLIC | Writes data rows to output file. Loops over table data. |

## Key Challenges

### 1. File I/O (ReadAFfile)
ReadAFfile is 459 lines of Fortran file parsing — `OPEN`, `READ`, `CLOSE`, line-by-line text processing. In C++, this requires either:
- Reimplementing with `std::ifstream` / C file I/O
- Translating the NWTC file-handling utilities it calls

NWTC routines used by ReadAFfile:
- `OpenFInpFile` — open a file for reading
- `ReadStr` — read a line as string
- `ReadVar` — read a typed variable from a line
- `ReadAry` — read an array from a line
- `Conv2UC` — convert string to uppercase
- `Num2LStr` — number to string
- `GetNewUnit` — get an unused Fortran I/O unit number

These would need C++ equivalents in `vit_nwtc.h/.cpp` or inline in the translation.

### 2. Dynamic Allocation
ReadAFfile allocates:
- `p%Table(:)` — array of AFI_Table_Type (NumTabs tables)
- `p%Table(i)%Alpha(:)` — AOA values per table
- `p%Table(i)%Coefs(:,:)` — coefficient matrix per table
- `p%secondVals(:)` — Re or UserProp values
- Various temporary arrays

AFI_Init allocates:
- `p%Table(i)%SplineCoefs(:,:,:)` — cubic spline coefficient array

In C++, these become `new`/`malloc` with sizes determined during parsing.

### 3. LAPACK Spline Setup
AFI_Init calls `DCSPLINE_SETWORKSPACE_DPL` (a custom LAPACK-based spline setup, not standard LAPACK). This computes the cubic spline coefficients used by CubicSplineInterpM. Options:
- Link against the existing Fortran LAPACK and call via extern "C"
- Reimplement the spline setup in C++ (it's a tridiagonal solve)
- Use a C++ spline library

### 4. No KGen Verification
AFI_Init and ReadAFfile are initialization functions — called once at startup. KGen captures per-timestep state, so these functions are not amenable to kernel verification. They would use simulation-level verification:
- Build with clean Fortran → generate baseline
- Build with C++ → verify output matches

This is the same approach used for ROSCO's Stage B functions (ReadControlParameterFileSub, ReadCpFile).

### 5. Registry-Generated Type Routines
AirfoilInfo_Types.f90 contains ~46 Registry-generated routines (Pack, Unpack, Copy, Destroy, ExtrapInterp for each type). These are NOT part of the hand-written AirfoilInfo module — they're auto-generated boilerplate. They will be handled by the Phase 0 Registry C++ generator (already complete), not by hand translation.

## Dependency Graph

```
AFI_Init (untranslated)
├── AFI_ValidateInitInput (INTEGRATED — C++)
├── ReadAFfile (untranslated)
│   ├── CalculateUACoeffs (INTEGRATED — C++)
│   ├── CheckValuesAreUniqueMonotonicIncreasing (untranslated — trivial)
│   └── [NWTC file I/O utilities] (need C++ equivalents)
├── CheckValuesAreUniqueMonotonicIncreasing (untranslated — trivial)
└── DCSPLINE_SETWORKSPACE_DPL (LAPACK — external)

AFI_WrTables (untranslated — debugging)
├── AFI_WrHeader (untranslated)
└── AFI_WrData (untranslated)
```

## Assessment

Completing AirfoilInfo means translating 6 functions (~1,027 lines), but the difficulty is front-loaded in ReadAFfile (459 lines of file parsing). This is a qualitative shift from the math functions we've been doing — more like ROSCO's Stage B work. The NWTC file I/O utilities are the main infrastructure gap.

The `AFI_Wr*` debugging functions (303 lines combined) are simpler — write-only file I/O — and could be deferred if needed, since they're only called from the UA driver (not from production AeroDyn).

The `CheckValuesAreUniqueMonotonicIncreasing` function is 19 lines of trivial logic.

## Recommended Order

1. CheckValuesAreUniqueMonotonicIncreasing (trivial, unblocks AFI_Init/ReadAFfile)
2. ReadAFfile (hardest, requires NWTC file I/O utilities)
3. AFI_Init (depends on ReadAFfile, adds LAPACK spline)
4. AFI_WrTables/WrHeader/WrData (debugging, can defer)
