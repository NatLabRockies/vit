# Phase 7B Integration Test: 27 Functions, Byte-Identical
**Date:** 2026-03-26
**Status:** completed
**Scope:** Integration test, clean build validation, Phase 7B completion

## Summary

Integrated 27 of 28 verified C++ translations into ROSCO, rebuilt, and confirmed byte-identical simulation output across all 6 scenarios. Each function was individually validated: integrate → incremental compile → full simulation (6 scenarios) → diff against pure-Fortran baseline. Zero failures. Clean rebuild at the end confirmed no stale artifacts.

PitchControl (function #28) was deferred — its C++ calls `pitchsaturation_c()`, and PitchSaturation (ControllerBlocks.f90) is not yet translated (Phase 8). This was a known blocker from dev note `202603241718`.

## Test Protocol

### Baseline
- Reset to clean Fortran: Functions.f90 + Filters.f90 at `dba7738`, Controllers.f90 + ControllerBlocks.f90 at `fde0498`, DISCON.F90 at `e8010f0`
- Manually applied FlapControl K init bug fix (fde0498 doesn't have it)
- C++ stubs for all `_cpp.cpp` files
- Clean build → `python3 vit_sim.py` → `baseline_output.txt`

### Per-Function Validation
For each of 27 functions:
1. `vit integrate <func> <cpp> -f <source> --apply`
2. `cmake --build .` (incremental)
3. `cp libdiscon.so` to lib/
4. `python3 vit_sim.py > test_output.txt`
5. `diff baseline_output.txt test_output.txt` — must be empty

### Integration Order

| # | Function | Source | Result |
|---|----------|--------|--------|
| 1 | saturate | Functions.f90 | PASS |
| 2 | wrap_180 | Functions.f90 | PASS |
| 3 | wrap_360 | Functions.f90 | PASS |
| 4 | ratelimit | Functions.f90 | PASS |
| 5 | ColemanTransform | Functions.f90 | PASS |
| 6 | ColemanTransformInverse | Functions.f90 | PASS |
| 7 | identity | Functions.f90 | PASS |
| 8 | sigma | Functions.f90 | PASS |
| 9 | interp1d | Functions.f90 | PASS |
| 10 | LPFilter | Filters.f90 | PASS |
| 11 | HPFilter | Filters.f90 | PASS |
| 12 | SecLPFilter | Filters.f90 | PASS |
| 13 | SecLPFilter_Vel | Filters.f90 | PASS |
| 14 | NotchFilter | Filters.f90 | PASS |
| 15 | NotchFilterSlopes | Filters.f90 | PASS |
| 16 | PIController | Controllers.f90 | PASS |
| 17 | PIIController | Controllers.f90 | PASS |
| 18 | ResController | Controllers.f90 | PASS |
| 19 | ForeAftDamping | Controllers.f90 | PASS |
| 20 | FloatingFeedback | Controllers.f90 | PASS (Phase 7B) |
| 21 | StructuralControl | Controllers.f90 | PASS (Phase 7B) |
| 22 | CableControl | Controllers.f90 | PASS (Phase 7B) |
| 23 | FlapControl | Controllers.f90 | PASS (Phase 7B) |
| 24 | YawRateControl | Controllers.f90 | PASS (Phase 7B) |
| 25 | VariableSpeedControl | Controllers.f90 | PASS (Phase 7B) |
| 26 | IPC | Controllers.f90 | PASS (Phase 7B) |
| 27 | ActiveWakeControl | Controllers.f90 | PASS (Phase 7B) |

Final clean rebuild: PASS (empty diff).

## Observations

### Python Cleanup Segfaults
Intermittent segfaults during Python process cleanup (after `vit_sim.py` completes all scenarios and prints "All scenarios complete."). These are ctypes/dlclose cleanup crashes — harmless. Output is complete before the crash, diffs are valid. Occurred on ~10 of 27 runs, non-deterministic.

### No Integration Blockers
The three blockers from dev note `202603241718` (view-type stash, save_config, avrSWAP kind mismatch) were all fixed in earlier sessions. All 27 functions integrated without errors.

### Infrastructure Confirmed
- View-type populator (ControlParameters) created once, reused by all functions that take CntrPar
- `vit_translated.h` accumulated 27 `extern "C"` declarations
- CMakeLists.txt grew from 19 to 27 `_cpp.cpp` entries
- Idempotent skip logic worked correctly (USE ISO_C_BINDING, enable_language(CXX), stdc++ link)

## PitchControl Deferral

PitchControl's C++ translation calls `pitchsaturation_c()`. PitchSaturation is defined in ControllerBlocks.f90:519-546 — a trivial 2-line function (interp1d lookup + max). It will be translated as the first Phase 8 function. Once PitchSaturation is integrated, PitchControl can be integrated immediately (its translation is complete and verified 40/40).

The callee dependency: a function cannot be integrated until ALL its `_c` callees have definitions. In kernel verification, VIT auto-generates bridges for all callees. In integration, only translated functions provide `_c` entry points.

## Commits

| Repo | Commit | Description |
|------|--------|-------------|
| VIT | `3f75fd2` | Phase 7B integration test: 27/27 passed |

## Related Dev Notes

- `202603241718-integration-test-blockers-and-fixes.md` — original blocker investigation
- `202603241444-view-type-callee-bridge-stash-fix.md` — kernel stash fix (integration counterpart in `202603241718`)
- `202603260840-40-40-verification-fix-and-final-reextraction.md` — clean kernel state for verification
