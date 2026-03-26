# DLL Persistence Scope Fix and Mechanism Reassessment
**Date:** 2026-03-25
**Status:** completed (scope fix); open question (is DEALLOCATE needed at all?)
**Scope:** VIT extraction, dll_persistence build wrapper, KGen SAVE variable reset

## Starting Point

Dev note `202603251752` documented the tagged insertion fix (`!VIT_DLL_RESET`) for the source contamination bug — where sed patches accumulated in DISCON.F90 across sequential extractions because `set -e` caused the build wrapper to exit before the cleanup step. That fix (tagged insertions + `set +e`) resolved the contamination, but Filters.f90 and ControllerBlocks.f90 extractions still produced 0 state files.

## Root Cause: init_condition Scope Mismatch

### The Problem

The dll_persistence DEALLOCATE is guarded by `IF (NINT(avrSWAP(1)) == 0 .AND. ALLOCATED(kgen_isstop))`. This `init_condition` (from vit.yaml) references `avrSWAP`, which is a parameter of certain Fortran subroutines but not all.

KGen instruments the **enclosing subroutine** of the target function's call site. When the DEALLOCATE sed runs on ALL `.f90`/`.F90` files, it inserts the `NINT(avrSWAP(1))` expression into whichever file has `kgen_isstop`. If the enclosing subroutine doesn't have `avrSWAP` as a parameter, the expression causes a compilation error.

| Target function | Enclosing subroutine | Has avrSWAP? | Result |
|----------------|---------------------|-------------|--------|
| saturate | PitchControl (Controllers.f90) | YES | 20 state files |
| PIController | PitchControl (Controllers.f90) | YES | 20 state files |
| IPC | IPC (Controllers.f90) | YES | 20 state files |
| wrap_180 | PreFilterMeasuredSignals (Filters.f90) | **NO** | compile error → 0 state files |
| LPFilter | PreFilterMeasuredSignals (Filters.f90) | **NO** | compile error → 0 state files |
| identity | WindSpeedEstimator (ControllerBlocks.f90) | **NO** | compile error → 0 state files |

The `set +e` fix (from the contamination bug) silently swallowed the compilation error. The build failed with exit code 2 (gfortran: "Symbol 'kgen_vit_needs_reset' has no IMPLICIT type"), the install was skipped, the simulation loaded the old uninstrumented library, and 0 state files were captured.

### How We Found It

1. Observed that Controllers.f90 extractions (saturate, ratelimit, PIController) produced 20 state files, while Filters.f90 (wrap_180, LPFilter) and ControllerBlocks.f90 (identity) produced 0
2. Checked the enclosing subroutine signatures — PreFilterMeasuredSignals takes `(CntrPar, LocalVar, DebugVar, objInst, ErrVar)` with no `avrSWAP`; PitchControl takes `(avrSWAP, CntrPar, LocalVar, ...)`
3. Added build output logging to the wrapper script — confirmed `BUILD_RC=2` with error: `Symbol 'kgen_vit_needs_reset' at (1) has no IMPLICIT type`

### Why This Wasn't an Issue Before

The old golden fixtures were extracted weeks ago, before the dll_persistence DEALLOCATE was generalized to all files. The original implementation (dev note `202603240952`) placed the DEALLOCATE only in DISCON.F90 (anchored on `CALL ReadAvrSWAP`). It was later generalized to all KGen-instrumented files (anchored on `IF (.NOT. ALLOCATED(kgen_isstop))`), which introduced the scope mismatch.

## Failed Fix Attempts

### Attempt 1: Module-level flag in kgen_utils_mod

Injected `LOGICAL, SAVE :: kgen_vit_needs_reset = .FALSE.` into kgen_utils.f90 (which VIT already copies into the source tree). Project anchors would set the flag, and the DEALLOCATE would check it instead of `init_condition`.

**Failed because:** The project anchor inserts the flag-setting code into DISCON.F90 — which does NOT `USE kgen_utils_mod` (only KGen-instrumented files do). So `kgen_vit_needs_reset` was undefined in DISCON.F90 → same compilation error, just for a different variable.

### Attempt 2: COMMON block

Considered using a Fortran COMMON block (scope-independent, no USE needed). **Abandoned because:** COMMON declarations must be in the declaration section of a subroutine, but our sed inserts code at executable statement positions (before `CALL ReadAvrSWAP` and before `IF (.NOT. ALLOCATED(kgen_isstop))`). Can't mix declarations and executable statements in standard Fortran.

## Working Fix: Scope-Guarded Insertion

The DEALLOCATE is now only inserted into files where **both**:
1. A project anchor pattern exists (e.g., `CALL ReadAvrSWAP`)
2. `kgen_isstop` exists (KGen instrumented the file)

```bash
if grep -q "CALL ReadAvrSWAP" "$f90" && grep -q "kgen_isstop" "$f90"; then
    sed -i '/IF (.NOT. ALLOCATED(kgen_isstop))/i\
        IF (NINT(avrSWAP(1)) == 0 .AND. ALLOCATED(kgen_isstop)) THEN !VIT_DLL_RESET
        ...' "$f90"
fi
```

