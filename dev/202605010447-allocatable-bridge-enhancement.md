# VIT Enhancement: ALLOCATABLE-Aware Test-Validate Bridges
**Date:** 2026-05-01
**Status:** completed
**Scope:** VIT test-validate bridge generation + DBEMT_ValidateInitInp Phase D

## Background

DBEMT_ValidateInitInp is the first Phase D function — a 49-line PRIVATE validation subroutine with 8 error branches. It takes `DBEMT_InitInputType`, which has an ALLOCATABLE field (`rLocal(:,:)`). This exposed a limitation in VIT's test-validate bridge generator.

## The Problem

`generate_fortran_bridge()` in `test_validate.py` handled ALL derived-type arguments the same way:

```fortran
TYPE(C_PTR), VALUE :: InitInp_ptr
TYPE(DBEMT_InitInputType), POINTER :: InitInp
CALL C_F_POINTER(InitInp_ptr, InitInp)
```

This uses `C_F_POINTER` to cast a C void pointer to a Fortran type pointer. When the type has ALLOCATABLE fields, this fails — the Fortran runtime's ALLOCATABLE descriptor is an internal data structure (typically 48-72 bytes with base pointer, bounds, stride, allocation status) that is completely different from the C view struct's layout (`double* rLocal` + `int32_t n_rLocal_rows` + `int32_t n_rLocal_cols`). The ALLOCATABLE appears in an undefined state, causing crashes or incorrect `ALLOCATED()` checks.

## Two Issues Fixed

### 1. ALLOCATABLE field decomposition (new feature)

When a derived type has ALLOCATABLE fields (detected via `DerivedTypeInfo.is_c_compatible == False`), the bridge now decomposes the struct into individual parameters:

- **Scalar fields** → `VALUE` parameters (e.g., `INTEGER(C_INT), VALUE :: vit_InitInp_NumBlades`)
- **ALLOCATABLE fields** → `TYPE(C_PTR), VALUE` data pointer + `INTEGER(C_INT), VALUE` dimension sizes
- **Bridge body** → constructs a real Fortran type, sets scalar fields, `ALLOCATE`s + populates ALLOCATABLE from C data via `C_F_POINTER` on the flat array, calls the original function, `DEALLOCATE`s

Parameter names are prefixed with `vit_{arg_name}_` to avoid collisions with names imported via `USE` statements (e.g., `numNodes` from `DBEMT_Types` collides with a dummy argument named `numNodes`).

The test harness skeleton's `extern "C"` declaration is updated to match.

### 2. VALUE attribute for INTENT(IN) scalars (bug fix)

Non-derived-type INTENT(IN) scalars (like `interval`) were generated as `REAL(C_DOUBLE), INTENT(IN)` without `VALUE`. In BIND(C) Fortran, this means pass-by-reference — the C caller must pass a pointer. But the C++ test passes by value, causing a segfault when Fortran dereferences the value as an address.

Fixed by adding `VALUE` to INTENT(IN) scalar parameters:
```fortran
REAL(C_DOUBLE), VALUE, INTENT(IN) :: interval   ! was: REAL(C_DOUBLE), INTENT(IN) :: interval
```

INTENT(OUT/INOUT) scalars correctly remain pass-by-reference (no VALUE).

## Implementation

### New functions in `test_validate.py`:
- `_has_allocatable_fields(arg, derived_types)` — checks if a derived-type arg's type needs decomposition
- `_emit_decomposed_bridge_params(arg, type_info, kind_aliases, ...)` — emits all parameters, locals, pre-call setup, and post-call cleanup for a decomposed type

### Modified functions:
- `generate_fortran_bridge()` — new `derived_types` parameter; conditional on `_has_allocatable_fields`
- `generate_test_harness_skeleton()` — extern "C" declaration matches decomposed bridge
- Non-derived-type branches — add `VALUE` for INTENT(IN)

### CLI change:
- `cmd_test_validate()` passes `derived_types` registry to `generate_fortran_bridge()`

### Backward compatibility:
When `derived_types` is not passed (existing callers, types without ALLOCATABLE), behavior is unchanged — C_F_POINTER path used. All 30 existing tests pass without modification.

## Generated Bridge Output

For DBEMT_ValidateInitInp, the auto-generated bridge:

```fortran
SUBROUTINE dbemt_validateinitinp_f90(interval, vit_InitInp_NumBlades, 
    vit_InitInp_NumNodes, vit_InitInp_tau1_const, vit_InitInp_DBEMT_Mod,
    vit_InitInp_rLocal_ptr, vit_InitInp_rLocal_rows, vit_InitInp_rLocal_cols,
    errStat) BIND(C, NAME='dbemt_validateinitinp_f90')
    ...
    REAL(C_DOUBLE), VALUE, INTENT(IN) :: interval
    INTEGER(C_INT), VALUE :: vit_InitInp_NumBlades
    ...
    TYPE(C_PTR), VALUE :: vit_InitInp_rLocal_ptr
    INTEGER(C_INT), VALUE :: vit_InitInp_rLocal_rows
    INTEGER(C_INT), VALUE :: vit_InitInp_rLocal_cols
    ...
    TYPE(DBEMT_InitInputType) :: vit_local_InitInp
    ...
    vit_local_InitInp%NumBlades = vit_InitInp_NumBlades
    ...
    IF (C_ASSOCIATED(vit_InitInp_rLocal_ptr)) THEN
        ALLOCATE(vit_local_InitInp%rLocal(rows, cols))
        ! column-major copy from C array
    END IF
    CALL DBEMT_ValidateInitInp(interval, vit_local_InitInp, errStat, errMsg)
    IF (ALLOCATED(vit_local_InitInp%rLocal)) DEALLOCATE(vit_local_InitInp%rLocal)
END SUBROUTINE
```

## Testing

- **VIT unit tests:** 658/658 pass (30 existing + 14 new ALLOCATABLE tests)
- **End-to-end:** VIT-generated bridge compiles, links, and runs 9/9 test-validate PASS
- **Baselines:** 17/17 PASS after integration

## Limitations

- Nested derived types within ALLOCATABLE types: not yet supported (no current use case). Generates a TODO comment.
- INTENT(INOUT) derived types with ALLOCATABLE: post-call reverse-copy of modified fields not implemented (test-validate functions are typically INTENT(IN) — validation only reads inputs).
- 3D+ ALLOCATABLE arrays: only 1D and 2D currently handled. No OpenFAST types have 3D ALLOCATABLE fields.

## Files Changed

| File | Change |
|------|--------|
| `vit/vit/test_validate.py` | ALLOCATABLE decomposition + VALUE fix |
| `vit/vit/cli.py` | Pass derived_types to bridge generator |
| `vit/tests/test_test_validate.py` | 14 new tests |
| `openfast/translations/DBEMT/dbemt_validateinitinp.cpp` | New translation |
| `openfast/translations/DBEMT/dbemt_validateinitinp_test.cpp` | 9 test cases |
| `openfast/translations/DBEMT/dbemt_validateinitinp_bridge.f90` | VIT-generated |
| `openfast/modules/aerodyn/src/DBEMT.f90` | Integrated wrapper |
| `openfast/scripts/integrate_all.sh` | Updated |
| `openfast/vit.yaml` | New entry |
