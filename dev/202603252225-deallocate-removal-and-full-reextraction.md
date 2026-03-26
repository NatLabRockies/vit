# KGen DEALLOCATE Removal and Full Re-extraction
**Date:** 2026-03-25
**Status:** completed
**Scope:** VIT extraction, dll_persistence, golden kernel fixtures

## Background

Dev note `202603252004` documented the scope-guarded DEALLOCATE fix and raised an open question: is the KGen DEALLOCATE needed at all? This note documents the investigation that proved it vestigial, the removal, and the full 28-function re-extraction.

## Investigation: Is the DEALLOCATE Needed?

### Execution model analysis

The dll_persistence mechanism had two parts:
1. **Project resets** (`AlreadyInitialized = 0`) — inserted before `CALL ReadAvrSWAP`
2. **KGen DEALLOCATE** — reset KGen's SAVE arrays (`kgen_isstop`, `kgen_invoke`, etc.) on init calls

The DEALLOCATE was based on the claim: "Python's ctypes.CDLL and dlclose() don't truly unload Fortran shared libraries — SAVE variables persist across loads, so KGen's profiling phase counters persist into the state capture phase."

Investigation of the actual execution model revealed this claim is incorrect:

1. **KGen runs vit_sim.py TWICE** — once for elapsedtime (profiling), once for state capture. Each is a **separate subprocess** invoked by KGen's Makefile (`make` → `run_cmd`). SAVE variables have separate address spaces and cannot persist across processes.

2. **Within a single vit_sim.py run**, 5 scenarios run sequentially. Each creates a new `ControllerInterface` → `cdll.LoadLibrary()` → init call → simulation → `kill_discon()` → `dlclose()`.

3. **KGen state files use `STATUS="REPLACE"`** in `OPEN` — if the invocation counter resets, new files overwrite old ones with the same filename (e.g., `saturate.0.0.1`).

### The DEALLOCATE is never beneficial

Three scenarios were analyzed:

| dlclose() behavior | Without DEALLOCATE | With DEALLOCATE |
|---|---|---|
| Works (unloads) | Each scenario starts fresh, last scenario's files survive | Same (DEALLOCATE irrelevant — ALLOCATED guard fails on fresh load) |
| Doesn't work | Scenario 3 captures 1-20, subsequent counters exceed range, files survive | DEALLOCATE fires on each init, resets counters, later scenarios OVERWRITE Scenario 3's files |

**In every case, the DEALLOCATE is either irrelevant (dlclose works) or actively harmful (causes state file overwriting).** The original claim about cross-phase persistence was false — phases are separate processes.

### The AlreadyInitialized reset IS needed

When dlclose() doesn't unload, ROSCO's `AlreadyInitialized` SAVE variable persists across scenarios. Without the reset, `SetParameters` is skipped on subsequent scenarios → stale `CntrPar` → incorrect controller behavior. This project-specific reset must be kept.

### Diagnostic test

Removed the DEALLOCATE code entirely and extracted two DISCON-targeted functions:

| Function | Type | Scenarios | Result |
|----------|------|-----------|--------|
| PitchControl | DISCON-targeted, multi-scenario | All 5 | 20 cases, 0 contamination |
| CableControl | DISCON-targeted, --scenario 3 | Only 3 | 20 cases, 0 contamination |

Both succeed without the DEALLOCATE, confirming it is unnecessary.

## Changes Made

### `vit/vit/extract.py`
- Removed the entire `if init_cond:` block (lines 448-469) that generated the scope-guarded DEALLOCATE insertion
- Removed `init_condition` references from both Step 2c and Step 5
- Updated comments to explain why KGen DEALLOCATE is not needed
- Simplified Step 2c guard from `if init_cond or resets:` to `if resets:`
- Simplified Step 5 guard similarly

### `ROSCO/vit.yaml`
- Removed `init_condition: NINT(avrSWAP(1)) == 0` from `dll_persistence`
- Config now only has `resets` (project-specific SAVE variable resets)

### `vit/CLAUDE.md`
- Updated `dll_persistence` config example (removed `init_condition`)
- Updated `dll_persistence` description in key fields section
- Updated Known Limitations section to accurately describe the mechanism

## Full Re-extraction Results

