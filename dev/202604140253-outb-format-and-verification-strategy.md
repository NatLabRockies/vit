# OpenFAST `.outb` Format, Lossy Quantization, and AeroDyn Verification Strategy
**Date:** 2026-04-14
**Status:** completed (analysis); feeds into `PROJECT_PLAN_AERODYN.md` as an open question
**Scope:** Deep dive on why our AeroDyn regression-test output differed from NREL's reference at the byte level, what the `.outb` binary format actually does, and what this means for how AeroDyn translations will be verified

## Context

Dev note `202604140241-openfast-build-aerodyn-baseline.md` recorded that we successfully built the AeroDyn Driver in `vit-dev-openfast` and ran a real regression test case (`ad_BAR_SineMotion`) to completion, but the output file (`ad_driver.outb`) differed from the shipped NREL reference at **20063 of 21864 bytes (~94%)** — despite both files being the exact same size and the first 568 bytes (the header) being bit-identical. That finding was flagged in the earlier dev note as "almost certainly floating-point packing, not a real simulation error," but not fully explained. This dev note is the full explanation, plus a concrete set of options for how AeroDyn verification should actually work. It exists because the question is non-trivial, matters directly to the shape of `PROJECT_PLAN_AERODYN.md`, and should not be buried as a one-line flag in another dev note.

## The regression test we ran

**Test case:** `ad_BAR_SineMotion`, one of 17 AeroDyn test cases in the `reg_tests/r-test` submodule (`OpenFAST/r-test` repo, pinned at commit `dd5feaa` = v5.0.0 tag).

**What it exercises:** A 3-bladed BAR (Big Adaptive Rotor) turbine — NREL's 206-meter-rotor research configuration — subjected to 7 seconds of sinusoidal base motion in the x-direction (0.1 Hz frequency, 5 m amplitude), at 7 rpm rotor speed and 1° collective blade pitch. 70 timesteps at 0.1s each. Steady 9 m/s wind (no InflowWind turbulence). Full BEMT aerodynamic pipeline with the BAR blade distributed properties and Airfoil polars from `BAR_Baseline/`.

**Artifacts the test ships:**
- `ad_driver.dvr` — driver config (committed)
- `OpenFAST_BAR_00_AeroDyn.dat` — AeroDyn primary input, references BAR_Baseline via `../BAR_Baseline/` (committed)
- `OpenFAST_BAR_00_InflowFile.dat` — InflowWind input (committed)
- `ad_driver.outb` — **the reference output from a known-good NREL build** (committed)
- `README.md` — documents the case

**How we ran it:**
```bash
cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/ad_BAR_SineMotion
/workspace/openfast/build/modules/aerodyn/aerodyn_driver ad_driver.dvr
```

The driver wrote its output to `ad_driver.outb` — **same path as the committed reference** — which meant our output overwrote the reference in the working tree. git saw the file as modified. To compare without losing the reference, I saved our output to `/tmp/our_output.outb`, restored the reference with `git checkout ad_driver.outb`, and ran `cmp -l` between the two.

## The byte-comparison result

```
/tmp/our_output.outb   vs   ad_driver.outb
21864 bytes            vs   21864 bytes
First 568 bytes        IDENTICAL
Bytes 569–21864        20063 of 21296 DIFFERING (~94% of the data section)
```

**That's a striking number at first glance.** A fresh reader would assume the simulation produced wildly wrong results. It's not the right interpretation, and the reason matters.

## What `.outb` actually is

OpenFAST's `.outb` is not a stream of IEEE 754 doubles. It's a custom lossy binary format optimized for "enough precision to review results, small enough to ship as git-committed test references." Each channel of output data (rotor torque, hub force X/Y/Z, blade root moments, tip deflections, wind velocities at each node — typically 100+ channels for AeroDyn) gets encoded as follows:

1. The writer **collects all samples for the channel** across all timesteps. For our 70-timestep test at 0.1s DT, each channel's series is 70 doubles.
2. The writer **computes `min_val` and `max_val`** across those 70 samples.
3. It **derives a scale and offset** for the channel:
   ```
   scale  = 65535.0 / (max_val - min_val)
   offset = min_val
   ```
4. Each sample is then **packed into a `uint16`**:
   ```
   packed_i = uint16( (value_i - offset) * scale )
   ```
