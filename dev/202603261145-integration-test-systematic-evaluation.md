# Integration Test Systematic Evaluation
**Date:** 2026-03-26
**Status:** completed
**Scope:** integration test methodology review, crack analysis

## Context

After discovering the stdout-only diff gap (dev note `202603261048`) and the non-determinism issue (dev note `202603261110`), we established a proper array-level comparison methodology and confirmed 412,000 float64 values identical between baseline and integrated. This note documents a systematic evaluation of that test, looking for remaining cracks.

## Evaluation: Six Potential Cracks

### 1. Are both runs loading different libraries?

**Concern:** `from rosco import discon_lib_path` resolves to `/workspace/ROSCO/rosco/lib/libdiscon.so`. Both baseline and integrated builds copy their output to this same path. If the copy failed silently, both runs would use the same binary.

**Verified:** MD5 checksums of the installed library captured at each stage:
- Baseline: `0f080c865f5d1ed500a2bcd90b700faa` (0 `_c(` refs, stubs)
- Integrated: `919c20d9305357efd3df9d2ab5d194d0` (54 `_c(` refs, 27 C++ files)

Different binaries at the same path. Each was checksummed from `/workspace/ROSCO/rosco/lib/libdiscon.so` immediately after the `cp` command.

**Status: No crack.**

### 2. Is `np.array_equal` byte-identical or just value-identical?

**Concern:** `np.array_equal` uses `==` (value comparison). `+0.0 == -0.0` is True in IEEE 754 but they have different bit patterns. Claiming "byte-identical" while using value comparison is inaccurate.

**Verified:** MD5 checksums of `arr.tobytes()` match for every array. `tobytes()` returns raw byte representation — MD5 collision probability is ~2^-128. Also confirmed with direct `tobytes() == tobytes()` comparison on sample arrays.

**Status: No crack.** The MD5 checksums prove true byte-identity, independent of `np.array_equal`.

### 3. Are some arrays trivially zero?

**Concern:** If both baseline and integrated produce all-zero arrays, the comparison proves nothing — both just produce zeros.

**Verified:** Analyzed all 28 arrays across 6 scenarios:
- **332,000 values (80%)**: Real, varying simulation data (thousands of unique values, ranges spanning kW to MW for power, rad for pitch, RPM for speed)
- **80,000 values (19%)**: Trivially zero — all `nac_yaw` arrays (1-DOF sim has no yaw dynamics)

The zero arrays are expected behavior for the 1-DOF simulation model. The 80% with real data is the meaningful comparison.

**Status: Minor weakness.** nac_yaw is never exercised in the 1-DOF sim. YawRateControl IS called (Scenario 3, Y_ControlMode=1) but with zero NacHeading/NacVane inputs, so the output is zero. A multi-DOF sim would exercise this more meaningfully.

### 4. Could clock skew cause an incomplete build?

**Concern:** Every build shows `gmake: warning: Clock skew detected. Your build may be incomplete.` (Mac filesystem timestamps vs container clock). If make skipped recompiling some files, the binary might not reflect the current source.

**Verified:**
- `rm -rf *` before every `cmake ..` eliminates all prior build artifacts
- 40 object files exist (27 C++ + 13 Fortran) — all present
- Object file timestamps are newer than source file timestamps
- The .so file is freshly linked (its timestamp matches the build)

**Status: No crack.** The `rm -rf *` guarantees a from-scratch build. Clock skew can cause unnecessary rebuilds on subsequent `make` runs but cannot cause files to be skipped when building from empty.

### 5. Does the simulation actually call the C++ code?

**Concern:** Could the Fortran wrappers be no-ops, or could the call path bypass C++ entirely?

**Verified:**
- Fortran wrapper for `saturate` contains `BIND(C, NAME='saturate_c')` interface declaration — confirmed by reading the source
- `nm saturate_cpp.cpp.o` shows `saturate_c` in the text section (`T saturate_c`)
- `nm -D libdiscon.so` shows `saturate_c` exported — the symbol is in the shared library
- The original Fortran function body is REPLACED by the wrapper — there is no "fallback" to the original code. The wrapper IS the function body now.
- If `_c` were undefined, the linker would fail (and it doesn't — the build succeeds)
- If `_c` produced wrong results, the arrays would differ (and they don't — they match the Fortran original)

**Status: No crack.**

### 6. Do all scenarios exercise the translated functions meaningfully?

**Concern:** Some scenarios provide near-zero inputs to certain controller modes, meaning the translated C++ code runs but processes trivial (zero) inputs.

**Verified per scenario:**

| Scenario | gen_torque | bld_pitch | gen_speed | gen_power | nac_yaw |
|----------|-----------|-----------|-----------|-----------|---------|
| 1 (1000s) | 25,004 unique | 11,713 unique | 39,993 unique | 39,376 unique | **all zero** |
| 2 (100s) | 3,401 unique | 60 unique | 4,000 unique | N/A | N/A |
| 3 (400s) | 14,728 unique | 10,231 unique | 16,000 unique | 15,460 unique | **all zero** |
| 4 (100s) | 3,402 unique | 60 unique | 4,000 unique | 3,464 unique | **all zero** |
| 5 (400s) | 15,397 unique | 11,302 unique | 16,000 unique | 15,473 unique | **all zero** |
| 6 (100s) | 3,402 unique | 60 unique | 4,000 unique | 3,464 unique | **all zero** |

Weaknesses:
- **nac_yaw always zero**: YawRateControl runs in Scenario 3 but produces zero output (no yaw dynamics in 1-DOF sim)
- **bld_pitch near-zero in Scenarios 2/4/6**: Below-rated wind speed, pitch controller inactive. Only 60 unique values (brief startup transient)
- **Scenario 2 missing gen_power/nac_yaw arrays**: Scenario 2 uses a manual loop (not the Sim class) and only saves gen_torque, bld_pitch, gen_speed

**Status: Minor weakness.** The core control loop (gen_torque, gen_speed) is thoroughly exercised in all scenarios. Pitch control is exercised in Scenarios 1/3/5 (above-rated wind). Yaw and below-rated pitch are weakly exercised due to 1-DOF sim limitations.

## Overall Assessment

The integration test is sound. The meaningful comparison covers 332,000 real, varying float64 values across the core simulation outputs (gen_torque, bld_pitch, gen_speed, gen_power). The byte-identity is proven via MD5 checksums of raw bytes. The binaries are provably different (different MD5s). The C++ code path is confirmed active (symbols exported, wrappers contain BIND(C) calls).

The remaining weakness is coverage depth for nac_yaw (always zero) and bld_pitch in below-rated scenarios (near-zero). These are limitations of the 1-DOF simulation model, not of the test methodology. A multi-DOF sim or synthetic input injection would exercise these paths more meaningfully.

## Comparison to KGen Verification

The integration test and KGen verification test different things:

| | KGen Verification | Integration Test |
|---|---|---|
| **What it tests** | Individual function correctness | Whole-system plumbing |
| **Comparison** | C++ output vs captured Fortran state | Full-sim Fortran vs full-sim integrated |
| **Inputs** | Real runtime state from actual simulation | Simulated inputs from 1-DOF model |
| **Coverage** | 40 captured invocations per function | Thousands of timesteps across 6 scenarios |
| **Precision** | IDENTICAL (bit-exact) per field | IDENTICAL (byte-exact) per output array |
| **What it can't test** | Wrapper correctness, struct layout, library loading | Functions with trivial (zero) inputs in 1-DOF sim |

The two are complementary. KGen proves each function in isolation against real runtime data. The integration test proves the functions work together in the real build.
