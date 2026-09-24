# Calculate_C_alpha: Full Pipeline Complete
**Date:** 2026-04-25
**Status:** completed
**Scope:** AeroDyn Phase 1, VIT infrastructure

## What Was Done

Calculate_C_alpha is the fifth AirfoilInfo function integrated — and the first with deep framework dependencies (LAPACK, NWTC error handling, CHARACTER(*) parameters). Its extraction, translation, verification, and integration required building seven pieces of VIT infrastructure that didn't exist at the start of the session.

## The Function

Computes Cn_alpha (slope of normal force coefficient vs angle of attack), Cl_alpha, and alpha0 (zero-lift angle) by least-squares fitting via LAPACK's `dgels`. Called during `CalculateUACoeffs` initialization for each airfoil table. Has `CHARACTER(*)` error output, calls `LAPACK_gels` and `EqualRealNos`.

## Infrastructure Built (7 Advances)

### 1. EXTRACTION_INTERNALS.md (VIT `d39e146`)
446-line reference documenting every step of the KGen+VIT extraction pipeline, 9 failure modes, and a debugging checklist. Created after the second "no state data" failure cost hours of debugging.

### 2. KGen Error Surfacing (KGen `0705f2d`, VIT `073e363`)
KGen's `run_shcmd` captured but silently discarded build/run output. Added 3 lines of Python 2.7 to print on non-zero return code. VIT's CLI now scans for error lines and displays them. Turned "no state data captured" from a 2-hour mystery into a 2-second diagnosis.

### 3. Cross-Module Instrumentation Fix (VIT `5ed3c52`)
KGen instruments files across cmake target boundaries. VIT's build wrapper now detects cross-module instrumented files (by comparing state/Makefile paths against the cmake target directory), extracts kw_* subroutines, finds the in-target module file by MODULE name, and transplants them. General — no hardcoded paths or module names.

### 4. CHARACTER(*) Type Mapping (VIT `8036548`)
VIT's type mapper now handles `CHARACTER(*)` assumed-length parameters: maps to `char*` with `character_len_param="*"`, and the interface generator uses `LEN(argname)` for copy loop bounds (standard Fortran intrinsic).

### 5. CHARACTER(*) INTENT(OUT) Reverse Copy (VIT `08d471d`)
VIT's Fortran wrapper converted CHARACTER → C_CHAR before the C call but never copied back. For INTENT(OUT/INOUT) CHARACTER parameters, the C++ function's output was lost. Added post-call reverse copy loop: `ErrMsg_c → ErrMsg`.

### 6. Compile-Error-Driven Kernel Repair (VIT `48adfca`)
The big one. KGen's reduced kernel modules are intentionally incomplete. Instead of using fragile heuristics (line count thresholds, hardcoded constants), VIT now:
1. Attempts `make build`
2. Parses gfortran/linker errors into structured objects
3. Fixes each error using the project source tree
4. Repeats until the build succeeds

Handles three error classes:
- **missing_constant**: `CHARACTER(ErrMsgLen)` → finds PARAMETER in project source, inlines literal
- **undefined_symbol**: adds declarations for state-read variables or inlines PARAMETERs
- **link_undefined**: LAPACK/BLAS detection by naming convention (adds `-llapack -lblas`), or finds Fortran subroutine in project source and injects into kernel module

Calculate_C_alpha required 5 iterations and 7 automatic fixes. No hardcoded constants, module names, or project-specific logic.

### 7. UTF-8 Decode Handling (VIT `48adfca`)
Kernel output may contain binary garbage from CHARACTER buffer comparisons. `_build_and_run_kernel` now uses `errors="replace"` when decoding subprocess output.

## Calculate_C_alpha Results

- **Extraction:** 20 cases from `ad_MHK_RM1_Fixed` (56 hits in coverage data)
- **Translation:** Calls LAPACK `dgels_` directly, implements `EqualRealNos` inline, blank-fills ErrMsg buffers
- **Verification:** 20/20 IDENTICAL (all numeric fields, plus errmsg2 via Fortran-reference comparison)
- **Integration:** 16/16 baseline regression pass

## C++ Translation Notes

- Calls LAPACK `dgels_` directly via `extern "C"` (no NWTC wrapper)
- Column-major arrays for LAPACK compatibility (A is M×2, B is max(2,M)×2)
- LAPACK workspace query pattern: call with LWORK=-1, then allocate, then solve
- `EqualRealNos` reimplemented inline: `|a-b| ≤ max(|a+b|, 1) × 100ε/2`
- ErrMsg blank-filled with `memset(ErrMsg, ' ', 8196)` to match Fortran CHARACTER semantics

## Session Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `d39e146` | EXTRACTION_INTERNALS.md |
| KGen | `0705f2d` | Error surfacing from state capture |
| VIT | `073e363` | CLI error display |
| VIT | `5ed3c52` | Cross-module instrumentation fix |
| VIT | `8036548` | CHARACTER(*) support (reverted NWTC-specific patches, kept general) |
| VIT | `48adfca` | Compile-error-driven kernel repair |
| VIT | `08d471d` | CHARACTER(*) INTENT(OUT) reverse copy |
| VIT | `afb9c35` | Dev note: cross-module fix |
| VIT | `9375357` | Dev note: kernel incompleteness cascade |
| VIT | `c00b9c7` | Dev note: compile-error-driven repair |
| OpenFAST | `6a655a64f` | Calculate_C_alpha golden kernel (20 cases) |
| OpenFAST | `abc0bcf7c` | Calculate_C_alpha integrated, 16/16 regression |

## Current State

5 of 23 AirfoilInfo functions integrated. 8 leaf functions remaining, then 11 mid-level. The infrastructure built for Calculate_C_alpha (especially the kernel repair loop) generalizes to every function with NWTC Library dependencies — which is most of AeroDyn.
