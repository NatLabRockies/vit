# WSE Integration Divergence: cos() Hypothesis Disproven
**Date:** 2026-03-28
**Status:** in-progress
**Scope:** Phase 8C integration test — continued investigation of Scenario 7/9 divergence

## Previous Hypothesis (Disproven)

The previous dev note (`202603281044`) hypothesized that the divergence was caused by a 1-ULP difference between gfortran's `COS()` and g++'s `std::cos()` on aarch64. Direct testing confirmed the 1-ULP difference exists:

```
Fortran cos(0.349...): 0.93969262085392453
C++    cos(0.349...): 0.93969262085392458
                                        ^-- 1-ULP difference
```

## Disproof

Created `vit_math_bridge.f90` — a Fortran module wrapping `COS()`, `SIN()`, `ATAN2()` with `BIND(C)` entry points (`vit_cos`, `vit_sin`, `vit_atan2`). Replaced all `std::cos/sin/atan2` calls in the C++ translations (WindSpeedEstimator and Shutdown) with these bridge functions.

Verified:
- `vit_cos` IS defined in the integrated library (`nm` shows symbol at 0x2bd40)
- The integrated `windspeedestimator_cpp.cpp` DOES call `vit_cos` (confirmed by grep)
- The bridge compiles and links correctly

**Result: Scenarios 7 and 9 still diverge with the exact same pattern.** Same first-diff index (4408 for Scenario 7, 10001 for Scenario 9), same magnitudes. cos/sin/atan2 are definitively NOT the cause.

## What We Know

1. **36 functions (29 + Phase 8B + interp2d + AeroDynTorque) → ALL IDENTICAL** for all 9 scenarios
2. **Adding WindSpeedEstimator → Scenarios 7 and 9 diverge**
3. **WSE kernel verification: 21/21 IDENTICAL** — per-step behavior matches Fortran exactly
4. **Math bridge doesn't fix it** — cos/sin/atan2 are not the cause
5. **The divergence is deterministic** — same pattern every run
6. **First diff is sub-ULP** (bld_pitch: -1.69e-21 vs 0.0 at Scenario 7 index 4408)

## The Paradox

Per-step behavior is IDENTICAL (kernel verification proves this), yet the integrated sim diverges over thousands of timesteps. This should be impossible if:
- WSE receives the same inputs in both baseline and integrated (proven — 36-function test is identical)
- WSE produces the same outputs for the same inputs (proven — 21/21 kernel verification)

The only explanation: **something about the integrated binary's execution path changes the WSE behavior in a way that the kernel test doesn't capture.** Possibilities:

### 1. LPFilter Instance Counter Interaction

The WSE calls `LPFilter` via `lpfilter_c`. LPFilter uses an instance counter (`objInst%instLPF`) to index into filter state arrays (FP). In the baseline, Fortran WSE calls Fortran LPFilter — the instance counter increments within the same Fortran call stack. In the integrated binary, C++ WSE calls `lpfilter_c` directly — the counter might increment at a different point in the call sequence if other C++ functions also call `lpfilter_c` before WSE in the same timestep.

The instance counter is shared across ALL LPFilter callers. If the ORDER of LPFilter calls changes between the baseline and integrated binary (due to integration changing when lpfilter_c vs the Fortran wrapper is called), different callers might get different instance slots, which would produce different filter outputs.

**This is the most likely candidate.** The kernel test doesn't capture this because the kernel replays WSE in isolation — there are no other callers competing for instance slots.

### 2. PerformanceData View Struct Field Ordering

AeroDynTorque receives PerformanceData as a view struct. If a field offset is wrong, the interp2d call inside AeroDynTorque would use wrong Cp surface data. But the 36-function test (which includes AeroDynTorque with PerformanceData view) is IDENTICAL, so this is unlikely.

### 3. Fortran Array Temporary Semantics

Fortran creates temporaries for array expressions like `P = P + DT * (...)`. My C++ does element-by-element updates, but only AFTER pre-computing MATMUL results from the original P. This should be equivalent. Would need very careful review to rule out completely.

## Next Steps

**Priority: investigate the LPFilter instance counter hypothesis.**

1. Check how `objInst%instLPF` evolves in the baseline vs integrated binary by comparing the value at the WSE call site
2. Trace ALL `lpfilter_c` / `LPFilter` calls in one timestep (from DISCON through all subroutines) and verify the call order is the same in both binaries
3. If the call order differs, identify which integrated function changed the ordering and fix it

**Secondary: add debug output to WSE**

If the instance counter is fine, add a debug write of WE_Vw and HorWindV_F to a file on each timestep in both the Fortran WSE (via a temporary WRITE statement) and C++ WSE (via fprintf). Compare bit-for-bit to find the exact timestep and field where the first difference appears.
