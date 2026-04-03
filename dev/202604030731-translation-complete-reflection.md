# ROSCO Translation Complete — Reflection
**Date:** 2026-04-03
**Status:** completed
**Scope:** Full project retrospective — Phase 1 through Phase 11

## What We Built

Two things: a **tool** and a **proof**.

**VIT (Verified Incremental Translator)** is a verification-first translation pipeline. It doesn't translate Fortran to C++ — it proves translations correct. The core insight is decomposition: extract one function's runtime state via KGen, wire the C++ replacement into a standalone verification kernel, verify bit-identical output against the captured state, integrate into the codebase, and move on. The tool grew organically across 11 phases — each phase exposed a new challenge (ALLOCATABLE arrays, view structs, two-pass allocation protocols, OPTIONAL parameters, callee bridges, manual integration patterns) and VIT evolved to handle it.

**The proof** is that 692,000 simulation values across 10 scenarios, covering every controller mode ROSCO supports, are byte-identical between the Fortran original and the C++ replacement. Not "close enough." Not "within tolerance." Identical. `-ffp-contract=off` made this possible — it eliminated the one source of legitimate divergence (FMA contraction scheduling) so that any difference was a real bug. This turned verification from a statistical exercise into a binary one: pass or fail, no ambiguity.

## Final State

- **53 C++ source files** (52 translated functions + 1 DISCON entry point)
- **0 Fortran source files** in the build
- **692,000 simulation values** byte-identical to original Fortran across 10 scenarios
- **15,999 debug data rows** identical
- **No libgfortran runtime dependency** — pure C/C++ shared library
- **598 unit tests** in the VIT tool
- **42 KGen-verified functions** + **10 simulation-verified functions**
- **11 phases** executed over the course of the project

## What Worked

### Incremental verification was the right architecture

The alternative — translate everything, then test — would have been a debugging nightmare. With 52 functions in closed-loop feedback (pitch controls torque which controls speed which controls pitch), a single wrong sign or off-by-one index would produce plausible-looking but wrong output. By verifying each function in isolation with captured runtime state, bugs were caught at the source. By the time we reached PitchControl (185 lines, 11+ callees), the callee bridge system was mature and the function verified on the first attempt.

### The phase structure provided natural checkpoints

Simple scalar functions first (Phases 1-3), then derived types (4-5), then filters (6), then controllers (7-8), then I/O and orchestrators (10, Stages C-E), then the entry point (11). Each phase built confidence and infrastructure for the next. No phase required revisiting a previous phase's verified output.

### View structs were the right abstraction for ALLOCATABLE types

The alternative — making Fortran ALLOCATABLE arrays directly C-compatible — would have required deep changes to ROSCO's type system. The view pattern (pointer+size pairs populated at runtime) let us leave the Fortran types untouched during the translation phase, then seamlessly transition to C++ ownership in Phase 11A. The same view structs that were transient windows into Fortran data became the authoritative storage in the pure C++ build.

### Manual integration for edge cases was pragmatic

Rather than making VIT handle every Fortran construct (preprocessor directives, two-pass ALLOCATE protocols, callee dispatch, `CHARACTER(variable)` arguments), we used manual integration for ~10 functions where the pattern was unique. The `integrate_all.sh` script captured these one-off patterns reproducibly, making the full pipeline runnable from a single command.

### `-ffp-contract=off` was essential

Without this flag, gfortran and g++ independently choose which multiply-adds to fuse into FMA instructions. Each makes different choices, producing 1-ULP differences that accumulate through closed-loop feedback into visible output divergence. By disabling FMA contraction on both compilers, we eliminated this noise and made every difference a real bug. This flag is for the translation phase only — with no mixed binary, FMA can be freely re-enabled.

## What Was Hard

### ALLOCATABLE ownership transition (Phase 11A)

The most intellectually demanding piece. The 80+ allocation calls in `discon.cpp` had to exactly match the Fortran wrapper's sizes — wrong sizes meant silent heap corruption, not a compiler error. The two-pass ReadControlParameterFileSub protocol (parse scalars → allocate arrays → fill arrays) required understanding the data flow across three layers (C++ DISCON → C++ pass1/pass2 → view struct). The `read_config_files()` helper factored this for reuse by both the initialization and restart paths.

### Field name case sensitivity

Fortran is case-insensitive; C++ is not. `NACIMU_FA_AccF` vs `NacIMU_FA_AccF` — the LocalVariables TYPE uses all-caps while DebugVariables uses mixed case, but Fortran source code uses mixed case for both. This trap appeared in Stage E (PreFilterMeasuredSignals) and would have been invisible without bit-identical verification.

### KGen's quirks

Python 2.7 dependency, PROCEDURE POINTER parsing bugs (fixed in our fork), SAVE variable persistence across DLL loads, invocation counting across scenarios, assumed-size array handling — each consumed real debugging time. VIT's `vit.yaml` configuration absorbed these workarounds (dll_persistence resets, assumed_size_arrays, fortran flags) so they didn't leak into the translation workflow.

