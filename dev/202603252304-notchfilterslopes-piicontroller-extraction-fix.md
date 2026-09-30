# NotchFilterSlopes and PIIController Extraction Coverage Fix
**Date:** 2026-03-25
**Status:** completed
**Scope:** vit_sim.py scenario coverage, KGen extraction, golden kernel fixtures

## Background

After the full 28-function re-extraction (dev note `202603252225`), two functions produced 0 state files:
- **NotchFilterSlopes** (Filters.f90:405)
- **PIIController** (Controllers.f90:682)

Neither had ever had working golden fixtures. Investigation revealed the root cause is KGen's invocation counting model combined with ROSCO's `IPC_ControlMode`/`Flp_Mode` mutual exclusion.

## Root Cause: KGen Invocation Counter Model

KGen counts invocations at the **enclosing subroutine** level, not at the specific call site. For a target function inside an IF block, the counter increments on every call to the enclosing subroutine — even when the IF block isn't entered. The state capture code at the call site only fires when both conditions are met: the invocation is in range (1-20) AND the call site is reached.

### NotchFilterSlopes

Call site at Filters.f90:405, inside PreFilterMeasuredSignals:
```fortran
IF ((CntrPar%IPC_ControlMode > 0) .OR. (CntrPar%Flp_Mode == 3)) THEN
    LocalVar%RootMOOPF(K) = NotchFilterSlopes(...)
```

PreFilterMeasuredSignals is called every timestep (~16,000 times in Scenario 3). Invocations 1-20 are consumed in the first fraction of a second. But in Scenario 3, `IPC_ControlMode=0` (default from NREL5MW tuning) and `Flp_Mode=1` (not 3), so the gate is FALSE — the call site is never reached during invocations 1-20.

**Why IPC_ControlMode isn't set in Scenario 3:** Commit `5827f49` removed `IPC_ControlMode=1` from Scenario 3 because ROSCO explicitly rejects `IPC_ControlMode > 0 AND Flp_Mode > 0` simultaneously (ReadSetParameters.f90:1108). Since Scenario 3 needs `Flp_Mode=1` for FlapControl, NotchFilterSlopes can't be exercised here. The replacement comment incorrectly claimed "IPC_ControlMode uses the default from the tuning" — but the default is 0.

### PIIController

Call site at Controllers.f90:682, inside FlapControl:
```fortran
ELSEIF (CntrPar%Flp_Mode == 2) THEN
    DO K = 1,LocalVar%NumBl
        LocalVar%Flp_Angle(K) = PIIController(...)
```

Scenario 4 has `Flp_Mode=2` and is designed for PIIController. But when all scenarios run (default), Scenario 3 runs first. FlapControl is called ~16,000 times in Scenario 3 (with `Flp_Mode=1`), exhausting invocations 1-20. By the time Scenario 4 starts, the counter is well past 20.

Additionally, PIIController requires two ROSCO bug fixes (uninitialized K in FlapControl init at line 669, uninitialized n in Flp_Mode==2 notch filter branch at Filters.f90:412). The extraction prep uses `fde0498` for Controllers.f90, which predates the K init fix at `dba7738`. The fix must be manually applied: wrap line 669 in `DO K = 1,LocalVar%NumBl ... END DO`.

## Fixes

### NotchFilterSlopes: New Scenario 6

Added `run_scenario_6()` to vit_sim.py with `IPC_ControlMode=1` and `Flp_Mode=0` (no mutual exclusion). This exercises the `IPC_ControlMode > 0` branch at Filters.f90:403, reaching the NotchFilterSlopes call site.

```python
def run_scenario_6(turbine, controller, cp_filename):
    """Sim with IPC_ControlMode=1 to exercise NotchFilterSlopes."""
    write_discon(..., patches={
        'IPC_ControlMode': 1,
        'Flp_Mode': 0,
        'IPC_KI': '0.0 0.0',
        'IPC_KP': '0.0 0.0',
    })
```

Extraction: `vit extract NotchFilterSlopes -f rosco/controller/src/Filters.f90 -l 405 --run-args '--scenario 6'`

### PIIController: Use --scenario 4

Extraction: `vit extract PIIController -f rosco/controller/src/Controllers.f90 -l 682 --run-args '--scenario 4'`

When only Scenario 4 runs, FlapControl invocations 1-20 all have `Flp_Mode=2`, so the PIIController call site at line 682 is reached during the capture range.

### Scenario 3 docstring fix

Updated Scenario 3's docstring and comments to remove the incorrect claim that NotchFilterSlopes is exercised. Added note about the mutual exclusion requiring Scenario 6.

## Results

| Function | Scenario | Cases | Verification |
|----------|----------|-------|-------------|
| NotchFilterSlopes | 6 (IPC_ControlMode=1) | 20 | 40/40 PASSED |
| PIIController | 4 (Flp_Mode=2) | 20 | 40/40 PASSED |

All 28 functions now have golden fixtures with 20 state files each. All 27 translated functions verify successfully.

## Commits

| Repo | Commit | Description |
|------|--------|-------------|
| VIT | `ded87bb` | Update CLAUDE.md extraction table with --scenario 4/6 |
| ROSCO | `813d0fe` | Add Scenario 6 (IPC), extract NotchFilterSlopes + PIIController |

## Key Insight: KGen Invocation Model

KGen's invocation counter increments at the enclosing subroutine entry, not at the target call site. For functions inside conditional branches that aren't active in the first scenario, the capture range (0:0:1-20) is consumed by the enclosing subroutine's calls before the target code path activates. The solution is `--run-args '--scenario N'` to run only the scenario where the target code path is active, ensuring invocations 1-20 reach the call site.

This pattern applies to any function gated by a mode flag that conflicts with other modes in Scenario 3:
- `Flp_Mode==2` functions → `--scenario 4`
- `IPC_ControlMode > 0` functions → `--scenario 6`
- `AWC_Mode==4` functions → `--scenario 5`

## Related Dev Notes

- `202603252225-deallocate-removal-and-full-reextraction.md` — full re-extraction, identified these 2 failures
- `202603201150-rosco-bug-investigation-narrative.md` — PIIController ROSCO bug fixes
- `202603252004-dll-persistence-scope-fix-and-reassessment.md` — dll_persistence investigation
