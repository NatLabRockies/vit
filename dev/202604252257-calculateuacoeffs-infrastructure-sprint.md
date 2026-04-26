# CalculateUACoeffs Infrastructure Sprint — 6 VIT Fixes
**Date:** 2026-04-25
**Status:** in-progress
**Scope:** VIT infrastructure (type mapping, bridge generation, field comparison), CalculateUACoeffs translation

## Summary

CalculateUACoeffs is the AirfoilInfo orchestrator (~326 lines, 7 callee bridges, 2 new NWTC utilities). Translating it exposed 6 VIT infrastructure gaps, all now fixed. Verification at 15/20 — remaining 5 cases have sub-ULP diffs (1.2e-14) needing investigation.

## Infrastructure Fixes (6 total)

### Fix 1: LOGICAL `.EQV.` field comparison detection
**File:** `kernel_verify.py` — `_patch_field_value_logging()`

KGen uses `.EQV.` (not `==`) for LOGICAL field comparisons. VIT's field detection regex only matched `==`. Without `.EQV.` matching, field names from a previous REAL-type verify routine carried over to the LOGICAL routine, generating `[VIT_FIELD]` tags with wrong field names (e.g., `cnbreaklower` from `AFI_UA_BL_Type` appearing in the `AFI_UA_BL_Default_Type` routine).

**Fix:** Added `logical_re` regex pattern for `.EQV.` comparisons, sets `current_type = "logical"`.

### Fix 2: LOGICAL type in field tag injection (no diff_ variables)
**File:** `kernel_verify.py` — `_patch_field_value_logging()`

LOGICAL fields have only IDENTICAL/OUT_TOL branches (no IN_TOL, no `diff_` variable). But `diff_fields` was scoped globally across ALL verify routines. Field names shared between REAL and LOGICAL types (e.g., `A1` exists in both `AFI_UA_BL_Type` and `AFI_UA_BL_Default_Type`) caused VIT to generate `diff_A1` references in the LOGICAL routine, where no such variable exists.

**Fix:** Added `"logical"` type handling that never references `diff_` or generates IN_TOL tags.

### Fix 3: CHARACTER bridge local buffer
**File:** `interface_gen.py` — `generate_kernel_callee_bridge()`, `_build_bridge_call_args()`

