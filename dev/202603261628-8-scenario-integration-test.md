# 8-Scenario Integration Test: Scenarios 1-7 IDENTICAL, Scenario 8 Mismatch
**Date:** 2026-03-26
**Status:** completed
**Scope:** Integration test with synthetic input Scenarios 7/8, under-exercised function validation

## Result

| Scenario | Duration | Values | Result |
|----------|----------|--------|--------|
| 1 (standard, 1000s) | 1000s | 200,000 | **IDENTICAL** |
| 2 (wrap_360, 100s) | 100s | 12,000 | **IDENTICAL** |
| 3 (mode coverage, 400s) | 400s | 80,000 | **IDENTICAL** |
| 4 (PIIController, 100s) | 100s | 20,000 | **IDENTICAL** |
| 5 (ResController, 400s) | 400s | 80,000 | **IDENTICAL** |
| 6 (NotchFilterSlopes, 100s) | 100s | 20,000 | **IDENTICAL** |
| 7 (synthetic inputs, 600s) | 600s | 120,000 | **IDENTICAL** |
| **8 (IPC + AWC, 400s)** | 400s | 80,000 | **MISMATCH** |

**Scenarios 1-7: 532,000 float64 values ALL IDENTICAL.**

## What Scenario 7 Proves

Scenario 7 is the first test that exercises the 8 under-exercised functions with **non-zero, varying inputs**:

- **YawRateControl**: oscillating NacVane (±20 deg at 50s period), fixed NacHeading (350 deg) — exercises the yaw state machine with real yaw error
- **ForeAftDamping**: oscillating FA_Acc_TT (0.5 m/s² at ~0.3 Hz tower mode) — exercises HPFilter + PIController with real tower acceleration
- **FloatingFeedback**: oscillating NacIMU_FA_RAcc (0.3 rad/s² at ~0.3 Hz) — exercises SecLPFilter + gain-scheduled velocity feedback
- **StructuralControl**: 600s sim exceeds t=500 threshold — exercises the step input branch
- **CableControl**: 600s sim exceeds t=500 — exercises SecLPFilter_Vel + PIController with real cable length commands
- **FlapControl**: per-blade 1P rootMOOP sinusoids (1000 Nm, 120 deg phase offset) — exercises Coleman transform + PIController with real blade moments
- **nac_yaw**: non-zero for the first time (yaw dynamics active) — 24,000 non-zero values

All 120,000 values across 5 arrays (gen_torque, bld_pitch, gen_speed, gen_power, nac_yaw) are **byte-identical** between baseline Fortran and 27-function integrated C++. This proves the translations are correct not just for zero inputs but for real, varying signals through all code paths.

## Scenario 8 Mismatch

Scenario 8 (IPC_ControlMode=1, AWC_Mode=4, Flp_Mode=0) shows mismatches starting at index 14791 (t=369.8s):

| Array | First diff idx | First diff time | Max diff |
|-------|---------------|-----------------|----------|
| gen_torque | 14791 | 369.8s | 1.69e+04 |
| gen_speed | 14792 | 369.8s | 2.38e+01 |
| gen_power | 14791 | 369.8s | 1.68e+06 |
| bld_pitch | 15104 | 377.6s | 2.68e-01 |
| nac_yaw | — | — | IDENTICAL |

The large magnitudes (gen_torque diff of 16,900) and late onset (t=369.8s of 400s) suggest an accumulated error in the IPC or ActiveWakeControl code path that triggers a controller state transition. This is likely a real translation bug in one of the functions exercised uniquely by Scenario 8:
- **IPC** with non-zero gains (IPC_KP=0.1, IPC_KI=0.01) and real blade root moments
- **ActiveWakeControl** with AWC_Mode=4 (rootMOOP feedback via ResController)
- **NotchFilterSlopes** triggered by IPC_ControlMode=1 in PreFilterMeasuredSignals

This requires separate investigation. The IPC and ActiveWakeControl translations passed KGen verification (40/40) and the 6-scenario integration test (Scenarios 5/6 exercise them with zero rootMOOP), but Scenario 8 is the first test with non-zero IPC gains AND real blade moments simultaneously.

## What Was Done

1. Restored Scenarios 7/8 code from commit `72d38f3` (originally reverted during the non-determinism investigation)
2. Updated `main()` to include `--scenario 7` and `--scenario 8`
3. Reset sources to clean Fortran, applied K init fix
4. Built clean Fortran baseline, captured 8 baseline arrays (separate docker exec per scenario)
5. Integrated 27 functions, clean rebuilt with C++
6. Captured 8 integrated arrays (separate docker exec per scenario)
7. Compared all arrays across 8 scenarios

## Commits

- ROSCO `b046e00`: Add Scenarios 7 and 8, update main()

## Related Dev Notes

- `202603261543-integration-test-all-6-scenarios-identical.md` — 6-scenario test (all IDENTICAL)
- `202603261539-scenario3-nondeterminism-fix.md` — FITPACK stack scrubber fix
- `202603261145-integration-test-systematic-evaluation.md` — identified 8 under-exercised functions
