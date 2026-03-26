# 8-Scenario Integration Test: ALL IDENTICAL (612,000 values)
**Date:** 2026-03-26
**Status:** completed
**Scope:** Definitive integration test, 27 functions, 8 scenarios, all fixes applied

## Result

```
RESULT: ALL IDENTICAL — 612000 total float64 values compared
```

Every array, every scenario, every timestep: byte-identical between pure Fortran baseline and 27-function integrated C++.

## Test Matrix

| Scenario | Duration | Values | Functions Exercised | Result |
|----------|----------|--------|-------------------|--------|
| 1 (standard) | 1000s | 200,000 | saturate, wrap_180, interp1d, LPFilter, HPFilter, SecLPFilter, PIController, VariableSpeedControl | IDENTICAL |
| 2 (wrap_360) | 100s | 12,000 | wrap_360 | IDENTICAL |
| 3 (mode coverage) | 400s | 80,000 | NotchFilter, SecLPFilter_Vel, ForeAftDamping, FloatingFeedback, FlapControl, YawRateControl, StructuralControl, CableControl | IDENTICAL |
| 4 (PIIController) | 100s | 20,000 | PIIController | IDENTICAL |
| 5 (ResController) | 400s | 80,000 | ResController, ActiveWakeControl | IDENTICAL |
| 6 (NotchFilterSlopes) | 100s | 20,000 | IPC, NotchFilterSlopes | IDENTICAL |
| 7 (synthetic inputs) | 600s | 120,000 | YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl (all with non-zero inputs) | IDENTICAL |
| 8 (IPC + AWC) | 400s | 80,000 | IPC (real gains), ActiveWakeControl (rootMOOP feedback), NotchFilterSlopes (non-zero rootMOOP) | IDENTICAL |

## What This Proves

- 27 C++ translations are **bit-exact** replacements for the original Fortran across all tested code paths
- The translations are correct not just for the default 1-DOF simulation (zero inputs to many functions) but also for:
  - **Non-zero yaw, tower, floating, structural, cable, and flap inputs** (Scenario 7)
  - **Non-zero IPC gains with real blade root moment feedback** (Scenario 8)
  - **Moving notch filter tracking rotor speed** with non-zero signal (Scenario 8)

## Fixes Applied This Session

Two issues were resolved to reach this result:

### 1. Scenario 3 non-determinism (FITPACK stack contamination)
- **Root cause**: scipy's FITPACK `bispev` reads an uninitialized stack variable; residuals from ROSCO's CableControl Fortran call vary with ASLR
- **Fix**: Monkey-patch in vit_sim.py — cache `RectBivariateSpline` + scrub 64KB of stack before each evaluation
- **ROSCO commit**: `034c656`

### 2. Scenario 8 NotchFilterSlopes expression grouping
- **Root cause**: Fortran `DT**2.0*CornerFreq_**2.0` groups as `(DT²)*(CF²)` due to `**` precedence; C++ `DT*DT*CF*CF` evaluates left-to-right with different intermediate rounding
- **Fix**: Pre-compute `DT2=DT*DT`, `CF2=CF*CF`, use `Damp*DT2*CF2`
- **ROSCO commit**: `cf004df`

## Reproducibility

Test was run 3 times consecutively (this session) with identical results each time, confirming both the Scenario 3 determinism fix and the Scenario 8 translation fix are stable.

## What Remains

- **PitchControl** (28th function): excluded because it depends on PitchSaturation (ControllerBlocks.f90, Phase 8), which is not yet translated
- **INTEGRATION_TEST.md**: should be updated to reflect 8-scenario protocol

## Related Dev Notes

- `202603261747-scenario8-notchfilterslopes-fix.md` — Scenario 8 fix
- `202603261539-scenario3-nondeterminism-fix.md` — Scenario 3 fix
- `202603261512-scipy-fitpack-bispev-uninitialized-variable.md` — scipy bug writeup
- `202603261506-scenario3-nondeterminism-definitive-root-cause.md` — root cause proof
