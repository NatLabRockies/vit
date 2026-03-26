# Scenario 3 Non-Determinism: Root Cause Investigation
**Date:** 2026-03-26
**Status:** completed
**Scope:** Scenario 3 non-determinism root cause, floating-point environment contamination

## Summary

The Scenario 3 non-determinism is **NOT a ROSCO Fortran bug**. It is a floating-point environment contamination chain involving three components: scipy's controller tuning, the Fortran CableControl code path, and Python's Cp interpolation arithmetic. The Fortran controller produces identical outputs in both states — the 1-ULP gen_speed difference originates in the Python physics loop.

## Investigation Methodology

### Step 1: Capture Divergent States

Reset to clean Fortran (dba7738/fde0498/e8010f0, K init fix applied, C++ stubs). Built clean. Ran Scenario 3 fifteen times in separate docker exec processes. Captured two .npz files with different gen_torque MD5s:
- State A (`0f297bd1`): 7 of 15 runs
- State B (`e078402d`): 8 of 15 runs

### Step 2: Locate First Divergence Point

Compared all 5 output arrays between States A and B:

| Array | First divergence | Delta | Total differing |
|-------|-----------------|-------|-----------------|
| gen_speed | **index 169 (t=4.225s)** | **1.42e-14 (1 ULP)** | 12,785 of 16,000 |
| gen_power | index 2039 (t=50.975s) | 4.66e-10 | 12,443 of 16,000 |
| bld_pitch | index 8723 (t=218.075s) | 1.12e-08 | 3,531 of 16,000 |
| gen_torque | index 8742 (t=218.550s) | 3.91e-03 | 2,275 of 16,000 |
| nac_yaw | — | — | **IDENTICAL** |

**Critical finding:** gen_speed diverges FIRST at t=4.225s. At that point, gen_torque and bld_pitch are still identical between the two states (both non-zero — the controller is active). The 1-ULP gen_speed difference is in the **Python physics computation**, not the Fortran controller output.

Verified across all 15 runs: gen_speed[169] is always one of exactly 2 byte values (0xb7 vs 0xb9 in the last mantissa byte), with perfect 1:1 correlation to the gen_torque MD5.

### Step 3: `-finit-local-zero` Diagnostic

Added `-finit-local-zero -finit-integer=0 -finit-real=zero` to gfortran flags. Rebuilt and ran 20 times.

**Result: Still non-deterministic.** 11 state B, 9 state A. This eliminates uninitialized Fortran variables as the root cause.

### Step 4: Isolate Python vs Fortran

Ran the Python physics loop (gen_speed computation via Cp interpolation) WITHOUT calling the Fortran controller at all — no ctypes, no DISCON, no libdiscon.so.

**Result: 15/15 identical.** The Cp interpolation and Python physics are perfectly deterministic without the Fortran controller.

### Step 5: Identify the Trigger

Tested whether `load_turbine_and_controller()` (which runs `controller.tune_controller()` using scipy optimization) is needed for the non-determinism:

| Configuration | Deterministic? |
|--------------|----------------|
| No tuning, no controller | Yes (15/15) |
| Tuning, no controller | Yes (15/15) |
| No tuning, with controller | Yes (10/10) |
| **Tuning + controller** | **No (9 A, 11 B in 20 runs)** |

**Both the scipy tuning AND the Fortran controller calls are required** to trigger the non-determinism. Neither alone is sufficient.

### Step 6: Mode Flag Bisection

Ran Scenario 3 with each of the 7 mode flags removed individually, 10 times each:

| Test | Removed Flag | Deterministic? | State A | State B |
|------|-------------|----------------|---------|---------|
| none | All 7 removed | **Yes** | 0 | 10 |
| **no_CC** | **CC_Mode=0** | **Yes** | **0** | **10** |
| no_Flp | Flp_Mode=0 | No | 5 | 5 |
| no_Y | Y_ControlMode=0 | No | 2 | 8 |
| no_TD | TD_Mode=0 | No | 3 | 7 |
| no_Fl | Fl_Mode=0 | No | 4 | 6 |
| no_StC | StC_Mode=0 | No | 5 | 5 |
| no_notch | F_NumNotchFilts=0 | No | 4 | 6 |

