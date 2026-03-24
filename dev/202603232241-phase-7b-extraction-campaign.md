# Phase 7B Extraction Campaign: Results, Line Number Bug, KGen DISCON.F90 Limitation
**Date:** 2026-03-23
**Status:** in-progress (2 of 9 functions extracted, 7 blocked)
**Scope:** Phase 7B batch extraction of main controller subroutines

## Starting State

Phase 4D complete. 19 functions verified. Plan: extract all 9 Phase 7B functions from clean source in one session, then translate them sequentially.

## Scenario Mode Setup

Three functions needed modes enabled in vit_sim.py Scenario 3:
- `Fl_Mode=1` — FloatingFeedback (nacelle velocity feedback)
- `Y_ControlMode=1` — YawRateControl (yaw rate control via yaw drive)
- `StC_Mode=1, StC_Group_N=1, StC_GroupIndex='2601'` — StructuralControl

All added alongside existing Scenario 3 modes (IPC_ControlMode=1, CC_Mode=1, TD_Mode=1). Baseline captured: 16,389 lines across 5 scenarios. All scenarios run successfully.

## Call Site Line Number Bug

The first batch extraction attempt produced 0 cases for all functions. Investigation revealed:

**Root cause:** Call site line numbers were taken from the INTEGRATED Controllers.f90 (which has VIT wrappers that shift line numbers). The extraction was running against CLEAN Controllers.f90 (fde0498, no wrappers). Line numbers don't match.

Example: IPC call site
- Integrated source: line 84
- Clean source: line 73

KGen error: `Can not find callsite` — because line 84 in the clean source is an ENDIF, not a CALL statement.

**Fix:** Used `grep -n` and `vit find` against the CLEAN source to get correct line numbers.

**Correct extraction table (clean source fde0498):**

| Function | Call site file | Line (clean) | Cases |
|----------|---------------|------|-------|
| IPC | Controllers.f90 | 73 | **20** ✓ |
| FloatingFeedback | Controllers.f90 | 96 | **0** ⚠ |
| ForeAftDamping | Controllers.f90 | 80 | **20** ✓ (existing) |
| ActiveWakeControl | Controllers.f90 | 144 | **20** ✓ |
| PitchControl | DISCON.F90 | 119 | **0** ✗ KGen parse error |
| VariableSpeedControl | DISCON.F90 | 117 | **0** ✗ KGen parse error |
| YawRateControl | DISCON.F90 | 123 | **0** ✗ KGen parse error |
| FlapControl | DISCON.F90 | 127 | **0** ✗ KGen parse error |
| CableControl | DISCON.F90 | 132 | **0** ✗ KGen parse error |
| StructuralControl | DISCON.F90 | 137 | **0** ✗ KGen parse error |

## KGen DISCON.F90 Parse Limitation

**The blocker for 6 functions:** KGen cannot parse DISCON.F90's dependency chain.

When extracting a function called from DISCON.F90, KGen traces the module USE chain to resolve the callee's signature. This chain passes through ExtControl.f90, which contains:

```fortran
PROCEDURE(BladedDLL_Legacy_Procedure), POINTER :: DLL_Legacy_Subroutine
```

KGen's Python 2.7 Fortran parser cannot handle `PROCEDURE(...), POINTER` declarations. It errors:

```
ERROR: 'Line' object has no attribute 'analyze'
```

This prevents KGen from resolving any function called from DISCON.F90. The error isn't in the target function — it's in a file KGen encounters while traversing USE dependencies.

**Functions affected:** PitchControl, VariableSpeedControl, YawRateControl, FlapControl, CableControl, StructuralControl — all called directly from DISCON.F90.

**Functions NOT affected:** IPC, FloatingFeedback, ActiveWakeControl, ForeAftDamping — all called from Controllers.f90 (PitchControl body), which doesn't trigger the ExtControl.f90 dependency chain.

**This is a known KGen limitation** with Fortran 2003+ features. The existing 19 successful extractions all use call sites in Controllers.f90, Filters.f90, or ControllerBlocks.f90 — never DISCON.F90.

## FloatingFeedback: 0 Cases Despite Correct Setup

FloatingFeedback extracted with 0 state files despite:
- Fl_Mode=1 in Scenario 3 DISCON_filters.IN (verified)
- Call site at Controllers.f90:96 (correct — inside PitchControl's `IF (Fl_Mode > 0)` block)
- PitchControl runs in every scenario (PC_ControlMode > 0)

Possible causes:
- The 1-DOF sim produces zero nacelle velocity/acceleration, so FloatingFeedback's PI controllers may reset every call (output is 0.0). KGen may treat all-zero state as "no data captured."
- KGen's invocation counter may not reach the Fl_Mode code path within the first 20 invocations of the PitchControl body (the pragma is on the FloatingFeedback call inside PitchControl, not on PitchControl itself)
- An earlier batch extraction's `model.ini` residue may have confused KGen's state capture

Needs further investigation.

## Options for DISCON.F90 Functions

### Option A: Temporary call sites in Controllers.f90

Add synthetic call sites (inside a never-executed IF block) to Controllers.f90 before extraction:

```fortran
! VIT extraction workaround — remove after extraction
IF (.FALSE.) THEN
    CALL PitchControl(avrSWAP, CntrPar, LocalVar, objInst, DebugVar, ErrVar)
END IF
```

KGen instruments the CALL statement. Even though the IF(.FALSE.) branch never executes, KGen's state capture happens at the call site in the calling function (which IS executed).

**Problem:** The IF(.FALSE.) means the call is never reached at runtime, so KGen captures 0 state files. This doesn't work.

### Option B: Call from a different .f90 file

Create a wrapper .f90 file that calls the target function. KGen instruments the wrapper's call site. The wrapper must actually be called during the simulation.

**Problem:** Requires modifying the build to include the wrapper, and the wrapper must be in the execution path.

### Option C: Fix KGen's ExtControl.f90 parsing

Modify ExtControl.f90 to remove or simplify the `PROCEDURE(...), POINTER` declaration before extraction. Restore after extraction.

**This is the simplest fix:** Comment out or stub the PROCEDURE POINTER declaration in ExtControl.f90 before extraction. KGen only needs to parse the module structure, not execute the DLL interface code. After extraction, restore the original file.

### Option D: Patch KGen's parser

Fix KGen's Python 2.7 Fortran parser to handle PROCEDURE POINTER declarations. This is the most robust but requires modifying third-party code.

### Recommendation: Option C

Temporarily modify ExtControl.f90 to comment out the problematic declaration before extraction. This is a per-extraction workaround that doesn't require any KGen changes, and the extraction restores source files automatically.

## Current State

**Successfully extracted (golden fixtures committed):**
- IPC: 20 cases (Controllers.f90:73)
- ActiveWakeControl: 20 cases (Controllers.f90:144)
- ForeAftDamping: 20 cases (Controllers.f90:80, from Phase 4D)

**Blocked by KGen DISCON.F90 parsing:**
- PitchControl, VariableSpeedControl, YawRateControl, FlapControl, CableControl, StructuralControl

**Needs investigation:**
- FloatingFeedback: 0 cases despite correct setup

**Ready for translation:**
- IPC (98 lines, all callees translated)
- ActiveWakeControl (170 lines, all callees translated)

## Next Steps

1. Investigate FloatingFeedback 0-case issue
2. Apply Option C (ExtControl.f90 workaround) for DISCON.F90 functions
3. Begin translating IPC (first ready function)
4. Continue with ActiveWakeControl
