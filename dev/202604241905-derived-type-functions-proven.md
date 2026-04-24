# First Derived Type Functions Proven on AeroDyn
**Date:** 2026-04-24
**Status:** completed
**Scope:** Phase 1 — view struct infrastructure for AeroDyn Registry-generated types

## Summary

Two AirfoilInfo functions with derived type arguments were translated, kernel-verified, integrated, and regression-tested. This is the first time VIT's view struct machinery has been tested on AeroDyn's Registry-generated types. Eight VIT infrastructure fixes were required — all one-time fixes that unblock the remaining 507 AeroDyn procedures.

## Results

| Function | Kernel | Regression | Derived type | Pattern tested |
|----------|--------|------------|--------------|----------------|
| FindBoundingTables | 21/21 IDENTICAL | 16/16 | AFI_ParameterType (view) | ALLOCATABLE arrays, CHARACTER(1024), opaque ALLOCATABLE TYPE() |
| Compute_iLoweriUpper | 20/20 IDENTICAL | 16/16 | AFI_Table_Type (view) | Nested struct embedding (AFI_UA_BL_Type, 45 fields), LOGICAL, rank-3 ALLOCATABLE |

Combined with Calculate_Cn (20/20, scalars/arrays only), three AirfoilInfo functions are now integrated — the AeroDyn translation pipeline handles all key type patterns.

## VIT Infrastructure Fixes (8)

### Fix 1: TYPE() space normalization
**File:** `vit/fortran_parser.py`
**Problem:** AeroDyn's Fortran source uses `TYPE (AFI_ParameterType)` with a space between TYPE and the parenthesis. VIT's parser preserved this literally, so `is_derived_type` (which checks `startswith("TYPE(")`) returned False. The type registry found zero derived types, and the C++ scaffold excluded the `p` argument entirely.
**Fix:** Added regex normalization in `_parse_type_declaration()`: `TYPE (Foo)` → `TYPE(Foo)`. All downstream `startswith("TYPE(")` checks work unchanged.

### Fix 2: ALLOCATABLE TYPE() fields in view structs
**Files:** `vit/derived_type_parser.py`, `vit/view_populator.py`
**Problem:** AFI_ParameterType has `Table(:)` — a `TYPE(AFI_Table_Type), DIMENSION(:), ALLOCATABLE` field. The C header generator called `get_type_mapping()` which returned `c_type="AFI_Table_Type"` — not a valid C type. The Fortran populator called `C_LOC(src%Table(1))` which fails because AFI_Table_Type is not C-interoperable.
**Fix:** For ALLOCATABLE TYPE() fields, emit `void*` pointer + `int32_t` size in the C header, and `TYPE(C_PTR)` + `C_NULL_PTR` + `SIZE()` in the Fortran populator. The opaque pointer preserves the count for functions that need to know how many tables exist, without attempting interop on the non-compatible nested type.

### Fix 3: Nested non-ALLOCATABLE TYPE() embedding
**Files:** `vit/derived_type_parser.py`, `vit/view_populator.py`
**Problem:** AFI_Table_Type has `UA_BL` — a `TYPE(AFI_UA_BL_Type)` field with 45 REAL scalars. Both the C header and Fortran populator skipped nested TYPE() fields entirely. Compute_iLoweriUpper accesses `p%UA_BL%alphaLower`, so the nested struct must be in the view.
**Fix:** For nested non-ALLOCATABLE TYPE() fields where the nested type is C-compatible (checked via `is_c_compatible_recursive()` using the type registry), embed the nested struct by value. The C header emits an inline struct definition with `#ifndef` guards. The Fortran mirror uses a nested BIND(C) type. The populator copies each field with kind conversion. Non-C-compatible nested types remain skipped.
**Key design decision:** The type registry is now passed through to `generate_view_struct_header()` and `generate_view_populator()` so they can look up nested types. Six call sites updated across `integrate.py`, `kernel_verify.py`, `translate.py`, and `derived_type_parser.py`.

### Fix 4: Rank-3 ALLOCATABLE fields
**Files:** `vit/derived_type_parser.py`, `vit/view_populator.py`
**Problem:** AFI_Table_Type has `SplineCoefs(:,:,:)` — a rank-3 ALLOCATABLE. VIT handled rank 1 (`n_field`) and rank 2 (`n_field_rows`, `n_field_cols`) but rank 3 fell through to the rank-2 branch, losing the third dimension.
**Fix:** Added rank-3 handling with `n_field_dim1`, `n_field_dim2`, `n_field_dim3` in both the C header and Fortran populator. Populator uses `C_LOC(src%field(1,1,1))` and `SIZE(src%field, 1/2/3)`.

### Fix 5: LOGICAL(C_BOOL) type mapping
**File:** `vit/type_map.py`
**Problem:** `LOGICAL` was mapped to `c_type="int"` (4 bytes) but `LOGICAL(C_BOOL)` in the BIND(C) mirror type is 1 byte. This caused a struct layout mismatch — all fields after the LOGICAL were at wrong offsets. Compute_iLoweriUpper produced `iLower=1, iUpper=2` (clamp defaults) instead of `94, 94` because the Alpha array pointer was at the wrong struct offset.
**Fix:** Changed LOGICAL mapping to `c_type="int8_t"`, `byte_size=1`. Updated two unit tests that expected `int`.
**Root cause of difficulty:** A stale `translations/vit_types.h` (generated before the fix) was being copied to `src/` by `integrate.py` instead of being regenerated. Required deleting the stale copy and regenerating from the type registry.