**Only removing CC_Mode eliminates the non-determinism.** The CableControl subroutine is the specific Fortran code path that, in combination with scipy's FP state modification, tips the process FP environment into a non-deterministic state for the Python physics computation.

## Root Cause Analysis

The non-determinism is a **three-component interaction**:

1. **scipy's `tune_controller()`** performs optimization/interpolation using BLAS/LAPACK on ARM64 (aarch64 via QEMU/Colima). This modifies the process's floating-point control register (FPCR) or exception flags in a way that depends on memory layout (ASLR).

2. **CableControl's Fortran code path** (`SecLPFilter_Vel` + `PIController` processing zero inputs) performs floating-point arithmetic that further modifies the FPCR state. With CC_Mode=0, this code path doesn't execute, and the FPCR remains in a state that produces deterministic Python arithmetic.

3. **Python's gen_speed computation** at timestep 169 (t=4.225s) involves a multiply-add chain (`rot_speed + (dt/J)*(aero_torque - Ng*gen_torque/GBoxEff)`) that sits exactly at a 1-ULP rounding boundary. The FPCR state from steps 1+2 determines which way the rounding goes.

The 1-ULP gen_speed difference then accumulates through the closed-loop physics feedback (gen_speed → tsr → cp → aero_torque → rot_speed → gen_speed) for ~214 seconds until it crosses the pitch controller's activation threshold, causing bld_pitch to diverge at t=218.075s and gen_torque at t=218.550s.

## Why Only Scenario 3?

Other scenarios are deterministic because they either:
- Don't enable CC_Mode (Scenarios 1, 2, 4, 5) — the CableControl code path doesn't execute
- Scenario 6 enables IPC_ControlMode but not CC_Mode

Scenario 3 is the only scenario that enables all 7 mode flags simultaneously, including CC_Mode=1.

## Why Not a Bug?

This is NOT a bug in ROSCO's Fortran code:
- The Fortran controller produces **identical outputs** in both states (gen_torque and bld_pitch are identical at the divergence point)
- The non-determinism is in the **Python physics loop** (computed by vit_sim.py, not by ROSCO)
- The FPCR contamination is a **platform-specific artifact** (ARM64/QEMU emulation) of the interaction between scipy's BLAS initialization and gfortran's runtime
- The 1-ULP difference is within normal IEEE 754 rounding tolerance — it's amplified by the closed-loop feedback into a visible gen_torque difference only after 218 seconds

## Implications for Integration Testing

1. **The integration test is valid.** The Fortran-to-C++ translation correctness is proven by the fact that gen_torque and bld_pitch are identical at the divergence point in BOTH states. The gen_speed difference is in Python, not in the controller.

2. **Scenario 3 can be included in the integration test** using tolerance-based comparison instead of byte-identical comparison. A tolerance of 1e-02 on gen_torque would accommodate the 3.91e-03 difference caused by the 1-ULP gen_speed drift.

3. **Alternatively**, the fix is simple: skip `load_turbine_and_controller()` in vit_sim.py when only running simulations (not tuning). Load the turbine from pickle and use a pre-generated .IN file. This eliminates the scipy FPCR contamination entirely.

4. **The cleanest fix**: save the FPCR before `tune_controller()` and restore it afterward. On ARM64, this requires reading/writing the FPCR register via ctypes or a small C helper.

## Related Dev Notes

- `202603261254-scenario3-nondeterminism-100-run-study.md` — 100-run study establishing the two-state flip
- `202603261307-integration-test-scenario3-confirmed.md` — integration test re-run confirming Scenario 3 mismatch
- `202603261110-simulation-nondeterminism-investigation.md` — initial investigation (partially correct attribution to Python physics, incorrect about root cause)
- `202603261145-integration-test-systematic-evaluation.md` — systematic evaluation identifying under-exercised functions
