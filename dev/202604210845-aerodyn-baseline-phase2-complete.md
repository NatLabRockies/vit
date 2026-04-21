# AeroDyn Baseline Phase 2: All 16 Cases Baselined
**Date:** 2026-04-21
**Status:** completed
**Scope:** Extend platform-specific baselines from 1 case (Phase 1) to all 16 non-Sphere AeroDyn r-test cases

## Context

Phase 1 (dev note `202604140330-aerodyn-baseline-infrastructure.md`) proved the baseline infrastructure on `ad_BAR_SineMotion`: determinism confirmed, generator/verifier/drift-checker scripts working, one baseline committed to the OpenFAST fork at `2f4b5aa95`. Phase 2 was flagged as "extend to the remaining 16 cases" and tracked via checkboxes in `baselines/README.md`.

This dev note records the Phase 2 execution: all 16 non-Sphere cases are now baselined and verified, with three script improvements discovered and applied during the run.

## Approach

Cases were categorized into three groups in `baselines/README.md`:

- **Group A (12 cases):** drop-in compatible with existing scripts — standard `ad_driver.outb` output, AT=1, NT=1
- **Group B (3 cases):** need output-variant detection — `ad_BAR_CombinedCases` (AT=3 → `.4.outb`), `ad_MultipleHAWT` (NT=2 → `.T2.outb`), `ad_QuadRotor_OLAF` (NT=5 → `.T2.outb`)
- **Group C (1 case):** skip — `ad_Sphere_OLAF` (NT=0, no `.outb`, VTK-based Cp comparison)

Execution order per the plan in `baselines/README.md`:
1. OLAF determinism check (gate for all OLAF cases)
2. Group A cases one at a time
3. Script updates for Group B
4. Group B cases
5. Final `verify_aerodyn_baselines.sh all`
6. Commit

## OLAF determinism check

Before trusting OLAF baselines, ran `ad_B1n2_OLAF` (simplest OLAF case: single blade, 2 nodes) twice in `vit-dev-openfast` with identical inputs.

**Result: DETERMINISTIC.** Both runs produced bit-identical 6105-byte `.outb` files. This confirms that the Free Vortex Wake solver on our platform (OpenBLAS + gfortran 13.3.0 + ARM64) is deterministic at the same level as BEMT was in Phase 1. OpenBLAS multithreading does not introduce visible nondeterminism for either BEMT or OLAF test cases.

## Group A execution (12 cases)

Ran each case through `generate_aerodyn_baseline.sh`. Results:

| Case | NREL drift summary | Notes |
|------|-------------------|-------|
| `ad_BAR_RNAMotion` | 97.95% within tol, 65 drifting ch, max abs 3.33 | RNA motion exercises more of the aero solver than SineMotion |
| `ad_BAR_SineMotion_UA4_DBEMT3` | 99.93% within tol, 9 drifting ch, max abs 0.42 | Very similar to Phase 1 SineMotion. UA4+DBEMT3 doesn't add drift |
| `ad_timeseries_shutdown` | 94.37% within tol, 5 drifting ch, max abs 0.026 | **Required script fix** (see below). Large dataset: 198K values |
| `ad_MHK_RM1_Fixed` | **100% bit-identical vs NREL** | Zero drift. Our build matches NREL exactly for this case |
| `ad_MHK_RM1_Fixed_IfW` | 99.96% within tol, 11 drifting ch, max abs 1.90 | InflowWind introduces drift that the no-InflowWind case doesn't have |
| `ad_MHK_RM1_Floating` | **100% bit-identical vs NREL** | Zero drift. Another perfect platform match |
| `ad_B1n2_OLAF` | 100% within tol, 4 ch, max abs 2.9e-7 | Tiny drift, essentially negligible |
| `ad_BAR_OLAF` | 98.68% within tol, **215 drifting ch, max abs 1749** | Substantial OLAF drift vs NREL. FVW is much more sensitive to platform FP differences than BEMT. Expected. |
| `ad_EllipticalWingInf_OLAF` | 100% within tol, 26 ch, max abs 7.6e-9 | Essentially negligible drift |
| `ad_HelicalWakeInf_OLAF` | 99.84% within tol, 48 ch, max abs 0.024 | Moderate OLAF drift |
| `ad_Kite_OLAF` | 99.27% within tol, 197 ch, max abs 0.028 | Moderate-high OLAF drift |
| `ad_VerticalAxis_OLAF` | **25.75% within tol**, 111/129 ch, max abs 0.031 | Highest drift case. Vertical-axis OLAF is extremely sensitive to cross-platform FP differences |

