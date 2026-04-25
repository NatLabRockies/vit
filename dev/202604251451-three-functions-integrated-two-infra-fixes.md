# Three AirfoilInfo Functions Integrated + Two Infrastructure Fixes
**Date:** 2026-04-25
**Status:** completed
**Scope:** AeroDyn Phase 1, VIT kernel verification infrastructure

## Summary

Integrated three AirfoilInfo functions (ComputeUASeparationFunction_zero, ComputeUA360_updateCnSeparated, ComputeUA360_updateSeparationF), bringing the total to 8 AeroDyn functions. Required two infrastructure fixes: view-struct INOUT reverse-copy in kernel verification, and callee PUBLIC visibility in kernel modules.

## Infrastructure Fix 1: View-Struct INOUT Reverse Copy

**Problem:** ComputeUASeparationFunction_zero failed kernel verification at 0/20. The C++ translation correctly modified scalar fields in the view struct (`p->UA_BL.alphaBreakUpper`, etc.), but KGen's comparator read the original Fortran type which was never updated. View structs are one-way copies: Fortran → C. ALLOCATABLE array modifications work (the view holds `C_LOC` pointers into Fortran memory), but scalar fields are copies.

**Root cause:** `kernel_verify.py` line 1110 called `generate_fortran_wrapper(sig, kind_aliases, derived_types, is_kernel=True)` without passing `reverse_copy=True`. VIT already had full reverse-copy infrastructure for integration wrappers (`--reverse-copy` flag) — the kernel verification wrapper just never enabled it.

**Fix:** One-line change: add `reverse_copy=True` to the `generate_fortran_wrapper` call. This is unconditionally safe — the code only emits reverse-copy calls for INOUT/OUT view-type arguments; functions without view types or with IN-only views are unaffected. The generated wrapper now calls `vit_copy_scalars_to_afi_table_type(p_view, p)` after the C++ call, copying all 46 scalar fields (5 top-level + 41 nested UA_BL) back to the original Fortran type.

**Impact:** Unblocks ALL remaining AirfoilInfo functions that modify view-type INOUT scalar fields.

## Infrastructure Fix 2: Callee PUBLIC Visibility in Kernel Modules

**Problem:** ComputeUA360_updateCnSeparated calls ComputeUA360_CnOffset (already integrated). VIT auto-generates a callee bridge that does `USE AirfoilInfo, ONLY: ComputeUA360_CnOffset`. But KGen's reduced AirfoilInfo module has `PRIVATE` default access and only exports `calculateuacoeffs` (the enclosing function). The callee function exists in the module's CONTAINS section but is PRIVATE.

**Fix:** New function `_make_callees_public_in_kernel()` in `kernel_verify.py`. After generating callee bridges, it scans kernel .f90 files to find which modules contain the callee functions. For modules with `PRIVATE` default access, it injects `PUBLIC callee_name` statements. Runs automatically during `setup_kernel_cpp` — no manual intervention needed.

**Impact:** Enables callee bridges for any function defined in a PRIVATE-default module. Previously, callee bridges only worked when the callee was in a PUBLIC-default module or explicitly exported.

## Functions Integrated

### ComputeUASeparationFunction_zero
- **Verification:** 20/20 IDENTICAL (kernel, ad_BAR_SineMotion_UA4_DBEMT3 line 1275)
- **Integration:** `--reverse-copy` (modifies `p%UA_BL.alphaBreakUpper/Lower`, `p%UA_BL.CnBreakUpper/Lower`)
- **Regression:** 16/16 baselines pass
- **Notes:** First function to require reverse-copy in kernel verification. Implements Fortran `MINLOC`/`MAXLOC` with masks manually. Column-major 2D COEFS macro.

### ComputeUA360_updateCnSeparated
- **Verification:** 20/20 IDENTICAL (kernel, ad_MHK_RM1_Fixed line 1089)
- **Integration:** Standard (only modifies ALLOCATABLE Coefs array — no reverse-copy needed)
- **Regression:** 16/16 baselines pass
- **Notes:** Calls ComputeUA360_CnOffset via callee bridge. Uses inline `EqualRealNos` (NWTC Library function, machine-precision comparison). Required the callee PUBLIC visibility fix.

### ComputeUA360_updateSeparationF
- **Verification:** 20/20 IDENTICAL (kernel, ad_MHK_RM1_Fixed line 1088)
- **Integration:** Standard (only modifies ALLOCATABLE Coefs array)
- **Regression:** 16/16 baselines pass
- **Notes:** Most complex leaf function. Uses Fortran `CSHIFT` intrinsic (circular array shift) — translated as a helper function with modular arithmetic. Also calls ComputeUA360_CnOffset via callee bridge and uses inline EqualRealNos. Stack-allocated variable-length arrays (`alpha_(p%NumAlf)`, `f_st(p%NumAlf)`) translated to `std::vector<double>`.

## Additional Lesson: `vit_translated.h` for Callee Calls

Production .cpp files that call other already-integrated functions need `#include "vit_translated.h"` to get the callee `_c` declarations. In kernel verification, VIT auto-generates `vit_kernel_callees.h` which provides these. But in production builds, the shared header is the source of truth. The `vit translate` scaffold doesn't auto-include this — translations that call other translated functions must add it manually.

## Extraction Notes

Both ComputeUA360_update* functions were extracted from `ad_MHK_RM1_Fixed`:
- ComputeUA360_updateSeparationF: call site at line 1088, 224 hits (in CalculateUACoeffs)
- ComputeUA360_updateCnSeparated: call site at line 1089, 56 hits (in CalculateUACoeffs)
- The first call site pair (lines 958/959) has NO coverage for updateSeparationF — only the second pair is hit by MHK test cases.

Coverage data was queried with `query_coverage.py --call-sites` to pick the right test case and line numbers. Both extractions produced 20 state files each.

## State After This Session

**8 AeroDyn functions integrated:**
1. Calculate_Cn
2. FindBoundingTables
3. Compute_iLoweriUpper
4. ComputeUA360_CnOffset
5. Calculate_C_alpha
6. ComputeUASeparationFunction_zero (this session)
7. ComputeUA360_updateCnSeparated (this session)
8. ComputeUA360_updateSeparationF (this session)

**Next in dependency order:**
- ComputeUASeparationFunction_onCl — calls ComputeUASeparationFunction_zero (now integrated)
- ComputeUA360_AttachedFlow — calls Compute_iLoweriUpper (integrated) + fZeros (unknown)
- CalculateUACoeffs — orchestrator, calls most of the above

## Commits

| Repo | What |
|------|------|
| VIT | `reverse_copy=True` in kernel_verify.py, `_make_callees_public_in_kernel()`, dev notes |
| OpenFAST | 3 functions integrated, golden kernel fixtures, vit.yaml updated |
