# View Populator: ALLOCATABLE TYPE() Array Marshalling
**Date:** 2026-04-26
**Status:** completed
**Scope:** VIT infrastructure — view populator enhancement for arrays of derived types

## Background

VIT's view struct pattern exposes Fortran derived types to C++ via BIND(C) mirror structs. ALLOCATABLE scalar arrays (REAL, INTEGER) are passed directly via `C_LOC` — the populator stores a pointer to the first element and the array size. But ALLOCATABLE TYPE() arrays (arrays of derived types) were treated as opaque: the populator set `view%Table = C_NULL_PTR` and only stored the count. C++ could see how many elements existed but couldn't access any of them.

This worked for the first 12 AirfoilInfo functions because the Fortran wrapper could pass individual table views (e.g., CalculateUACoeffs receives a single `afi_table_type_view_t*` for `p%Table(1)`). But AFI_ComputeAirfoilCoefs needs to index the Table array at runtime — the C++ dispatch logic (based on AFTabMod, FindBoundingTables results) decides which table to access. The Fortran wrapper can't pre-select the table because the selection logic is in C++.

## Solution

Enhanced the view populator to detect when an ALLOCATABLE TYPE() field's inner type is also a view type (has `strategy: view` in `vit.yaml`). For these fields, the populator:

1. **Allocates a module-level SAVE array** of child view structs (e.g., `TYPE(afi_table_type_view_t), ALLOCATABLE, TARGET, SAVE :: vit_table_views_afi_parametertype(:)`)
2. **Populates each element** by calling the child type's populator subroutine in a loop: `CALL vit_populate_afi_table_type(src%Table(i), views(i))`
3. **Stores a C_LOC pointer** to the first element: `view%Table = C_LOC(views(1))`
4. **C++ sees** `afi_table_type_view_t* Table` instead of `void* Table` and can index `p->Table[iTab]`

Non-view inner types remain `void*` / `C_NULL_PTR` (no change for those).

## Memory Management

The child view array is a module-level `SAVE` variable, not a local. This is necessary because local ALLOCATABLEs would be deallocated when the populator subroutine returns — before C++ reads them. The SAVE array persists until the next populator call, which DEALLOCATEs and re-ALLOCATEs it. Since wrapper calls are synchronous (populator → C++ → return), there's no race condition. Same lifetime pattern as the existing stash pointer (`vit_original_*`).

## Files Changed

### `vit/vit/view_populator.py`
- Added `view_type_alloc_fields` detection (checks registry for `view_strategy == 'view'`)
- Added `USE child_populator_module` in module preamble
- Added module-level `SAVE` array declaration
- Added loop counter `INTEGER :: vit_i` in populator subroutine
- Replaced `C_NULL_PTR` assignment with DEALLOCATE/ALLOCATE/DO loop/C_LOC for view-type children

### `vit/vit/derived_type_parser.py`
- Changed C header generation: `void* Table` → `afi_table_type_view_t* Table` when inner type is a view type
- Registry lookup checks `view_strategy` attribute

### `vit/vit/kernel_verify.py`
- Added step 3d: inter-populator Makefile dependencies
- Scans generated populator `.f90` for `USE vit_*_view` references to other populators
- Adds child populator `.o` as dependency to parent populator's Makefile rule

## Generated Fortran (AFI_ParameterType populator)

```fortran
MODULE vit_afi_parametertype_view
    USE ISO_C_BINDING
    USE AirfoilInfo_Types, ONLY: AFI_ParameterType
    USE vit_afi_table_type_view, ONLY: afi_table_type_view_t, vit_populate_afi_table_type
    ...
    TYPE(afi_table_type_view_t), ALLOCATABLE, TARGET, SAVE :: vit_table_views_afi_parametertype(:)

CONTAINS
    SUBROUTINE vit_populate_afi_parametertype(src, view)
        ...
        INTEGER :: vit_i
        ...
        IF (ALLOCATED(src%Table)) THEN
            IF (ALLOCATED(vit_table_views_afi_parametertype)) DEALLOCATE(vit_table_views_afi_parametertype)
            ALLOCATE(vit_table_views_afi_parametertype(SIZE(src%Table)))
            DO vit_i = 1, SIZE(src%Table)
                CALL vit_populate_afi_table_type(src%Table(vit_i), vit_table_views_afi_parametertype(vit_i))
            END DO
            view%Table = C_LOC(vit_table_views_afi_parametertype(1))
            view%n_Table = INT(SIZE(src%Table), C_INT32_T)
        ...
```

## Generated Makefile Dependency

```makefile
vit_afi_parametertype_view.o: vit_afi_parametertype_view.f90 AirfoilInfo_Types.o vit_afi_table_type_view.o
```

## Generated C Header

```c
// Before:
void* Table;  // TYPE(AFI_Table_Type), ALLOCATABLE — opaque

// After:
afi_table_type_view_t* Table;  // Array of view structs, populated by Fortran
```

## Verification

- **Unit tests:** 590 passed, 8 pre-existing failures (no new regressions)
- **FindBoundingTables kernel:** 21/21 IDENTICAL — populator now fills Table array, function still passes (regression-safe even though FindBoundingTables doesn't access Table elements from C++)
- **Build:** Makefile correctly orders parent populator after child populator

## Generality

This is a general mechanism, not AeroDyn-specific. Any `vit.yaml` project with an ALLOCATABLE TYPE() field whose inner type has `strategy: view` will automatically get the allocate+populate loop. The detection is fully automatic — based on the type registry, not hardcoded names.

## What This Enables

AFI_ComputeAirfoilCoefs can now receive the full `AFI_ParameterType` view struct and access `p->Table[iTab]` for arbitrary table indices. The 1D lookup accesses `p->Table[iTab].Alpha`, `p->Table[iTab].Coefs`, `p->Table[iTab].SplineCoefs`; the 2D lookup calls `FindBoundingTables` (already translated) to get bounding table indices and then does two 1D lookups. All of this table indexing happens in C++ without Fortran wrapper involvement.

## Related

- **Enabling function:** AFI_ComputeAirfoilCoefs (next translation target — Steps 2-6 in the plan)
- **Previous workaround:** CalculateUACoeffs passed a single `afi_table_type_view_t*` from the Fortran wrapper
- **Plan file:** `memoized-cooking-otter.md` Steps 1-6
