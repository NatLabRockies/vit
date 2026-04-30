# DBEMT_RK4 Translation + Derived-Type-Array Infrastructure Fix
**Date:** 2026-04-29
**Status:** completed
**Scope:** VIT interface_gen.py, kernel_verify.py, DBEMT Phase B

## Summary

Translated DBEMT_RK4 (100-line 4th-order Runge-Kutta integrator) — the first Phase B time integrator. This required fixing a VIT infrastructure gap: assumed-shape arrays of C-compatible derived types were silently treated as scalar pointers, missing size parameters entirely. Seven bugs were fixed across VIT and OpenFAST to unblock this translation.

Results: 20/20 KGen IDENTICAL, 17/17 baselines PASS.

## Bug 1–5: Derived-Type-Array Gap in interface_gen.py

**Problem:** Four functions in `interface_gen.py` check `is_derived and is_c_compatible` BEFORE `is_array`. When a parameter is both (like `TYPE(DBEMT_ElementInputType) :: u(:)` — an assumed-shape array of a C-compatible struct), the derived-type branch fires first, emitting a single `TYPE(C_PTR)` / `struct*` without array size parameters. The `is_array` branch (which adds size params) is never reached.

This affected:
- `build_c_params()` — C++ parameter list missing `int n_u`
- `_build_interface_args()` — BIND(C) interface missing `INTEGER(C_INT), VALUE :: n_u`
- `_build_wrapper_call_args()` — wrapper passing `C_LOC(u)` without `SIZE(u)`
- `_build_bridge_call_args()` — bridge call using scalar `u_f` instead of `u_f(1:n_u)`
- Bridge body in `generate_kernel_callee_bridge()` — declaring scalar pointer instead of array pointer, and `C_F_POINTER` without shape argument

**Fix:** In each function, after the derived-type pointer/C_LOC is emitted, check `arg.is_array and arg.is_assumed_shape` and add the appropriate size handling. Also applies to OPTIONAL branches for future-proofing.

**Impact:** The ExtrapInterp callee bridge now correctly handles `u(:)` as an array:
```fortran
TYPE(C_PTR), VALUE :: u
INTEGER(C_INT), VALUE :: n_u
...
TYPE(DBEMT_ElementInputType), POINTER :: u_f(:)
CALL C_F_POINTER(u, u_f, [n_u])
CALL DBEMT_ElementInputType_ExtrapInterp(u_f(1:n_u), ...)
```

This pattern will recur in AB4 and ABM4 (same `u(:)` parameter), and in BEMT's `u_DBEMT(:,:)` input arrays.

## Bug 6: Case-Insensitive View Strategy Propagation

**File:** `kernel_verify.py`, `_generate_callee_bridges()` (~line 1218)

**Problem:** View strategy propagation from the target function's registry to callee registries used exact Python dict key lookup: `type_name in derived_types`. The target registry had keys like `DBEMT_ParameterType` (from vit.yaml), but the callee registry had `DBEMT_PARAMETERTYPE` (from the integrated Fortran source, which upper-cases type names in declarations). The lookup silently returned False, so view-type args were treated as passthrough (skipped entirely from the bridge).

**Result:** CalcContStateDeriv's bridge was missing `p` and `OtherState` parameters — the call had 9 args instead of 11, causing type mismatches.

**Fix:** Build a case-insensitive lookup dict:
```python
parent_upper = {k.upper(): v for k, v in derived_types.items()}
for type_name, type_info in callee_registry.items():
    parent_info = parent_upper.get(type_name.upper())
```

**Why this wasn't caught before:** Previous callee bridges (ComputeTau2 from CalcContStateDeriv) had matching case because the call site was in clean source where type names preserved original case. RK4 is the first function verified on integrated source where the wrapper's declarations use all-caps type names.

## Bug 7: Stale KGen Artifacts in NWTC_Base.f90

**File:** `modules/nwtc-library/src/NWTC_Base.f90`

