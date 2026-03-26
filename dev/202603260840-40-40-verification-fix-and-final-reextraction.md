# 40/40 Verification Fix: Stale .kgen Backup Corruption and Final Re-extraction
**Date:** 2026-03-25
**Status:** completed
**Scope:** kernel verification, golden fixtures, .kgen backup/restore logic

## Background

After fixing the last two 0-state-file functions (dev note `202603252304`), NotchFilterSlopes and PIIController both verified 40/40 — but the other 26 functions still showed 20/20. The missing 20 cases per function are the Fortran reference run, which validates the original KGen Fortran by running it through the same kernel harness before the C++ run. Together, the two runs give 40/40 (20 Fortran reference + 20 C++ verification).

The 20/20 results were functionally correct — all C++ verification cases passed — but the absent Fortran reference run meant we weren't validating the kernel's Fortran baseline, which is a valuable sanity check.

## Investigation

### How the Fortran reference run works

In `kernel_verify.py`, the Fortran reference run (`_run_fortran_reference()`) is gated on several conditions:
1. A `.kgen` backup file must exist for the function's source file
2. The backup must not already contain VIT patches (checked for `_c(` references)
3. The kernel must build successfully with the restored original Fortran

The backup/restore logic in `_backup_or_restore_kgen_originals()` works as follows:
- **First verify run** (no `.kgen` files exist): backs up current `.f90` files as `.f90.kgen`
- **Subsequent verify runs** (`.kgen` files exist): restores `.f90` from `.kgen` backups

This ensures the Fortran reference run always uses the clean KGen-generated Fortran, not the VIT-patched version that has `_c()` function calls.

### Root cause: .kgen backups created from already-patched files

The corruption happened during the batch verification after the DEALLOCATE removal re-extraction (dev note `202603252225`). The sequence was:

1. All 28 functions were freshly extracted — kernel `.f90` files were clean KGen originals
2. Batch verification ran all 26 working functions sequentially
3. During each function's verify, `setup_kernel_cpp()` patches the `.f90` files (replacing Fortran function bodies with `_c()` calls)
4. On the **first** function, `_backup_or_restore_kgen_originals()` created `.kgen` backups from the clean originals — correct
5. But subsequent functions in the same kernel directory shared `.f90` files. By the time a later function was verified, the `.f90` had already been patched by an earlier function's verify run
6. When `.kgen` files were deleted during debugging and verify re-ran, new `.kgen` backups were created from the already-patched `.f90` files

The corrupted `.kgen` files were then committed to the ROSCO repo at `813d0fe`.

### Why NotchFilterSlopes and PIIController worked

These two functions were extracted fresh in a separate step (after the batch), into their own kernel directories. Their `.f90` files had never been VIT-patched, so the first verify created correct `.kgen` backups. Result: 40/40.

### Diagnosis steps

1. Checked `.kgen` file contents — found `_c(` references (VIT wrapper calls) in `Functions.f90.kgen` for the saturate kernel
2. Verified the `.kgen` was identical to the `.f90` — both VIT-patched, confirming the backup was created from an already-patched file
3. Deleted `.kgen` files and re-ran verify — still 20/20, because the `.f90` files themselves were already patched from the previous verify
4. Confirmed the committed kernel state also had this corruption (`git checkout HEAD -- kernel/` still showed `_c(` in `.kgen`)

## Fix

The only reliable fix was a full re-extraction of all 28 functions. This produces clean KGen-generated `.f90` files with zero VIT patches:

### Source preparation (Mac)

```bash
cd vit_translation/ROSCO
git checkout dba7738 -- rosco/controller/src/Functions.f90 rosco/controller/src/Filters.f90
git checkout fde0498 -- rosco/controller/src/Controllers.f90 rosco/controller/src/ControllerBlocks.f90
git checkout e8010f0 -- rosco/controller/src/DISCON.F90
for f in rosco/controller/src/*_cpp.cpp; do echo "// stub" > "$f"; done
```

For PIIController, the K init bug fix was manually applied to `fde0498`'s Controllers.f90 (wrapping line 669 in `DO K = 1,LocalVar%NumBl ... END DO`).

### Extraction (container)

All 28 functions extracted sequentially. Each produced 20 state files:

