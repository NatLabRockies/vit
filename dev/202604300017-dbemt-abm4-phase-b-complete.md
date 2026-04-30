# DBEMT_ABM4 Translation — Phase B Complete
**Date:** 2026-04-30
**Status:** completed
**Scope:** DBEMT Phase B (3/3)

## Summary

Translated DBEMT_ABM4 (64-line Adams-Bashforth-Moulton predictor-corrector). Completes DBEMT Phase B — all three time integrators (RK4, AB4, ABM4) are now in C++. 17/17 baselines PASS.

## KGen Extraction Issue

ABM4's only call site is inside DBEMT_UpdateStates (line 370 in clean source). KGen extracts the enclosing function (UpdateStates), not the target (ABM4). The UpdateStates kernel crashes during state data replay — segfault in `kr_dbemt_types_dbemt_elementinputtype` when reading the `u_elem(2)` local array.

**Root cause:** KGen serializes UpdateStates' full local state, which includes complex derived-type arrays. The state reader fails during deserialization. This is a KGen limitation for functions with complex local state — not a VIT issue.

**Mitigation:** KGen verification skipped for ABM4. Baselines provide ground-truth verification (16,920 calls in `ad_BAR_SineMotion_UA4_DBEMT3`). All three callees are individually KGen-verified:
- CalcContStateDeriv: 20/20
- AB4: 20/20 (which transitively verifies RK4: 20/20)
- ExtrapInterp: verified through RK4 and AB4 kernels

ABM4's body is 64 lines of arithmetic — the correctness risk is minimal given the callee coverage and baseline density.

## Extraction Pitfall: Coverage Query vs Clean Source Line Numbers

The coverage query returned line 370 for ABM4's call site. An earlier attempt used line 473 (from `git show HEAD` which shows the integrated source, not the clean source). The KGen pragma landed inside ComputeTau2's definition — wrong function entirely. Always use the coverage query result (which reads from the clean source commit) or grep the actual on-disk file after `reset_to_clean.sh`.

## Translation Notes

ABM4 follows the predictor-corrector pattern:
1. Save pre-prediction state: `x_in = x->element[x_idx]`
2. Predict via AB4 (overwrites `x->element[x_idx]`)
3. If n > 2: correct using Adams-Moulton formula
   - ExtrapInterp at `t + DT` → `u_interp`
   - CalcContStateDeriv on predicted state → `xdot_pred`
   - `x = x_in + (DT/24) * (9*xdot_pred + 19*xdot[0] - 5*xdot[1] + 1*xdot[2])`

Note: correction uses `x_in` (saved pre-prediction state), not `x->element[x_idx]` (which was overwritten by AB4).

## Phase B Summary

| Function | KGen | Baselines | Lines | Hits |
|----------|------|-----------|-------|------|
| DBEMT_RK4 | 20/20 | 17/17 | 100 | 270 |
| DBEMT_AB4 | 20/20 | 17/17 | 77 | 16,920 |
| DBEMT_ABM4 | skipped | 17/17 | 64 | 16,920 |

**Phase B: COMPLETE.** All time integrators translated. Infrastructure proven: derived-type arrays, xdot[4] view arrays, production bridges, C++-calls-C++ callee chains, shared SetErrStat + Fortran intrinsics.

**Next:** DBEMT Phase C — orchestrators (DBEMT_UpdateStates, DBEMT_InitStates_AllNodes).
