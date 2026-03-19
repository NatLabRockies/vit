# Phase 6: Filters Module Translation (Partial)
**Date:** 2026-03-18
**Status:** in-progress
**Scope:** Filters.f90 — LPFilter, HPFilter, SecLPFilter

## Summary

Translated 3 of 6 individual filter functions from ROSCO's Filters module to C++. All 3 verified (40/40 passed each) and integrated. Full clean build test: 12 C++ translations (9 Functions.f90 + 3 Filters.f90) produce byte-identical simulation output.

No VIT code changes were needed — all patterns (FilterParameters struct pointer, OPTIONAL args, LOGICAL(4)→int, inst INOUT→int*) were already proven in Phase 3 ratelimit.

## Functions Translated

| Function | Lines | FP prefix | State fields | Extraction line |
|----------|-------|-----------|------|------|
| LPFilter | 30-72 | lpf1_ | 6 | Filters.f90:347 |
| HPFilter | 176-212 | hpf_ | 2 | Filters.f90:375 |
| SecLPFilter | 74-122 | lpf2_ | 10 | Filters.f90:371 |

## Functions Deferred (not called in test simulation)

| Function | Reason | Required config change |
|----------|--------|----------------------|
| SecLPFilter_Vel | Cable control loop inactive (CC_Group_N=0) | Enable CC_Mode |
| NotchFilter | Gen speed notch count is 0 (F_GenSpdNotch_N=0) | Set F_GenSpdNotch_N > 0 |
| NotchFilterSlopes | IPC mode disabled (IPC_ControlMode=0) | Enable IPC |
| PreFilterMeasuredSignals | 5 derived type args, 2 not C-compatible | Phase 4C + 4D |

## Extraction Challenges

Several planned extraction call sites failed because they're in conditional branches not activated by the test simulation config:

- **Controllers.f90 line 167** (LPFilter): Inside `PA_Mode == 1` branch — not active. Switched to Filters.f90 line 347 (inside PreFilterMeasuredSignals, always executes).
- **Filters.f90 line 350** (SecLPFilter): Inside `F_LPFType == 2` branch — test uses type 1. Switched to line 371 (unconditional acceleration filtering).
- **Controllers.f90 line 941** (SecLPFilter_Vel): Inside `CC_Group_N` loop — count is 0 in generated DISCON.IN.
- **Filters.f90 line 356** (NotchFilter): Inside `F_GenSpdNotch_N` loop — count is 0.
- **Filters.f90 line 405** (NotchFilterSlopes): Inside `IPC_ControlMode > 0` block — IPC disabled.

The `vit_sim.py` script generates DISCON.IN from `write_DISCON()` with tuned controller parameters. The `patches` mechanism could enable additional features, but modifying tuned parameters risks simulation instability.

## Translation Pattern

All 3 filters follow the identical C++ pattern established by ratelimit:

```cpp
double filtername(double InputSignal, ..., filterparameters_t* FP,
                  int iStatus, int reset, int* inst,
                  int has_InitialValue, double InitialValue) {
    int idx = *inst - 1;  // 1-based → 0-based
    double InitialValue_ = InputSignal;
    if (has_InitialValue) InitialValue_ = InitialValue;

    if (iStatus == 0 || reset) { /* init state + coefficients */ }
    double result = /* filter computation using FP->prefix_*[idx] */;
    FP->prefix_InputSignalLast[idx] = InputSignal;
    FP->prefix_OutputSignalLast[idx] = result;
    *inst = *inst + 1;
    return result;
}
```

FilterParameters is a 46-field struct (all `double[1024]`), passed by pointer. Each filter only accesses its own prefix (lpf1_, hpf_, lpf2_).

## Verification Results

- LPFilter: 40/40 passed
- HPFilter: 40/40 passed
- SecLPFilter: 40/40 passed
- All 9 existing golden fixtures: 40/40 passed (regression)
- Full clean build test: 12 integrations, byte-identical output

## Files Changed

### VIT repo
| File | Changes |
|------|---------|
| `PROJECT_PLAN.md` | Phase 6 items updated, 3 checked off, 4 deferred with rationale |
| `CLAUDE.md` | Golden fixture table, extraction params, verification regression, clean build test — all expanded to 12 functions |
| `dev/202603182158-phase6-filters-module.md` | This dev note |

### ROSCO repo
| File | Changes |
|------|---------|
| `translations/Filters/lpfilter.cpp` | NEW — LPFilter C++ translation |
| `translations/Filters/hpfilter.cpp` | NEW — HPFilter C++ translation |
| `translations/Filters/seclpfilter.cpp` | NEW — SecLPFilter C++ translation |
| `kernel/LPFilter/` | NEW — golden kernel fixture |
| `kernel/HPFilter/` | NEW — golden kernel fixture |
| `kernel/SecLPFilter/` | NEW — golden kernel fixture |
| `vit.yaml` | 3 new translation entries (auto-added by `vit integrate --apply`) |

## Next Steps

1. Find a way to exercise SecLPFilter_Vel, NotchFilter, and NotchFilterSlopes in the test simulation (patch DISCON.IN or create a dedicated test scenario)
2. PreFilterMeasuredSignals remains blocked on Phase 4C (LocalVariables) and Phase 4D (ControlParameters)
3. Consider Phase 4C next, or continue with Phase 5B (remaining Functions.f90)
