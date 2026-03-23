# Include All ALLOCATABLE Fields in View Struct
**Date:** 2026-03-23
**Status:** completed
**Scope:** Phase 4D — view struct completeness

## The Problem

12 of 68 ALLOCATABLE fields in ControlParameters were commented out of the view struct because the automatic scanner couldn't find their size sources. C++ translations couldn't access these fields.

The 12 fields:
- **OL_Channels** (2D) — allocated inside `Read_OL_Input()` with sizes from file I/O (line count from CSV)
- **10 OL_ 1D fields** (OL_Breakpoints, OL_BldPitch1-3, OL_GenTq, OL_YawRate, OL_Azimuth, OL_R_Speed, OL_R_Torque, OL_R_Pitch) — allocated via Fortran's allocate-on-assignment from `OL_Channels` columns
- **WE_CP** — dead code, declared ALLOCATABLE but never allocated (ParseAry call commented out, marked "Retired WSE inputs")

5 Phase 7B functions use OL_ fields: PitchControl, VariableSpeedControl, YawRateControl, CableControl, PowerControlSetpoints. All access is read-only interpolation via `interp1d`.

## The Insight

The scanner's role was conflated. It was doing two things:
1. **Classifying size sources** — documenting WHERE each field gets its size (Type A: field reference, Type B: literal, etc.)
2. **Gating inclusion** — deciding which fields appear in the view struct

But the populator doesn't use the scanner's size expressions. It uses Fortran's `SIZE()` intrinsic at runtime, which returns the actual allocated dimensions for any ALLOCATABLE array regardless of how it was allocated. And `IF (ALLOCATED(...))` safely handles unallocated fields by setting the pointer to null and size to 0.

The scanner was gatekeeping field inclusion for no technical reason.

## The Fix

Changed `generate_view_struct_header()` and `generate_view_populator()` to iterate ALL ALLOCATABLE fields, not just scanner-mapped ones. For fields without scanner mappings, rank is inferred from the Fortran declaration (`DIMENSION(:)` = 1D, `DIMENSION(:,:)` = 2D).

The scanner still runs. Its classification (56 mapped, 12 unmapped) is still reported. But it's informational — it documents size source provenance for human review, not for code generation decisions.

## Correctness

No effect on correctness. The populator generates identical code for every ALLOCATABLE field:

```fortran
IF (ALLOCATED(src%field)) THEN
    view%field = C_LOC(src%field(1))
    view%n_field = INT(SIZE(src%field), C_INT32_T)
ELSE
    view%field = C_NULL_PTR
    view%n_field = 0
END IF
```

`SIZE()` is authoritative — it queries the Fortran runtime's allocation metadata, not VIT's static analysis. Whether the array was allocated by ParseAry, explicit ALLOCATE, allocate-on-assignment from a column slice, or a helper subroutine, `SIZE()` returns the correct value.

For WE_CP (never allocated): `ALLOCATED()` returns false, pointer is null, size is 0. Safe as long as C++ checks the size before accessing.

## Result

- ControlParameters view struct: all 68 ALLOCATABLE fields included (was 56)
- Populator: 941 lines (was 830), compiles cleanly with gfortran
- Scanner report unchanged: 56 mapped + 12 unmapped (informational only)
- All 486 tests pass
