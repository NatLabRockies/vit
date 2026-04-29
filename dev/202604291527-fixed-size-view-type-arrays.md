# Fixed-Size Arrays of View Types in VIT
**Date:** 2026-04-29
**Status:** completed
**Scope:** VIT enhancement, type infrastructure

## Problem

VIT could not generate C view structs or Fortran populators for fixed-size arrays of types with ALLOCATABLE fields. When it encountered a field like:

```fortran
TYPE(DBEMT_ContinuousStateType), DIMENSION(1:4) :: xdot
```

where `DBEMT_ContinuousStateType` has `ALLOCATABLE :: element(:,:)`, VIT commented the field out:

```c
// TYPE(DBEMT_ContinuousStateType) xdot; // nested type — not in view (not C-compatible)
```

This is the standard OpenFAST idiom for multi-step integrator history. It appears 7 times across 4 AeroDyn modules:

| Module | Field | Size | Purpose |
|--------|-------|------|---------|
| DBEMT_Types | `xdot` | 1:4 | AB4/ABM4 derivative history |
| BEMT_Types | `xdot` | 1:4 | AB4/ABM4 derivative history |
| BEMT_Types | `u_DBEMT` | 1:2 | DBEMT input time levels |
| BEMT_Types | `u_SkewWake` | 1:2 | SkewWake input time levels |
| UnsteadyAero_Types | `xdot` | 1:4 | AB4/ABM4 derivative history |
| UnsteadyAero_Types | `xHistory` | 1:4 | State history |
| AeroDyn_Types | `BEMT_u` | 1:2 | BEMT input time levels |

Without this enhancement, DBEMT Phase B (time integrators AB4/ABM4) could not access `OtherState%xdot(n)%element(i,j)%vind` from C++.

## Root Cause

VIT's `is_c_compatible_recursive()` correctly returns False for types with ALLOCATABLE fields. But the code that emits view struct fields treated this as a binary decision: either the nested type is C-compatible (embed it) or it's not (comment it out). There was no path for "the nested type isn't C-compatible, but it has a view struct that IS embeddable."

## Solution

The inner types (e.g., `DBEMT_ContinuousStateType`) already have `strategy: view` — they have view structs (`dbemt_continuousstatetype_view_t`) and Fortran populator modules. The fix embeds an array of the inner view struct in the outer view struct.

### What VIT now generates

**C struct (`vit_types.h`):**
```c
typedef struct {
    double tau1;
    double tau2;
    dbemt_continuousstatetype_view_t xdot[4];  // ← was commented out
    int32_t* areStatesInitialized;
    // ...
} dbemt_otherstatetype_view_t;
```

**Fortran BIND(C) mirror:**
```fortran
TYPE, BIND(C) :: dbemt_otherstatetype_view_t
    REAL(C_DOUBLE) :: tau1 = 0
    REAL(C_DOUBLE) :: tau2 = 0
    TYPE(dbemt_continuousstatetype_view_t) :: xdot(4)
    ! ALLOCATABLE fields...
END TYPE
```

**Populator loop:**
```fortran
DO vit_k = 1, 4
    CALL vit_populate_dbemt_continuousstatetype(src%xdot(vit_k), view%xdot(vit_k))
END DO
```

**Reverse-copy loop:**
```fortran
DO vit_k = 1, 4
    CALL vit_copy_scalars_to_dbemt_continuousstatetype(view%xdot(vit_k), dest%xdot(vit_k))
END DO
```

### Code changes

**`derived_type_parser.py`:**
1. Added `_extract_fixed_sizes()` — converts `"1:4"` → `[4]`, `"0:3"` → `[4]`, etc.
2. Modified `generate_view_struct_header()` — when a non-ALLOCATABLE TYPE() field has a view-strategy inner type and array dimensions, emits `inner_view_t name[N]` instead of commenting out.
3. Modified `classify_field_blockers()` — fixed-size arrays of view types are no longer reported as blockers.

**`view_populator.py`:**
1. Added `view_type_fixed_fields` detection — identifies non-ALLOCATABLE TYPE() array fields whose inner type has `view_strategy='view'`.
2. Added USE statements for child populator modules (imports view type, populate sub, and reverse-copy sub).
3. Added BIND(C) mirror field — `TYPE(child_view_t) :: field(N)`.
4. Added populate loop with `vit_k` loop variable.
5. Added reverse-copy loop.

## Design Decisions

**Why not change `is_c_compatible_recursive()`?** It correctly identifies these types as not directly C-compatible (they have ALLOCATABLE fields that need pointer+size representation). The fix belongs at the struct generation level, not the compatibility check — we're embedding a VIEW struct, not the raw type.

**Why `vit_k` instead of `vit_i`?** `vit_i` is already used for ALLOCATABLE TYPE() view field loops. Using a separate variable avoids confusion and potential conflicts if both patterns appear in the same type.

**Edge cases handled:**
- `DIMENSION(0:3)` — all 7 known instances use 1-based bounds. The code uses the array size (4) for both C `[4]` and Fortran `(4)`. Non-1-based bounds would compile but the loop assumes 1-based — a warning should be added if this case is ever encountered.
- Multi-dimensional fixed TYPE() arrays — not seen in AeroDyn, not implemented.
- Scalar TYPE() with view strategy (no DIMENSION) — different pattern, left as future work.

## Verification

- Regenerated `DBEMT_OtherStateType` view struct and populator with the enhancement
- OpenFAST compiles cleanly with `xdot[4]` embedded in the view struct
- 17/17 baselines PASS (bit-identical — the xdot data flows through the view but DBEMT_InitStates doesn't access it)
- Functional validation will come with Phase B when DBEMT_AB4/ABM4 are translated

## Impact

This enhancement unblocks DBEMT Phase B (time integrators) and pre-solves the same pattern in BEMT, UnsteadyAero, and AeroDyn. Phase B functions like DBEMT_AB4 can now access `OtherState->xdot[n].element[idx].vind[0]` directly from C++ — no manual view construction needed.
