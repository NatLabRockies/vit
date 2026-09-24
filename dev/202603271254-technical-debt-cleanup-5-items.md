# Technical Debt Cleanup: 5 Items From Dev Note Audit
**Date:** 2026-03-27
**Status:** completed
**Scope:** Source reset automation, K init fix, .kgen backup fragility, DLL persistence, FITPACK docs

## Origin

After unifying the assumed-size array handling (dev note `202603271039`), the user asked: "Is there anything else in the codebase like this? This is the type of redundancy/inefficiency I was looking for previously. Rather than looking through the codebase, read through the dev notes and project plan with an eye for catching inconsistencies or hacks/monkey patches that should be cleaned up or brought to a higher level of rigor."

A comprehensive audit of all 95+ dev notes identified 5 items matching the pattern of retroactive workarounds, manual procedures that should be automated, and open questions that were never resolved.

## The 5 Items

### Item 1: FlapControl K Init Fix — Applied Manually Every Test Run (FIXED)

**Problem:** Every source reset to clean Fortran required manually editing Controllers.f90 line 669 to wrap the PIIController call in a `DO K = 1,LocalVar%NumBl` loop. This was done 12+ times across sessions because `fde0498` (the Phase 4C Controllers.f90 commit) didn't include the fix. The fix existed in `dba7738` (used for Functions/Filters) but not in the Controllers commit.

**Fix:** Created commit `46e8d4f` on the ROSCO fork: `fde0498`'s Controllers.f90 + K init fix applied. All documentation updated to reference `46e8d4f` instead of `fde0498`. The manual "Apply FlapControl K init bug fix" section was removed from INTEGRATION_TEST.md entirely.

**Commit:** ROSCO `46e8d4f`

### Item 2: Source File Reset Automation (FIXED)

**Problem:** 6-step manual procedure (3 git checkouts to different commits, manual K init patch, C++ stub creation, artifact cleanup) documented in CLAUDE.md and INTEGRATION_TEST.md. Error-prone, repeated frequently, and the exact commit hashes had to be remembered.

**Fix:** Created `ROSCO/scripts/reset_to_clean.sh` — a single executable script that performs all reset steps. After Item 1 baked the K init fix into `46e8d4f`, the script is pure git checkouts + stubs with no manual patches. Includes verification output (grep for `_c(` references, all should be 0).

Updated CLAUDE.md Step 1 and INTEGRATION_TEST.md Step 1 to reference `bash scripts/reset_to_clean.sh` instead of raw commands.

**Commit:** ROSCO `9cef10c`

### Item 3: .kgen Backup Fragility (FIXED)

**Problem:** `.kgen` backups (clean Fortran snapshots used by `vit verify` to restore kernel files between runs) were created during the FIRST `vit verify`, not during `vit extract`. If `.kgen` files were deleted and re-created after VIT had already patched the kernel files, the backups would capture the patched (corrupt) state. All subsequent Fortran reference runs would use broken code. Identified in dev note `202603260840` (line 125-131) but never fixed.

**Fix:** Two changes:
1. `extract.py`: New `_create_kgen_backups()` function called immediately after successful KGen extraction, when source files are guaranteed clean. Creates `.f90.kgen` and `Makefile.kgen` backups at the only safe point in the pipeline.
2. `kernel_verify.py`: `_backup_or_restore_kgen_originals()` now ONLY restores from existing `.kgen` backups — it never creates new ones. If backups don't exist, it raises `FileNotFoundError` with a clear message: "Re-run 'vit extract' to create them."

**Commit:** VIT `5dc5a57`

### Item 4: DLL Persistence DEALLOCATE Question (ALREADY RESOLVED)

**Problem:** Dev note `202603252004` left an open question: "Could we remove the KGen DEALLOCATE entirely and rely solely on the `AlreadyInitialized` project reset?" The evidence suggested yes but it was never tested by removal.

**Finding:** Investigation revealed the DEALLOCATE code was ALREADY removed during the scope fix (dev note `202603252004`). The current `extract.py` only applies project-specific resets from `vit.yaml` (the `AlreadyInitialized` reset). The comment at line 438-442 explicitly documents why KGen's own SAVE arrays don't need resetting. All 28 functions have been extracting successfully without the DEALLOCATE for weeks.

**Action:** None needed. The question was answered by practice.

### Item 5: FITPACK Scrubber Documentation (DOCUMENTED)

**Problem:** The FITPACK stack scrubber monkey-patch in vit_sim.py is a permanent workaround for a scipy bug (uninitialized variable in FITPACK's `fpbisp`). It works but should be tracked for eventual removal when scipy fixes the upstream issue.

**Fix:** Added a comment to vit_sim.py noting the scipy version where the bug was confirmed (1.17.1) and the conditions for removal: "To test if a future scipy version fixes this, comment out the monkey-patch below and run Scenario 3 thirty times — all runs should produce the same gen_torque MD5."

**Commit:** ROSCO `2b222e3`

## Documentation Updates

- **CLAUDE.md** (VIT `929a366`): Updated `fde0498` → `46e8d4f`, replaced manual reset commands with `bash scripts/reset_to_clean.sh`, removed manual K init patch instructions
- **INTEGRATION_TEST.md** (ROSCO `fe97ea3`): Replaced 44-line manual Step 1 with 4-line script call

## End-to-End Verification

After all changes, ran the full verification regression against all 28 golden fixtures (3 batches in parallel):

```
=== Functions (9) ===
saturate: 40/40 passed
wrap_180: 40/40 passed
wrap_360: 40/40 passed
ratelimit: 40/40 passed
sigma: 40/40 passed
interp1d: 40/40 passed
ColemanTransform: 40/40 passed
ColemanTransformInverse: 40/40 passed
identity: 40/40 passed

=== Filters (6) ===
LPFilter: 40/40 passed
HPFilter: 40/40 passed
SecLPFilter: 40/40 passed
NotchFilter: 40/40 passed
NotchFilterSlopes: 40/40 passed
SecLPFilter_Vel: 40/40 passed

=== Controllers (13) ===
PIController: 40/40 passed
PIIController: 40/40 passed
ResController: 40/40 passed
ForeAftDamping: 40/40 passed
FloatingFeedback: 40/40 passed
StructuralControl: 40/40 passed
CableControl: 40/40 passed
FlapControl: 40/40 passed
YawRateControl: 40/40 passed
VariableSpeedControl: 40/40 passed
IPC: 40/40 passed
ActiveWakeControl: 40/40 passed
PitchControl: 40/40 passed
```

**28/28 functions, all 40/40 passed.** Plus 553 unit tests passing.

## Commits Summary

**VIT repo:**
| Hash | Description |
|------|-------------|
| `929a366` | Update CLAUDE.md: use reset_to_clean.sh, update fde0498 to 46e8d4f |
| `5dc5a57` | Fix .kgen backup fragility: create during extraction, not verification |

**ROSCO repo:**
| Hash | Description |
|------|-------------|
| `46e8d4f` | Apply FlapControl K init fix to Phase 4C Controllers.f90 |
| `9cef10c` | Add scripts/reset_to_clean.sh: automated source reset |
| `fe97ea3` | Simplify INTEGRATION_TEST.md Step 1: use reset_to_clean.sh |
| `2b222e3` | Document FITPACK scrubber scipy version and removal test procedure |