### Fix 6: LOGICAL default initializer
**File:** `vit/view_populator.py`
**Problem:** The Fortran mirror type used `LOGICAL(C_BOOL) :: ConstData = 0` which triggers a gfortran warning about INTEGER→LOGICAL conversion.
**Fix:** Changed to `LOGICAL(C_BOOL) :: ConstData = .FALSE.` — type-appropriate default.

### Fix 7: Reverse-copy ISO C kinds
**File:** `vit/view_populator.py`
**Problem:** The reverse-copy subroutine used project-specific kind names (`ReKi`, `IntKi`) but the populator module only USEs `ISO_C_BINDING`. Compilation failed with `Symbol 'reki' has no IMPLICIT type`.
**Fix:** Changed to use ISO C kind names (`C_DOUBLE`, `C_INT`) in all reverse-copy assignments. Applied to both top-level and nested field assignments.

### Fix 8: `_lookup_type` helper
**File:** `vit/derived_type_parser.py`
**Problem:** Case-insensitive type lookup was needed in multiple places for nested type resolution. No reusable helper existed.
**Fix:** Added `_lookup_type(registry, type_name)` — a simple case-insensitive dict lookup used by both `generate_view_struct_header()` and `generate_view_populator()`.

## Extraction Lessons Learned

### Extraction requires clean source
AeroDyn extraction with already-integrated source fails because:
1. Integration wrappers reference `vit_*_view` modules that KGen's build can't find
2. KGen instrumentation on integrated source produces state files that contain `_c` wrapper calls

**Procedure for extraction after integration:**
1. `git checkout -- modules/aerodyn/src/AirfoilInfo.f90` to restore clean source
2. Create stubs for CMakeLists-referenced `.cpp` and `.f90` files
3. Extract with correct line numbers from the **clean** source
4. Restore the integrated version and real files afterward

### Line numbers shift after integration
Integration wrappers change line counts. Always re-check `grep -n "call FunctionName"` against the current source before extraction. Using stale line numbers caused four failed extraction attempts.

### Test case selection for UA-dependent functions
- `ad_BAR_SineMotion` has `UA_Mod=0` — no Unsteady Aero, `CalculateUACoeffs` never called
- `ad_BAR_SineMotion_UA4_DBEMT3` has `UA_Mod=4` (HGM) — exercises the `if (.not. UA_f_cn)` branch
- `ad_MHK_RM1_Fixed` has `AFTabMod=2` — exercises multi-table interpolation (FindBoundingTables)

Extraction parameters for the two new functions:

| Function | Call site file | Line (clean) | Test case | Invocation |
|----------|---------------|------|-----------|------------|
| FindBoundingTables | AirfoilInfo.f90 | 1703 | ad_MHK_RM1_Fixed | 0:0:1-5 |
| Compute_iLoweriUpper | AirfoilInfo.f90 | 1085 | ad_BAR_SineMotion_UA4_DBEMT3 | 0:0:1-20 |

### Stale `translations/vit_types.h` trap
`vit integrate` copies `vit_types.h` from the translations directory to `src/` rather than generating it. If a stale version exists in translations, all subsequent integrations use the wrong header. After any type-mapping fix, delete `translations/vit_types.h` and regenerate from the type registry.

## Files Modified

**VIT tool (8 files):**
- `vit/fortran_parser.py` — TYPE normalization
- `vit/derived_type_parser.py` — `_lookup_type`, nested TYPE embedding, ALLOCATABLE TYPE opaque, rank-3, registry passthrough
- `vit/view_populator.py` — nested TYPE support, ALLOCATABLE TYPE opaque, rank-3, LOGICAL defaults, ISO C kinds
- `vit/type_map.py` — LOGICAL → int8_t
- `vit/integrate.py` — pass registry to view populator
- `vit/kernel_verify.py` — pass registry to view populator
- `vit/translate.py` — pass registry to view struct header
- `tests/test_core.py` — update LOGICAL test assertions

**OpenFAST (7 files):**
- `modules/aerodyn/src/AirfoilInfo.f90` — integration wrappers for 3 functions
- `modules/aerodyn/src/vit_types.h` — consolidated C struct header (3 types)
- `modules/aerodyn/src/vit_afi_parametertype_view.f90` — AFI_ParameterType populator
- `modules/aerodyn/src/vit_afi_table_type_view.f90` — AFI_Table_Type populator
- `modules/aerodyn/src/calculate_cn.cpp` — Calculate_Cn C++ + extern C wrapper
- `modules/aerodyn/src/findboundingtables.cpp` — FindBoundingTables C++ + extern C wrapper
- `modules/aerodyn/src/compute_iloweriupper.cpp` — Compute_iLoweriUpper C++ + extern C wrapper
- `modules/aerodyn/CMakeLists.txt` — added C++ sources and populator modules to basicaerolib
- `vit.yaml` — types section, preprocessor_defines, test case notes
- `translations/vit_types.h` — regenerated with correct LOGICAL mapping

## What This Proves

1. **VIT's view struct pattern works on AeroDyn's Registry-generated types** — ALLOCATABLE arrays, nested structs, CHARACTER fields, LOGICAL fields, opaque TYPE() arrays, rank-3 arrays all handled correctly.
2. **The type registry is operational** — auto-discovers types from source, applies strategies from config, recursively resolves nested types for C-compatibility checking.
3. **The pipeline scales** — each function follows the same extract→translate→verify→integrate workflow. The infrastructure fixes are one-time; subsequent functions should be straightforward.

## Related
- Dev note `202604241459` — four infrastructure fixes (FMA, baselines, ReKi, cmake)
- Dev note `202604241400` — first full AeroDyn verification (Calculate_Cn)
- `PROJECT_PLAN_AERODYN.md` — Phase 1