This ensures `init_condition` is only used in files where its variables are in scope — because the project anchor (e.g., `CALL ReadAvrSWAP`) only exists in the DLL entry point file (DISCON.F90), and the init_condition references variables available in that file's scope.

Files without the project anchor get NO DEALLOCATE. This is the key design question addressed below.

### Verification

Three sequential extractions covering all three affected modules:

| Function | Module | Has avrSWAP? | Cases | Contamination | Build RC |
|----------|--------|-------------|-------|---------------|----------|
| saturate | Controllers.f90 | YES (PitchControl) | 20 | 0 | 0 |
| wrap_180 | Filters.f90 | NO (PreFilterMeasuredSignals) | 20 | 0 | 0 |
| identity | ControllerBlocks.f90 | NO (WindSpeedEstimator) | 20 | 0 | 0 |

All three succeed with 20 cases, 0 contamination, and build exit code 0.

## Reassessment: Is the KGen DEALLOCATE Needed At All?

During this investigation, we examined the dll_persistence mechanism more carefully and discovered that some foundational assumptions may be incorrect.

### The Original Claim

Dev note `202603240952` states: "Python's ctypes.CDLL and dlclose() don't truly unload Fortran shared libraries. SAVE variables persist across loads."

### What We Actually Know

1. **Each extraction phase is a separate process.** KGen runs `python3 vit_sim.py` as a subprocess for both the elapsedtime (profiling) and state capture phases. Each is a separate Python process with its own address space. SAVE variables are initialized fresh in each process. The `dlclose()` claim is likely irrelevant for cross-process cases.

2. **Within a single `vit_sim.py` run, SAVE variables DO persist across scenarios.** This is just how SAVE works in a running program — not a ctypes bug. When vit_sim.py runs Scenario 3 then Scenario 1, the DLL stays loaded and SAVE variables carry over.

3. **The `AlreadyInitialized` reset IS needed.** Without it, `SetParameters` doesn't re-run between scenarios. CntrPar keeps Scenario 3's values when Scenario 1 starts. This can cause incorrect controller behavior or validation errors that prevent functions from being called.

4. **The KGen DEALLOCATE may NOT be needed.** KGen's invocation counter (`kgen_invoke`) persists between scenarios, but we WANT invocations 1-20 from Scenario 3 (which runs first in vit_sim.py). Later scenarios would be at 21+, outside the capture range. That's the desired behavior — no reset needed.

### Evidence Supporting DEALLOCATE Being Unnecessary

The scope fix just implemented proves this: for Filters.f90 and ControllerBlocks.f90 extractions, NO DEALLOCATE is inserted (the file doesn't have the project anchor). Yet they successfully capture 20 state files. If the DEALLOCATE were necessary for these functions, they would get 0 state files.

### What the DEALLOCATE Was Actually Protecting Against

The DEALLOCATE was designed for DISCON.F90-targeted extractions (VariableSpeedControl, PitchControl, YawRateControl, etc.). For these, DISCON.F90 IS the instrumented file, the anchor matches, and the DEALLOCATE fires.

But even for these: the invocation counter starts at 1 for Scenario 3 and increments correctly through 20. The DEALLOCATE fires on the init call (iStatus=0), but at that point kgen_isstop was JUST allocated (fresh process). `ALLOCATED(kgen_isstop)` is TRUE only after the first `IF (.NOT. ALLOCATED(kgen_isstop)) THEN ALLOCATE(...)` block runs. The DEALLOCATE's guard `init_cond .AND. ALLOCATED(kgen_isstop)` would only be TRUE if the DLL persists from a PREVIOUS load — which doesn't happen across processes.

So the DEALLOCATE fires, but as a no-op (kgen_isstop just allocated, nothing to reset). It's harmless but unnecessary.

### Open Question

Could we remove the KGen DEALLOCATE entirely and rely solely on the `AlreadyInitialized` project reset? The evidence suggests yes — but it hasn't been tested across all 28 functions. The conservative approach: keep the scope-guarded DEALLOCATE (it's harmless where it fires, absent where it would cause problems), but document that it may be vestigial.

## Commits

| Repo | Commit | Description |
|------|--------|-------------|
| VIT | `93bcffa` | Fix dll_persistence DEALLOCATE scope: only insert where init_condition is valid |

## What's Next

Full re-extraction of all 28 kernels with the corrected code (avrSWAP=3000, tagged insertions, scope-guarded DEALLOCATE). Source prep and rebuild already done. The 3-function test above confirms the extraction pipeline works for all three module types (Controllers.f90, Filters.f90, ControllerBlocks.f90).

## Related Dev Notes

- `202603251752-dll-persistence-contamination-fix.md` — tagged insertion fix (set +e, !VIT_DLL_RESET)
- `202603251411-avrswap-heap-overflow-fix.md` — avrSWAP 2000→3000 root cause
- `202603240952-dll-save-persistence-and-discon-extraction-completion.md` — original dll_persistence implementation
