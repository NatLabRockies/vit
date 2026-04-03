# Scenario 26: Closing the Flap Output Coverage Gap
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification coverage — non-zero flap angle output

## Summary

Created Scenario 26 to close the last remaining output coverage gap. All four existing flap scenarios (3, 4, 7, 16) produced zero flap output despite exercising FlapControl. Investigation revealed three distinct root causes, each requiring a different fix. Scenario 26 combines Flp_Mode=3 with synthetic rootMOOP injection to produce non-zero, per-blade-differentiated flap angle commands verified bit-identically across upstream Fortran, modified Fortran, and C++.

## Why Flap Output Was Zero

| Scenario | Flp_Mode | rootMOOP | Why zero |
|----------|----------|----------|----------|
| 3 | 1 (steady-state) | ~0 (1-DOF) | Mode 1 holds `Flp_Angle` constant at `CntrPar%Flp_Angle` (0.0 rad). No controller runs. **rootMOOP is completely ignored.** |
| 7 | 1 (steady-state) | 1000 Nm (synthetic) | Same as Scenario 3. The synthetic rootMOOP is injected for ForeAftDamping/FloatingFeedback — FlapControl in Mode 1 never reads it. |
| 4 | 2 (PIIController) | ~0 (1-DOF) | Mode 2 reads rootMOOP through SecLPFilter + HPFilter → PIIController, but the 1-DOF sim produces zero blade root moments. `PIIController(0) = 0`. |
| 16 | 3 (Coleman cyclic) | ~0 (1-DOF) | Mode 3 reads rootMOOP through NotchFilterSlopes → ColemanTransform → PIController, but the 1-DOF sim produces zero moments. `ColemanTransform(0,0,0) = (0,0)`. |

The critical insight: **Flp_Mode=1 is not a controller — it's a fixed setpoint.** The Fortran code for Mode 1 is literally:

```fortran
LocalVar%Flp_Angle(1) = LocalVar%Flp_Angle(1)
LocalVar%Flp_Angle(2) = LocalVar%Flp_Angle(2)
LocalVar%Flp_Angle(3) = LocalVar%Flp_Angle(3)
```

Three self-assignments. The value is set once at initialization to `CntrPar%Flp_Angle` (0.0 rad) and never changes. Scenario 7's 1000 Nm synthetic rootMOOP was being injected but never consumed by the flap controller.

## The Fix: Scenario 26

A manual-loop scenario (like Scenarios 2, 7, 8) that combines:
- **Flp_Mode=3** (Coleman transform cyclic control — the most complex flap path)
- **Synthetic rootMOOP**: 1000 Nm amplitude, 1P per-blade with 120° phase offset
- **Synthetic azimuth**: Accumulated from rotor speed (needed for Coleman transform)
- **Gains**: Flp_Kp=-0.001, Flp_Ki=-0.0005
- **400s simulation**, dt=0.025s, ws=9 m/s constant

Flp_Mode=3 chosen over Mode=2 because it exercises the full path: NotchFilterSlopes → ColemanTransform → dual-axis PIController → ColemanTransformInverse → per-blade output. Mode=2 (PIIController per blade) is simpler and already KGen-verified at the function level.

### Data flow exercised

```
rootMOOP (1000 Nm, 1P) → NotchFilterSlopes (isolate 1P) → ColemanTransform (3-blade → tilt/yaw)
  → PIController(tilt_axis) → Flp_axisTilt
  → PIController(yaw_axis)  → Flp_axisYaw
    → ColemanTransformInverse (tilt/yaw → 3-blade) → Flp_Angle(1:3)
      → avrSWAP(120:122) (non-zero, per-blade cyclic commands)
```

### Why 1000 Nm is sufficient

For Flp_Mode=3 with Kp=-0.001:
- Coleman transform of 1000 Nm 1P sinusoid → non-zero tilt/yaw moments (~667 Nm peak)
- PTerm = -0.001 × 667 = -0.667 rad
- Saturated by Flp_MaxPit = 0.1745 rad (±10 deg)
- Output: ~±0.247 rad peak (saturated, with integral accumulation)

## Verification Results

### Scenario 26 flap values

| Array | Nonzero count | Max |Flp_Angle|| Blade differentiation |
|-------|--------------|---------------------|----------------------|
| flp_angle_1 | 15,999 of 16,000 | 0.2468 rad | Different from blades 2/3 |
| flp_angle_2 | 15,999 of 16,000 | 0.2468 rad | Different from blades 1/3 |
| flp_angle_3 | 15,999 of 16,000 | 0.2468 rad | Different from blades 1/2 |

Sample values at index 4000: blade 1 = -0.2072, blade 2 = 0.0108, blade 3 = 0.1963. Three genuinely different values — the Coleman transform produces phase-shifted cyclic commands per blade.

All three builds (upstream, modified, C++) produce **bit-identical** flap output.

### Full verification numbers

| Comparison | Values | Scenarios | Result |
|-----------|--------|-----------|--------|
| Upstream == Modified | 4,888,000 | 25 (excl. 4) | ALL IDENTICAL |
| Modified == C++ | 4,940,000 | 26 | 3 known Scenario 16 artifacts |

Up from 4,732,000 in the previous commit.

## Complete Output Coverage Table

Every controller output path now has at least one scenario with non-trivial verified values:

| Output | Non-zero scenarios | Status |
|--------|-------------------|--------|
| bld_pitch (blade 1) | 1-9, 11-22, 24-26 | Verified |
| bld_pitch_2/3 (per-blade) | 5, 8, 11, 15, 19, 21, 22 | Verified |
| gen_torque | 1-26 (excl. 13 trivial) | Verified |
| gen_power | 1-12, 14-26 | Verified |
| nac_yaw | 7 | Verified |
| flp_angle_1/2/3 | **26** | **Verified (was zero in all prev. scenarios)** |
| cc_actuated_l/dl | 7, 24 | Verified |
| stc_input | 7, 24 | Verified |

There are no remaining unverified output paths. Every avrSWAP index written by the controller has at least one scenario where it produces a non-zero value that is compared bit-identically across all three builds.

## Commits

- ROSCO `d84782e`: Add Scenario 26 (Flp_Mode=3 with synthetic rootMOOP) and updated arrays
