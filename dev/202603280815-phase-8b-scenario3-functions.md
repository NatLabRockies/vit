# Phase 8B Progress: Four Scenario 3 ControllerBlocks Functions
**Date:** 2026-03-28
**Status:** in-progress
**Scope:** Phase 8B — first four of seven ControllerBlocks functions (Scenario 3 subset)

## What Was Done

Extracted, translated, and verified four ControllerBlocks functions that are called unconditionally from DISCON.F90 (Scenario 3):

| Function | Lines | Cases | Callees | Notes |
|----------|-------|-------|---------|-------|
| StateMachine | 195-265 | 21/21 | None | Pure conditional logic, 2 args only (CntrPar, LocalVar). Uses integer constants from Constants.f90 for state IDs. |
| SetpointSmoother | 490-517 | 21/21 | LPFilter | Simple: one LPFilter call gated by SS_Mode. Initial verification failed due to LPFilter OPTIONAL param signature mismatch — see lesson below. |
| PowerControlSetpoints | 27-83 | 22/22 | interp1d | Three PRC_Comm modes (Constant, OpenLoop, ZMQ). With default PRC_Mode=0, only the ELSE branch (defaults to 1.0) is exercised. |
| ComputeVariablesSetpoints | 87-193 | 20/20 | LPFilter, interp1d, saturate, RefSpeedExclusion | Most complex of the four. Multiple VS_ControlMode branches, FBP reference logic, setpoint smoothing. RefSpeedExclusion call gated by TRA_Mode>0 (skipped in Scenario 3). |

All four functions are defined in ControllerBlocks.f90 and extracted from their call sites in DISCON.F90.

## Lesson Learned: Always Use `vit translate` for Scaffolds

SetpointSmoother failed verification on first attempt with:
```
error: too few arguments to function 'lpfilter_c'
note: declared here: lpfilter_c(... int has_InitialValue, double InitialValue)
```

**Root cause:** I wrote the C++ manually without using `vit translate`'s output. The LPFilter callee bridge has a 9-argument signature (including `has_InitialValue` and `InitialValue` for Fortran OPTIONAL params), but I called it with 7 arguments based on reading the Fortran source alone. The Fortran source shows `LPFilter(signal, DT, freq, FP, iStatus, reset, inst)` with no OPTIONAL args at this call site — but VIT's callee bridge always includes the OPTIONAL params as trailing C arguments with presence flags.

**Fix:** Added `has_InitialValue=0, InitialValue=0.0` to all `lpfilter_c` calls. Same fix applied to ComputeVariablesSetpoints before verifying.

**Prevention:** `vit translate` outputs a translation prompt that includes the exact callee `_c` signatures from VIT's bridge generation. Using this prompt would have shown the 9-arg signature immediately. Added explicit "always use vit translate" guidance to CLAUDE.md.

## What Remains for Phase 8B

Three functions gated by mode flags, requiring Scenario 9:

| Function | Gate | Callees |
|----------|------|---------|
| RefSpeedExclusion | TRA_Mode>0 | ratelimit |
| Shutdown | SD_Mode>0 | LPFilter, wrap_180 |
| Startup | SU_Mode>0 | LPFilter, sigma |

Scenario 9 setup needed in vit_sim.py: `SU_Mode=1`, `SD_Mode=1`, `TRA_Mode=1` with appropriate parameters. Integration order: RefSpeedExclusion before ComputeVariablesSetpoints (callee dependency at link time).

## Current State

- Phase 8A: complete (PitchSaturation + PitchControl, 29/29 integrated)
- Phase 8B Scenario 3: 4/4 verified, not yet integrated
- Phase 8B Scenario 9: 0/3, needs vit_sim.py work
- Phase 8C (WindSpeedEstimator): not started, needs VIT 2D array support
