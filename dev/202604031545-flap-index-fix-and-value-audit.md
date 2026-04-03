# Flap Index Off-by-One Fix and Output Value Audit
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification correctness — flap angle avrSWAP index mapping, visual value audit

## Summary

A post-commit visual audit of sampled output values across all three builds (upstream, modified Fortran, C++) caught an off-by-one error in the flap angle avrSWAP indices. The fix was applied and all arrays regenerated. The audit also confirmed that all other new arrays contain real, non-trivial values where expected, and that flap angles are genuinely zero in all scenarios — a real coverage limitation of the 1-DOF simulator.

## The Bug

The `EXTRA_AVRSWAP` constant in `vit_sim.py` defined flap angle indices as:

```python
'flp_angle_1': 120,    # WRONG — reading one past the actual write
'flp_angle_2': 121,
'flp_angle_3': 122,
```

The Fortran source (`Controllers.f90:704-706`) writes:

```fortran
avrSWAP(120) = LocalVar%Flp_Angle(1)
avrSWAP(121) = LocalVar%Flp_Angle(2)
avrSWAP(122) = LocalVar%Flp_Angle(3)
```

Fortran uses 1-based indexing. Python uses 0-based. So `avrSWAP(120)` in Fortran is `avrSWAP[119]` in Python. The C++ translation confirms: `avrSWAP[119] = LocalVar->Flp_Angle[0]` (comment: "Fortran indices 120,121,122 → C 119,120,121").

The dev note `202604031449` that originally identified these indices wrote `avrSWAP[120-122]` without clarifying which indexing convention — this ambiguity propagated into the implementation.

### Corrected indices

```python
'flp_angle_1': 119,    # Fortran avrSWAP(120) → Python [119]
'flp_angle_2': 120,    # Fortran avrSWAP(121) → Python [120]
'flp_angle_3': 121,    # Fortran avrSWAP(122) → Python [121]
```

## How It Was Found

After the initial commit (`e491c93`), the user asked to spot-check sampled values across all three builds. The check revealed that all flap angle arrays had `nonzero=0` across every scenario that exercises flap control (3, 4, 7, 16). This was suspicious because:

1. FlapControl IS called in those scenarios (confirmed by KGen extraction and mode flags)
2. The MD5 checksums of the flap arrays in the first run differed from the all-zero MD5 for other zero arrays (due to `-0.0` negative zero values from the wrong-index reads)

Grepping for `avrSWAP.*Flp_Angle` in the source confirmed the Fortran writes to indices 120-122 (1-based), while the Python code read from 120-122 (0-based) — off by one.

## Flap Angles Are Genuinely Zero

After fixing the indices and regenerating all arrays, flap angles remain all-zero in every scenario:

| Scenario | Flp_Mode | flp_angle nonzero count | Why zero |
|----------|----------|------------------------|----------|
| 3 | 1 (steady-state lookup) | 0 | 1-DOF sim produces near-zero blade root moments |
| 4 | 2 (PI control) | 0 | Same — tiny moments × small gains = zero after float32 |
| 7 | 1 (with synthetic rootMOOP) | 0 | Synthetic rootMOOP injected but Flp_Mode=1 lookup may not respond |
| 16 | 3 (Coleman transform) | 0 | Coleman decomposition of near-zero moments = zero |

This is NOT a capture bug. It's a genuine limitation of the 1-DOF simulator:

- The 1-DOF sim models only rotor speed. It doesn't model blade dynamics that produce root bending moments.
- Scenario 7 injects synthetic rootMOOP (1000 N·m, 1P per-blade), but the flap controller's gains, filters, and lookup tables may produce output too small to survive the float32 conversion in avrSWAP.
- FlapControl IS exercised (the code runs, filters are called, state is updated), but the final `Flp_Angle` written to avrSWAP is zero.

All three builds (upstream Fortran, modified Fortran, C++) produce identical all-zero flap output, so the comparison is valid — it proves the C++ translation preserves the zero-output behavior. But a translation bug that produced a *wrong non-zero* flap value would not be caught by a zero-vs-zero comparison.

## Visual Audit Results

The full audit confirmed real, differentiated values in all other new arrays:

**Per-blade pitch (Scenario 8, IPC + AWC):**
- Blade 1: `0.0112` rad at t=25s
- Blade 2: `0.3050` rad at t=25s
- Blade 3: `0.0000` rad at t=25s
- All 3 builds identical. IPC producing genuinely different per-blade commands.

**Per-blade pitch (Scenario 11, AWC_Mode=1):**
- All 3 blades different (complex number phase-locked modulation)
- Blade 1 has 12,017 nonzero values, Blade 3 has 11,303 — different zero/nonzero patterns confirm per-blade behavior
- All 3 builds identical.

**Cable control (Scenario 7):**
- `cc_actuated_l`: Zero before t=500s, `-3.6275` after (step input)
- `cc_actuated_dl`: Transient spikes at step then settles (3,507 nonzero values)
- All 3 builds identical.

**Cable + structural control (Scenario 24, open-loop):**
- `cc_actuated_l`: Oscillating 1.19-1.29 (from OL input file)
- `cc_actuated_dl`: Oscillating ±0.007 (rate of change)
- `stc_input`: 900K-1.1M forces (from OL input file)
- All 3 builds identical.

**Trivial-by-design (Scenario 1, no IPC/flap/CC/StC):**
- `bld_pitch == bld_pitch_2 == bld_pitch_3` (all blades identical without IPC)
- `flp_angle`, `cc_actuated_l`, `stc_input` all zero (modes disabled)
- Correct behavior confirmed.

## Remaining Coverage Gap

Flap control output is the one remaining unverified output path at the simulation level. The flap control *code* is verified:
- KGen kernel verification proves the FlapControl function produces identical output for captured inputs (Scenarios 3, 7 in KGen)
- The avrSWAP write of `Flp_Angle` is identical between builds (zero == zero)

But a bug that only manifests with realistic blade moments (e.g., wrong scaling, wrong blade index in the Coleman transform) would be invisible.

**Future fix:** Create a scenario with Flp_Mode=1 or 3 and inject rootMOOP values large enough (e.g., 100,000 N·m) to drive non-zero flap output through the controller's filters and gains.

## Performance Observation

The user noted that simulations ran significantly faster after the `avr_size` increase from 500 to 3000. With avr_size=500, every scenario with CC_Mode>0 or StC_Mode>0 (Scenarios 3, 7, 24) wrote to avrSWAP indices 2600 and 2800 — 2100 elements past the end of the 500-element ctypes buffer. This silent heap corruption on every timestep degraded Python's memory allocator performance for all subsequent scenarios in the same process.

## Commits

- ROSCO `e491c93`: Initial output array expansion (had flap index bug)
- ROSCO `96131d4`: Fix flap angle indices (119-121, not 120-122) and regenerate all arrays
