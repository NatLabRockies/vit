# Scenario 8 Fix: NotchFilterSlopes Expression Grouping
**Date:** 2026-03-26
**Status:** completed
**Scope:** Scenario 8 integration test fix, NotchFilterSlopes coefficient computation

## Problem

Scenario 8 (IPC_ControlMode=1 + AWC_Mode=4 with synthetic rootMOOP) showed a mismatch starting at index 14791 (t=369.8s) with gen_torque max_diff=1.69e+04.

## Root Cause

The NotchFilterSlopes C++ translation had incorrect expression grouping in the coefficient computation. Fortran's `**` operator has higher precedence than `*`, creating grouped subexpressions:

```fortran
! Fortran: ** groups first
Damp*DT**2.0*CornerFreq_**2.0
! Evaluates as: (Damp * (DT²)) * (CF²)
```

```cpp
// C++ (bug): all * have equal precedence, left-to-right
Damp * DT * DT * CornerFreq_ * CornerFreq_
// Evaluates as: (((Damp * DT) * DT) * CF) * CF
```

Different intermediate products produce different IEEE 754 rounding. With `Moving=.TRUE.` (the NotchFilterSlopes coefficients are recomputed every timestep because the filter tracks the rotor speed), this 1-ULP coefficient difference accumulated over 14,791 timesteps until the filter output diverged enough to push gen_torque across a quantization boundary.

## How We Found It

1. **Binary search**: Integrated 24 "safe" functions (excluding IPC, AWC, ResController) — mismatch still present. So the bug was in the 24 safe functions, not in the 3 suspects.

2. **Single function test**: Integrated only NotchFilterSlopes — mismatch reproduced with identical max_diff and indices.

3. **Code comparison**: Identified the `**` precedence difference in the coefficient computation.

## Why KGen Verification Didn't Catch It

NotchFilterSlopes was extracted with `--scenario 6` (IPC_ControlMode=1) but the rootMOOP inputs were zero (from the 1-DOF sim). With zero inputs, the filter output is zero regardless of coefficient precision. The 40/40 KGen verification passed because `0 * wrong_coefficient = 0 * correct_coefficient = 0`.

Scenario 8 is the first test with non-zero rootMOOP flowing through the NotchFilterSlopes filter (via the `IPC_ControlMode > 0` path in PreFilterMeasuredSignals). The non-zero inputs expose the coefficient error.

## Fix

Pre-compute `DT² = DT*DT` and `CF² = CornerFreq_*CornerFreq_` as intermediate variables, then use them in the coefficient expressions:

```cpp
double DT2 = DT * DT;
double CF2 = CornerFreq_ * CornerFreq_;
FP->nfs_a2[idx] = Damp * DT2 * CF2 + 2.0 * DT * CornerFreq_ + 4.0 * Damp;
FP->nfs_a1[idx] = 2.0 * Damp * DT2 * CF2 - 8.0 * Damp;
FP->nfs_a0[idx] = Damp * DT2 * CF2 - 2.0 * DT * CornerFreq_ + 4.0 * Damp;
```

This matches the Fortran grouping: `Damp * (DT**2) * (CF**2)` = `(Damp * DT²) * CF²`.

## Verification

Full 8-scenario integration test: **ALL IDENTICAL — 612,000 float64 values**.

## Lesson

This is the same `**` precedence lesson documented in `TRANSLATION_TIPS.md`. It was already known from an earlier translation failure but wasn't applied when NotchFilterSlopes was translated. The synthetic input scenarios (7/8) are essential for catching translation bugs that hide behind zero inputs.

## Commits

- ROSCO `cf004df`: Fix NotchFilterSlopes coefficient grouping
