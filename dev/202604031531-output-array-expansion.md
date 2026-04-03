# Output Array Expansion: Full Controller Output Coverage
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification coverage — expand from 5 to 13 output arrays per scenario

## Summary

Expanded the simulation verification from 5 output arrays to 13 per scenario, capturing every controller output written to avrSWAP. Previously, only blade 1 pitch, generator torque, generator speed, generator power, and nacelle yaw were compared. Now we also capture blades 2 and 3 pitch, all 3 flap angle commands, cable control actuated length and delta-length, and structural control input.

This closes the output coverage gaps identified in dev note `202604031449`. The full verification chain now covers **4.7 million values** across 25 scenarios, up from 1.8 million.

## What Was Missing

The ROSCO controller writes far more to avrSWAP than we were capturing. The 5 original arrays came from `call_controller`'s 3 return values (torque, pitch, yaw rate) plus derived gen_power and gen_speed. But the controller also writes:

| Output | avrSWAP Index (Python) | Written By | Previously Captured |
|--------|----------------------|------------|-------------------|
| Blade 1 pitch | 41 | PitchControl | Yes (as `bld_pitch`) |
| Blade 2 pitch | 42 | PitchControl | **No** |
| Blade 3 pitch | 43 | PitchControl | **No** |
| Generator torque | 46 | VariableSpeedControl | Yes |
| Nacelle yaw rate | 47 | YawRateControl | Yes (integrated to `nac_yaw`) |
| Blade 1 flap angle | 120 | FlapControl | **No** |
| Blade 2 flap angle | 121 | FlapControl | **No** |
| Blade 3 flap angle | 122 | FlapControl | **No** |
| Cable actuated length | 2600 | CableControl | **No** |
| Cable actuated delta-length | 2601 | CableControl | **No** |
| Structural control input | 2800 | StructuralControl | **No** |

IPC produces different pitch commands per blade — a blade-indexing bug in the IPC translation would have been invisible with only blade 1 captured. Flap control outputs were completely unverified across 4 scenarios. Cable and structural control outputs were completely unverified across 3 scenarios.

## Implementation

### avr_size increase: 500 to 3000

`control_interface.py` allocated avrSWAP with only 500 elements. CC_GroupIndex=2601 and StC_GroupIndex=2801 write to indices 2600 and 2800 respectively. With avr_size=500, these were **silent buffer overflows** — the controller wrote past the end of the numpy array via ctypes. This happened in every run of Scenarios 3, 7, and 24 without crashing (the heap happened to have writable memory there).

Increased to 3000 to match `vit.yaml`'s `kgen.assumed_size_arrays.avrSWAP: 3000`. This is both a safety fix and a prerequisite for reading CC/StC outputs.

### Sim class extension

Added `extra_avrswap` parameter to `sim_ws_series()` in `sim.py`. It accepts a dict mapping array names to avrSWAP indices. The Sim class pre-allocates arrays, reads the specified indices after each `call_controller` call, and stores them as instance attributes alongside the existing `gen_torque`, `bld_pitch`, etc.

`sim_ws_wd_series` was not modified — no scenarios use it.

### vit_sim.py changes

Defined `EXTRA_AVRSWAP` constant at module level with the 8 new indices. Added `build_save_dict()` helper that constructs the full 13-array dict from a Sim object.

For Sim-based scenarios (1, 3-6, 9-25): pass `extra_avrswap=EXTRA_AVRSWAP` to `sim_ws_series`, use `build_save_dict()` for the save call.

For manual-loop scenarios (2, 7, 8): pre-allocate extra arrays, read avrSWAP indices after each `call_controller` call, merge into the save dict.

All 25 scenarios now save 13 arrays per .npz file.

## Verification Results

### Upstream vs Modified Fortran

Built upstream ROSCO (`e8010f0`, unmodified) and compared against modified Fortran fork across 24 scenarios (Scenario 4 skipped — upstream Flp_Mode=2 bug).

**4,680,000 values IDENTICAL** across 24 scenarios x 13 arrays.

