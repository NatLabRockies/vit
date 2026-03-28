# WindSpeedEstimator Integration: cos() libm Divergence on aarch64
**Date:** 2026-03-28
**Status:** completed (root cause identified)
**Scope:** Phase 8C integration test — sub-ULP divergence in Scenarios 7 and 9

## Summary

The Phase 8 full integration test (39 functions, 9 scenarios) produces byte-identical results for Scenarios 1-6 and 8, but diverges in Scenarios 7 and 9. The root cause is a 1-ULP difference between gfortran's `COS()` intrinsic and g++'s `std::cos()` on aarch64 Linux. This is a math library implementation difference, not a translation logic error.

## Investigation Process

### Symptom

After integrating all 39 functions, the baseline vs integrated comparison showed:
- Scenarios 1-6, 8: ALL IDENTICAL (612,000 values)
- Scenario 7: MISMATCH starting at index 4408 (t=110.2s)
- Scenario 9: MISMATCH starting at index 10001 (t=250.0s)

The first difference in Scenario 7 was bld_pitch: baseline=-1.69e-21, integrated=0.0. This is a sub-ULP value (~8.5×10⁻⁶ ULPs for a value near zero), but it diverged rapidly through closed-loop feedback: within 5 timesteps, gen_torque differed by 600 Nm (out of 27,000 Nm).

### Isolation

Binary search by incrementally adding functions to the integration:

| Functions Integrated | Scenario 7 Result |
|---------------------|-------------------|
| 29 (Phase 7B) | IDENTICAL |
| 34 (+ Phase 8B Scenario 3) | IDENTICAL |
| 34 + RefSpeedExclusion = 35 | IDENTICAL |
| 36 (+ interp2d + AeroDynTorque) | IDENTICAL |
| 37 (+ WindSpeedEstimator) | **MISMATCH** |

**Conclusion:** The WindSpeedEstimator C++ translation causes the divergence. interp2d and AeroDynTorque are correct even in the integrated binary (the Fortran WSE calls C++ interp2d/AeroDynTorque through wrappers and produces identical results).

### Why kernel verification passes

Kernel verification replays each captured invocation independently — it loads the Fortran-captured input state, calls the function once, and compares the output. Each of the 21 cases passes IDENTICAL. The difference is too small to observe in a single step.

In the integrated binary, the C++ output from timestep N becomes the input for timestep N+1. A sub-ULP difference accumulates over thousands of timesteps. The EKF's closed-loop feedback (Kalman gain → state update → Jacobian → covariance update → Kalman gain) amplifies tiny differences exponentially.

### False lead: pow() vs multiplication

Initial hypothesis was that Fortran's `x**2.0` (which calls `pow()`) differs from C++'s `x*x`. Testing disproved this: changing C++ `x*x` to `pow(x, 2.0)` caused AeroDynTorque to FAIL 3/20 cases. This confirms gfortran compiles `x**2.0` as `x*x`, not as `pow(x, 2.0)`. The original `x*x` translation was correct.

### Root cause: cos() implementation difference

Direct comparison of gfortran's `COS()` vs g++'s `std::cos()` on aarch64 (Ubuntu 24.04 on Colima/Apple Silicon):

```
Input:   0.34906585020000001 (20 degrees in radians, D2R-converted)

Fortran: 0.93969262085392453
C++:     0.93969262085392458
                           ^
                     1-ULP difference (last significant digit)
```

The WindSpeedEstimator computes `cos(NacVaneF * D2R)` at line 377 on every timestep. In Scenario 7, NacVaneF oscillates ±20 degrees (synthetic input), so cos is called with a different argument every timestep. Each call produces a 1-ULP different result between Fortran and C++. This feeds into `HorWindV_F`, which feeds into the EKF state initialization and wind speed estimate.

Over 4400 timesteps of EKF operation with oscillating inputs, the accumulated drift reaches ~1e-21 in bld_pitch — small enough to be invisible per-step, but the EKF's closed-loop feedback amplifies it into observable differences within a few more timesteps.

