# Kernel Precision Mismatch — ReKi=R4Ki in Kernel vs R8Ki in Simulation
**Date:** 2026-04-24
**Status:** in-progress
**Scope:** Phase 1 verification — first `vit verify` attempt on AeroDyn

## Summary

After the first successful AeroDyn extraction (Calculate_Cn, 21 cases), the kernel verification initially failed due to a precision mismatch: the kernel's `SingPrec.f90` defines `ReKi = R4Ki` (single precision, 4 bytes) while the simulation was built with `DOUBLE_PRECISION=ON` (`ReKi = R8Ki`, double precision, 8 bytes). State data captured in double precision was read by the kernel as single precision — producing garbage values.

After manually fixing the kernel's SingPrec.f90 to define R8Ki and set `ReKi = R8Ki`, the pure Fortran kernel verification passes for all 21 cases.

## Root Cause

OpenFAST's `SingPrec.f90` uses a C preprocessor conditional:
```fortran
#ifdef OPENFAST_DOUBLE_PRECISION
  INTEGER, PARAMETER :: ReKi = R8Ki   ! 8-byte double
#else
  INTEGER, PARAMETER :: ReKi = R4Ki   ! 4-byte float
#endif
```

The cmake build defines `OPENFAST_DOUBLE_PRECISION` (confirmed: `DOUBLE_PRECISION:BOOL=ON` in CMakeCache.txt), so the simulation uses `ReKi = R8Ki`. But KGen strips preprocessor conditionals when extracting the kernel — it sees the resolved code (without `#ifdef`), which defaults to the `#else` branch: `ReKi = R4Ki`. KGen also strips the `R8Ki` definition entirely since it wasn't in the active branch.

This is a fundamental issue: **KGen's kernel doesn't inherit preprocessor defines from the project's cmake build.** For ROSCO, this wasn't a problem because ROSCO used `-fdefault-real-8` (a compiler flag that promotes all REAL to 8 bytes) rather than preprocessor-based precision selection. VIT's `fortran.flags` config propagated `-fdefault-real-8` to the kernel Makefile, which matched the project build. For AeroDyn, the precision is controlled by preprocessor defines, not compiler flags.

## Manual Fix (confirmed working)

Added R8Ki definition and changed ReKi in the kernel's SingPrec.f90:
```fortran
! Added:
INTEGER, PARAMETER :: R8Ki = SELECTED_REAL_KIND(15, 300)
! Changed:
INTEGER, PARAMETER :: ReKi = R8Ki  ! was R4Ki
```

After this fix, the pure Fortran kernel compiles and all 21 cases pass:
```
Verification PASSED with Calculate_Cn.0.0.1
Verification PASSED with Calculate_Cn.0.0.2
...
```

All cases report "non-identical variables within tolerance: 1" — this is KGen's sum-based comparison, not a precision issue. The tolerance is 1e-14.

## VIT Enhancement Needed

VIT needs to automatically patch the kernel's SingPrec.f90 to match the project's precision settings. Options:

1. **Patch SingPrec.f90 during `vit verify`** — detect `ReKi = R4Ki` and replace with `R8Ki` based on `fortran.kind_aliases` in vit.yaml. Since vit.yaml already has `ReKi: "REAL(8)"`, VIT knows the project uses double precision. Patch the kernel accordingly.

2. **Pass preprocessor defines in `fortran.flags`** — add `-DOPENFAST_DOUBLE_PRECISION` to the kernel build. But KGen's extracted SingPrec.f90 already has the `#ifdef` stripped out, so the define won't help.

3. **New vit.yaml field `fortran.preprocessor_defines`** — explicitly list defines that affect precision. VIT patches the kernel source to reflect them.

Option 1 is the most practical — VIT already has the kind_aliases information and already patches kernel files during verification.

## Other Issues Found During Verification Attempt

### Hung kernel.exe

The first `vit verify` attempt produced a `kernel.exe` that ran for 8+ minutes at 100% CPU before being killed. This was because the kernel was built with `ReKi = R4Ki` — reading 8-byte state data as 4-byte floats produced enormous garbage values that may have caused infinite loops or extreme iteration counts in KGen's verification driver.

### Duplicate `vit verify` runs

I accidentally launched two `vit verify` processes on the same kernel directory, causing interference. Should have waited for the first to complete.

### Kernel Makefile missing FC_0 default

The clean kernel Makefile uses `${FC_0}` and `${FC_FLAGS_SET_0}` without defaults. When run manually outside of `vit verify`, these are empty, causing `make` to use `c` as the compiler. VIT sets these variables when it patches the Makefile, but manual testing requires exporting them.

The existing Makefile already has `FC_0 ?= gfortran` and `FC_FLAGS_SET_0 ?= ...` in the VIT-patched version. The issue is that the `.kgen` backup (clean version) doesn't have these defaults. VIT should consider adding defaults to the clean Makefile or documenting this.

## What's Next

1. Implement the SingPrec.f90 precision patch in VIT's `kernel_verify.py`
2. Re-run `vit verify` with the C++ translation
3. If verification passes, proceed to `vit integrate`

## Related

- Dev note `202604241004` — first AeroDyn extraction (Calculate_Cn, 21 cases)
- `openfast/vit.yaml` — kind_aliases shows `ReKi: "REAL(8)"`
- `openfast/modules/nwtc-library/src/SingPrec.f90` — the project's precision definition
- `openfast/kernel/Calculate_Cn/SingPrec.f90` — the kernel's (wrong) precision definition
