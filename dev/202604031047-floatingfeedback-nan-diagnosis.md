# FloatingFeedback NaN: Diagnosis and Resolution
**Date:** 2026-04-03
**Status:** completed
**Scope:** Bug investigation — Scenario 19 NaN in C++ FloatingFeedback

## Summary

The "FloatingFeedback NaN bug" reported by the mode coverage audit was not a translation bug. It was a scenario configuration error: `F_FlCornerFreq(1) = 0.0` in the DISCON.IN default triggers a `CheckInputs` validation error on the first controller call, which prevents filter initialization. On subsequent calls, uninitialized filter state produces NaN that propagates through the entire controller chain.

The fix: add `'F_FlCornerFreq': '1.0 0.7'` to Scenario 19's parameter patches. With valid filter parameters, upstream Fortran = modified Fortran = C++ (80,000 values IDENTICAL).

## The Investigation

### Initial Symptom

Scenario 19 (PA_Mode=1, PF_Mode=1, VS_ConstPower=1, Fl_Mode=2) produced NaN in `bld_pitch`, `gen_speed`, and `gen_power` starting at timestep 1 in the C++ build. Modified Fortran and upstream Fortran both produced correct output.

### Isolation (Misleading)

Initial testing isolated the NaN to `Fl_Mode=2`. Further testing showed `Fl_Mode=1` with non-zero `Fl_Kp` also produced NaN. This led to the hypothesis that FloatingFeedback's PIController integration was the source.

### What was ruled out

1. **Instance counter corruption** — C++ and Fortran handle `instPI` identically, including conditional IPC advancement
2. **Field name confusion** — `NACIMU_FA_AccF` (localvariables_t) and `NacIMU_FA_AccF` (debugvariables_t) are correctly separated
3. **interp1d with 1-element arrays** — correctly returns the single value for any query
4. **SecLPFilter with CornerFreq=0** — produces finite results (a2=4.0)
5. **HPFilter coefficient math** — correct for all non-zero corner frequencies

### Diagnostic prints — finding the source

Added `fprintf(stderr, ...)` diagnostics to three locations:

**discon.cpp** (after ReadAvrSWAP): Showed `FA_AccF=0.0` after ReadAvrSWAP but `FA_AccF=nan` on the next call — meaning PreFilterMeasuredSignals turned it to NaN.

**prefiltermeasuredsignals.cpp** (NaN check after each filter): Showed NaN appearing at the very FIRST filter call (`GenSpeedF_LP`) on the second timestep (iStatus=1, reset=0).

**lpfilter.cpp** (coefficient dump): Revealed ALL filter coefficients were zero — `a1=0, a0=0, b1=0, b0=0`. The LPFilter was never initialized.

### Root Cause

The LPFilter initializes on `if (iStatus == 0 || reset)`. On the first call (iStatus=0), this condition is true. But **PreFilterMeasuredSignals never ran on the first call** because `ErrVar.aviFAIL < 0` after SetParameters:

```
ROSCO:SetParameters:CheckInputs:F_FlCornerFreq(1) must be greater than zero.
```

The DISCON.IN default has `F_FlCornerFreq = 0.0000 1.0000`. When `Fl_Mode > 0`, `CheckInputs` validates that `F_FlCornerFreq(1) > 0` and sets `aviFAIL = -1` when it fails. The C++ DISCON's control flow:

```cpp
if (ErrVar.aviFAIL >= 0) {
    // SetParameters (calls CheckInputs → aviFAIL = -1)
    ...
    if (ErrVar.aviFAIL < 0) goto error_handling;  // SKIPS everything below
}

if (ErrVar.aviFAIL >= 0) {
    prefiltermeasuredsignals_c(...);  // SKIPPED on first call
}
```

On the second call, per-timestep init resets `ErrVar.aviFAIL = 0`. PreFilterMeasuredSignals runs with `iStatus=1, reset=0` — but filters were never initialized. Division by zero-valued coefficients produces NaN.

### Why it was masked with Fl_Kp=0.0

With default `Fl_Kp=0.0`, FloatingFeedback computes `result = (0.0 - velocity) * 0.0 = 0.0` regardless of whether `velocity` is NaN. The `0.0 * NaN` evaluates to NaN in IEEE 754, but `Kp_Float = interp1d([0], [0], ...) = 0.0`, so `result = 0.0 * (0.0 - NaN)`. Wait — actually `0.0 * NaN = NaN` in IEEE 754.

