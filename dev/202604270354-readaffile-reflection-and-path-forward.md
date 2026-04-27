# ReadAFfile Reflection and Path Forward
**Date:** 2026-04-27
**Status:** completed
**Scope:** AirfoilInfo module translation progress assessment

## Current State: 16/~20 AirfoilInfo Functions in C++

ReadAFfile (16th function) was the hardest AeroDyn translation to date — not for algorithmic complexity but because it sits at the intersection of three systems with hidden behaviors:

1. **NWTC Library's ParseVar keyword-check-before-consume.** Undocumented behavior: when the expected keyword isn't found on the current line, the line is NOT consumed and the next ParseVar re-reads it. Every BAR airfoil file depends on this — they have fewer UA parameters than the 36 the code expects. A positional parser over-consumes lines; a keyword-checked parser silently skips missing parameters. This is not documented anywhere in OpenFAST.

2. **AFI_Init's conditional column mapping.** When `InCol_Cm=0`, `ColCpmin` shifts from 4 to 3. Six lines of conditional logic in AFI_Init, invisible from ReadAFfile's perspective, that cause 1200 wrong cavitation values in MHK tests if the C++ hardcodes column indices.

3. **Fortran cleanup of uninitialized locals.** The previous session's heap corruption came from keeping the original `Cleanup()` subroutine which called `NWTC_Library_DestroyFileInfoType` on a never-allocated `FileInfo`. Manifested as `free(): invalid pointer` in `ad_init`, far from the source.

## Key Insights

**"Idiomatic C++" means idiomatic I/O with Fortran-compatible semantics.** The pivot away from line-by-line Fortran replication was correct — reimplementing `ProcessComFile` + `FileInfoType` would have been brittle. But "idiomatic" doesn't mean "ignore Fortran's contracts." Keyword-checking before consuming is not a quirk — it's a backward-compatibility contract that airfoil files depend on. The right approach is idiomatic C++ file I/O (`std::ifstream`, `std::getline`, `std::istringstream`) with Fortran-compatible keyword semantics (`parseVarDouble` checks keyword, only advances on match).

**Two-pass file I/O is a proven, reusable pattern.** Second successful use after ROSCO's `ReadControlParameterFileSub`. Pass 1 returns sizes, Fortran allocates, Pass 2 fills. Handles the C++/Fortran ALLOCATABLE impedance mismatch mechanically.

**Error message propagation is not optional.** The first debugging round produced `AFI_Init:` with a blank message because the wrapper never copied `c_errMsg` back to Fortran `ErrMsg`. Adding `setErrMsg` to the C++ and the copy-back loop immediately revealed "failed to parse NumAlf for table 1 in BAR0_AeroDyn_Polar_01.dat" — pointing directly at the symptom. Without that, debugging would have been blind.

## Remaining AirfoilInfo Functions

| Function | Lines | Complexity | Notes |
|----------|-------|-----------|-------|
| AFI_Init | ~246 | Medium | Orchestrator: calls ReadAFfile (done), validates tables, computes spline coefficients. Calls `CubicSplineInitM`/`CubicLinSplineInitM` from NWTC Library — need bridging or translation. |
| AFI_WrTables | ~80 | Low | Debug output: opens file, writes header + data for all tables. |
| AFI_WrHeader | ~70 | Low | Writes column header line to output file. |
| AFI_WrData | ~153 | Low | Writes one row of airfoil data to output file. |

**AFI_Init** is the natural next target — it's the last structural function. Once translated, the entire airfoil initialization path (file read → validation → spline computation → parameter setup) is in C++. The Wr* functions are debug output, lower priority but needed for module completeness.

## Milestone Significance

Completing AirfoilInfo would be the **first complete OpenFAST module translated with VIT** — every public and private function in C++. The module handles airfoil data for AeroDyn's blade element momentum theory, touching every blade station at every timestep. It's a contained, well-tested module (17 regression baselines, 9 airfoil file formats) that exercises the full VIT workflow: view structs for ALLOCATABLE types, two-pass file I/O, NWTC Library bridging, and keyword-compatible parsing.
