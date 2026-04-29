# DBEMT Phase A Complete — All 5 Leaf Functions Integrated
**Date:** 2026-04-29
**Status:** completed
**Scope:** DBEMT Phase A completion, callee bridge fix

## What We Did

Completed DBEMT_CalcOutput and DBEMT_CalcContStateDeriv — the final two functions in DBEMT Phase A. All 5 leaf math functions are now integrated with 20/20 KGen and 17/17 baselines passing.

## DBEMT_CalcOutput (35 lines)

Simple conditional output: return unfiltered input velocity if states aren't initialized, else return filtered state. Cross-module extraction from BEMT.f90:1215.

**Issue found:** ErrMsg blank-padding. Fortran `ErrMsg = ""` fills the buffer with spaces; C++ `errMsg[0] = '\0'` leaves garbage. Fix: `std::memset(errMsg, ' ', ErrMsgLen)`. Added to TRANSLATION_TIPS.md as standard pattern.

## DBEMT_CalcContStateDeriv (44 lines)

ODE time derivative computation for Mode 3. Computes d(vind)/dt and d(vind_1)/dt using tau1/tau2 filter equations.

**Two issues found:**

### 1. Callee bridge for view-type parameters

The Fortran CalcContStateDeriv calls ComputeTau2, which takes `TYPE(DBEMT_ParameterType)` — a view type. VIT generated a callee bridge, but the bridge couldn't compile because:
- Module procedures inherit USE statements from the parent module
- VIT's `_get_use_statements_for_derived_types()` only checks the function's own USE statements
- The bridge declared `TYPE(DBEMT_ELEMENTINPUTTYPE), POINTER :: u_f` without USE-ing `DBEMT_Types`

Additionally, the bridge can't handle view-type parameters: the bridge receives a view struct (different layout) and can't reconstruct the full Fortran type from it. It would need the kernel stash pattern (USE the populator module's `vit_original_*` pointer).

**Resolution:** Inlined ComputeTau2's computation (3 lines) since the Mode 3 guard guarantees `DBEMT_Mod == 3`, which always uses `p%spanRatio(i,j)`. This is equivalent and avoids the bridge entirely. 20/20 KGen IDENTICAL confirms the inline is correct.

### 2. Bridge skip for unreferenced callees

After inlining, VIT still generated the broken bridge because callee detection works on the Fortran source (which has `CALL ComputeTau2`), not the C++ source. VIT doesn't know the C++ inlined it.

**Fix in VIT (`kernel_verify.py`):** Before generating each bridge, check if the callee name (or `callee_c`) appears in the C++ source text. If neither appears, skip the bridge. This prevents generating broken bridges for callees that were inlined in the C++ translation.

**Gotcha:** A C++ comment containing the callee name (e.g., `// Inline ComputeTau2`) counts as a reference. Renamed the comment to avoid this.

### Open item: callee bridge for view-type parameters

The underlying bridge issue (view-type parameters needing the kernel stash) remains unfixed. For Phase B, if a C++ function needs to call ComputeTau2 via the bridge (rather than inlining), the bridge generator needs enhancement:
1. Add USE statements for types defined in parent modules
2. Use `vit_original_*` stash for view-type parameters instead of `C_F_POINTER`

This isn't urgent — inlining works for CalcContStateDeriv, and Phase B functions can use the same pattern or call ComputeTau2 directly as C++ (via `vit_translated.h` in production).

## Phase A Summary

| Function | Lines | KGen | Baselines | Key Lesson |
|----------|-------|------|-----------|------------|
| ComputeTau2 | 26 | 20/20 | 17/17 | Operator precedence `a*x**2`, vectorization divergence |
| ComputeTau1 | 66 | 20/20 | 17/17 | Error handling pattern (SetErrStat lambda), 3 VIT bugs |
| DBEMT_InitStates | 26 | 20/20 | 17/17 | C-compatible typed pointers, field access callee detection |
| DBEMT_CalcOutput | 35 | 20/20 | 17/17 | ErrMsg blank-padding with memset |
| DBEMT_CalcContStateDeriv | 44 | 20/20 | 17/17 | Callee bridge limitations, inline alternative |

**Total VIT infrastructure improvements during Phase A:**
- 8 bug fixes (regex, rank mismatch, CHARACTER(*), C++ scan, intrinsic detection, typed pointers, field access callee, bridge skip)
- 3 enhancements (auto-type infrastructure, fixed-size view arrays, coverage query clean-source)
- 2 process improvements (ErrMsg blank-padding tip, commit-before-test-cycle)

## What's Next

DBEMT Phase B: Time integrators (DBEMT_RK4, DBEMT_AB4, DBEMT_ABM4). These use the xdot[4] history array infrastructure (KD9). The bridge limitation for view-type parameters may need to be addressed if these functions call translated callees through bridges rather than inlining.
