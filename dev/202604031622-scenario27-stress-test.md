# Scenario 27: Maximum-Coverage Stress Test
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification coverage — 11-mode simultaneous stress test

## Summary

Created Scenario 27 to stress-test the C++ translation by running 11 controller modes simultaneously with all synthetic inputs active. The pitch command is composed from 6 concurrent sources — no previous scenario had more than 3. All three builds (upstream Fortran, modified Fortran, C++) produce bit-identical output across 24,000 timesteps.

## Motivation

Previous scenarios exercised modes in relative isolation. Scenario 7 came closest with 6 modes, but its synthetic inputs were gentle sinusoids and several modes produced near-zero contributions. No scenario tested the full interaction of IPC per-blade corrections, AWC resonant modulation, tower damping, floating feedback, yaw control, and pitch fault offsets all modifying the same pitch command simultaneously and feeding back through the closed-loop rotor speed model.

If the C++ translation had a subtle interaction bug — e.g., a wrong accumulation order, a missing term in the pitch sum, or a state variable shared between two modes that was updated in the wrong sequence — it would most likely show under maximum simultaneous load.

## Mode Compatibility Analysis

The controller has only a few hard mutual exclusions:

1. **IPC_ControlMode > 0 vs Flp_Mode > 0** — both use per-blade pitch actuators
2. **IPC_ControlMode > 0 vs Y_ControlMode = 2** — yaw-by-IPC conflicts with load-reduction IPC
3. **VS_FBP > 0** blocks PC_ControlMode, PRC_Mode, and VS_ConstPower
4. **CC_Mode=2 / StC_Mode=2** require OL_Mode=1

Everything else is compatible. AWC + IPC produces a warning but is allowed (Scenario 8 uses both).

We chose IPC over Flp because IPC has richer interactions with the pitch chain — per-blade corrections from Coleman-transformed rootMOOP that interact with AWC, tower damping, and floating feedback all modifying pitch simultaneously.

## Scenario 27 Configuration

| Mode | Value | What it adds to the controller | Synthetic input |
|------|-------|-------------------------------|----------------|
| IPC_ControlMode | 1 | Per-blade pitch from Coleman-transformed rootMOOP | rootMOOP (1000 Nm 1P), azimuth |
| AWC_Mode | 4 | Closed-loop resonant per-blade pitch modulation | rootMOOP, azimuth |
| Y_ControlMode | 1 | Yaw rate commands from wind direction error | NacVane (±20 deg), NacHeading |
| TD_Mode | 1 | Tower damping pitch correction | FA_Acc_TT (0.5 m/s² at 0.3 Hz) |
| Fl_Mode | 2 | Floating feedback pitch from rotational IMU | NacIMU_FA_RAcc (0.3 rad/s² at 0.3 Hz) |
| CC_Mode | 1 | Cable control (time-based step at t>500s) | None (time-driven) |
| StC_Mode | 1 | Structural control (time-based step at t>500s) | None (time-driven) |
| PA_Mode | 2 | 2nd-order pitch actuator dynamics filter | None (filters pitch output) |
| PF_Mode | 1 | Per-blade pitch offset fault (+0.01, -0.01, +0.005 rad) | None (constant offsets) |
| VS_ConstPower | 1 | Constant power above rated (not constant torque) | None (torque control mode) |
| PRC_Mode | 1 | Power reference speed from lookup table | None (modifies setpoints) |

**11 active modes.** This is the maximum compatible set.

## Pitch Command Composition

The unique value of Scenario 27 is that the pitch command for each blade is the sum of **6 simultaneous contributions**:

```
PitComAct(K) = PC_PitComT              (collective PI pitch control)
             + IPC_PitComF(K)          (per-blade IPC from rootMOOP)
             + FA_PitCom               (tower damping from FA_Acc_TT)
             + Fl_PitCom               (floating feedback from NacIMU)
             + AWC_complexangle_re(K)  (per-blade AWC resonant modulation)
             + PF_Offsets(K)           (per-blade fault offsets: +0.01, -0.01, +0.005)
             → PA_Mode=2 filter        (2nd-order actuator dynamics)
             → saturation + rate limit
```

Previous maximum was 3 contributions (Scenario 8: collective PI + IPC + AWC).

## Synthetic Input Summary

All injected every timestep via manual avrSWAP writes:

| avrSWAP Index | Signal | Value |
|--------------|--------|-------|
| [23] | NacVane | ±20° sinusoid (period 50s) |
| [36] | NacHeading | 350° constant |
| [52] | FA_Acc_TT | 0.5 m/s² sinusoid (period 3s, ~0.3 Hz tower mode) |
| [82] | NacIMU_FA_RAcc | 0.3 rad/s² sinusoid (period 3s) |
| [29-31] | rootMOOP (3 blades) | 1000 Nm 1P sinusoids, 120° phase offset |
| [59] | Azimuth | Accumulated from rotor speed |

Simulation: 600s, dt=0.025s, ws=9+floor(t/100) m/s. Manual loop with 1-DOF rotor model.

## Results

### Scenario 27 output characteristics

| Output | Nonzero | Max |value| | Notes |
|--------|---------|------------|-------|
| bld_pitch | 23,999 | 1.580 rad | Collective + IPC + TD + Fl + AWC + PF |
| bld_pitch_2 | 23,999 | 1.560 rad | Different from blade 1 (IPC + AWC + PF) |
| bld_pitch_3 | 23,999 | 1.575 rad | Different from blades 1&2 |
| gen_torque | 23,999 | (varies) | VS_ConstPower + PRC_Mode active |
| nac_yaw | 23,812 | 0.186 rad | Y_ControlMode=1 responding to NacVane |
| cc_actuated_l | 3,999 | 3.628 | Step at t>500s |
| stc_input | 3,999 | 1.234e6 | Step at t>500s |
| flp_angle_1/2/3 | 0 | 0 | Expected (Flp_Mode=0) |

All three blades have different pitch values (from IPC per-blade corrections + AWC per-blade modulation + PF per-blade offsets). The max pitch amplitude (~1.58 rad ≈ 90°) reflects the saturation limits being approached under the combined load of 6 pitch contributions — the controller is working hard.

### Full verification

| Comparison | Values | Scenarios | Result |
|-----------|--------|-----------|--------|
| Upstream == Modified | 5,200,000 | 26 (excl. 4) | ALL IDENTICAL |
| Modified == C++ | 5,252,000 | 27 | 3 known Scenario 16 artifacts |

No new mismatches from Scenario 27. The 11-mode stress test produces bit-identical output between Fortran and C++.

## What This Proves

The C++ translation handles simultaneous mode interactions correctly. When 6 different pitch contributions are summed per blade, filtered through a 2nd-order actuator model, saturated, rate-limited, and fed back through the closed-loop rotor speed model for 24,000 timesteps — the output matches Fortran to the last bit.

This is the hardest test we can construct within the constraints of the 1-DOF simulator and ROSCO's mode compatibility rules.

## Commits

- ROSCO `fab67e6`: Add Scenario 27 (11-mode stress test) and updated arrays