**One failure during Group A:** `ad_timeseries_shutdown` failed on first attempt because its AeroDyn primary input references `../../../glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat` — a path that climbs out of the aerodyn r-test tree into the `glue-codes/` directory. When inputs are copied to a scratch build dir, this relative path no longer resolves.

**Fix:** added 5MW_Baseline shared-data copy to `generate_aerodyn_baseline.sh` (and `verify_aerodyn_baselines.sh`), same pattern as BAR_Baseline:
```bash
FMW_BASELINE_SRC="${OPENFAST_ROOT}/reg_tests/r-test/glue-codes/openfast/5MW_Baseline"
FMW_BASELINE_DST="${OPENFAST_ROOT}/build/glue-codes/openfast/5MW_Baseline"
if [[ -d "$FMW_BASELINE_SRC" ]] && [[ ! -d "$FMW_BASELINE_DST" ]]; then
    mkdir -p "$(dirname "$FMW_BASELINE_DST")"
    cp -a "$FMW_BASELINE_SRC" "$FMW_BASELINE_DST"
fi
```
After the fix, `ad_timeseries_shutdown` generated successfully.

## Group B execution (3 cases)

**Script updates required:** Group B cases don't have `ad_driver.outb` in their r-test directories — they only have variant output files (`.T2.outb` for multi-turbine, `.4.outb` for combined cases). Three changes to the scripts:

1. **Precondition check relaxed** — changed from "check for `ad_driver.outb` specifically" to "check for any `ad_driver*.outb`"
2. **Rename logic updated** — renamed all `.outb` files in the scratch dir (not just `ad_driver.outb`) to `*_nrel_ref.outb` variants
3. **Output-variant detection** — after driver run, checks for `.T2.outb` and `.4.outb` in both generator and verifier, falling back to `.outb` if neither exists. Matches the detection logic in NREL's `executeAerodynRegressionCase.py` (lines 107-113)

Results:

| Case | NREL drift summary | Notes |
|------|-------------------|-------|
| `ad_BAR_CombinedCases` | 99.99% within tol, 16 ch, max abs 0.072 | AT=3, output: `.4.outb`. Low drift. |
| `ad_MultipleHAWT` | 100% within tol, 1 ch, max abs 3.3e-10 | NT=2, output: `.T2.outb`. Essentially bit-identical vs NREL. |
| `ad_QuadRotor_OLAF` | 99.48% within tol, 25 ch, max abs 573 | NT=5, OLAF. Substantial drift on the quad-rotor vortex wake. |

## Header-only binary diff discovery

Initial `verify_aerodyn_baselines.sh all` showed 14 passes and 2 failures: `ad_BAR_CombinedCases` and `ad_MultipleHAWT` failed `cmp -s` despite the `fast_io` diagnostic showing **100% bit-identical data** (0 differing values out of 21930 and 1278 respectively).

**Root cause:** the `.outb` binary header embeds metadata that varies between the generator's scratch directory name (`baseline_gen_*`) and the verifier's scratch directory name (`verify_*`). The header length differs by 1 byte for `ad_BAR_CombinedCases` (50682 vs 50681) and 15 bytes for `ad_MultipleHAWT` (16342 vs 16327). This only affects Group B cases — Group A cases produce identical headers because their `.outb` format doesn't embed the varying path metadata.

**Fix:** updated `verify_aerodyn_baselines.sh` with a header-tolerant comparison path. When `cmp -s` fails, the verifier now:
1. Parses both files with `fast_io.load_output()` (from `reg_tests/lib/`)
2. Checks `np.array_equal(ours, ref)` on the resulting numpy arrays
3. If data is identical → prints `✓ <case>: data identical vs our baseline (header-only binary diff)` and counts as PASS
4. If data differs → prints the diagnostic (shape, bit-identical count, max abs/rel, top drifting channels) and counts as FAIL

After the fix, all 16 cases pass:
- 14 cases: `✓ bit-identical vs our baseline`
- 2 cases: `✓ data identical vs our baseline (header-only binary diff)`

## Final verification

