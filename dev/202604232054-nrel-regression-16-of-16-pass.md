# NREL Regression 16/16 PASS — Build Correctness Confirmed
**Date:** 2026-04-23
**Status:** completed
**Scope:** Definitive build-correctness verification via NREL's own regression test; wrapper script for repeatable 16/16 execution

## Summary

Our ARM64 + OpenBLAS build passes **NREL's own `executeAerodynRegressionCase.py` at NREL's own tolerance (`rtol=2`, `atol=1.9` → 1e-2 relative) for all 16 AeroDyn r-test cases**. A wrapper script (`scripts/run_nrel_regression.sh`) handles shared-data setup and produces a clean 16/16 PASS in a single command with no prerequisites.

This closes the "is our build correct?" question that drove the platform drift investigation (dev notes `202604210915`, `202604212346`). The answer is: **yes, by NREL's own standards.**

## What was the problem

After generating platform-specific baselines for 16 AeroDyn test cases (Phase 2, dev note `202604210845`) and characterizing the drift vs NREL's shipped references, the user raised a valid concern: we were labeling the differences "platform drift" without proof that our build was actually producing correct results. The investigation that followed (NREL CI configuration analysis, time-series drift profiling, two NREL-matching container builds, Rosetta FP behavior discovery) characterized the causes comprehensively — but never ran the one test that NREL themselves use to gate correctness.

The definitive test was always available: run NREL's `executeAerodynRegressionCase.py` at NREL's own tolerance (`rtol=2`, `atol=1.9`). This is the exact test NREL's CI runs on every pull request. If it passes, the build is correct by NREL's definition. If it fails, something is wrong.

## What happened when we first tried

### Attempt 1: wrong binary architecture (all 16 fail with rc=50)

The first run returned rc=50 for every case. This was not a tolerance failure — rc=50 means the AeroDyn driver itself returned exit code 5 (`returnCode*10` in the Python script). Root cause: the shared `build/` directory (mounted by all three containers) contained an **x86_64 binary** from the previous NREL-matching container build, but we were running in the **ARM64 container**. Wrong-architecture binary.

**Fix:** always rebuild after switching containers. `cd build && rm -rf * && cmake ... && make aerodyn_driver`. The shared mount means any container's build overwrites the previous container's artifacts.

**Lesson:** document the shared-build-directory gotcha. When multiple containers mount the same workspace, the build artifacts belong to whichever container built last. Switching containers without rebuilding produces confusing failures.

### Attempt 2: ARM64 rebuild, 15/16 pass

After rebuilding the ARM64 binary, 15 of 16 cases passed. The one failure was `ad_timeseries_shutdown` with rc=10 (driver exit code 1). The driver crashed because it couldn't find `../../../glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat` — the same path-resolution issue documented in Phase 2.

NREL's `executeAerodynRegressionCase.py` copies test inputs to a scratch build directory before running. The script copies `BAR_Baseline/` (used by BAR-family cases) but does NOT copy `5MW_Baseline/` (used by `ad_timeseries_shutdown`). From the scratch directory at `build/modules/aerodyn/ad_timeseries_shutdown/`, the relative path `../../../glue-codes/` resolves to `build/glue-codes/` — which doesn't exist.

In NREL's own CI, this works because they build the full OpenFAST tree (including glue-codes), so the path exists naturally. We only build AeroDyn_Driver, so only `build/modules/aerodyn/` exists.

Our own scripts (`generate_aerodyn_baseline.sh`, `verify_aerodyn_baselines.sh`) handle this with an explicit copy step. NREL's script doesn't have it.

### Attempt 3: x86_64 NREL-matching build, same 15/16

We also ran the test from the NREL-matching x86_64 (Rosetta) container with ATLAS + gfortran-14 + RelWithDebInfo + OpenMP. Same result: 15/16 pass, same `ad_timeseries_shutdown` path failure. This confirmed that the path issue is script-level, not platform-level.

## The wrapper script solution

Rather than patching NREL's script (which would diverge from upstream), we wrote `scripts/run_nrel_regression.sh` — a thin wrapper that:

1. **Sets up shared data directories** before running. Copies `BAR_Baseline/` and `5MW_Baseline/` from the r-test submodule to the build tree at the paths the relative references expect. Both copies are idempotent (skipped if already present).

2. **Resolves the case list.** `all` auto-discovers every `ad_*/` directory in the r-test aerodyn module tree that has an `ad_driver.dvr` file, excluding `ad_Sphere_OLAF` (VTK comparison, not `.outb`).

3. **Runs NREL's script** for each case with configurable tolerance. Defaults to NREL's CI values (`rtol=2`, `atol=1.9`). Captures output and shows relevant error lines on failure.

4. **Reports pass/fail** with a summary line. Exits 0 if all pass, 1 if any fail.

### Usage

```bash
# NREL's own tolerance (1e-2 relative) — the definitive "is our build correct" test:
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && ./scripts/run_nrel_regression.sh all"

# Stricter tolerance (1e-5 relative) — for investigating platform drift:
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && ./scripts/run_nrel_regression.sh all 5 5"

# Single case:
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && ./scripts/run_nrel_regression.sh ad_BAR_SineMotion"
```

### Result