KGen instrumentation (`PUBLIC kw_externs_in_nwtc_base` + subroutine body) from a previous extraction (AFI_ComputeUACoefs, commit `f08ed63df`) was accidentally committed. When KGen ran for RK4 and added its own copy, the duplicates caused `ACCESS specification already specified` and `Procedure already defined` compilation errors during state capture.

**Fix:** Removed the stale KGen artifacts from the committed NWTC_Base.f90.

## Additional Changes

### AbortErrLev Constant

Added `static constexpr int AbortErrLev = ErrID_Fatal` to `vit_nwtc.h`. Removed duplicate local definition from `afi_computeairfoilcoefs.cpp`. Fortran's `AbortErrLev` is a mutable global in NWTC_Base defaulting to `ErrID_Fatal`; it's never changed in AeroDyn, so a constant is safe.

### Production ExtrapInterp Bridge

Created `vit_bridge_extrapinterp.f90` — a permanent BIND(C) bridge for `DBEMT_ElementInputType_ExtrapInterp`. This is needed because RK4's C++ calls ExtrapInterp (a Fortran routine in DBEMT_Types.f90 that isn't translated). In the kernel, VIT auto-generates temporary bridges; in production, a committed bridge file is required.

This is the first "production bridge" — a pattern that will recur whenever translated C++ calls untranslated Fortran routines that aren't simple enough to inline.

### ErrMsg Handling in ExtrapInterp Bridge

The kernel bridge didn't copy `local_ErrMsg` back to the `ErrMsg(*)` C_CHAR array. The production bridge adds the copy loop so error messages propagate correctly through the C++ caller's SetErrStat lambda.

## Translation Notes

DBEMT_RK4 is a straightforward RK4 integrator:
- 3 ExtrapInterp calls (at t, t+dt/2, t+dt) for input interpolation
- 4 CalcContStateDeriv calls (k1, k2, k3, k4) for derivative evaluation
- Error checks on all 3 ExtrapInterp calls + first CalcContStateDeriv only (per original Fortran)
- SetErrStat lambda pattern (same as ComputeTau1)
- Array element access: `x->element[(j-1) * x->n_element_rows + (i-1)]`
- Field name: `p->DT` (uppercase, matches Fortran `p%DT`)

## Files Modified

- `vit/vit/interface_gen.py` — 5 derived-type-array fixes + 2 OPTIONAL branch fixes
- `vit/vit/kernel_verify.py` — case-insensitive view strategy propagation
- `openfast/modules/nwtc-library/src/NWTC_Base.f90` — removed stale KGen artifacts
- `openfast/modules/aerodyn/src/vit_nwtc.h` — added AbortErrLev constant
- `openfast/modules/aerodyn/src/afi_computeairfoilcoefs.cpp` — removed duplicate AbortErrLev
- `openfast/modules/aerodyn/src/vit_bridge_extrapinterp.f90` — new production bridge
- `openfast/modules/aerodyn/src/vit_translated.h` — added ExtrapInterp declaration
- `openfast/modules/aerodyn/CMakeLists.txt` — added bridge + .cpp
- `openfast/translations/DBEMT/dbemt_rk4.cpp` — C++ translation
- `openfast/vit.yaml` — added DBEMT_RK4 translation entry
- `openfast/scripts/integrate_all.sh` — added RK4 integration step

## Phase B Progress

| Function | Status | Lines | Hits |
|----------|--------|-------|------|
| DBEMT_RK4 | **COMPLETE** | 100 | 270 |
| DBEMT_AB4 | pending | 77 | 16,920 |
| DBEMT_ABM4 | pending | 64 | 16,920 |

AB4 calls RK4 (bootstrap for n≤2) and uses `OtherState%xdot(1..4)%element(i,j)` — the first real exercise of the xdot[4] view infrastructure. ABM4 calls AB4 for prediction + correction. Both share the same `u(:)` / `utimes(:)` assumed-shape pattern now solved by the derived-type-array fix, and both call ExtrapInterp through the production bridge.
