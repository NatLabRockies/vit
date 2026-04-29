# DBEMT_InitStates Integration + Three VIT Fixes
**Date:** 2026-04-29
**Status:** completed
**Scope:** DBEMT Phase A (3/5), VIT bug fixes, workflow infrastructure

## What We Did

Completed the full 7-step workflow for DBEMT_InitStates — a 26-line function that initializes per-node continuous states. This is the 3rd of 5 leaf functions in DBEMT Phase A.

Result: 20/20 KGen IDENTICAL, 17/17 baselines PASS.

The function itself was straightforward. The session's real value was in three VIT bugs discovered and fixed, plus a script bug that would have blocked future extractions.

## The Function

DBEMT_InitStates checks `OtherState%areStatesInitialized(i,j)` and, on first call per node, initializes:
- `x%element(i,j)%vind` from `u%element(i,j)%vind_s`
- `x%element(i,j)%vind_1` — mode-dependent: `(1 - k_0ye) * vind_s` for Mode 3, plain `vind_s` otherwise

Two new types required: `DBEMT_ContinuousStateType` (ALLOCATABLE `element(:,:)` of fixed-size `ElementContinuousStateType`) and `DBEMT_OtherStateType` (ALLOCATABLE `areStatesInitialized(:,:)`, `n(:,:)`, plus `xdot(1:4)` — a fixed-size array of ContinuousStateType).

### The OtherStateType Challenge

`DBEMT_OtherStateType` has `xdot(1:4)` — a fixed-size array of `DBEMT_ContinuousStateType`, which itself has ALLOCATABLE `element(:,:)`. VIT's auto-generator correctly identified this as non-embeddable and commented it out in the view struct. Since DBEMT_InitStates doesn't access `xdot`, this was acceptable. Future functions that need `xdot` (Phase B time integrators) will require either a VIT enhancement or manual view construction.

## VIT Bug 1: void* for C-Compatible ALLOCATABLE TYPE() Fields

**File:** `vit/vit/derived_type_parser.py`

When generating C view structs, ALLOCATABLE TYPE() fields were always emitted as `void*` unless the inner type had `view_strategy='view'`. But many inner types (like `DBEMT_ElementContinuousStateType` with just `double vind[2]; double vind_1[2];`) are C-compatible and should get typed pointers.

**Fix:** Added a check for `is_c_compatible_recursive()` between the view-type and opaque fallback paths:

```python
if nested_info and getattr(nested_info, 'view_strategy', None) == 'view':
    # → view_t* (existing)
elif nested_info and nested_info.is_c_compatible_recursive(registry or {}):
    # → type_t* (NEW — typed pointer for C-compatible inner types)
else:
    # → void* (existing — truly opaque)
```

This generates `dbemt_elementcontinuousstatetype_t* element` instead of `void* element`, making the array directly accessible from C++.

## VIT Bug 2: C_NULL_PTR for C-Compatible ALLOCATABLE TYPE() Fields

**File:** `vit/vit/view_populator.py`

The Fortran view populator had the same gap — ALLOCATABLE TYPE() fields with C-compatible inner types were populated with `C_NULL_PTR` (opaque) instead of `C_LOC`. The C++ code received a null pointer and would segfault on access.

**Fix:** Added `c_compat_type_alloc_fields` dictionary alongside the existing `view_type_alloc_fields`. Fields whose inner type is C-compatible get `C_LOC(src%element(LBOUND(...)))` — the same pattern used for primitive ALLOCATABLE arrays.

Both fixes work together: `derived_type_parser.py` generates the typed pointer in the C struct, and `view_populator.py` provides the real address via `C_LOC`.

## VIT Bug 3: Field Access Detected as Function Call

**File:** `vit/vit/kernel_verify.py`

The callee detection regex `\bfunc_name\s*\(` matched `x%element(i,j)` as a call to `Element` (a subroutine in FEAMooring). This generated a bridge file that tried to `USE FEAMooring`, which doesn't exist in the kernel.

**Fix:** Added negative lookbehind `(?<!%)` to the known-function matching pattern:

```python
pattern = rf'(?<!%)\b{_re.escape(func_name)}\s*\('
```

This correctly excludes Fortran derived-type field access patterns (`type%field(args)`) while still matching standalone function calls.

### Pattern: "element" as a VIT Stress Test

The field name `element` is ubiquitous in OpenFAST's DBEMT types. It triggered:
- Session 1: catastrophic regex backtracking in `scan_allocatable_sizes` (the `_extract_allocate_dims` fix)
- This session: false callee bridge to FEAMooring

Each new DBEMT function exercises more of VIT's code paths because DBEMT's type shapes differ from AirfoilInfo's. This is the same pattern observed with ComputeTau1 — latent assumptions that held for AirfoilInfo break on new modules.

## Script Bug: Missing InputType View in integrate_all.sh

`integrate_all.sh` didn't restore `vit_dbemt_inputtype_view.f90` in its Step 1. After `reset_to_clean.sh` stubbed all view populator files, the committed DBEMT.f90 (with ComputeTau1 wrapper) couldn't compile because the stub had no exports.

**Fix:** Added `vit_dbemt_inputtype_view.f90` to the git checkout list. This was a pre-existing gap from the ComputeTau1 integration that was never caught because the reset+integrate cycle hadn't been tested end-to-end since that integration.

## Translation Notes

- No `--reverse-copy` needed — all modifications (vind, vind_1, areStatesInitialized) go through C_LOC pointers directly into Fortran memory.
- `LOGICAL` field mapping: Fortran LOGICAL(4) → `int32_t`. `.true.` = `1`, `.not.` = `!`.
- `DBEMT_cont_tauConst = 3` already in `vit_aerodyn_constants.h`.
- No operator precedence concerns — `(1.0 - p->k_0ye)` is straightforward subtraction.

## Where We Are Now

DBEMT Phase A: **3/5 leaf functions complete** (ComputeTau2, ComputeTau1, DBEMT_InitStates).

Remaining:
- DBEMT_CalcOutput (35 lines) — output filtered velocity, conditional return
- DBEMT_CalcContStateDeriv (44 lines) — ODE derivatives, calls ComputeTau2 (C++ direct)

Infrastructure improvements from this session:
- VIT now generates typed pointers for C-compatible ALLOCATABLE TYPE() fields (both C structs and Fortran populators)
- Callee detection no longer generates false bridges for derived-type field access
- integrate_all.sh restore list is complete for all current integrations