```
✓ ad_B1n2_OLAF: bit-identical vs our baseline (6105 bytes)
✓ ad_BAR_CombinedCases: data identical vs our baseline (header-only binary diff, 50682 vs 50681 bytes)
✓ ad_BAR_OLAF: bit-identical vs our baseline (82561 bytes)
✓ ad_BAR_RNAMotion: bit-identical vs our baseline (28481 bytes)
✓ ad_BAR_SineMotion: bit-identical vs our baseline (21849 bytes)
✓ ad_BAR_SineMotion_UA4_DBEMT3: bit-identical vs our baseline (51585 bytes)
✓ ad_EllipticalWingInf_OLAF: bit-identical vs our baseline (16453 bytes)
✓ ad_HelicalWakeInf_OLAF: bit-identical vs our baseline (41345 bytes)
✓ ad_Kite_OLAF: bit-identical vs our baseline (57201 bytes)
✓ ad_MHK_RM1_Fixed: bit-identical vs our baseline (65365 bytes)
✓ ad_MHK_RM1_Fixed_IfW: bit-identical vs our baseline (67825 bytes)
✓ ad_MHK_RM1_Floating: bit-identical vs our baseline (65365 bytes)
✓ ad_MultipleHAWT: data identical vs our baseline (header-only binary diff, 16342 vs 16327 bytes)
✓ ad_QuadRotor_OLAF: bit-identical vs our baseline (11201 bytes)
✓ ad_VerticalAxis_OLAF: bit-identical vs our baseline (13745 bytes)
✓ ad_timeseries_shutdown: bit-identical vs our baseline (378805 bytes)

Summary: 16 passed, 0 failed
```

## Interesting platform-drift observations

Two cases stood out as data points for understanding cross-platform FP behavior:

**MHK cases are bit-identical to NREL.** `ad_MHK_RM1_Fixed` and `ad_MHK_RM1_Floating` produce output with zero drift from NREL's reference — every value matches exactly. This is likely because the MHK BEMT solver exercises a simpler code path (no tower shadow, no nacelle, different geometry that avoids the specific BLAS/FMA operations where our platform diverges from NREL's). It's a useful data point: not ALL AeroDyn output diverges cross-platform. The divergence is concentrated in specific code paths.

**Vertical-axis OLAF has extreme drift.** `ad_VerticalAxis_OLAF` shows only 25.75% of values within `1e-5` tolerance vs NREL — by far the worst case. The absolute diffs are small (max 0.031) but the relative diffs are large (max 7.7) because many channels have very small magnitudes. Vertical-axis OLAF exercises a fundamentally different vortex geometry (blades sweeping through their own wake at every revolution) which amplifies FP differences through the iterative wake solver. This is informational only — our own baseline is deterministic and passes bit-identical.

## Commit record

**OpenFAST fork commit:** `69ca17ddf` on `main`
- 33 files changed, 1139 insertions, 36 deletions
- 15 new baseline directories (each with `.baseline.outb` + `drift_vs_nrel.txt`)
- 3 script files updated (`generate_aerodyn_baseline.sh`, `verify_aerodyn_baselines.sh`, `check_aerodyn_nrel_drift.sh` inherited the 5MW path fix)
- `baselines/README.md` updated with all checkboxes checked and final progress summary

## What this unblocks

The baseline infrastructure is now complete for AeroDyn. When we begin translating AeroDyn functions:
- **Tier 1 (per-function):** KGen kernel verification at float64 — unchanged from ROSCO
- **Tier 2 (simulation-level):** `verify_aerodyn_baselines.sh all` — run all 16 r-test cases, compare bit-identically against committed baselines. Any translation-introduced regression will show as a `cmp` failure (or a data-differs failure via `fast_io` fallback)
- **Informational:** `check_aerodyn_nrel_drift.sh all` — cross-platform drift signal, not a gate

The 16 r-test cases exercise BEMT, DBEMT, UA, OLAF/FVW, MHK, multi-turbine, combined-cases, time-series, RNA-motion, vertical-axis, and kite configurations. This is broad enough coverage that a regression in any major AeroDyn code path would be caught by at least one case.

## Related dev notes

- `202604140330-aerodyn-baseline-infrastructure.md` — Phase 1 (infrastructure + first baseline)
- `202604140253-outb-format-and-verification-strategy.md` — `.outb` format analysis and the strategy decision that motivated this infrastructure
- `202604140241-openfast-build-aerodyn-baseline.md` — original OpenFAST build + driver smoke test