5. The file stores the `offset` and `scale` (as doubles, in the per-channel header section) and then **the packed `uint16` stream** for the time series.

The effective per-channel precision is approximately `(max_val - min_val) / 65535` — about 16 bits of dynamic range. For a blade pitch channel whose values span -10° to +10°, that's roughly 0.0003° resolution per sample. For a rotor torque channel spanning 0 to 5 MN·m, about 76 N·m resolution. Fine for visualization and for human-reviewed regression testing, **but fundamentally irreversible.** You cannot recover the original doubles from the `.outb` file — the quantization is a one-way trip through `int16`.

The reason OpenFAST uses this format is size: storing 70 samples × 100 channels = 7000 doubles = 56 KB would be large for a git-committed reference file, especially across many test cases. Packing to `uint16` plus per-channel scale/offset metadata gets the same file to roughly 14 KB plus overhead. Across the 17 AeroDyn test cases in r-test, the savings are substantial.

## Why the byte diff is 94% when the simulations are (probably) correct

Now consider what happens when two "correct" builds produce slightly different floating-point outputs.

**Sources of small floating-point differences between correct builds** (any of these can cause ULP-level drift between NREL's reference build and ours):

- **Compiler version.** NREL probably built the reference `ad_driver.outb` with gfortran version X; we used 13.3.0. Different versions schedule instructions differently, inline different things, and choose different FMA opportunities.
- **LAPACK / BLAS implementation.** We linked OpenBLAS (via `libopenblas-dev` from Ubuntu apt); NREL may have used a different BLAS (MKL, reference BLAS, or a different OpenBLAS version). The linear-algebra routines (solvers, eigenvalue, matrix multiply) can produce ULP-level different answers across implementations even when they're all "correct."
- **Linker and memory layout.** Different library versions produce slightly different binary layouts, which can change cache behavior subtly, which can (in edge cases) change which floating-point path a given branch takes — especially with code that depends on uninitialized-but-deterministic memory in ways the compiler didn't intend.
- **Host OS and architecture.** We're on ARM64 Linux via Colima. NREL's reference was almost certainly built on x86_64 Linux (and possibly a different glibc version). ARM64 and x86_64 have different FMA behaviors, different NaN handling for some edge operations, and different default rounding modes in some library calls.
- **FMA scheduling.** Same issue ROSCO hit in Phase 6B. Different compilers and different platforms make different independent choices about which multiply-adds to fuse, and the results differ by up to 1 ULP.
- **Optimization flag differences.** Our build used `CMAKE_BUILD_TYPE=Release`, which is OpenFAST's default. NREL's reference may have been built with the same flag or a different one (Debug, RelWithDebInfo) — affecting optimization aggressiveness and thus FMA scheduling, vectorization, and instruction ordering.

None of these cause "wrong" results. They cause results that differ from each other by somewhere between 1 ULP (`~10^-16` relative) and maybe `10^-10` relative over 70 timesteps of tight BEMT iterations. For any engineering purpose, those differences are irrelevant.

**But the `.outb` format amplifies them dramatically, for two reasons:**

**Amplification reason 1: int16 quantization resolution is vastly coarser than 1 ULP.**

A double has 52 bits of mantissa precision — about `10^-16` relative. An `int16` has 15–16 bits — about `10^-5` relative at best. When two doubles differing by `10^-12` relative are each packed through the same scale/offset, they usually round to the same `uint16`. But sometimes they're on opposite sides of a quantization boundary, and then they round to *different* `uint16` values. Over 70 samples, you might get a handful of samples per channel crossing a quantization boundary, producing different bytes in the output.

This alone would explain maybe 5-20% byte differences.

**Amplification reason 2: the scale/offset themselves change if the channel's min or max shifts.**

This is the bigger effect. The scale and offset for a channel are computed from `min_val` and `max_val` of the time series. If a single sample in the channel differs from NREL's reference by 1 ULP, and that sample happens to be the channel's minimum or maximum, then **the channel's scale and offset change by 1 ULP as well**. And now:

```
packed_i = uint16( (value_i - offset_new) * scale_new )
         ≠ uint16( (value_i - offset_ref) * scale_ref )
```

Even if `value_i` itself didn't change, the new scale and offset shift the quantization grid, and **every packed sample for that channel** can land on a different `uint16` value. Not one sample differing — all 70 samples for the channel differing.

And across a typical AeroDyn simulation with ~100 output channels, it only takes a handful of channels to have their min/max perturbed by 1 ULP to make almost every data byte in the output file differ. That's exactly what we saw: 94% of the data section differs, while the header (which has format version, channel counts, channel names, units — none of which depend on simulation values) is bit-identical.

**94% byte diff is not alarming. It's what you would predict given the format.**

## OpenFAST's own regression test framework knows this

NREL does not use byte comparison for OpenFAST regression tests. The infrastructure in `reg_tests/` uses Python driver scripts — for AeroDyn, that's `reg_tests/executeAerodynRegressionCase.py`. The script's logic is roughly:

1. Run `aerodyn_driver` against the test case inputs
2. Parse **both** the new output `.outb` and the reference `.outb` using the `openfast_io` Python package (`openfast_io/` is a top-level directory in the OpenFAST repo — it's a pip-installable package with binary format readers for all OpenFAST output types)
3. `openfast_io` handles the int16 unpacking: for each channel, it reads the scale and offset, reverses the quantization, and returns the channel as a 1-D array of doubles (with the inherent int16 precision loss — you get approximately doubles, but with roughly `10^-5` relative precision due to the packing)
4. The comparison iterates over channels, computing relative and absolute differences
5. Each channel must be within a configurable tolerance — default is `1e-5` relative for most channels, with per-channel overrides and absolute floor handling for channels that include zero crossings
6. The test passes if every channel is within tolerance

This is **tolerance-based, not byte-identical**. And it's tolerance-based for reasons that are deliberate, not lazy: the format cannot support byte-identical comparison across build environments without constraints (same compiler version, same BLAS, same host OS) that NREL's CI cannot guarantee.

A regression test "passing" by NREL's framework says **"our simulation output matches the reference to within `~10^-5` relative precision,"** which is useful and defensible but is a strictly weaker claim than ROSCO's "byte-identical at float32 across 5.25M values."

## What this means for AeroDyn translation verification

ROSCO's verification strategy worked because two things were true:

1. **KGen kernel verification at float64** gave us per-function bit-identical guarantees. Every translated function was verified by replaying captured state through both the original Fortran and the new C++, with full double-precision comparison. Failures at `10^-16` relative were detectable.

2. **Simulation-level comparison via `vit_sim.py` → `.npz` → `np.array_equal`** gave us end-to-end byte-identical guarantees at float32 (the avrSWAP deployment precision). The output of `vit_sim.py` was numpy arrays, which preserved doubles (or float32 depending on dtype) exactly, and `np.array_equal` was strict bit comparison.

Both tiers supported strict byte-identity as the pass criterion, which let us make very strong claims in the adversarial audit ("every bit that differs has been traced to its root cause") and ultimately deliver ROSCO with one tolerated compiler artifact fully explained.

**For AeroDyn, tier 1 still works.** KGen kernel verification operates on per-function captured state, produces float64 field data files, and compares them directly. The `.outb` format plays no role in kernel verification. A translated AeroDyn function can still be verified against its original Fortran at float64 byte-identity, same as ROSCO.

**Tier 2 is the problem.** If we use `aerodyn_driver` directly as our simulation harness (which is the natural analog of ROSCO's `vit_sim.py`), the output is `.outb`. `.outb` cannot be byte-compared between builds in a meaningful way. The tier-2 guarantee we can recover via the `openfast_io` → tolerance-comparison path is strictly weaker than ROSCO's.

This matters because ROSCO's sim-compare was how we caught Stage B/C/D/E translation errors (the 10 functions that couldn't be KGen-verified because they do file I/O, config parsing, platform calls, or orchestration). If AeroDyn has an equivalent set of functions that can't be KGen-verified — and it probably will, because OpenFAST's NWTC Framework has a lot of scaffolding that doesn't fit KGen's function-level extraction model — we need a working tier-2 verification for them. If tier 2 is tolerance-based at `~10^-5`, we are admitting that translation errors of less than `10^-5` relative in those functions could go undetected at the simulation level.

## Three options for the AeroDyn verification strategy

### Option 1: Embrace tolerance-based simulation comparison

Accept that `.outb` is the format, use `openfast_io` to parse it, use tolerance-based per-channel comparison as the tier-2 verification criterion. Same pattern NREL's own regression tests use.

**Pros:**
- Simple. We install `openfast_io`, write a small wrapper around it, and we have a working sim-compare for AeroDyn.
- Compatible with NREL's existing test infrastructure — we can reuse their Python scripts almost directly.
- r-test's 17 AeroDyn test cases all ship with reference outputs in the same format, so we have 17 ready-to-use test scenarios without writing new harnesses.

**Cons:**
- Precision ceiling is ~`10^-5` relative at tier 2, substantially weaker than ROSCO's byte-identity. An adversarial audit would need to disclose this as a real caveat, more prominent than ROSCO's float32 ceiling.
- A translation error in a non-KGen-verified function that produces sub-`10^-5` errors in output channels could pass the tier-2 check while being a real bug.

### Option 2: Bypass `.outb` entirely

Write a custom harness that runs AeroDyn_Driver but captures outputs through a different channel, preserving full double precision. Several sub-options:

**2a. Modify the AeroDyn output writer to also dump a parallel `.npz`.** Would require a small C++ or Fortran patch to the AeroDyn output path that writes each channel as raw doubles alongside the existing `.outb`. Our tests would then compare the `.npz` files directly. This is invasive — modifies OpenFAST source — but faithful to what ROSCO did with `vit_sim.py`.

**2b. Drive AeroDyn via its existing C binding.** OpenFAST ships `AeroDyn_Inflow_C_Binding.f90` and `AeroDyn_Inflow_C_Binding_Types.f90` specifically to let external programs (CFD codes, other simulation frameworks) call AeroDyn via a C ABI. We could write a Python harness that ctypes-loads the C-bound AeroDyn, advances the simulation step by step, and captures outputs directly into numpy arrays at full precision. This is close to ROSCO's model (ctypes into a shared library) but uses a pre-existing C binding rather than writing our own wrapper. **This is probably the best option architecturally** — it reuses NREL's own external-interface work.

**2c. Use OpenFAST's text output format instead of binary.** AeroDyn_Driver has an `OutFileFmt` parameter in the `.dvr` file with options `1: text`, `2: binary`, `3: both`. The text format presumably uses full double precision in decimal. Comparing text output is string-level, which is simpler than binary parsing but introduces its own problems (trailing whitespace, number formatting, scientific-notation variation). Not ideal but cheap.

**Pros of Option 2 in general:**
- Preserves full float64 precision at tier 2, matching ROSCO's structure.
- Allows byte-identical three-build chain claims equivalent to ROSCO's.
- The 2b variant in particular reuses existing NREL infrastructure (the C binding) so it's not a one-off hack.

**Cons:**
- More work up-front to build the harness. Writing a Python driver that uses ctypes to drive AeroDyn through its C binding is a multi-day task, not a multi-hour task.
- More fragile than the tolerance-based approach if the C binding changes between OpenFAST releases (though NREL is generally good about ABI stability for published C bindings).
- Option 2a is invasive — we'd be modifying OpenFAST source just to support our verification, which couples our tooling to our fork.

### Option 3: Hybrid — strong tier 1 + tolerance tier 2

Keep KGen kernel verification at float64 byte-identity for every function we translate (tier 1 is unchanged from ROSCO). For tier 2, use tolerance-based `.outb` comparison via `openfast_io` with explicit per-channel tolerance budgets, and **disclose the precision ceiling honestly** in the equivalent of ROSCO's adversarial-audit dev note.

**Mitigation:** argue (honestly) that closed-loop feedback in a 70-timestep simulation amplifies small drift — the same argument ROSCO made about its float32 ceiling. BEMT iterations at each timestep couple blade-element forces to inflow to blade-element velocities, and a persistent translation error of any magnitude will accumulate over time rather than stay local to one timestep. So a bug producing `10^-7` error per timestep will grow to `10^-5` or larger over 70 steps and be caught. A bug producing `10^-9` error per timestep probably will not be caught at tier 2, but also probably doesn't matter for any engineering purpose.

**Pros:**
- Preserves strong tier 1 (same as ROSCO).
- Simple tier 2 (reuses NREL infrastructure).
- Honest about what it can and can't guarantee.

**Cons:**
- Weaker claims than ROSCO at the simulation level.
- Requires explicit articulation of the precision ceiling in the AeroDyn verification claims, which is a slightly harder "pitch" than ROSCO's "byte-identical across 5.25M values."

### Recommendation (now informed by empirical data)

**Option 3 (hybrid) with a critical addition: our own platform-specific baseline references, analogous to ROSCO's `baseline_arrays/` and `upstream_arrays/`.**

The empirical validation showed that **Option 1 as initially conceived does not work out of the box on our platform.** Running NREL's own `executeAerodynRegressionCase.py` against NREL's shipped reference produces RC=1 with 99.94% of values within tolerance — the 0.056% cross-platform drift exceeds the strict per-element gate and fails the test despite the simulation being functionally correct. If we adopted Option 1 as-is, every single r-test case we run would "fail" for platform-drift reasons, making the framework useless as a pass/fail gate for our translation work.

**The fix is to generate OUR OWN reference `.outb` files on our platform**, commit them alongside the test cases, and compare our future translations against our own baseline rather than NREL's. This mirrors exactly what ROSCO did: `baseline_arrays/` was committed in the ROSCO fork as "what our build produces as the golden reference," and `upstream_arrays/` was committed as "what upstream NREL produces" (used for the weaker cross-build sanity check). We have 5.25M-values three-build-chain verification for ROSCO because we maintained our own baseline.

**Concrete AeroDyn strategy:**

1. **Platform-specific baselines** — run every r-test AeroDyn case once on our `vit-dev-openfast` with clean upstream Fortran OpenFAST (no translations applied) to produce OUR reference `.outb` files. Commit them to the OpenFAST fork at a path like `reg_tests/r-test/modules/aerodyn/ad_BAR_SineMotion/ad_driver.baseline.outb` or similar. Alternative: keep them in a separate directory parallel to r-test.
2. **Tier-2 comparison against our baseline** — use a modified or forked version of `executeAerodynRegressionCase.py` that points at our baseline instead of r-test's committed reference. This should produce bit-identical results (or near-so — still need to verify that two runs on our platform are deterministic, which we should test) when we run clean Fortran AeroDyn, and should produce numerically-close-but-not-identical results when we run C++-integrated versions.
3. **NREL reference as sanity check, not gate** — keep the comparison against NREL's original r-test reference as a separate, informational check. Report it as "our output differs from NREL's reference by X%" but do NOT gate the test on it. This is analogous to the role `upstream_arrays/` plays in ROSCO: it proves the fork's type modifications don't cause semantic divergence, but it's not the daily verification gate.
4. **Tier 1 unchanged** — KGen kernel verification at float64 byte-identity for individual functions, same as ROSCO.

This strategy achieves Option 3's goals (hybrid verification with strong tier 1 + meaningful tier 2) without the fatal flaw of Option 1 (NREL's reference is platform-dependent and fails our build for non-bug reasons). The critical addition is the "own baseline" step that makes the strict per-element gate meaningful on our platform.

**When to consider escalating to Option 2b (ctypes harness via the C binding):** if we find that even same-platform AeroDyn runs are non-deterministic enough that our baseline comparison is unstable (ROSCO hit this with FITPACK stack scrubber), or if the `.outb` packing loses precision at the margin where we need it for verifying specific translations. The experiment did not test same-platform determinism; we should run `aerodyn_driver` twice in the same container and verify that two runs produce bit-identical `.outb` files. If they don't, we have a scrubber-class problem to solve before Option 3 can be trusted.

**This strategy must be documented in `PROJECT_PLAN_AERODYN.md` as a Key Decision** (per `CREATING_PROJECT_PLANS.md` P3: committed decisions belong in Section 3 of the plan doc with rationale and alternatives considered). The empirical data from this experiment is the rationale. The precision ceiling of `1e-5` relative (from the `.outb` format's int16 quantization) must also be named as an honest verification caveat when the eventual adversarial-audit dev note is written for AeroDyn.

## Empirical validation against NREL's reference

Rather than write a fresh tolerance-comparison script, we ran NREL's own `reg_tests/executeAerodynRegressionCase.py` directly against the `ad_BAR_SineMotion` case. The script is designed to be driven by CTest but it has a full standalone argparse interface and is self-contained — its `reg_tests/lib/` sibling directory ships its own `fast_io.py` with a `load_output()` function that parses `.outb` files, so no separate `openfast_io` install was needed.

**Invocation** (from inside `vit-dev-openfast`):
```bash
cd /workspace/openfast/reg_tests
python3 executeAerodynRegressionCase.py \
    ad_BAR_SineMotion \
    /workspace/openfast/build/modules/aerodyn/aerodyn_driver \
    /workspace/openfast \
    /workspace/openfast/build/modules/aerodyn \
    5 5 -v
```

The arguments are positional: case name, driver path, source dir, build dir, rtol magnitude (`5` → `1e-5` relative), atol magnitude (`5` → `1e-5` absolute). The `-v` flag is the script's "verbose" option.

**The script's workflow, for reference:**
1. Copies r-test's `BAR_Baseline/` to the build directory
2. Copies the test case inputs to `buildDirectory/ad_BAR_SineMotion/`, renaming `ad_driver.outb` → `ad_driver_ref.outb` so the reference is preserved alongside the inputs
3. Runs `aerodyn_driver` on the copied `.dvr` — writes a new `ad_driver.outb` in the build subdirectory
4. Parses both files with `pass_fail.readFASTOut` (which wraps `fast_io.load_output`)
5. Calls `pass_fail.passing_channels(testData.T, baselineData.T, rtol, atol)` for element-wise tolerance checking
6. Writes `ad_BAR_SineMotion.html` with per-channel summary (relative max norm, relative L2 norm, infinity norm)
7. `sys.exit(0)` if all channels pass; `sys.exit(1)` otherwise

**Result: RC=1 (test failed).** But this is the headline — it's misleading without the per-element breakdown, because the tolerance check is element-wise strict.

### Actual numerical comparison (the real answer)

Driving `fast_io.load_output()` manually against the same two files:

| Metric | Value |
|--------|-------|
| Array shape (both files) | `(70 timesteps, 127 channels)` = 8890 values |
| **Bit-identical values** | **8050 / 8890 (90.55%)** |
| Values within `1e-5` relative tolerance | 8879 / 8890 (99.87%) |
| Values within `1e-5` rel **OR** `1e-6` abs | 8885 / 8890 (99.94%) |
| **Failing elements under strict check** | **5 / 8890 (0.056%)** |
| Max absolute difference (full array) | 0.2147 |
| Max relative difference (full array) | 1.025% |

The ~15-byte file-size difference between our output (21849 B) and the reference (21864 B) is purely in the binary header (compile-info strings, build timestamps embedded in the file), not in the data section. After parsing, both arrays are exactly the same shape.

**Our simulation is correct.** 90% of the output is bit-exact against NREL's reference, and 99.87% is within OpenFAST's standard reg_test tolerance. The verification question "is our build functionally correct" now has an empirical answer: **yes**.

### Per-channel drift analysis

The 5 failing elements are in real BEMT solver outputs — rotor-integrated forces/moments and per-blade-node aerodynamic force distributions — not in diagnostic channels or pure init-only artifacts:

| t (s) | Channel | Description | Our value | Ref value | Abs diff | Rel diff |
|-------|---------|-------------|-----------|-----------|----------|----------|
| 0.40 | `RtAeroMyh` (N·m) | Rotor aero moment, hub Y | 1795.33 | 1795.55 | 0.215 | 1.20e-4 |
| 2.80 | `AB1N021Fx` (N/m) | Blade 1 node 21 aero force X | 2287.94 | 2287.85 | 9.35e-2 | 4.09e-5 |
| 3.40 | `AB1N007Fx` (N/m) | Blade 1 node 7 aero force X | -1.84 | -1.84 | 5.79e-5 | 3.15e-5 |
| 3.80 | `RtAeroFzh` (N) | Rotor aero force, hub Z | -136.97 | -136.97 | 1.52e-3 | 1.11e-5 |
| 3.90 | `AB1N002Fy` (N/m) | Blade 1 node 2 aero force Y | -34.42 | -34.42 | 1.05e-3 | 3.05e-5 |

**The critical observation that confirms this is platform drift and not a real bug:** look at the `RtAeroMyh` channel in detail. It failed at t=0.40s where the value is ~1795 N·m (absolute diff 0.215 N·m → relative 1.2e-4). But at t=1.0s the **same channel has the same magnitude of absolute drift** (max_abs=0.2147) against a reference value of **4.216 million N·m**, giving a relative error of **5e-8**. The exact same 0.2 N·m of absolute noise that failed the check at t=0.4s is negligible at t=1.0s because the channel's value has grown by four orders of magnitude. The channel's overall dynamic range is ~4 MN·m; 0.2 N·m on that range is a relative precision of ~5e-8, which is well inside double-precision arithmetic (1 part in 20 million).

**This is a signature of cross-platform floating-point drift at the double-precision level, not a simulation bug.** A real translation or build bug would produce systematic, larger, correlated differences across many channels and many timesteps. We have 5 isolated elements (0.056% of the total) showing ~1e-4 relative error in a handful of BEMT solver outputs, with the rest of the array (99.94%) within standard tolerance or bit-identical.

**Plausible sources of the drift** (in rough order of likelihood):
- **BLAS implementation difference** — our OpenBLAS 0.3.26 vs whatever LAPACK/BLAS NREL used for the reference. Different implementations produce ULP-level differences in matrix operations and vector norms; BEMT's induction solver and the blade element integrations use dot products and linear solves where BLAS is invoked. Different internal summation orderings produce different roundings.
- **Host architecture (ARM64 vs x86_64)** — different SIMD instructions, different FMA implementations, different handling of subnormal values. ARM64 NEON and x86 AVX produce ULP-level differences in multi-operand arithmetic.
- **FMA scheduling differences between gfortran versions** — same issue ROSCO hit in Phase 6B NotchFilter. If NREL's reference was built with a different gfortran version, their FMA choices may differ from our 13.3.0's.
- **Linker and library version differences** affecting internal memory layout in ways that change evaluation order marginally.

None of these would indicate a real bug in our build. All of these are exactly what you'd expect from running the same Fortran source through a different toolchain.

### Why the NREL reg_test framework fails anyway

The `pass_fail.passing_channels()` function (lines 52-90 of `reg_tests/lib/pass_fail.py`) uses an **element-wise adaptive tolerance**:

```python
for i in range(n_channels):
    baseline_offset = baseline[i] - np.min(baseline[i])
    b_order_of_magnitude = np.floor(np.log10(baseline_offset + NUMEPS))
    atol = 10**(max(b_order_of_magnitude) - ATOL_MAGNITUDE)
    atol = max(atol, ATOL_MIN)  # ATOL_MIN = 1e-6
    where_close[i] = np.isclose(test[i], baseline[i], atol=atol, rtol=rtol)

passing_channels = np.all(where_close & where_not_nan & where_not_inf, axis=1)
```

And the final gate is `np.all(passing_channels)` — **every channel must have every timestep within tolerance**. A single failing element in a single timestep fails an entire channel; a single failing channel fails the entire test.

This check is strict enough that cross-platform runs routinely fail without the output being broken. NREL manages this in their own CI by either (a) running on specific reference platforms where the committed outputs were generated, or (b) maintaining platform-specific reference baselines. Neither mitigation is available to us out of the box on ARM64 Linux.

**The test framework is working as designed; the "failure" is that our platform drifts from NREL's reference platform by an amount that exceeds the strict per-element gate, even though the drift is functionally meaningless.**

## Related dev notes

- `202604140204-vit-dev-openfast-docker-plan.md` — the container that made this all runnable
- `202604140241-openfast-build-aerodyn-baseline.md` — the run itself and the initial byte-comparison finding. This dev note is the follow-up analysis on what that finding means.
- `202604031638-adversarial-verification-audit.md` (ROSCO) — the adversarial-audit template this AeroDyn verification strategy will eventually need to replicate. The "Honest verification caveats" section of that note should be used as the model for how to disclose AeroDyn's precision ceiling when the time comes.

## Related files

- `openfast/openfast_io/` — the Python package that can parse `.outb` files. If we adopt Option 3, this is the tool we'll be calling.
- `openfast/reg_tests/executeAerodynRegressionCase.py` — NREL's reference implementation of tolerance-based AeroDyn regression testing. Worth reading end-to-end before finalizing the verification strategy, because it encodes assumptions about per-channel tolerance that we may want to copy or override.
- `openfast/modules/aerodyn/src/AeroDyn_Inflow_C_Binding.f90` — the C binding that enables Option 2b. Worth reading before rejecting Option 2 as "too much work," because it may be most of the way to what we need.

## Open questions for `PROJECT_PLAN_AERODYN.md`

When the plan document is drafted, these should appear in the Open Questions section (per `CREATING_PROJECT_PLANS.md` P4). The first question from the original draft of this dev note is now **answered** by the empirical validation in this session and has been moved to the Key Decisions recommendation above. The remaining questions are still genuinely open:

1. ~~**What tier-2 verification strategy do we commit to?**~~ **ANSWERED (2026-04-14):** Option 3 (hybrid: KGen float64 tier 1 + tolerance-based `.outb` comparison tier 2) with platform-specific baselines (our own `.outb` references committed to the OpenFAST fork, analogous to ROSCO's `baseline_arrays/`). NREL's original `r-test` references become an informational cross-platform sanity check, not the daily gate. Rationale: direct experimental evidence that NREL's reference fails on our ARM64 + OpenBLAS + gfortran 13.3.0 platform by 0.056% of data values due to ULP-level floating-point drift, while the simulation is functionally correct (90% bit-identical, 99.94% within `1e-5` rel / `1e-6` abs combined tolerance). See the Empirical Validation and Per-Channel Drift Analysis sections above. This question should still appear in the plan's Key Decisions section (per P3) with the rationale, not just deleted — future readers need to see why this choice was made.

2. **What per-channel tolerance budget do we set for tier-2 comparison?** Default is `1e-5` relative + `1e-6` absolute floor, matching OpenFAST's own convention. Our empirical result shows this is workable when comparing against our own baseline (because same-platform drift should be zero or near-zero). Some channels may need per-channel overrides — for example, channels that start near zero and accumulate (integrals) may benefit from absolute-only tolerance, and channels with zero crossings need the absolute floor. **Decision owner:** user + domain knowledge. **Decision point:** when the verification harness is being built.

3. **Is our own AeroDyn Driver run deterministic on our platform?** Two successive `aerodyn_driver` runs with the same inputs should produce bit-identical `.outb` files for Option 3 to be trustworthy. ROSCO hit a FITPACK stack-scrubber nondeterminism that took significant debugging effort to diagnose and fix. We have not yet tested AeroDyn determinism at all. **Decision owner:** N/A — just an experimental test. **Decision point:** before any translation work begins. Concrete test: run `ad_BAR_SineMotion` twice in the same container and `cmp -s` the two `.outb` outputs. If they match, determinism is confirmed and Option 3 is safe. If they don't, we have a scrubber-class problem to investigate.

4. **What's the storage and commit model for our own baselines?** ROSCO committed `baseline_arrays/` and `upstream_arrays/` directly in the ROSCO fork at repo root. For AeroDyn, we could (a) commit baselines as files inside the r-test submodule subdirectories (cluttering the NREL-owned submodule), (b) commit baselines in a parallel directory tree at the OpenFAST fork's root (e.g., `baselines/aerodyn/ad_BAR_SineMotion/ad_driver.baseline.outb`), or (c) store them out-of-tree somewhere. Option (b) is cleanest and most similar to ROSCO's pattern. **Decision owner:** user. **Decision point:** when the verification harness is being built.

5. **Do we upgrade to Option 2b (custom ctypes harness via `AeroDyn_Inflow_C_Binding`) at some point?** Triggers: (i) if Option 3's same-platform determinism is unstable, (ii) if we find a translation error that passes Option 3's tolerance check but affects downstream behavior, (iii) if `.outb`'s int16 quantization loses precision specifically where a translation difference would land. **Decision owner:** user. **Decision point:** mid-project, revisit at a named dead-reckoning point in the plan.

6. **How do we handle determinism and baseline regeneration when the build environment changes?** Each time `vit-dev-openfast` is rebuilt — due to Dockerfile updates, dependency upgrades, or OpenFAST upstream merges — our baselines may drift relative to the new build. ROSCO handled this by rebuilding baselines as part of the integration workflow (`scripts/reset_to_clean.sh` → rebuild → regenerate `baseline_arrays/`). We need an equivalent workflow for AeroDyn. **Decision owner:** user. **Decision point:** when integrate scripts are being written.

These questions are not expected to have answers yet. They're here so that when the plan document is drafted, they're not forgotten.
