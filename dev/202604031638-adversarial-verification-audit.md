# Adversarial Audit of Verification Process
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification integrity — systematic adversarial examination of the entire comparison pipeline

## Summary

Conducted a systematic adversarial audit of the upstream-modified-C++ verification pipeline, probing every link in the chain for ways the "bit-identical" claim could be vacuous. The audit confirmed the results are legitimate but identified two meaningful weaknesses that should be stated alongside the headline numbers: 51% of compared values are trivially zero, and all simulation-level comparisons operate at float32 precision.

## Methodology

Approached the verification pipeline as an adversary trying to prove the results are smoke and mirrors. For each concern, ran concrete tests rather than reasoning abstractly.

## Concerns Investigated and Results

### 1. Are we running different binaries? — YES

Built all three configurations and captured MD5 hashes of `libdiscon.so`:

| Build | MD5 |
|-------|-----|
| Upstream Fortran | `d284e86efccbc33113dcbbfb346c91b1` |
| Modified Fortran | `204b2b52d8077c53752c82e930065f46` |
| C++ | `f86382c26c40dc0e9af39507c15ebd33` |

Three distinct binaries. The upstream and modified differ because ROSCO_Types.f90 has structural changes (BIND(C), INTEGER restart, REAL AWC_complexangle). The C++ binary is a completely different language.

### 2. Does Python ctypes load the correct library? — YES

`ControllerInterface.__init__` calls `cdll.LoadLibrary(self.lib_name)` (→ `dlopen`). `kill_discon` calls `dlclose(handle)` and `del self.discon`. Each scenario creates a new `ControllerInterface` instance.

Between builds, each `docker exec ... python3 vit_sim.py` is a separate OS process. `dlopen` handles are per-process. The new binary on disk is loaded fresh.

Verified by reading `control_interface.py:97` (LoadLibrary) and `control_interface.py:240-319` (kill_discon with dlclose).

### 3. Are captured avrSWAP values genuine controller outputs? — YES

`call_controller` sets 15 avrSWAP indices BEFORE calling DISCON (indices 0-3, 14, 19-20, 22-23, 26, 32-33, 36, 52, 82). These are controller INPUTS.

Our captured output indices are 42, 43, 119-121, 2600-2601, 2800. **Zero overlap** with the pre-call input indices. The values we read after `call_discon()` are what the controller wrote through the float pointer.

In `call_discon()`: `data = self.avrSWAP.astype(np.float32)` creates a copy, DISCON modifies it through ctypes, then `self.avrSWAP = data` stores the result. Post-call reads are from the DISCON-modified buffer.

### 4. Is the FITPACK stack scrubber loaded? — YES

`vit_sim.py:126-146` loads `libscrub.so` and monkey-patches `RotorPerformance.interp_surface` and `interp_gradient` to call `scrub_stack()` before each FITPACK evaluation. This eliminates non-determinism from uninitialized stack bytes in Scenario 3.

### 5. Are results deterministic? — YES

Ran Scenario 27 (the most complex — 11 modes, 24,000 timesteps) twice in succession with the same modified Fortran build. Both runs produced **bit-identical** output across all 13 arrays.

### 6. How much of the comparison is trivially zero? — 51.2%

Audited all 351 arrays (27 scenarios × 13 arrays). **185 arrays are all-zero** (2,688,000 values). **166 arrays have non-trivial values** (2,564,000 values).

The all-zero arrays are expected — modes produce zero output when disabled (CC_Mode=0 → cc_actuated_l=0) or when inputs are zero (FlapControl with Flp_Mode=1 ignores rootMOOP). Zero == zero comparisons confirm "the controller doesn't accidentally write non-zero values when modes are disabled," which is valuable but less powerful than comparing non-zero outputs.

**No scenario has all 13 arrays zero.** Every scenario has at least gen_speed and gen_torque non-trivial (from the 1-DOF rotor model). Scenario 13 is the minimum: only 2/13 non-trivial (gen_speed, gen_torque). Scenarios 7 and 27 are the maximum: 10/13 non-trivial.