| Batch | Functions | Notes |
|-------|-----------|-------|
| Functions + Filters + early Controllers | saturate, wrap_180, wrap_360, ratelimit, ColemanTransform, ColemanTransformInverse, identity, sigma, interp1d | Standard extraction |
| Filters | LPFilter, HPFilter, SecLPFilter, NotchFilter, SecLPFilter_Vel | Standard extraction |
| Controllers (scenario-specific) | NotchFilterSlopes (`--scenario 6`), PIIController (`--scenario 4`), FloatingFeedback (`--scenario 3`) | Scenario-gated functions |
| Controllers (standard) | PIController, ResController, ForeAftDamping, IPC, ActiveWakeControl | Standard extraction |
| DISCON-targeted | VariableSpeedControl, PitchControl, YawRateControl | Standard extraction |
| DISCON-targeted (scenario 3) | FlapControl, CableControl, StructuralControl | `--run-args '--scenario 3'` |

### Verification

All 28 functions verified in a single batch. Each function's first verify created correct `.kgen` backups from the clean KGen originals.

**Result: 28/28 functions at 40/40 passed.**

```
saturate                  40/40 passed
wrap_180                  40/40 passed
wrap_360                  40/40 passed
ratelimit                 40/40 passed
ColemanTransform          40/40 passed
ColemanTransformInverse   40/40 passed
identity                  40/40 passed
sigma                     40/40 passed
interp1d                  40/40 passed
LPFilter                  40/40 passed
HPFilter                  40/40 passed
SecLPFilter               40/40 passed
NotchFilter               40/40 passed
NotchFilterSlopes         40/40 passed
SecLPFilter_Vel           40/40 passed
PIController              40/40 passed
PIIController             40/40 passed
ResController             40/40 passed
ForeAftDamping            40/40 passed
IPC                       40/40 passed
FloatingFeedback          40/40 passed
ActiveWakeControl         40/40 passed
VariableSpeedControl      40/40 passed
PitchControl              40/40 passed
YawRateControl            40/40 passed
FlapControl               40/40 passed
CableControl              40/40 passed
StructuralControl         40/40 passed
```

## Commit

| Repo | Commit | Description |
|------|--------|-------------|
| ROSCO | `8154ea8` | Re-extract all 28 kernels: clean KGen state for 40/40 verification |

1006 files changed — reflects the full replacement of all kernel directories with clean state.

## Architectural Insight: .kgen Backup Fragility

The `.kgen` backup mechanism has an inherent fragility: it assumes the `.f90` files are in their original KGen state when the first verify runs. If any VIT patching has occurred before the `.kgen` is created, the backup captures the patched state, and all subsequent Fortran reference runs fail.

This is safe when the workflow is: extract → verify (creates .kgen) → re-verify (restores from .kgen). It breaks when: extract → verify → delete .kgen → re-verify (creates .kgen from patched .f90).

A potential future fix would be to create `.kgen` backups during extraction (when files are guaranteed clean) rather than during the first verify. This would make the backup idempotent regardless of verify history. However, for now the clean state is committed and the current logic works correctly as long as `.kgen` files are not manually deleted.

## Session Summary

This session accomplished three things:

1. **NotchFilterSlopes fix** — Added Scenario 6 (IPC_ControlMode=1, Flp_Mode=0) to bypass ROSCO's IPC/Flp mutual exclusion. Extracted with `--run-args '--scenario 6'`. Verified 40/40.

2. **PIIController fix** — Extracted with `--run-args '--scenario 4'` so Flp_Mode=2 invocations fall within KGen's capture range. Manually applied K init bug fix from `dba7738` onto `fde0498`. Verified 40/40.

3. **40/40 fix** — Diagnosed stale `.kgen` backups containing VIT-patched code. Full re-extraction of all 28 functions from clean source, followed by batch verify. All 28 at 40/40. Clean state committed.

The project now has complete golden fixtures: 28 functions, 20 state files each, all verified 40/40 (Fortran reference + C++ verification), committed with clean KGen originals and correct `.kgen` backups.

## Related Dev Notes

- `202603252304-notchfilterslopes-piicontroller-extraction-fix.md` — NotchFilterSlopes and PIIController scenario coverage
- `202603252225-deallocate-removal-and-full-reextraction.md` — DEALLOCATE removal and initial re-extraction (found the 2 failures)
- `202603252004-dll-persistence-scope-fix-and-reassessment.md` — dll_persistence scope fix
- `202603251411-avrswap-heap-overflow-fix.md` — avrSWAP size fix that triggered the re-extraction chain
