# WSE Scenario 9 Divergence: Investigation Reset

**Date:** 2026-03-28
**Status:** completed (see `202603282007-phase8c-scenario9-resolution.md` for full resolution)
**Scope:** Phase 8C integration test — Scenario 9 remaining divergence after pow() and MATMUL fixes

## Summary

After extensive investigation of the WindSpeedEstimator (WSE) integration divergence, two root causes were identified and fixed, resolving 8 of 9 scenarios. Scenario 9 remains divergent. A flawed diagnostic approach was discovered and abandoned, requiring a fresh start with properly time-aligned analysis.

## Fixes Applied (Confirmed Working)

### Fix 1: `v_m**3.0` → `std::pow(v_m, 3.0)`

**Root cause:** gfortran compiles `x**3.0` (REAL exponent) as a call to libm `pow(x, 3.0)`, NOT as `x*x*x`. The results differ by 1 ULP for some input values. Confirmed via hex comparison on aarch64:

```
v=6.4246: pow()=407092DB0FE608E6, x*x*x=407092DB0FE608E5 (1 ULP)
```

gfortran DOES optimize `x**2.0` → `x*x` and `x**2` → `x*x`, but NOT `x**3.0`.

**Fix:** Changed Q(2,2) computation in `windspeedestimator.cpp`:
```cpp
// Before (wrong): (PI * (v_m * v_m * v_m) * (Ti * Ti)) / L
// After (correct): (PI * std::pow(v_m, 3.0) * (Ti * Ti)) / L
```

**Impact:** Fixed Scenario 7.

### Fix 2: EKF MATMUL via Fortran bridge

**Root cause:** gfortran's MATMUL for 3×3 matrices produces subtly different results (1 ULP) from explicit C++ triple loops for some matrix element computations. Diagnosed by decomposing the P matrix update into FP, PFt, KRKt intermediates and finding that PFt(2,1) diverged by exactly 1 ULP at EKF step 5 despite identical inputs.

**Fix:** Created `vit_ekf_update` in `vit_math_bridge.f90` — a Fortran subroutine that performs the entire EKF prediction + measurement update using Fortran's native MATMUL and array expressions. The C++ WSE calls this bridge instead of doing the matrix math in C++.

```fortran
SUBROUTINE vit_ekf_update(xh, P, K, F, Q, dxh, R_m, DT, WE_Inp_Speed, om_r) BIND(C)
    ! State prediction: xh = xh + DT * dxh
    ! P prediction: P = P + DT*(F*P + P*F^T + Q - K*R_m*K^T)
    ! Measurement: S, K, xh update, P update — all using Fortran MATMUL
END SUBROUTINE
```

**Impact:** Fixed Scenario 8.

### Disproven Hypotheses

