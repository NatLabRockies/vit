# Session State Before Context Compaction
**Date:** 2026-03-26
**Status:** in-progress
**Scope:** Session continuity reference

## What Was Accomplished This Session

### 1. Phase 7B Integration Test (27 functions)
- All 27 functions integrate, compile, and don't crash across 6 scenarios
- PitchControl excluded (depends on untranslated PitchSaturation in ControllerBlocks.f90)
- Committed: VIT `8b02689`, ROSCO kernel state at `8154ea8`

### 2. Integration Test Numerical Validation Gap Discovery
- Previous tests only diffed stdout text (scenario headers + "PASSED" messages)
- Added `--output-dir` to vit_sim.py: saves gen_torque, bld_pitch, gen_speed, gen_power, nac_yaw arrays to .npz files per scenario
- Added MD5 checksums to stdout
- Committed: ROSCO `7294c23`

### 3. Definitive Array Comparison (412k values)
- Ran proper test: clean Fortran baseline vs 27-function integrated, per-scenario isolation
- All 412,000 values IDENTICAL across 6 scenarios
- Verified with binary MD5 checksums (baseline `0f080c86...` vs integrated `919c20d9...`)
- Spot-checked sample values at multiple indices per array

### 4. Systematic Test Evaluation (6 cracks examined)
- No critical flaws in methodology
- 332k values (80%) are real varying data; 80k (19%) trivially zero (nac_yaw in 1-DOF sim)
- Build completeness verified despite clock skew warning
- C++ code path confirmed active via symbol tables

### 5. Under-Exercised Functions Identified
- 8 functions receive zero/near-zero inputs in 1-DOF sim: YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl, IPC, ActiveWakeControl
- Designed Scenarios 7 and 8 with synthetic input injection (committed at `72d38f3`, then reverted vit_sim.py to `7294c23` for testing)

### 6. Scenario 3 Non-Determinism Discovery
- 100-run study: Scenario 3 gen_torque flips between exactly 2 states (71%/29% split)
- All other scenarios: 100/100 deterministic
- Pre-existing ROSCO issue (pure Fortran, no C++ involvement)
- Scenario 3 enables 7 mode flags simultaneously — unique among all scenarios
- Integration test re-run confirmed: Scenario 3 mismatch matches the known two states

## Current State of Files

### ROSCO repo (working tree)
- **Source files**: currently INTEGRATED (27 `_c(` references, from last integration test run)
- **vit_sim.py**: reverted to `7294c23` (6 scenarios, with --output-dir). Scenarios 7/8 saved at commit `72d38f3`
- **baseline_final/**: 6 .npz files from clean Fortran (last integration test baseline)
- **integrated_final/**: 6 .npz files from integrated build (last integration test)
- **INTEGRATION_TEST.md**: needs update for Scenario 3 non-determinism
- **Stale test dirs**: proof_baseline/, proof_integrated/, baseline_iso/, integrated_iso/, baseline_arrays/, integrated_arrays/, test_full_a/, test_full_b/, test_s1_a/, test_s1_b/, integ_run_a/, integ_run_b/ — can be cleaned up

### VIT repo
- Latest commit: `b37bc66` (dev note on Scenario 3 confirmation)
- All dev notes committed through the session

### Docker container
- `vit-dev` running, clean Fortran build last, but sources are integrated
- Environment intact (gfortran, g++, cmake, Python 2.7/3.12, VIT, ROSCO)

## Next Step: Investigate Scenario 3 Non-Determinism

The investigation should determine:
1. **What flips**: which specific variable or computation produces two values?
2. **Why it flips**: what non-deterministic input (uninitialized memory? process state?) causes it?
3. **Can it be fixed**: either in ROSCO source or in the test setup?

Key facts for the investigation:
- Only Scenario 3 is affected (7 mode flags enabled simultaneously)
- gen_torque has exactly 2 distinct MD5 states, 71%/29% split
- Scenarios 1,2,4,5,6 are 100% deterministic over 100 runs each
- The non-determinism is in pure Fortran (no C++ involvement)
- The flip is binary (exactly 2 states, never a third), suggesting a single bit somewhere

Approach: use the .npz files from two different-state runs to find the first divergence point (timestep and array), then trace back to which Fortran function/variable produces the difference.