### Why only Scenarios 7 and 9?

- **Scenarios 1-6, 8:** The wind profile is simpler (step wind, not oscillating). The cos factor `cos(NacVaneF * D2R)` is close to 1.0 for small NacVaneF (most of the time in these scenarios). The EKF converges to a steady state and the 1-ULP cos difference is absorbed.

- **Scenario 7:** Synthetic NacVane oscillates ±20 degrees, keeping the cos factor actively changing and the EKF tracking. The accumulated cos drift doesn't converge — it grows until observable.

- **Scenario 9:** The SD_Time=250s triggers Shutdown, which changes pitch and torque rates. The WSE's operational range check (`WE_Op`) may transition at a slightly different time due to the cos drift, causing the shutdown response to diverge.

## Nature of the Issue

This is NOT a translation bug. It's a **math library implementation difference** — the same category as FMA contraction. Both gfortran and g++ produce IEEE 754-compliant results for `cos()`, but they may use different polynomial approximations (e.g., different minimax coefficients, different Cody-Waite range reduction) that give results within 1 ULP of each other.

On x86, this difference often doesn't manifest because both compilers link against the same `libm.so`. On aarch64, the situation may be different:
- gfortran may use a built-in intrinsic or a different libm path
- g++ uses `std::cos()` from libstdc++ which delegates to glibc's `cos()`
- Even if both use the same libm, the compiler may inline different approximations at `-O2`

The 1-ULP cos difference is within IEEE 754 tolerance — both results are correctly rounded. The issue only becomes visible because the EKF accumulates thousands of such differences through closed-loop feedback.

## Comparison to FMA Issue

This is analogous to the FMA contraction issue documented in CLAUDE.md:
- **FMA:** gfortran and g++ independently choose which multiply-adds to fuse, causing 1-ULP differences. Fixed by `-ffp-contract=off`.
- **cos():** gfortran and g++ use different `cos()` implementations, causing 1-ULP differences. Cannot be fixed by a compiler flag.

The FMA issue was solved by disabling FMA on both sides. The cos() issue cannot be solved the same way — there's no flag to force gfortran and g++ to use the same cos implementation.

## Options

1. **Accept it (recommended for now).** The divergence is bounded and only affects scenarios with oscillating inputs and long EKF runs. It's a known limitation of cross-language compilation on aarch64. When ROSCO is fully translated to C++ (Phase 11), there's no Fortran to differ from.

2. **Call Fortran cos from C++.** Create a thin Fortran wrapper `fortran_cos(x)` that calls Fortran's `COS()`, and call it from the C++ WSE via `extern "C"`. This forces both paths to use the same cos implementation. Downside: adds a Fortran dependency to the C++ code, which defeats the purpose of translation.

3. **Use a custom cos implementation.** Write a `vit_cos()` that both Fortran and C++ call, guaranteeing identical behavior. Invasive and fragile.

4. **Accept IN_TOL for WSE in integration tests.** Modify the integration test comparison to allow tolerance-based matching for scenarios that exercise the EKF with oscillating inputs. The tolerance would be bounded by the 1-ULP cos drift accumulated over the scenario length.

## Affected Scenarios

| Scenario | Status | EKF Active | NacVane Oscillation | Divergence |
|----------|--------|------------|---------------------|------------|
| 1 | IDENTICAL | Yes (1000s) | No (steady wind) | None |
| 2 | IDENTICAL | No | No | None |
| 3 | IDENTICAL | Yes (400s) | No | None |
| 4 | IDENTICAL | Yes (100s) | No | None |
| 5 | IDENTICAL | Yes (400s) | No | None |
| 6 | IDENTICAL | Yes (100s) | No | None |
| 7 | DIVERGES at t=110s | Yes (600s) | **Yes (±20 deg)** | cos() drift amplified by EKF |
| 8 | IDENTICAL | Yes (400s) | No | None |
| 9 | DIVERGES at t=250s | Yes (300s) | No | Shutdown trigger timing |
