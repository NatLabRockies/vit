# All Translations Renamed to Fortran Source Casing

**Date:** 2026-03-29
**Status:** completed
**Scope:** Rename 30 C++ translation function names to match Fortran source declarations, remove auto-detection workaround

## Background

When VIT was updated to preserve Fortran source casing in `sig.name` (dev note `202603282208`), an auto-detection workaround was added to `integrate.py` and `interface_gen.py`. This workaround scanned each translation file to find the actual function name, because all existing translations used lowercase (`lpfilter`) while the Fortran source uses PascalCase (`LPFilter`). The wrapper generator needed to call the function by whatever name it was actually defined as.

This was a workaround, not a solution. The correct fix: rename the translations to match source casing, then use `sig.cpp_impl_name` directly.

## What Changed

### 30 translations renamed

Each file's function definition was changed from lowercase to Fortran source casing:

| Module | Functions renamed |
|--------|------------------|
| Filters (6) | `lpfilter`→`LPFilter`, `hpfilter`→`HPFilter`, `seclpfilter`→`SecLPFilter`, `seclpfilter_vel`→`SecLPFilter_Vel`, `notchfilter`→`NotchFilter`, `notchfilterslopes`→`NotchFilterSlopes` |
| Controllers (13) | `picontroller`→`PIController`, `piicontroller`→`PIIController`, `rescontroller`→`ResController`, `foreaftdamping`→`ForeAftDamping`, `floatingfeedback`→`FloatingFeedback`, `pitchcontrol`→`PitchControl`, `variablespeedcontrol`→`VariableSpeedControl`, `yawratecontrol`→`YawRateControl`, `ipc`→`IPC`, `activewakecontrol`→`ActiveWakeControl`, `flapcontrol`→`FlapControl`, `cablecontrol`→`CableControl`, `structuralcontrol`→`StructuralControl` |
| Functions (3) | `colemantransform`→`ColemanTransform`, `colemantransforminverse`→`ColemanTransformInverse`, `aerodyntorque`→`AeroDynTorque` |
| ControllerBlocks (8) | `pitchsaturation`→`PitchSaturation`, `statemachine`→`StateMachine`, `setpointsmoother`→`SetpointSmoother`, `powercontrolsetpoints`→`PowerControlSetpoints`, `refspeedexclusion`→`RefSpeedExclusion`, `computevariablessetpoints`→`ComputeVariablesSetpoints`, `startup`→`Startup`, `windspeedestimator`→`WindSpeedEstimator` |

9 functions were already correct (lowercase in Fortran source): `saturate`, `ratelimit`, `interp1d`, `interp2d`, `identity`, `wrap_180`, `wrap_360`, `sigma`, `Shutdown`.

### Auto-detection removed

**`integrate.py`:** Removed the 8-line `impl_name_match` block that scanned implementation content with a case-insensitive regex. Replaced with `impl_func_name = sig.cpp_impl_name`.

**`interface_gen.py`:** Removed the 10-line `try/except` block that read the implementation file and searched for the function definition. Replaced with `impl_name = sig.cpp_impl_name`. Also removed the now-unused `import re`.

The wrapper generator now trusts `sig.cpp_impl_name` directly — no file scanning, no regex, no fallback. This works because:
- `sig.name` comes from the Fortran source (exact casing)
- `sig.cpp_impl_name` returns `sig.name` unless it collides with libc (then `sig.name + "_impl"`)
- The translation file defines the function with the same name

## Test Results

- Unit tests: 566/566 passed
- Kernel verification: 39/39 passed
- Integration: 39/39 passed
- 9-scenario comparison: ALL IDENTICAL — 672,000 values

## Design Principle Established

VIT now has a single, consistent naming convention with no workarounds:

1. **Fortran source is authoritative** for function name casing
2. `sig.name` preserves source casing (extracted from `FUNCTION`/`SUBROUTINE` declaration)
3. `sig.cpp_impl_name` adds `_impl` only for libc collisions (all-lowercase names matching libc symbols)
4. `sig.c_func_name` is always lowercase + `_c` suffix (for BIND(C) compatibility)
5. Translation files define functions with `sig.cpp_impl_name` casing
6. Wrapper generators use `sig.cpp_impl_name` directly — no scanning, no detection