```
Running NREL AeroDyn regression test (rtol=2 → 1e-2, atol=1.9 → ~1e-1.9)
Cases: 16

✓ ad_B1n2_OLAF
✓ ad_BAR_CombinedCases
✓ ad_BAR_OLAF
✓ ad_BAR_RNAMotion
✓ ad_BAR_SineMotion
✓ ad_BAR_SineMotion_UA4_DBEMT3
✓ ad_EllipticalWingInf_OLAF
✓ ad_HelicalWakeInf_OLAF
✓ ad_Kite_OLAF
✓ ad_MHK_RM1_Fixed
✓ ad_MHK_RM1_Fixed_IfW
✓ ad_MHK_RM1_Floating
✓ ad_MultipleHAWT
✓ ad_QuadRotor_OLAF
✓ ad_VerticalAxis_OLAF
✓ ad_timeseries_shutdown

NREL regression: 16/16 passed (rtol=2, atol=1.9)
ALL CASES PASS
```

## The complete verification infrastructure (final state)

The AeroDyn verification infrastructure now has three tiers:

### Tier 1: Platform-specific baselines (bit-identical gate)

```bash
./scripts/verify_aerodyn_baselines.sh all    # 16/16 PASS
```

Compares our build's output against our own committed baselines. Primary gate for catching translation regressions. Bit-identical comparison (via `cmp -s` with `fast_io` fallback for header-only diffs). Same platform, same BLAS, same compiler → byte-identical output guaranteed by determinism.

### Tier 2: NREL regression test (correctness gate)

```bash
./scripts/run_nrel_regression.sh all          # 16/16 PASS
```

Compares our build's output against NREL's shipped r-test references at NREL's own 1e-2 tolerance. Proves our build is correct by NREL's standards. Catches catastrophic regressions (wrong algorithms, broken physics) that would show up as >1% errors.

### Tier 3: NREL drift reporter (informational)

```bash
./scripts/check_aerodyn_nrel_drift.sh all
```

Detailed per-channel drift summary against NREL's references. Not a gate — informational only. Tracks cross-platform drift over time. Useful for diagnosing specific channels when a Tier 1 or Tier 2 failure occurs.

### Summary of scripts

| Script | Purpose | Gate? | Pass criterion | Current result |
|--------|---------|-------|----------------|----------------|
| `verify_aerodyn_baselines.sh` | Same-platform regression | **YES** | Bit-identical (data) | 16/16 PASS |
| `run_nrel_regression.sh` | NREL correctness | **YES** | 1e-2 relative | 16/16 PASS |
| `check_aerodyn_nrel_drift.sh` | Cross-platform drift | No | Informational | N/A (reports numbers) |
| `generate_aerodyn_baseline.sh` | Baseline creation | No | N/A (generates) | 16 baselines committed |

## Also fixed during this work

**Verifier 5MW path bug:** `verify_aerodyn_baselines.sh` used `${RTEST_CASES_ROOT}/../../../glue-codes/openfast/5MW_Baseline` as the source path, which didn't resolve correctly inside the container. Changed to absolute path `${OPENFAST_ROOT}/reg_tests/r-test/glue-codes/openfast/5MW_Baseline` matching the generator script's pattern. Committed at `1c57bd2ba` on the OpenFAST fork.

## What this means for the AeroDyn translation effort

The verification infrastructure is complete and proven. When we begin translating AeroDyn functions to C++:

1. **Each translated function** gets KGen kernel verification at float64 (Tier 0 — same as ROSCO, not part of this infrastructure but will use the same `vit verify` pipeline).

2. **After integrating a translation**, run `verify_aerodyn_baselines.sh all` (Tier 1). If any case fails bit-identity against our baseline, the translation introduced a regression. Investigate the failing case's drift report.

3. **Periodically**, run `run_nrel_regression.sh all` (Tier 2) to confirm we haven't drifted beyond NREL's tolerance. This is especially important after integrating many translations — accumulated small changes might individually pass Tier 1 (same as our new baseline) but collectively push us past NREL's 1e-2 gate.

4. **When baselines need regeneration** (after environment changes, OpenFAST upstream merges, or deliberate rebaselining after verified translations), use `generate_aerodyn_baseline.sh` and commit the new baselines. Then verify both Tier 1 and Tier 2 pass.

## Related dev notes

- `202604212346-nrel-matching-attempt-and-findings.md` — the full NREL-matching investigation (infrastructure, Rosetta findings, toolchain mismatch discovery, definitive test results)
- `202604210915-platform-drift-root-cause.md` — root cause analysis (ATLAS vs OpenBLAS, time-series drift profiles)
- `202604210845-aerodyn-baseline-phase2-complete.md` — Phase 2 all 16 baselines
- `202604140330-aerodyn-baseline-infrastructure.md` — Phase 1 infrastructure
- `202604140253-outb-format-and-verification-strategy.md` — original `.outb` format analysis

## Related commits

- OpenFAST fork `f32edeb21` — `run_nrel_regression.sh` wrapper script
- OpenFAST fork `1c57bd2ba` — verifier 5MW path fix + definitive test documentation
- OpenFAST fork `a61447f10` — Phase 3 conclusion (ARM64 baselines confirmed correct)
- VIT repo `ac7d8e8` — dev note update with definitive test results
- VIT repo `c6aef75` — rtest-matching Dockerfile + Rosetta conclusion
