# View-Type Callee Bridge Stash Fix
**Date:** 2026-03-24
**Status:** completed
**Scope:** kernel_verify.py, interface_gen.py, view_populator.py — callee bridge architecture for view-type arguments

## The Problem

PitchControl (185 lines, the master pitch orchestrator) was the last Phase 7B function to translate. The C++ translation compiled and linked but segfaulted at runtime:

```
Program received signal SIGSEGV: Segmentation fault - invalid memory reference.
#3  0xbf3eec6c52e8 in __functions_MOD_interp1d
#4  0xbf3eec7dbd0f in __controllerblocks_MOD_pitchsaturation
#5  ... in pitchcontrol (C++)
```

The crash was in `interp1d` called from `PitchSaturation`, which was invoked via a callee bridge. PitchSaturation takes `TYPE(ControlParameters)` — a view type (has ALLOCATABLE fields, uses the pointer+size view pattern).

## Root Cause

In VIT's kernel verification architecture, the data flow for view-type arguments is:

1. **Kernel wrapper** receives the original `TYPE(ControlParameters)` as a Fortran dummy argument
2. **Populator** creates a `controlparameters_view_t` (flat C struct with `double*` + `int32_t` size pairs for each ALLOCATABLE field)
3. **C++ function** receives `C_LOC(view)` — a pointer to the view struct
4. **C++ calls callee bridge** (e.g., `pitchsaturation_c(...)`) passing the same view pointer
5. **Bridge** does `C_F_POINTER(CntrPar, CntrPar_f)` where `CntrPar_f` is `TYPE(ControlParameters), POINTER`

Step 5 is the crash. The bridge interprets the view struct pointer as a `TYPE(ControlParameters)` pointer. These have completely different memory layouts:

- **View struct**: `{ ..., double* Fl_U, int32_t n_Fl_U, double* Fl_Kp, int32_t n_Fl_Kp, ... }` — flat C struct, ~310 fields
- **Original type**: `{ ..., REAL(DbKi), DIMENSION(:), ALLOCATABLE :: Fl_U, ... }` — Fortran descriptor with hidden metadata, different size, different alignment

When `C_F_POINTER` creates a Fortran pointer from the view struct's address, every field access reads from the wrong memory offset. The first ALLOCATABLE access (in `interp1d`, which dereferences `CntrPar%PS_WindSpeeds`) reads garbage → segfault.

## Why This Only Affects PitchControl