Per-array non-trivial coverage:
- `gen_speed`, `gen_torque`: Non-trivial in 27/27 scenarios
- `bld_pitch`, `bld_pitch_2`, `bld_pitch_3`: Non-trivial in 24/27
- `gen_power`: Non-trivial in 26/27
- `cc_actuated_l/dl`: Non-trivial in 3/27 (Scenarios 7, 24, 27)
- `stc_input`: Non-trivial in 3/27
- `nac_yaw`: Non-trivial in 2/27 (Scenarios 7, 27)
- `flp_angle_1/2/3`: Non-trivial in 1/27 (Scenario 26 only)

### 7. What precision are we comparing at? — float32

Verified by checking float32 round-trip on every stored value:

```
bld_pitch[1] = 1.00509244948625564575e-02, f32 roundtrip: True
gen_torque[31] = 2.90338348388671875000e+02, f32 roundtrip: True
cc_actuated_l[20001] = -1.73515977803617715836e-03, f32 roundtrip: True
```

Every value is an exact float32 representation stored in a float64 container. This is because `call_discon` converts `avrSWAP` to `np.float32` before passing to DISCON, and DISCON writes float32 outputs. A translation bug producing a difference smaller than float32 epsilon (~1.2e-7 relative) would be invisible at the simulation level.

**Mitigations:**

1. **KGen kernel verification operates at float64.** 42 functions are verified with full double-precision field data comparison. This is the first line of defense and catches sub-float32 differences.

2. **Closed-loop feedback amplifies small differences.** The 1-DOF model integrates torque into rotor speed: `rot_speed[i] = rot_speed[i-1] + (dt/J) * (aero_torque - Ng * gen_torque / GBoxEff)`. A 1-ULP torque error at timestep N causes a measurable speed divergence by timestep N+100, which feeds back into the next controller call. A systematic translation error cannot hide at float32 precision over 24,000 closed-loop timesteps.

3. **Float32 is the deployment precision.** OpenFAST uses the same float32 avrSWAP interface. If two implementations produce the same float32 output, they are functionally identical for the real application.

### 8. Could the C++ build be a hybrid (some Fortran leaking through)? — NO

The C++ build output shows `Building CXX object` for all 53 source files and `Linking CXX shared library`. No Fortran compilation step. CMakeLists.txt has `LANGUAGES C CXX` (no Fortran). `rm -rf *` in the build directory before cmake prevents stale object files.

### 9. Could the comparison script be buggy? — Checked

`np.array_equal(a, b)` performs element-wise `==` comparison. For IEEE 754 floats, this is bit-exact (NaN != NaN, but we don't expect NaN). The script iterates `for key in sorted(b.files)` — all arrays in the baseline are compared. If an array existed in one .npz but not the other, `KeyError` would crash the script.

### 10. Are the upstream arrays from a single consistent run? — Partially

The upstream_arrays directory was built incrementally across 3 runs:
- Scenarios 1-25: Generated during the flap index fix run
- Scenario 26: Generated when adding Scenario 26
- Scenario 27: Generated when adding Scenario 27

All 3 runs used the same upstream source (e8010f0), same compiler, same flags. Since results are deterministic (verified above) and scenarios are independent (each creates a new ControllerInterface with dlclose between them), the incremental generation is equivalent to a single run.

## Honest Assessment

**What we can claim:** 2,564,000 non-trivial values across 27 scenarios are bit-identical (at float32 precision) between upstream Fortran, modified Fortran, and C++. Additionally, 42 functions are verified at full float64 precision through KGen kernel comparison. The closed-loop feedback model amplifies small errors, making it unlikely that a systematic translation bug could hide at float32 precision over thousands of timesteps.

**What we should not claim:** "5.25 million values identical" without qualification. 51% of those values are trivially zero. The non-trivial comparison is 2.56M values.

**Known blind spots:**
- A translation bug that only affects a single timestep by less than float32 epsilon, in a code path that doesn't feed back into the closed loop, would be invisible at the simulation level (but would be caught by KGen if the function is in the 42 KGen-verified set)
- Flap output has meaningful non-zero comparison in only 1 of 27 scenarios
- Stage B/C/D/E functions (10 functions not KGen-verified) rely solely on simulation-level float32 comparison

## Conclusion

The verification pipeline is legitimate. The three builds are genuinely different binaries, the comparison compares genuine controller outputs, results are deterministic, and non-trivial values differ between blades and vary over time in expected ways. The main weaknesses — float32 precision ceiling and 51% trivially-zero arrays — are real but mitigated by KGen float64 verification and closed-loop feedback amplification. These weaknesses should be stated transparently alongside any headline verification numbers.
