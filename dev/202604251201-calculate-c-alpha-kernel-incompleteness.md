# Calculate_C_alpha: KGen Kernel Incompleteness Cascade
**Date:** 2026-04-25
**Status:** in-progress
**Scope:** VIT kernel verification, KGen reduced module copies

## Background

Calculate_C_alpha is the fifth AirfoilInfo function and the first to call LAPACK (via NWTC Library's `LAPACK_gels` wrapper). This is the first function whose transitive dependency chain reaches deep into the NWTC framework — and it exposed a cascade of KGen kernel incompleteness issues that previous functions (Calculate_Cn, FindBoundingTables, Compute_iLoweriUpper, ComputeUA360_CnOffset) didn't trigger because they had no external library dependencies.

## The Function

```fortran
SUBROUTINE Calculate_C_alpha(alpha, Cn, Cl, Default_Cn_alpha, Default_Cl_alpha, Default_alpha0, ErrStat, ErrMsg)
```

Does least-squares fitting via LAPACK `dgels` to compute Cn_alpha (slope of Cn vs alpha), Cl_alpha, and alpha0 (zero-lift angle). Called during `CalculateUACoeffs` initialization. Has a `CHARACTER(*)` output parameter for error messages and calls two NWTC Library functions: `LAPACK_gels` and `EqualRealNos`.

## Extraction: Two Blockers Solved

### 1. Cross-module instrumentation (solved in this session)

KGen instruments `NWTC_Base_reduced.f90` (icefloe module) because of the transitive dependency chain Calculate_C_alpha → LAPACK_gels → NWTC_Base. VIT only builds the aerodyn target. Solution: `fix_cross_module_instrumentation()` in extract.py transplants `kw_*` subroutines from cross-module files into the in-target module file that defines the same Fortran MODULE. General solution, not OpenFAST-specific. Committed at VIT `5ed3c52`.

### 2. Silent build failure (solved earlier in this session)

KGen swallows all build/run errors from the state capture `make` command. Solution: KGen now prints stdout/stderr on non-zero return, VIT scans for error lines. Committed at KGen `0705f2d`, VIT `073e363`.

Extraction now succeeds: 20 state files from `ad_MHK_RM1_Fixed` test case.

## Translation: Written

C++ translation written at `translations/AirfoilInfo/calculate_c_alpha.cpp`. Calls LAPACK `dgels_` directly (no NWTC wrapper), implements `EqualRealNos` inline. Compiles cleanly.

## Kernel Verification: Blocked by Dependency Cascade

KGen bundles full copies of files in the direct call chain but **reduced** copies of transitive dependencies. The reduced copies strip out everything except what KGen's parser thinks is needed for the specific call site. For Calculate_C_alpha, KGen bundles:

- `NWTC_LAPACK.f90` — **full copy** (contains `LAPACK_DGELS` and `LAPACK_SGELS`)
- `NWTC_Base_reduced.f90` — **reduced** (has `ErrID_Fatal`, `AbortErrLev`, but NOT `SetErrStat` or `ErrMsgLen`)
- `NWTC_IO.f90` — **reduced** (has type definitions, but NOT `AllocAry`)

The full `NWTC_LAPACK.f90` calls functions that don't exist in the reduced copies:

| Missing Symbol | Called By | Defined In (full) | Present in Kernel | Status |
|---------------|----------|-------------------|-------------------|--------|
| `ErrMsgLen` | NWTC_LAPACK (CHARACTER declarations) | NWTC_Base.f90 | No | **Fixed** — VIT inlines `CHARACTER(8196)` |
| `dgels_` / `sgels_` | NWTC_LAPACK (LAPACK call) | liblapack.so | Not linked | **Fixed** — VIT adds `-llapack -lblas` to Makefile |
| `SetErrStat` | NWTC_LAPACK (error handling) | NWTC_Base.f90 | No | **Blocking** |
| `AllocAry` | NWTC_LAPACK (workspace allocation) | NWTC_IO.f90 | No | **Blocking** |

### Issues Solved (committed at VIT `423e8ff`)

1. **`CHARACTER(*)` type mapping** — `type_map.py` now maps `CHARACTER(*)` to `char*` with `character_len_param="*"` instead of rejecting it.

2. **`CHARACTER(*)` loop bounds** — `interface_gen.py` now uses `LEN(argname)` for CHARACTER(*) copy loops instead of the literal `*` (which is invalid Fortran).

3. **Missing `ErrMsgLen` constant** — `kernel_verify.py` detects `CHARACTER(IDENTIFIER)` patterns where IDENTIFIER is undefined, and inlines the known literal value. Also adds a dummy INTEGER variable for the sequential binary `READ` that KGen generates.

4. **LAPACK library linking** — `kernel_verify.py` adds `-llapack -lblas` to the kernel Makefile link line when `NWTC_LAPACK` is present.

### Issues Still Blocking

5. **Missing `SetErrStat`** — Called by NWTC_LAPACK's error handling code after `dgels` returns non-zero INFO. The subroutine is defined in `NWTC_Base.f90` (full, 20 lines) but absent from the kernel's `NWTC_Base_reduced.f90`.

6. **Missing `AllocAry`** — Called by NWTC_LAPACK to allocate the LAPACK workspace array. Defined in `NWTC_IO.f90` (full, generic interface with multiple type-specific implementations) but absent from the kernel's reduced `NWTC_IO.f90`.

Both are only called on LAPACK error/allocation paths, not in the normal compute path. During kernel verification, `dgels` should succeed for all 20 cases (the state data was captured during a successful simulation), so `SetErrStat` and `AllocAry` should never actually execute. But the linker requires them to exist.

## Options Forward

1. **Stub the missing functions** — Add do-nothing stubs for `SetErrStat` and `AllocAry` to the kernel. Safe because they're error-path-only functions that shouldn't trigger during verification. Most surgical.

2. **Replace kernel's NWTC_LAPACK.f90** — Swap with a minimal version that calls `dgels_` directly without NWTC error handling. Matches what the C++ translation does. Avoids the entire dependency chain.

3. **Skip Calculate_C_alpha temporarily** — Move to other leaf functions without LAPACK dependencies. Return after building out kernel stub infrastructure.

4. **Add full dependency copies** — Replace KGen's reduced copies with full copies from OpenFAST source. Most general but may pull in more missing dependencies.

5. **General kernel dependency resolution** — Build VIT infrastructure that detects undefined symbols in the kernel after patching and automatically stubs them, or pulls in full copies from the project source tree. Most robust long-term solution.

## Root Cause

KGen bundles framework files (NWTC_LAPACK, NWTC_IO, etc.) as complete files because they're in the target function's USE chain, but it "reduces" their dependencies (NWTC_Base, etc.) by stripping out everything it doesn't think is needed. The reduction is too aggressive — it doesn't account for the fact that the bundled framework files have internal call chains to functions in the reduced dependencies.

This is a general KGen limitation that will affect any function calling NWTC Library utilities (LAPACK, matrix operations, error handling, file I/O). As we move into BEMT and FVW, these dependencies will be even more common.

## Commits This Session

| Repo | Commit | What |
|------|--------|------|
| KGen | `0705f2d` | Surface build/run errors from state capture |
| VIT | `073e363` | Surface errors in CLI when 0 state files |
| VIT | `5ed3c52` | Cross-module instrumentation fix (subroutine transplant) |
| VIT | `d39e146` | EXTRACTION_INTERNALS.md (446-line pipeline reference) |
| VIT | `423e8ff` | CHARACTER(*) support + kernel incompleteness patches |
| VIT | `afb9c35` | Dev note: cross-module instrumentation fix |
| OpenFAST | `6a655a64f` | Calculate_C_alpha golden kernel (20 cases) |
