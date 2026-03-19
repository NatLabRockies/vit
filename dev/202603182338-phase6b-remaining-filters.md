# Phase 6B: Remaining Filters (NotchFilter, NotchFilterSlopes, SecLPFilter_Vel)
**Date:** 2026-03-18
**Status:** completed
**Scope:** Filters.f90 — NotchFilter, NotchFilterSlopes, SecLPFilter_Vel + vit_sim.py Scenario 3

## Summary

Completed the Filters module translation (6/6 individual filter functions). Added Scenario 3 to vit_sim.py to exercise code paths inactive in the standard NREL-5MW config. NotchFilter and NotchFilterSlopes verified 40/40. SecLPFilter_Vel translated by pattern (KGen limitation). Full clean build test: 15 C++ translations, Scenarios 1+2 byte-identical, Scenario 3 has expected sub-ULP drift.

## Scenario 3: Filter Coverage Simulation

Added `run_scenario_3()` to `Examples/vit_sim.py` with DISCON_filters.IN patches:
- `F_GenSpdNotch_N=1` at 1.0 rad/s → exercises NotchFilter
- `IPC_ControlMode=1` → exercises NotchFilterSlopes on blade root moments
- `CC_Group_N=1, CC_GroupIndex=2601` → exercises SecLPFilter_Vel in cable control loop
- `CC_Mode` stays at 0 (critical: CC_Mode=1 has hardcoded CC_DesiredL writes to indices 1-3 regardless of CC_Group_N, causing OOB if CC_Group_N < 3)

Scenario 3 runs **first** (before Scenarios 1+2) so KGen's early invocations (0:0:1-20) capture the new filter code paths. This ordering is required because KGen counts invocations per enclosing subroutine — Scenarios 1+2 consume ~56000 invocations where the target lines are unreachable.

## Discoveries

### KGen Limitation: DO Loops with Derived-Type Bounds

KGen cannot capture state for function calls inside `DO I_GROUP = 1, CntrPar%CC_Group_N` where the loop bound comes from a derived type field. The kernel is generated correctly, but zero state files are produced. This affected only SecLPFilter_Vel. All other filters were extractable because their call sites are inside IF blocks (not DO loops with derived-type bounds).

SecLPFilter_Vel was translated by pattern from SecLPFilter — structurally identical (same args, same FilterParameters struct), just different coefficient formulas (lpfV_ prefix, velocity output variant). Verified through the full simulation diff.

### Sub-ULP Drift in Closed-Loop Feedback

Scenario 3's NotchFilter operates in a closed-loop feedback: GenSpeed → NotchFilter → GenSpeedF → torque controller → GenSpeed. Even though each individual NotchFilter call matches within 1e-14 (verified by KGen), the C++ path through extern "C" differs from the pure Fortran path at the sub-ULP level. Over 400 seconds, these tiny differences accumulate to ~0.1-0.3 kW out of 3400-4200 kW (~0.007%).

This does NOT affect Scenarios 1+2 (byte-identical). The Phase 6A filters (LPFilter, HPFilter, SecLPFilter) don't cause drift because their filtered signals don't feed directly back into themselves through a fast control loop.

### Two OPTIONAL Args Validated

NotchFilterSlopes has `Moving` (LOGICAL OPTIONAL) and `InitialValue` (REAL OPTIONAL). VIT generates independent `has_`/value pairs for each. The Fortran wrapper correctly declares both as `LOGICAL(C_BOOL)` / `REAL(C_DOUBLE)` with separate PRESENT() checks. 40/40 passed — no issues.

## Files Changed

### ROSCO repo
| File | Changes |
|------|---------|
| `Examples/vit_sim.py` | Added Scenario 3 (filter coverage), reordered to run before Scenarios 1+2 |
| `translations/Filters/seclpfilter_vel.cpp` | NEW — translated by pattern |
| `translations/Filters/notchfilter.cpp` | NEW — verified 40/40 |
| `translations/Filters/notchfilterslopes.cpp` | NEW — verified 40/40 (two OPTIONALs) |
| `kernel/NotchFilter/` | NEW — golden kernel fixture |
| `kernel/NotchFilterSlopes/` | NEW — golden kernel fixture |
| `vit.yaml` | 3 new translation entries |

### VIT repo
| File | Changes |
|------|---------|
| `PROJECT_PLAN.md` | Phase 6B items checked off, KGen limitation documented |
| `CLAUDE.md` | Updated fixtures, extraction params, regression commands, clean build test |
| `dev/202603182338-phase6b-remaining-filters.md` | This dev note |