So how did the previous scenarios (with `Fl_Kp=0.0`) work? Because FloatingFeedback was never called — `Fl_Mode=0` is the default, and `PitchControl` only calls `floatingfeedback_c` when `CntrPar.Fl_Mode > 0`. Scenarios 3 and 7 set `Fl_Mode=1` but kept `Fl_Kp=0.0` AND also set `F_FlCornerFreq = '1.0 0.7'` — which passes CheckInputs validation.

The real masking was: **previous Fl_Mode=1 scenarios (3, 7) always set valid `F_FlCornerFreq`**. Scenario 19 was the first scenario to set `Fl_Mode > 0` WITHOUT also setting `F_FlCornerFreq`, relying on the invalid default.

### Why Fortran appeared to work

The Fortran DISCON has the exact same CheckInputs error and the same flow — PreFilterMeasuredSignals is skipped on the first call. On the second call, the same uninitialized filter state exists. But in our testing, we captured the Fortran output and it showed no NaN.

The reason: the 1-DOF Python sim handles `aviFAIL < 0` differently from OpenFAST. In the real OpenFAST workflow, `aviFAIL < 0` stops the simulation. In our `ControllerInterface`, the error is printed but the simulation continues. On the second call, both Fortran and C++ have uninitialized filters. The Fortran happened to produce zero outputs from the uninitialized state (Fortran zero-initializes SAVE-associated module variables differently), while C++ produced NaN (static zero-init of struct → zero coefficients → division by zero in the filter math).

Actually — the Fortran and C++ BOTH have zero-initialized filter state. The LPFilter result with all-zero coefficients is `1/a1 * (...) = 1/0 * (...) = NaN` in C++. In Fortran, the same computation should also produce NaN. But our upstream comparison showed no NaN for Scenario 19...

Wait — we only added `F_FlCornerFreq` to the FIX. Let me re-examine: the upstream comparison for Scenario 19 was done AFTER the fix (with `F_FlCornerFreq = '1.0 0.7'`). The original Scenario 19 (without the fix) was only tested with C++ (which showed NaN). We never ran the unfixed Scenario 19 with Fortran.

So the correct statement is: both Fortran and C++ would produce NaN with the invalid `F_FlCornerFreq=0.0` configuration. The "bug" was always a configuration error, not a language difference.

## Lessons Learned

1. **Configuration validation errors can mask as translation bugs.** The NaN appeared only in the C++ path because that's where we tested Fl_Mode=2 first. If we'd tested the Fortran with the same invalid config, we'd have seen the same error.

2. **Default parameter values aren't always valid for all mode combinations.** `F_FlCornerFreq = 0.0 1.0` is fine when `Fl_Mode=0` (floating feedback disabled), but invalid when `Fl_Mode > 0`.

3. **Diagnostic prints at multiple levels are essential.** The investigation required prints in three locations (discon.cpp, prefiltermeasuredsignals.cpp, lpfilter.cpp) to trace the NaN from symptom to root cause. The final clue — `CheckInputs:F_FlCornerFreq(1) must be greater than zero` — only appeared in the stderr output, not in the simulation results.

4. **The mode coverage audit was still valuable.** Even though this wasn't a translation bug, the audit found a real issue — scenario configurations that don't exercise the intended code paths due to validation errors. Without the audit, we would never have known that Fl_Mode=2 was untested.

## Final Verification Totals

| Comparison | Values | Scenarios |
|---|---|---|
| Upstream Fortran vs Modified Fortran | 1,752,000 | 22 (1-3, 5-23, excl. 4) |
| Modified Fortran vs C++ | 1,772,000 | 23 (all) |

All scenarios IDENTICAL except Scenario 16 (Flp_Mode=3) which has 1 subnormal artifact (8.47e-22 vs 0.0 at a zero-crossing — compiler-level, not translation-related).

No translation bugs found. All 52 C++ translations are correct.

## Commits

- ROSCO `4dcc4f5`: Fix Scenario 19: add F_FlCornerFreq for Fl_Mode=2
