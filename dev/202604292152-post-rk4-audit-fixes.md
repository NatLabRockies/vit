# Post-RK4 Audit Fixes
**Date:** 2026-04-29
**Status:** completed
**Scope:** VIT interface_gen.py, kernel bridge generator

## Context

After completing DBEMT_RK4 translation, a systematic audit of all session changes found three additional bugs in VIT infrastructure — two in OPTIONAL branch consistency and one in the kernel bridge generator's CHARACTER argument handling. All three are latent bugs that would surface when future functions hit the affected patterns.

## Bug 1: OPTIONAL Derived-Type Arrays Missing SIZE Params

**File:** `vit/vit/interface_gen.py`, `_build_wrapper_call_args()` (~line 952)

The OPTIONAL branch for C-compatible derived types emitted `C_LOC(arg)` without checking whether the arg is an array. The non-OPTIONAL counterpart (added in the derived-type-array fix) correctly checks `arg.is_array` and appends `SIZE()` params.

**Before:** OPTIONAL derived-type array generates `has_flag, C_LOC(u)` — missing size params, wrong element address.
**After:** Generates `has_flag, C_LOC(u(1)), SIZE(u)` — matching non-OPTIONAL behavior.

**Impact:** Any OPTIONAL parameter that is an assumed-shape array of a C-compatible derived type would produce mismatched argument counts between the Fortran wrapper and C function. No current AeroDyn functions hit this pattern, but it would fail silently on the first one encountered.

## Bug 2: OPTIONAL Non-Derived 2D Arrays Missing Rank Handling

**File:** `vit/vit/interface_gen.py`, `_build_interface_args()` (~line 828)

Pre-existing gap: the OPTIONAL branch for non-derived arrays emitted a single `n_{arg.name}` size param regardless of rank. The non-OPTIONAL counterpart correctly distinguishes 1D (`n_u`) from 2D (`n_u_rows`, `n_u_cols`).

**Before:** OPTIONAL 2D array generates `INTEGER(C_INT), VALUE :: n_u` — wrong for 2D.
**After:** Generates `n_u_rows` and `n_u_cols` for rank-2 arrays, matching non-OPTIONAL behavior.

## Bug 3: Kernel Bridge Generator Missing ErrMsg Copy-Back

**File:** `vit/vit/interface_gen.py`, `generate_kernel_callee_bridge()` (~line 1360)

The bridge generator declares `CHARACTER(1024) :: local_ErrMsg` buffers for CHARACTER OUT/INOUT args and passes them to the Fortran callee. But after the call, it never copies `local_ErrMsg` back to the `ErrMsg(*)` C_CHAR array. The C++ caller receives uninitialized data for error messages.

**Root cause:** The `char_buffer_args` list was populated (tracking which args need copy-back) but never consumed. The copy-back loop was implemented in hand-written production bridges but missing from the generator.

**Fix:** Added a copy-back loop after the Fortran call + view re-sync:
```python
if char_buffer_args:
    for buf_name in char_buffer_args:
        lines.append(f"        DO vit_i = 1, MIN(LEN_TRIM(local_{buf_name}), 1024)")
        lines.append(f"            {buf_name}(vit_i) = local_{buf_name}(vit_i:vit_i)")
        lines.append(f"        END DO")
```
Also added `INTEGER :: vit_i` declaration when char_buffer_args is non-empty.

**Why 20/20 still passed without this:** ExtrapInterp returned no errors for the 20 captured states (ErrStat=0, ErrMsg=spaces). The C++ SetErrStat lambda checked `ErrStat2` (correctly received via INTENT(OUT)) and skipped the message string when level was `ErrID_None`. The uninitialized ErrMsg data was never read.

**Impact:** All auto-generated kernel callee bridges with CHARACTER OUT/INOUT args now correctly propagate error messages to C++ callers. This affects every bridge for functions that take ErrMsg (most OpenFAST routines).

## Noted But Not Fixed

- **Duplicate USE statements in auto-generated bridges** — cosmetic (e.g., `USE DBEMT_Types, ONLY: DBEMT_ElementInputType` appears twice when the type is used for both an input and output arg). Harmless in Fortran.
- **AbortErrLev as constexpr** — Fortran declares it as a mutable global; C++ treats it as a compile-time constant. Safe in AeroDyn (no code modifies it), but fragile if NWTC Library behavior changes.

## Files Modified

- `vit/vit/interface_gen.py` — 3 fixes (OPTIONAL wrapper args, OPTIONAL interface args, bridge ErrMsg copy-back)

## Verification

644/644 VIT tests pass. Production build succeeds. 17/17 baselines unchanged.