- **cos/sin/atan2 1-ULP difference**: Created `vit_cos`/`vit_sin`/`vit_atan2` bridges wrapping Fortran intrinsics. Divergence persisted unchanged. NOT the cause. (Bridges kept in codebase as they're harmless and theoretically correct.)
- **LPFilter instance counter interaction**: Call order is determined by DISCON.F90 (still Fortran), so instance slots are consistent.

## Current State

| Scenario | Status | Fix that resolved it |
|----------|--------|---------------------|
| 1-6 | IDENTICAL | Already identical before WSE fixes |
| 7 | IDENTICAL | pow() fix |
| 8 | IDENTICAL | MATMUL bridge |
| 9 | **DIVERGES** | Still under investigation |

## Scenario 9 Investigation: What Went Wrong

### The alignment error

The diagnostic approach used per-EKF-step debug output — a WRITE/fprintf that fired only when the EKF normal path was active (`WE_Mode == 2 && WE_Op > 0 && RestartWSE != 0`). This produced:

- Baseline: **9,651** EKF steps
- Integrated: **11,229** EKF steps

A difference of **1,578 steps**. This means `WE_Op` transitions happen at different simulation timesteps between baseline and integrated, causing the EKF to activate/deactivate at different times. Step N in the baseline file does NOT correspond to the same simulation time as step N in the integrated file.

All step-by-step comparisons performed on these misaligned files (including the "17 ULP Cp_op difference at step 1" and "2 ULP pitch_R2D at step 1") were **comparing different operating points**, not the same timestep. These findings are invalid.

### The interp2d error check anomaly

During investigation, we discovered that the Fortran `interp2d` function has **swapped dimension checks**:

```fortran
! Comment says "xData and zData(:,1)" but checks dim 1 (rows), not dim 2 (cols)
IF (SIZE(xData) .NE. SIZE(zData,1)) THEN  ! Should be SIZE(zData,2)
IF (SIZE(yData) .NE. SIZE(zData,2)) THEN  ! Should be SIZE(zData,1)
```

With the non-square Cp_mat (26×36), both checks fire in the Fortran baseline, setting `aviFAIL = -1`. The C++ `interp2d_c` has the correct checks (`n_xData == n_zData_cols`), which pass. This means `aviFAIL` differs between baseline and integrated within each timestep.

However, this does NOT affect numerical computation:
- The error check just sets a flag and continues — no early return
- `aviFAIL` is reset to 0 at the start of each timestep by `SetParameters`
- The bilinear interpolation computes and returns the correct value regardless of the error flag
- The error message modification (`RoutineName//':'//TRIM(ErrMsg)`) only affects the ErrMsg string, not numerics

### What we actually know about Scenario 9

1. The divergence is real — first visible in simulation output at index ~10001 (t≈250s, the shutdown boundary)
2. The 1,578-step difference in EKF activation means the integrated WSE produces subtly different WE_Vw over time, causing pitch/torque feedback differences, which eventually change when pitch crosses the `Max_Op_Pitch` boundary for WE_Op
3. All individual expressions (F matrix, dxh, lambda, interp1d, interp2d) have been verified identical via hex comparison for specific test values — but the comparison was not exhaustive across all values encountered during Scenario 9
4. The EKF bridge ensures all matrix math is done in Fortran — the remaining C++ code is limited to: F/Q matrix value computation, dxh computation, input saturation, WE_Op logic, HorWindV_F filter, and state extraction

## Fresh Approach: Time-Aligned Diagnostics

### Round 1: WE_Vw + WE_Op per timestep

Added one debug line per WSE call (11,999 lines each, perfectly aligned). Found:
- **First WE_Vw diff at call 321** — 1 ULP, intermittent initially, growing to 2-3 ULP over time
- WE_Op doesn't differ until call 10422 (accumulated WE_Vw drift causes threshold crossing)

### Round 2: Add Cp_op, pitch_R2D, lambda, Tau_r

Used SYMMETRIC conditions (only output on EKF normal path in both) to avoid artifacts.

- **Cp_op first differs at call 212 by 17 ULP** — from interp2d
- **Tau_r first differs at call 212 by 24 ULP** — from AeroDynTorque (which calls interp2d)
- **pitch_R2D first differs at call 212 by 2 ULP** — input to interp2d
- Call 211: ALL IDENTICAL. Call 213: ALL IDENTICAL. Intermittent pattern.
- **lambda: identical at call 212** — so the pitch input to interp2d is the source

### Round 3: Add BlPitchCMeas

Added BlPitchCMeas to trace pitch difference source.

**Key finding: `BlPitchCMeas` differs at call 203 by 2 ULP — BEFORE any WSE output diverges.**

This means the simulation is already feeding different pitch to the controller before WSE has had any effect on the simulation. Since:
- WE_Vw is identical through call 320
- All 36 other functions produce identical outputs (36-function test)
- The simulation is deterministic (baseline runs identically twice)

The `BlPitchCMeas` comes from `PitComAct` (the previous timestep's pitch command, in double precision — NOT from float32 avrSWAP). ROSCO reads `BlPitch = PitComAct` after iStatus=0 (see ReadSetParameters.f90 line 114-116), preserving double-precision controller state across timesteps.

### Hypothesis tested: aviFAIL state difference

The Fortran `interp2d` has swapped dimension error checks that fire for the non-square 26×36 Cp_mat, setting `aviFAIL = -1`. The C++ `interp2d_c` has correct checks that don't fire, leaving `aviFAIL = 0`. This aviFAIL difference persists within each timestep.

**Test:** Swapped the C++ checks to match Fortran's wrong behavior. Result: divergence became WORSE (earlier, at index 211). **Hypothesis disproven** — the correct error checks are better, and the aviFAIL difference is not the cause.

### Current state of understanding

The `BlPitchCMeas` 2-ULP difference at call 203 remains unexplained. All known sources of difference have been eliminated:
- WE_Vw identical before call 203
- All 36 non-WSE functions verified identical
- Simulation deterministic
- aviFAIL hypothesis disproven
- All individual expressions verified identical via hex comparison

The difference must originate from an interaction not yet identified — possibly in a filter state, instance counter, or SAVE variable that differs due to the WSE integration in a way not captured by the 36-function test.

## Files Modified (Permanent Fixes)

| File | Change |
|------|--------|
| `translations/ControllerBlocks/windspeedestimator.cpp` | `std::pow(v_m, 3.0)` for Q(2,2); `vit_ekf_update` bridge call; all debug code removed |
| `rosco/controller/src/vit_math_bridge.f90` | Added `vit_ekf_update` bridge subroutine |

## Lessons Learned

### 1. Always align debug output by simulation time

Per-EKF-step output creates implicit assumptions about alignment that fail when the EKF activation pattern differs. Always include an absolute timestamp or sequential counter.

### 2. Verify alignment before comparing

Before drawing conclusions from step-by-step comparisons, verify that the files have the same number of lines AND that corresponding lines represent the same simulation state.

### 3. The `x**3.0` ≠ `x*x*x` rule generalizes

Any Fortran `x**N.0` where N > 2 and the exponent is REAL (not INTEGER) will use `pow()`. This must be matched in C++ translations. Add to TRANSLATION_TIPS.md:
- `x**2` or `x**2.0` → `x*x` (gfortran optimizes both)
- `x**3` → `x*x*x` (integer exponent, gfortran optimizes)
- `x**3.0` → `std::pow(x, 3.0)` (REAL exponent, gfortran calls pow)
- `x**N` for small integer N → repeated multiply
- `x**N.0` for N > 2 → `std::pow(x, (double)N)`