### Stage B/C/D/E functions (non-KGen verification)

Ten functions couldn't use KGen extraction — file I/O (ReadControlParameterFileSub, ReadCpFile, CheckInputs), restart (WriteRestartFile, ReadRestartFile), debug logging (Debug), platform-specific (UpdateZeroMQ, ExtController), and orchestrators (PreFilterMeasuredSignals, SetParameters). Each required a different integration pattern. Simulation-level verification (comparing 692,000 values across 10 scenarios) replaced kernel-level verification for these functions.

## Where We Are Now

ROSCO is a 53-file C++ shared library producing output indistinguishable from its Fortran predecessor. The Fortran source files still exist in the repository (needed for `reset_to_clean.sh` baseline builds and KGen extraction) but are not compiled.

Cosmetic artifacts remain:
- The `_c` naming convention on function entry points is vestigial (no Fortran wrappers to call them)
- View struct types (`controlparameters_view_t`) could be renamed to simpler names
- CLAUDE.md and TRANSLATION_TIPS.md still describe the Fortran interop workflow in detail

VIT has 598 unit tests and handles the full extract → verify → integrate pipeline, but its type system handling, view struct generation, and integration patching are ROSCO-specific in practice.

## What Lies Ahead

### Immediate cleanup
- **FMA re-enablement**: Remove `-ffp-contract=off` from CMake. One-line change, validated with the FMA sanity check (small bounded differences expected in Scenario 3, not widespread divergence).
- **Documentation update**: CLAUDE.md, README.md, and TRANSLATION_TIPS.md describe the now-removed Fortran interop layer. Should be updated to reflect the pure C++ reality.
- **Baseline array workflow**: The committed CMakeLists.txt can no longer build a clean Fortran baseline (DISCON.F90 is swapped out). `reset_to_clean.sh` or a separate baseline CMake needs to handle this.

### Phase 12: Generalization
VIT's core pipeline (extract → verify → integrate) is sound for any Fortran codebase that KGen can instrument. Generalizing requires:
- Abstracting the type system handling (BIND(C), view structs, ALLOCATABLE)
- Making integration patching work with different Fortran coding styles
- Supporting codebases without a single-DLL entry point pattern
- Documenting VIT as a standalone tool rather than a ROSCO-specific pipeline

## The Bigger Picture

What we demonstrated is that **verified incremental translation** works for real scientific codebases. Not toy examples — a production wind turbine controller with closed-loop feedback, 52 functions, 8 modules, ALLOCATABLE arrays, OPTIONAL parameters, file I/O, DLL interfaces, and 10 simulation scenarios exercising every code path.

The key insight: **translation is easy; verification is hard.** LLMs can write plausible C++ from Fortran. But "plausible" isn't "correct" in scientific computing where a 1-ULP error in a filter coefficient accumulates through 80,000 timesteps of closed-loop feedback. VIT's contribution is making correctness provable — one function at a time, bit by bit, until the entire system is replaced and the proof is complete.

The staged approach — Phase 11A (C++ DISCON with Fortran dead code) followed by Phase 11B (remove Fortran) — exemplified the philosophy: prove correctness first, then remove the safety net. At every step, we could have stopped and had a working system. We never had to take a leap of faith.

## Project Timeline

| Phase | Scope | Functions |
|-------|-------|-----------|
| 1-3 | Simple scalar functions | saturate, wrap_180, wrap_360, ratelimit |
| 4 | Derived type system (BIND(C), view structs, type fixes) | ForeAftDamping |
| 5 | Arrays + ErrorVariables | sigma, interp1d |
| 6 | Filters module | LPFilter, HPFilter, SecLPFilter, NotchFilter, NotchFilterSlopes |
| 7 | Controllers + remaining filters | SecLPFilter_Vel, PIController, PIIController, ResController, FloatingFeedback, StructuralControl, CableControl, FlapControl, YawRateControl, VariableSpeedControl, IPC, ActiveWakeControl |
| 8 | ControllerBlocks + complex functions | PitchSaturation, PitchControl, interp2d, AeroDynTorque, StateMachine, SetpointSmoother, PowerControlSetpoints, RefSpeedExclusion, ComputeVariablesSetpoints, Shutdown, Startup, WindSpeedEstimator |
| 9 | (Absorbed into 8) | — |
| 10A | ReadSetParameters + late additions | ReadAvrSWAP, PIDController, unwrap |
| 10B (Stage B) | Config parsing (non-KGen) | CheckInputs, ReadCpFile, ReadControlParameterFileSub |
| Stage C | I/O (non-KGen) | WriteRestartFile, ReadRestartFile, Debug |
| Stage D | Platform modules (non-KGen) | UpdateZeroMQ, ExtController |
| Stage E | Orchestrators (non-KGen) | PreFilterMeasuredSignals, SetParameters |
| 11A | C++ DISCON entry point | DISCON (with Fortran dead code) |
| 11B | Remove Fortran | Pure C++ libdiscon.so |

**52 functions translated. 692,000 values verified. 0 Fortran remaining.**
