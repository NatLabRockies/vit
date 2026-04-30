# DBEMT_UpdateStates Integration — Phase C Begins
**Date:** 2026-04-30
**Status:** completed
**Scope:** DBEMT Phase C, VIT interface_gen.py

## Context

DBEMT_UpdateStates is the first Phase C orchestrator — the PUBLIC hot-path entry point called by BEMT per blade/node per timestep (38,400 calls in ad_MHK_RM1_Fixed, 16,920 in ad_BAR_SineMotion_UA4_DBEMT3). It calls all 4 already-translated leaf/integrator functions (ComputeTau1, ComputeTau2, DBEMT_InitStates, DBEMT_ABM4) and contains ~20 lines of inline finite difference exponential filter math for DBEMT Modes 1/2.

The project plan originally marked this function as "hand-integrated." Investigation showed no technical justification — it went through the standard 7-step VIT workflow without issues.

## VIT Infrastructure Fix: Fixed-Size View-Type Array Parameters

The function takes `type(DBEMT_InputType), intent(in) :: u(2)` — a fixed-size array of 2 view types as a dummy argument. VIT's interface_gen.py only handled view-type arrays for assumed-shape (`:`) parameters, not fixed-size.

**Root cause:** Six locations in interface_gen.py gated derived-type array handling on `arg.is_assumed_shape`. Fixed-size arrays (`arg.is_array and not arg.is_assumed_shape`) fell through to scalar handling, producing a single view declaration and single populate call instead of an array with a loop.

**Fix (6 changes in interface_gen.py):**

1. `build_c_params`: Changed `if arg.is_array and arg.is_assumed_shape:` to `if arg.is_array:` — both fixed-size and assumed-shape need size parameters in the C signature.

2. `_build_interface_args`: Same change — BIND(C) interface needs size param regardless.

3. `_build_wrapper_call_args`: Added `elif arg.is_array:` after assumed-shape block to pass the literal fixed size from `arg.array_dims` (e.g., `2`) instead of `SIZE(arg)`.

4. `_generate_view_declarations`: If `arg.is_array`, emit `TYPE(view_t), TARGET :: u_view(N)` and declare loop variable `INTEGER :: vit_view_i`.

5. `_generate_view_executables`: If `arg.is_array`, emit `DO vit_view_i = 1, N; CALL populate(u(vit_view_i), u_view(vit_view_i)); END DO` instead of single call. Kernel stash points to first element `u(1)`.

6. Reverse-copy block: If `arg.is_array`, emit DO loop for per-element reverse-copy.

**Result:** VIT generates correct wrappers. 644/644 VIT tests pass (no regressions — this is a pure addition, no existing translations use this pattern).

**Kernel stash limitation:** For array view types, the stash `vit_original_{type} => u(1)` only points to the first element. This works for DBEMT_UpdateStates because all callees receive `u(1)`, never `u(2)` directly. If a future function passes different array elements to different callees, the stash mechanism will need per-element support.

## Extraction

- **Call site:** BEMT.f90:939 (cross-module, enclosing function BEMT_UpdateStates)
- **Test case:** ad_MHK_RM1_Fixed (DBEMT_Mod=2, 38,400 hits)
- **Why Mode 2:** The finite difference math (~20 lines of inline exp() equations) is the new untranslated code. Mode 3 just calls already-verified DBEMT_ABM4.
- **Cases captured:** 20

## Translation

Direct calls to all 4 translated callees via `_c` entry points:
- `computetau1_c(&u[0], p, m, &OtherState->tau1, errStat, errMsg)`
- `computetau2_c(i, j, &u[0].element[x_idx], p, OtherState->tau1, &OtherState->tau2, 1, &k_tau)`
- `dbemt_initstates_c(i, j, &u[0], p, x, OtherState)`
- `dbemt_abm4_c(i, j, t, n, u_elem, 2, uTimes, 2, p, x, OtherState, m, errStat, errMsg)`

Finite difference branch: straightforward translation of exponential filter equations. No operator precedence issues (no `**` operator in this code — all multiplications are explicit).

`--reverse-copy` needed: OtherState has scalar fields `tau1` and `tau2` modified by ComputeTau1/ComputeTau2 through the view struct.

## KGen Verification Issue

VIT's `setup_kernel_cpp` iterative repair loop failed (reached max 5 iterations) on this large cross-module kernel (BEMT.f90 + DBEMT.f90 + full NWTC Library chain). The repair loop was chasing SetErrStat dependencies through multiple files unnecessarily. However, building the kernel directly with `make build` succeeded, and running `./kernel.exe` produced **20/20 IDENTICAL**.

This is a pre-existing VIT issue with large kernels — the repair loop does unnecessary fixes that introduce new problems. Not a blocker (the kernel itself is correct), but worth fixing for future large cross-module extractions.

## Verification

- KGen kernel: 20/20 IDENTICAL (all Mode 2, finite difference path)
- Baselines: 17/17 PASS (Mode 2 + Mode 3 paths both verified)
- VIT tests: 644/644

## Files Modified

- `vit/vit/interface_gen.py` — 6 changes for fixed-size view-type array parameters
- `openfast/translations/DBEMT/dbemt_updatestates.cpp` — new C++ translation
- `openfast/modules/aerodyn/src/DBEMT.f90` — VIT-generated wrapper
- `openfast/modules/aerodyn/src/dbemt_updatestates.cpp` — integrated copy
- `openfast/scripts/integrate_all.sh` — added UpdateStates step
- `openfast/vit.yaml` — added translation entry

## Corrected Assumption

The project plan originally said "Hand-integrated" for both Phase C orchestrators. This was a premature assumption — DBEMT_UpdateStates went through the standard 7-step VIT workflow with no issues. The only infrastructure gap (fixed-size view-type array parameters) was a VIT fix, not a reason to bypass the workflow. Updated project plan to "Standard VIT workflow" for both functions.