The 19 previously translated functions either:
- **Don't have CntrPar** (leaf functions like PIController, saturate, interp1d) — no view type in signature
- **Have CntrPar but no callee bridges that need it** (ForeAftDamping's only callee is PIController, which takes scalars)

PitchControl is the first function where **both conditions are true**: it has a view-type arg AND it calls other functions that also need that view-type arg (IPC, ForeAftDamping, FloatingFeedback, ActiveWakeControl, PitchSaturation — 6 of 11 callee bridges).

## The Solution: Module Variable Stash

The kernel wrapper has access to **both** the original Fortran type (dummy argument) and the view struct (local variable). The original type remains in scope for the entire wrapper execution. The fix: store a pointer to the original type in a module-level SAVE variable that callee bridges can access.

### Data Flow After Fix

```
Kernel wrapper:
  TYPE(ControlParameters), INTENT(INOUT), TARGET :: CntrPar   ← original
  TYPE(controlparameters_view_t), TARGET :: CntrPar_view       ← view struct

  CALL vit_populate_controlparameters(CntrPar, CntrPar_view)  ← fill view
  vit_original_controlparameters => CntrPar                     ← STASH [NEW]
  CALL pitchcontrol_c(C_LOC(CntrPar_view), ...)                ← C++ gets view

C++ calls: pitchsaturation_c(LocalVar, CntrPar_view_ptr, ...)

Bridge (PitchSaturation):
  USE vit_controlparameters_view, ONLY: vit_original_controlparameters  [NEW]
  ! CntrPar arg accepted in BIND(C) interface but IGNORED
  ! Uses module stash instead of C_F_POINTER
  bridge_result = PitchSaturation(LocalVar_f, vit_original_controlparameters, ...)  [CHANGED]
```

The bridge still accepts CntrPar as `TYPE(C_PTR), VALUE` in its BIND(C) interface (so the ABI matches the C++ header), but it **ignores** the pointer and instead uses the module stash variable. This is safe because:
- The stash is set before C++ is called
- The wrapper doesn't return until C++ (and all callee bridges) complete
- The stash points to the wrapper's dummy argument, which is valid for the entire call

### Implementation Details

**Three files changed, 54 lines added, 13 removed.**

#### 1. view_populator.py — Added stash variable to generated module

```fortran
MODULE vit_controlparameters_view
    ...
    PUBLIC :: controlparameters_view_t, vit_populate_controlparameters, vit_original_controlparameters

    ! Kernel stash: pointer to original Fortran type, set by wrapper before calling C++.
    ! Callee bridges USE this to pass the original type to Fortran callees.
    TYPE(ControlParameters), POINTER, SAVE :: vit_original_controlparameters => NULL()
    ...
```

The variable name follows the convention `vit_original_{type_lower}`. Initialized to NULL — set by the wrapper at runtime.

#### 2. interface_gen.py — Wrapper sets stash; bridges use stash

**Wrapper preamble** (`_generate_view_preamble`): After populating the view struct, set the stash:
```fortran
! Stash original Fortran pointers for callee bridges
vit_original_controlparameters => CntrPar
```

**USE statements** (`_get_view_use_statements`): Import the stash name alongside the view type and populator.

**Bridge generation** (`generate_kernel_callee_bridge`): Split `struct_args` into `struct_args` (regular BIND(C) types → C_F_POINTER) and `view_struct_args` (view types → module stash). View args:
- Still included in BIND(C) interface as `TYPE(C_PTR), VALUE` (ABI consistency)
- NOT declared as local Fortran pointers
- NOT C_F_POINTER'd
- Bridge adds `USE vit_{type_lower}_view, ONLY: vit_original_{type_lower}`

**Call args** (`_build_bridge_call_args`): View-type args emit `vit_original_{type_lower}` instead of `{arg.name}_f`.

#### 3. kernel_verify.py — Propagate view strategy to callee registries

Each callee gets a fresh type registry from `build_derived_type_registry()`. This registry doesn't know about view strategies from vit.yaml. The fix propagates `view_strategy` from the target's registry (which has the config applied) to the callee's registry:

```python
if derived_types and callee_registry:
    for type_name, type_info in callee_registry.items():
        if type_name in derived_types:
            parent_info = derived_types[type_name]
            if getattr(parent_info, 'view_strategy', None) == "view":
                type_info.view_strategy = "view"
```

This ensures `get_type_mapping()` returns `is_view_type=True` and `is_c_compatible=True` for ControlParameters in callee bridges.

## Alternatives Considered

1. **Pass both view and original pointers to C++** — adds an extra `void*` per view-type arg to the C function signature. Rejected: changes the ABI and the C++ translation code, violating the requirement that the same `.cpp` works in both kernel and integration.

2. **Reverse populator** (view → original type reconstruction) — impossible for ALLOCATABLE arrays. Can't reconstruct a Fortran allocatable descriptor from a bare C pointer.

3. **Patch kind constants** (make ReKi=C_DOUBLE in kernel) — tried earlier for the avrSWAP kind mismatch. Corrupts state file deserialization. Not applicable here but same class of "change the kernel types" approach.

4. **Skip CntrPar entirely in bridges** (passthrough) — the approach before this fix. Works when the callee doesn't actually access CntrPar (ForeAftDamping's PIController callee), but fails for callees that do (PitchSaturation reads CntrPar%PS_WindSpeeds).

## Result

- **PitchControl**: 40/40 PASSED — the segfault is resolved
- **ForeAftDamping regression**: 40/40 PASSED — the stash doesn't break existing bridges
- **Unit tests**: 501/501 PASSED
- **All 9 Phase 7B functions verified**: FloatingFeedback, StructuralControl, CableControl, FlapControl, YawRateControl, VariableSpeedControl, IPC, ActiveWakeControl, PitchControl
- **Total**: 28 functions translated and KGen-verified across 4 Fortran modules

## Design Properties

The module variable stash approach has several desirable properties:

1. **General**: Not hardcoded to ControlParameters. Any view type gets a stash variable automatically. If PerformanceData or ExtControlType are used in future callee bridges, the same mechanism handles them.

2. **Zero C++ changes**: The C++ translation is unchanged. The same `.cpp` file works in both kernel verification and real integration. The stash is entirely a Fortran-side mechanism.

3. **Safe**: The stash pointer is valid for the entire wrapper call duration. The stash is SAVE (persistent) but only meaningful during an active wrapper invocation. Thread safety is not a concern (KGen kernel is single-threaded).

4. **Minimal**: 54 lines changed across 3 files. No new modules, no new CLI flags, no configuration changes.

## Commit

**VIT repo:** `cfd1fad` — Fix view-type callee bridge segfault with module variable stash
