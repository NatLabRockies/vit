# DBEMT_Init: Hand-Written Wrapper and INTENT(OUT) Allocation Problem
**Date:** 2026-05-01
**Status:** integrated but problematic
**Scope:** DBEMT Phase D (3/3), init function translation pattern

## What Was Done

DBEMT_Init (127 lines) was "translated" by splitting it:
- **Fortran wrapper (~60 lines):** InitOut%Ver, ValidateInitInp call, 5 ALLOCATE blocks with error handling, DBEMT_CopyContState for xdot history, populate views, reverse-copy scalars, DBEMT_ReInit call
- **C++ function (~25 lines):** Set 7 scalar parameters, compute rTip = max(rlocal), compute spanRatio = rlocal/rTip, set lin_nx

KGen verification: 3/3 IDENTICAL. Baselines: 17/17 PASS.

## Why This Is a Concern

### 1. It's barely a translation

~80% of the function logic remains in Fortran. The C++ "translation" handles only the trivial part — setting scalars and one division loop. The hard part (conditional allocation, error handling, callee orchestration) stays in Fortran. This isn't moving the codebase toward pure C++; it's adding a C++ veneer over a Fortran function.

### 2. Hand-written wrapper breaks VIT's automation

VIT's `integrate` command generates standard wrappers (populate views → call C++ → reverse-copy). For DBEMT_Init, this wrapper was hand-written because:
- INTENT(OUT) types are unallocated at function entry
- `vit_populate` calls C_LOC on unallocated arrays → segfault
- Allocation logic (which arrays to allocate, what sizes) IS the function body

This means:
- `integrate_all.sh` can't auto-regenerate the Init wrapper — it's excluded with a comment
- If the original Fortran changes, the wrapper must be manually updated
- The pattern doesn't transfer to other init functions (AFI_Init has the same problem)

### 3. Duplication

The wrapper duplicates the original Fortran's allocation logic verbatim. Both the kernel wrapper and the production wrapper have the same hand-written allocation code. If OpenFAST upstream changes DBEMT_Init's allocation pattern, we have three copies to update: original (gone, replaced by wrapper), kernel wrapper, production wrapper.

### 4. This will recur for every init function

Every OpenFAST module has an `_Init` function that allocates INTENT(OUT) types. BEMT_Init, AD_Init, UA_Init — all follow the same pattern. If each requires a hand-written wrapper, we'll have dozens of hand-maintained Fortran wrappers.

## Root Cause

C++ can't allocate Fortran ALLOCATABLE arrays. The VIT view struct pattern exposes ALLOCATABLEs as `pointer + size` pairs, which works when the arrays are already allocated. For init functions that CREATE the allocations, the view structs receive null pointers.

## Possible Solutions (Not Yet Implemented)

1. **BIND(C) allocation helper:** A small Fortran subroutine like `vit_allocate_2d(type_ptr, field_id, rows, cols)` that C++ calls to perform the Fortran ALLOCATE. The C++ init function would call this helper instead of trying to allocate directly.

2. **Two-pass VIT pattern:** VIT generates a wrapper that calls C++ pass1 (returns allocation sizes), does Fortran ALLOCATE, re-populates views, calls C++ pass2 (fills values). AFI_Init uses this but it was also hand-written.

3. **C++ manages its own allocation, wrapper copies back:** C++ allocates via malloc/new, fills values, returns dimensions. Wrapper does Fortran ALLOCATE + memcpy. Wasteful (double allocation) but automated.

4. **Accept init functions stay in Fortran during incremental phase.** Init runs once at startup — no performance concern. Translate fully only when building pure C++ module.

## Current State

DBEMT_Init is integrated and passing. The wrapper works. But the approach is fragile and won't scale. This needs a proper solution before translating BEMT_Init, AD_Init, or any other init function.
