# VIT Adaptation Issues for AeroDyn — Systematic Catalog
**Date:** 2026-04-24
**Status:** in-progress
**Scope:** All issues discovered during Phase 1 proof-of-concept (Calculate_Cn extraction + verification)

## Summary

The first AeroDyn extraction succeeded (Calculate_Cn, 20-21 cases). But kernel verification revealed a chain of issues that need systematic fixes before `vit verify` can pass on AeroDyn. These are all consequences of differences between ROSCO (VIT's original target) and AeroDyn/OpenFAST.

## Issue Chain

### 1. KGen preprocessor resolution (FIXED)

**Problem:** KGen resolves `#ifdef OPENFAST_DOUBLE_PRECISION` in SingPrec.f90 but picks the wrong branch because it doesn't receive `-D` defines from the project's cmake build.

**Root cause:** VIT didn't pass `-D` flags to KGen. KGen supports `-D MACRO` on its CLI but VIT's `generate_kgen_command()` had no support for it.

**Fix implemented:** 
- Added `preprocessor_defines` field to vit.yaml (`fortran.preprocessor_defines: [OPENFAST_DOUBLE_PRECISION, HAS_FORTRAN2008_FEATURES]`)
- Added `preprocessor_defines` parameter to `generate_kgen_command()` and `run_kgen_extract()`
- Wired through config loading and CLI handler
- KGen now receives `-D OPENFAST_DOUBLE_PRECISION -D HAS_FORTRAN2008_FEATURES`

**Result:** KGen correctly resolves `ReKi = R8Ki` in the kernel's SingPrec.f90.

### 2. ISO_FORTRAN_ENV unresolved symbols (FIXED)

**Problem:** After fixing the preprocessor, KGen resolves the `#ifdef` correctly but can't resolve `real64`, `int32` from `ISO_FORTRAN_ENV` (a Fortran 2008 intrinsic module). The kernel's SingPrec.f90 has `R8Ki = real64 !!! UNRESOLVED !!! real64` but no `USE ISO_FORTRAN_ENV` statement.

**Root cause:** KGen strips USE statements for modules it can't find/parse. `ISO_FORTRAN_ENV` is a compiler intrinsic — there's no source file for it. KGen keeps the symbol references but drops the USE.

**Fix implemented:** Added patch in `_patch_kernel_fortran()` that detects `UNRESOLVED` markers with `real64`/`int32`/`real32` and injects `USE, INTRINSIC :: ISO_FORTRAN_ENV` after the `USE kgen_utils_mod` line.

### 3. Wrapper function missing USE Precision (NOT YET FIXED)

**Problem:** VIT replaces the original `Calculate_Cn` body with a C-calling wrapper, but the wrapper uses `REAL(ReKi)` in its declarations without importing the `Precision` module. In the original code, `Calculate_Cn` is a module procedure inside `AirfoilInfo` which USEs `Precision`. But VIT's wrapper is placed outside the module scope (after `END SUBROUTINE calculateuacoeffs`).

**Root cause:** VIT generates the wrapper function as a standalone procedure. For ROSCO, kind aliases like `ReKi` were resolved via `-fdefault-real-8` (compiler flag promoting all REAL to 8 bytes), so explicit kind parameters weren't needed. For AeroDyn, ReKi is defined in the `Precision` module — the wrapper needs `USE Precision` or must use explicit types.

**Fix needed:** Either:
- (a) Add `USE Precision` to the wrapper function, OR
- (b) Replace `REAL(ReKi)` with `REAL(8)` (or `REAL(R8Ki)`) in the wrapper using kind_aliases from vit.yaml, OR
- (c) Keep the wrapper inside the module scope (before `END MODULE`)

Option (b) is most robust — it uses information VIT already has (kind_aliases) and doesn't depend on module availability.

### 4. Syntax error in wrapper result declaration (NOT YET FIXED)

**Problem:** VIT generates `DIMENSION(SIZE(ALPHA)` — missing closing parenthesis. Should be `DIMENSION(SIZE(ALPHA))`.

**Root cause:** Bug in VIT's wrapper generation for FUNCTION results that use `SIZE()` to determine array dimensions.

**Fix needed:** Fix the parenthesization in `generate_fortran_wrapper()` or wherever the result dimension is computed.

### 5. Kernel Fortran flags propagation (FIXED)

**Problem:** The kernel Makefile uses `${FC_FLAGS_SET_0}` which VIT now populates from `fortran.flags` in vit.yaml. Initially this was just `-ffp-contract=off`. Updated to include `-cpp -fdefault-real-8 -fdefault-double-8 -ffp-contract=off` to match the project's compiler flags.

**Why these flags matter:**
- `-cpp`: enables C preprocessor for `.f90` files (gfortran only preprocesses `.F90` by default)
- `-fdefault-real-8`: promotes `REAL` (without explicit kind) to 8 bytes — matches OpenFAST's build
- `-fdefault-double-8`: promotes `DOUBLE PRECISION` to 8 bytes — matches OpenFAST's build
- `-ffp-contract=off`: disables FMA for bit-identical verification

### 6. Build wrapper issues (FIXED in earlier session)

Three bugs fixed during extraction:
- sed pattern matched `generate_f90_types()` instead of library source list
- kgen_utils.f90 placed in wrong directory
- Docker timestamps prevented cmake recompilation

See dev note `202604241004` for details.

## Issues NOT Related to VIT (Already Working)

- KGen extraction on AeroDyn cmake build ✓ (OQ2 answered)
- include.ini generation with broad source_dir ✓
- kgen_utils.f90 injection and compilation ✓
- State capture with correct call site (line 974) ✓
- vit.yaml configuration for AeroDyn ✓

## Priority Order for Fixes

1. **Issue 3 (USE Precision / kind resolution in wrapper)** — blocks verification
2. **Issue 4 (DIMENSION syntax error)** — blocks verification
3. Both are in `kernel_verify.py`'s wrapper generation code

Issues 1, 2, 5, 6 are already fixed.

## Broader Observation

ROSCO's simplicity (single module, `-fdefault-real-8`, DLL target, no preprocessor) meant VIT never encountered these issues. AeroDyn exposes them because:
- Preprocessor-based precision selection (`#ifdef OPENFAST_DOUBLE_PRECISION`)
- Intrinsic module dependencies (`ISO_FORTRAN_ENV`)
- Module-level kind parameters (`USE Precision`)
- Multi-target cmake build
- Executable target (not shared library)

Each fix is small, but the cumulative effect is significant. These are all one-time fixes — once VIT handles them, they work for all AeroDyn functions.

## Related

- Dev note `202604241004` — first extraction (extraction-side bugs)
- Dev note `202604241026` — precision mismatch discovery
- `vit/vit/extract.py` — extraction fixes (preprocessor_defines, sed pattern, touch)
- `vit/vit/kernel_verify.py` — verification fixes (ISO_FORTRAN_ENV patch)
- `vit/vit/config.py` — new preprocessor_defines field
