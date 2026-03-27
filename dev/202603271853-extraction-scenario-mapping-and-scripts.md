# Extraction Scenario Mapping and Automation Scripts
**Date:** 2026-03-27
**Status:** completed
**Scope:** Extraction scenario assignments, extract_all.sh, verify_all.sh, CLAUDE.md cleanup

## Origin

During a full 28-function re-extraction, 5 functions failed with "0 cases captured" because they were extracted with `--scenario 3` but their call sites are gated by mode flags not active in Scenario 3. This exposed a broader issue: many functions were previously extracted without explicit scenario flags, running all 8 scenarios wastefully and relying on Scenario 3 running first to capture invocations 1-20. Additionally, 8 functions (YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl, IPC, ActiveWakeControl) receive zero inputs in Scenarios 1-3, so their kernel verification only proved plumbing (zero in → zero out), not arithmetic correctness.

## Per-Function Scenario Mapping

Each function now has an explicit `--run-args '--scenario N'` based on which scenario exercises its call site with non-zero inputs:

| Scenario | Functions | Reason |
|----------|-----------|--------|
| 2 | wrap_360 | `Y_ControlMode=2` (yaw-by-IPC branch) |
| 3 | saturate, wrap_180, ratelimit, identity, interp1d, LPFilter, HPFilter, SecLPFilter, NotchFilter, SecLPFilter_Vel, PIController, VariableSpeedControl, PitchControl | All mode flags enabled, all code paths reachable |
| 4 | PIIController | `Flp_Mode=2` (required for PIIController call at line 682) |
| 6 | NotchFilterSlopes | `IPC_ControlMode=1` (mutual exclusion with Flp_Mode > 0 in Scenario 3) |
| 7 | ForeAftDamping, FloatingFeedback, YawRateControl, FlapControl, CableControl, StructuralControl | Synthetic non-zero inputs for functions that receive zero inputs in Scenarios 1-3 |
| 8 | ColemanTransform, ColemanTransformInverse, sigma, ResController, IPC, ActiveWakeControl | Non-zero blade root moments, IPC gains, and AWC_Mode=4 |

### Why 5 Functions Failed with Scenario 3

- **wrap_360** (line 517): Inside `IF (Y_ControlMode == 2)` — Scenario 3 uses `Y_ControlMode=1`
- **ColemanTransform** (line 510): Inside IPC subroutine — Scenario 3 has `IPC_ControlMode=0` (mutual exclusion with `Flp_Mode > 0`)
- **ColemanTransformInverse** (line 561): Same — inside IPC
- **sigma** (line 528): Same — inside IPC
- **ResController** (line 813): Inside `IF (AWC_Mode == 4)` — Scenario 3 doesn't use AWC_Mode=4

## Automation Scripts

Created two scripts in `ROSCO/scripts/`:

### `extract_all.sh`
Extracts all 28 functions sequentially with the correct scenario for each. Cleans `model/`, `model.ini`, `kgen.log`, `elapsedtime/`, `state/` between extractions. Reports pass/fail summary.

### `verify_all.sh`
Verifies all 28 C++ translations against golden kernel fixtures. Reports pass/fail summary.

Usage:
```bash
docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/extract_all.sh"
docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/verify_all.sh"
```

## CLAUDE.md Updates

- Extraction parameters table: every function now has explicit `--run-args '--scenario N'`
- Regression testing section: replaced 28 individual `docker exec vit verify` commands with `bash scripts/verify_all.sh`
- Added `bash scripts/extract_all.sh` for full re-extraction
- Clean Build Test Steps 3-4: replaced inline command lists with script calls
- Updated determinism stress test to use `--scenario 3` and expect `20/20` (not `40/40`)
- Added note explaining scenario selection rationale

## Full Re-extraction Results

All 28 functions extracted successfully with correct scenarios. Case counts range from 20-23 (ElapsedTime selects a few additional invocations beyond the CLI 1-20 range). All `kgen_statefile.lst` files are deduplicated (case count = list entry count).