Source prepped (DISCON.F90 at e8010f0, Controllers.f90 at fde0498, Filters.f90/Functions.f90 at dba7738, C++ stubs created). Clean Fortran build. All 28 functions extracted:

| # | Function | Module | Cases | Notes |
|---|----------|--------|-------|-------|
| 1 | saturate | Controllers.f90 | 20 | |
| 2 | wrap_180 | Filters.f90 | 20 | |
| 3 | wrap_360 | Controllers.f90 | 20 | |
| 4 | ratelimit | Controllers.f90 | 20 | |
| 5 | ColemanTransform | Controllers.f90 | 20 | |
| 6 | ColemanTransformInverse | Controllers.f90 | 20 | |
| 7 | identity | ControllerBlocks.f90 | 20 | |
| 8 | sigma | Controllers.f90 | 20 | |
| 9 | interp1d | Controllers.f90 | 20 | |
| 10 | LPFilter | Filters.f90 | 20 | |
| 11 | HPFilter | Filters.f90 | 20 | |
| 12 | SecLPFilter | Filters.f90 | 20 | |
| 13 | NotchFilter | Filters.f90 | 20 | |
| 14 | NotchFilterSlopes | Filters.f90 | **0** | Pre-existing: IPC_ControlMode=0 by default |
| 15 | SecLPFilter_Vel | Controllers.f90 | 20 | |
| 16 | PIController | Controllers.f90 | 20 | |
| 17 | PIIController | Controllers.f90 | **0** | Pre-existing: Flp_Mode=2 only in Scenario 4 |
| 18 | ResController | Controllers.f90 | 20 | |
| 19 | ForeAftDamping | Controllers.f90 | 20 | |
| 20 | IPC | Controllers.f90 | 20 | |
| 21 | FloatingFeedback | Controllers.f90 | 20 | --scenario 3 |
| 22 | ActiveWakeControl | Controllers.f90 | 20 | |
| 23 | VariableSpeedControl | DISCON.F90 | 20 | |
| 24 | PitchControl | DISCON.F90 | 20 | |
| 25 | YawRateControl | DISCON.F90 | 20 | |
| 26 | FlapControl | DISCON.F90 | 20 | --scenario 3 |
| 27 | CableControl | DISCON.F90 | 20 | --scenario 3 |
| 28 | StructuralControl | DISCON.F90 | 20 | --scenario 3 |

**26 of 28 extracted with 20 cases. 0 contamination across all. 0 build failures.**

### Two pre-existing 0-state-file functions

These never had golden fixtures (no `kernel_old/` directories exist for them):

1. **NotchFilterSlopes**: Called at Filters.f90:405, gated by `IF (IPC_ControlMode > 0 .OR. Flp_Mode == 3)`. The default `IPC_ControlMode` from the NREL 5MW tuning is 0, and no scenario sets `Flp_Mode=3`. Fix: add `IPC_ControlMode=1` to a scenario (but conflicts with `Flp_Mode > 0` — mutual exclusion in ROSCO).

2. **PIIController**: Called at Controllers.f90:682, inside `Flp_Mode == 2` branch. Only Scenario 4 has `Flp_Mode=2`, but the KGen invocation counter may not reach this call site within the capture range depending on DLL reload behavior. Needs investigation of whether `--scenario 4` would work.

## Verification Regression

All 25 translated functions verified (excluding NotchFilterSlopes/PIIController which have 0 state files):

- 25/25 functions: 20/20 passed
- 0 failures
- NotchFilterSlopes: FAILED (expected — 0 state files)

The 20/20 count (vs the usual 40/40) reflects running without Fortran reference comparison data. All C++ verification cases pass — the translations match KGen's captured state exactly.

## Commits

| Repo | Description |
|------|-------------|
| VIT | Remove vestigial KGen DEALLOCATE, simplify dll_persistence |
| ROSCO | Remove init_condition from vit.yaml, re-extracted golden fixtures |

## Related Dev Notes

- `202603252004-dll-persistence-scope-fix-and-reassessment.md` — scope fix + initial reassessment
- `202603251752-dll-persistence-contamination-fix.md` — tagged insertion fix
- `202603251411-avrswap-heap-overflow-fix.md` — avrSWAP size fix that triggered re-extraction
- `202603240952-dll-save-persistence-and-discon-extraction-completion.md` — original dll_persistence implementation