All 8 new arrays match exactly between upstream and modified, confirming the type modifications (BIND(C), LOGICAL to INTEGER, COMPLEX to REAL pair, CHARACTER ALLOCATABLE to fixed) have zero impact on any controller output.

### Modified Fortran vs C++

Compared modified Fortran baseline against C++ (pure C++ build via Phase 11) across all 25 scenarios.

**4,732,000 values total**, 3 mismatches:

| Scenario | Array | Index | Difference | Root Cause |
|----------|-------|-------|------------|------------|
| 16 | bld_pitch | 4444 | 8.47e-22 | gfortran code gen artifact |
| 16 | bld_pitch_2 | 4444 | 8.47e-22 | Same — all blades identical without IPC |
| 16 | bld_pitch_3 | 4444 | 8.47e-22 | Same — all blades identical without IPC |

This is the known `2^(-70)` difference in the rate limiter, previously tracked in dev note `202604031325`. It now shows 3 times instead of 1 because Scenario 16 (Flp_Mode=3, no IPC) produces identical pitch for all 3 blades, so the same artifact appears in `bld_pitch`, `bld_pitch_2`, and `bld_pitch_3`. This is not a new issue — it's the same single-value compiler artifact observed through 3 lenses.

### Non-trivial value verification

Spot-checked that new arrays have meaningful (non-zero) values where expected:

| Array | Non-trivial in Scenarios | Confirmed |
|-------|--------------------------|-----------|
| `bld_pitch_2` differs from `bld_pitch` | 5, 8, 11, 15, 19, 21, 22 (AWC/IPC per-blade) | Yes |
| `bld_pitch_3` differs from `bld_pitch` | 5, 8, 11, 15, 19, 21, 22 | Yes |
| `flp_angle_1/2/3` non-zero | 7, 16 (Flp_Mode > 0 with non-zero inputs) | Yes |
| `cc_actuated_l` non-zero | 7, 24 (CC_Mode > 0) | Yes |
| `cc_actuated_dl` non-zero | 7, 24 | Yes |
| `stc_input` non-zero | 7, 24 (StC_Mode > 0) | Yes |

### Intentionally excluded outputs

| Signal | Why |
|--------|-----|
| avrSWAP[44] (collective pitch) | Duplicate of blade 1 pitch |
| avrSWAP[34] (generator contactor) | Set to 1.0 on init, never changes |
| Init-only indices (35-36, 40, 45, 54-56, 64, 71, 78-80) | Written once at startup, constant |
| avrSWAP[48-50] (ExtController metadata) | String buffer lengths, not control outputs |

## Updated Verification Numbers

| Comparison | Values | Scenarios | Arrays/Scenario | Result |
|-----------|--------|-----------|-----------------|--------|
| Upstream vs Modified | 4,680,000 | 24 (excl. 4) | 13 | ALL IDENTICAL |
| Modified vs C++ | 4,732,000 | 25 | 13 | 3 known artifacts |
| **Total verified** | **4,732,000** | **25** | **13** | **1 root cause** |

Previously: 1,812,000 values, 5 arrays/scenario, 1 known artifact.
Now: 4,732,000 values, 13 arrays/scenario, same 1 known artifact (visible in 3 blade pitch arrays).

## Files Changed

| File | Change |
|------|--------|
| `rosco/toolbox/control_interface.py` | `avr_size = 500` to `3000` (fixes silent buffer overflow) |
| `rosco/toolbox/sim.py` | Added `extra_avrswap` parameter to `sim_ws_series` |
| `Examples/vit_sim.py` | `EXTRA_AVRSWAP` dict, `build_save_dict()`, all 25 scenarios updated |
| `upstream_arrays/` (new) | Golden upstream output arrays for 24 scenarios |
| `baseline_arrays/` | Regenerated with 13 arrays, now includes scenarios 1-25 |

## What This Proves

Every controller output written to avrSWAP is now verified across the full verification chain:

```
Upstream Fortran = Modified Fortran = C++
(4.68M values)    (4.73M values, 1 compiler artifact)
```

There is no remaining unverified output path. A translation bug in any controller function — pitch, torque, yaw, flap, cable, or structural control — would be detected by the comparison.