Callee bridges for functions with `CHARACTER(*), INTENT(OUT)` args (like Calculate_C_alpha's ErrMsg) passed `CHARACTER(C_CHAR) :: arg(*)` (assumed-size char array) where Fortran expects `CHARACTER(*)` (assumed-length string). Rank mismatch compile error.

**Fix:** For CHARACTER OUT/INOUT args, declare `CHARACTER(1024) :: local_arg` in the bridge and pass the local buffer to the Fortran callee.

### Fix 4: LOGICAL mapping — int32_t for non-view types
**Files:** `type_map.py`, `derived_type_parser.py`, `view_populator.py`

VIT mapped all LOGICAL → `int8_t` (1 byte, C_BOOL). Default Fortran LOGICAL is 4 bytes. For non-view types like `AFI_UA_BL_Default_Type` (36 LOGICALs), the C struct was 36 bytes vs Fortran's 144 bytes — complete layout mismatch. All CalcDefaults boolean flags read as wrong values.

**Fix:**
- `type_map.py`: LOGICAL → `int32_t` (4 bytes, matching Fortran default)
- `derived_type_parser.py`: Removed LOGICAL field blocker. Added `int8_t` override for view types (view populators convert via LOGICAL(C_BOOL) explicitly).
- `view_populator.py`: All 4 assignment functions (scalar, nested, reverse, nested-reverse) now detect LOGICAL by source Fortran type and use LOGICAL(C_BOOL) conversion, independent of the type_map.

**Why int8_t was there:** ROSCO used `--fix logical` to convert LOGICAL→INTEGER(C_INT) in source before translation. After fixing, fields are INTEGER, not LOGICAL. AeroDyn's Registry-generated types can't be modified.

### Fix 5: Array-returning FUNCTION bridges
**Files:** `kernel_verify.py`, `interface_gen.py`

VIT skipped generating callee bridges for functions returning arrays (Calculate_Cn returns `REAL(:)`). Comment said "require allocatable temporaries that we can't generate reliably." But the production wrapper pattern (pre-allocated output buffer) works fine.

**Fix:**
- `kernel_verify.py`: Removed `result_is_array` skip
- `interface_gen.py`: `_build_interface_args()` already adds result buffer parameter for array-returning functions. `_make_call()` now generates array assignment `result(1:n) = FuncName(args)` instead of CALL. Both optional-args and non-optional paths handle `result_is_array`.
- `generate_callee_declarations_header()`: Cleaned up — result buffer already in `build_c_params()`.

### Fix 6: Callee bridge pre-call flush (view→Fortran sync)
**File:** `interface_gen.py` — `generate_kernel_callee_bridge()`

When C++ modifies view scalars BEFORE calling a callee bridge, the post-call re-sync (Fortran→view) overwrites the C++ modifications with stale values from the original Fortran type. Example: C++ sets `p->UA_BL.T_f0 = 3.0`, then calls ComputeUA360_AttachedFlow via bridge. The bridge's post-call re-sync copies the Fortran p (which still has T_f0 = 0) back to the view, clobbering 3.0.

**Root cause:** The re-sync copies ALL scalars from Fortran to view, not just the ones the callee modified. Any C++ modifications made before the callee call are lost.

**Fix:** Added pre-call flush: before calling the Fortran callee, copy view scalars to the Fortran type via `vit_copy_scalars_to_<type>()`. This preserves C++ modifications in the Fortran type before the callee runs. The post-call re-sync then reads the updated Fortran type (which now has both C++ modifications and callee modifications).

Pattern: **flush-call-reload**
1. Flush: `vit_copy_scalars_to(view, fortran)` — C++ modifications → Fortran
2. Call: Fortran callee modifies Fortran type directly
3. Reload: `vit_populate(fortran, view)` — all modifications → C++ view

Also imported `vit_copy_scalars_to_*` in the bridge USE statement.

**Why previous functions didn't trigger this:** ComputeUASeparationFunction_onCl calls its callee FIRST, then modifies scalars. CalculateUACoeffs modifies scalars (defaults) FIRST, then calls 7+ callees. The bug only manifests when C++ modifies scalars before a callee call.

## New NWTC Functions Added

### InterpStp (NWTC_Num.f90:3455, InterpStpReal8)
Like InterpExtrapStp but clamps at boundaries instead of extrapolating. Used 7 times in CalculateUACoeffs.

### kernelSmoothing (NWTC_Num.f90:4157)
Weighted kernel density smoothing with multiple kernel types. Used twice (CnSlope smoothing, ClSlope smoothing).

### Constants
`kernelType_TRIWEIGHT = 4` and other kernel type constants added to `vit_nwtc.h`.

## CalculateUACoeffs Translation

~280 lines of C++. Key patterns:
- 7 callee `_c()` calls via bridges (Calculate_Cn, AttachedFlow, updateSeparationF, updateCnSeparated, onCl, Compute_iLoweriUpper, Calculate_C_alpha)
- 2 NWTC utilities from vit_nwtc.h (InterpStp, kernelSmoothing)
- fZeros with computed temporary arrays for array-sliced expressions
- MINLOC/MAXLOC with compound MASK → manual loops
- CalcDefaults struct (36 int32_t flags) for conditional defaults
- Variable-length local arrays via std::vector

## Current Verification Status

**15/20 passed.** 5 cases fail with sub-ULP diffs:
- `cnbreakupper`: ~1.2e-14 (4 cases)
- `cnbreaklower`: ~1.1e-14 (1 case)
- `coefs` array: rms 1.1e-14 with 182 differences (1 case)
- `numtabs`/`incluadata`: false positives from non-function fields

Investigation needed for the sub-ULP diffs — likely related to the flush-reload cycle introducing one extra value roundtrip, or expression ordering in the chain Calculate_Cn → AttachedFlow → CnBreakUpper computation.

## Commits

| Repo | What |
|------|------|
| VIT | type_map.py, derived_type_parser.py, view_populator.py, kernel_verify.py, interface_gen.py — 6 infrastructure fixes + InterpStp/kernelSmoothing in vit_nwtc |
| OpenFAST | vit_nwtc.h/.cpp (InterpStp, kernelSmoothing), calculateuacoeffs.cpp translation, kernel fixture |
