# Phase 1: Extract, Verify, Integrate 3 ErrVar-Free Functions
**Date:** 2026-03-18
**Status:** completed
**Scope:** Phase 1 of PROJECT_PLAN.md — first real integration from clean ROSCO

## Overview

Took the three ErrVar-free Functions.f90 functions (`saturate`, `wrap_180`, `wrap_360`) through the full VIT pipeline: extract kernels, verify C++ translations against captured state, integrate into the codebase, rebuild with C++, and confirm byte-identical simulation output.

## The wrap_360 Problem

The plan assumed all 3 functions could be extracted using `04_simple_sim.py`, but `wrap_360` is never called during that simulation. Both call sites are inside conditionals that the standard sim doesn't trigger:

- **Line 517** (`Controllers.f90`): `IF (CntrPar%Y_ControlMode == 2)` — yaw-by-IPC mode
- **Line 845** (`Controllers.f90`): `ELSEIF (CntrPar%AWC_Mode == 5)` — closed-loop Strouhal AWC

The standard DISCON.IN has `Y_ControlMode = 0` and `AWC_Mode = 0`. The other example scripts that set these modes (`33_yaw_control.py`, `20_active_wake_control.py`) all use OpenFAST, a full aeroelastic simulator that isn't installed in the container — they can't substitute for the simple 1-DOF Python sim that VIT's extraction pipeline uses.

### Investigation of the Simple Simulator

The 1-DOF simulator (`rosco/toolbox/sim.py`) passes turbine state to the controller via `avrSWAP` through `control_interface.py`. For `Y_ControlMode == 2` to exercise `wrap_360`, the controller needs:

| Signal | Fortran avrSWAP | Python index | Status in simple sim |
|--------|-----------------|--------------|---------------------|
| NacVane | avrSWAP(24) | [23] | Not set (used for Y_MeasErr) |
| NacHeading | avrSWAP(37) | [36] | Not set (used for Yaw_fromNorth) |
| Azimuth | avrSWAP(60) | [59] | Not set |
| rootMOOP(1:3) | avrSWAP(30:32) | [29:31] | Not set |

The simple sim has no physical nacelle vane or heading sensor model. It was designed for basic rotor speed control testing only.

### Solution: vit_sim.py

Created `Examples/vit_sim.py` — a VIT-specific multi-scenario simulation runner that exercises all controller code paths needed for translation verification. It runs two scenarios sequentially using the same compiled `libdiscon.so`:

**Scenario 1: Standard step-wind simulation** (identical to `04_simple_sim.py`)
- 1000s simulation, wind steps from 7 to 16 m/s
- Exercises: `saturate`, `wrap_180`, `interp1d`, and most other functions
- Includes the double-run deallocation check from the original script

**Scenario 2: Yaw-by-IPC simulation** (new)
- Generates a modified `DISCON_yaw.IN` with `Y_ControlMode = 2` via regex patching of the auto-generated DISCON file
- Sets synthetic NacVane and NacHeading values directly on `controller_int.avrSWAP` before each `call_controller()` — this bypasses the `call_controller` method's fixed turbine_state mapping
- NacHeading fixed at 350 degrees, NacVane oscillates +/- 20 degrees with a 50s period, so the sum crosses both the 0 and 360 boundaries, exercising all 3 branches of `wrap_360`
- Synthetic rotor azimuth accumulated from rotor speed (needed for ColemanTransform, which runs unconditionally in the IPC subroutine — zero rootMOOP is fine since the rootMOOPF filter only runs when `IPC_ControlMode > 0`)
- 100s simulation at 9 m/s constant wind — just enough for KGen to capture 20 invocations

Key design decisions:
- The DISCON.IN patching uses regex replacement (`re.subn`) rather than modifying the YAML tuning parameters, because `Y_ControlMode` is derived from controller tuning and there's no clean way to override it through the ROSCO toolbox API
- `IPC_ControlMode` must remain 0 when `Y_ControlMode = 2` (ROSCO validation at ReadSetParameters.f90:1057 rejects both being active simultaneously)
- The Y_IPC gains (KP, KI, IntSat) are all zero in the generated DISCON.IN, but this doesn't matter — `wrap_360` is called unconditionally within the `Y_ControlMode == 2` block, before the PI controller
- `matplotlib.use('Agg')` avoids display backend issues; `make_plots=False` skips plot generation

### vit.yaml Update

Changed `run_cmd` from `python3 04_simple_sim.py` to `python3 vit_sim.py`. This means all future extractions use the multi-scenario sim automatically. The `--scenario` flag allows running individual scenarios if needed.

Baseline output grew from 242 lines (04_simple_sim.py) to 282 lines (both scenarios).

## The Scaffold Overwrite Problem

During verification, all 3 functions showed 0/40 passed. Investigation revealed the `translations/Functions/*.cpp` files contained scaffolds (`return 0;`) instead of actual implementations. These had been overwritten by a `vit translate` run that generated scaffolds without checking for existing content.

The canonical translations in `vit_translation/translations/` (flat layout, outside ROSCO) were intact. Restored from there.

This exposed a risk: `vit translate` can silently overwrite verified translations. Future work should add a safety check — refuse to overwrite files with `Status: verified` in their metadata header without an explicit `--force` flag.

## Extraction Details

All extractions used `vit_sim.py` as `run_cmd` and captured 20 cases each:

| Function | Call site file | Line | Invocation source |
|----------|---------------|------|-------------------|
| `saturate` | `Controllers.f90` | 102 | Scenario 1 (standard sim) |
| `wrap_180` | `Filters.f90` | 430 | Scenario 1 (standard sim) |
| `wrap_360` | `Controllers.f90` | 517 | Scenario 2 (yaw-by-IPC sim) |

Note: The wrap_360 extraction initially used line 515 (from the original plan), but the actual call site is at line 517 after accounting for the conditional block. VIT's pragma insertion correctly found the call at line 517.

### Source Restoration Bug

During early extraction attempts, the wrap_360 extraction left KGen pragmas (`!$kgen begin_callsite` / `!$kgen end_callsite`) in `Controllers.f90`. This caused subsequent extractions to fail verification — the kernel was built from already-instrumented source, producing state files that didn't match the original code's behavior.

The fix was to `git checkout rosco/controller/src/Controllers.f90` and rebuild before re-extracting. This is a known fragility: if VIT's source restoration fails (e.g., due to a crash during extraction), subsequent extractions silently produce bad kernels. The pure-Fortran kernel replay also fails (0/40), which is the diagnostic — if even the unpatched Fortran kernel fails, the state capture is corrupted, not the C++ translation.

## Verification Results

All 3 functions: 40/40 passed (20 cases x 2 directions = 40 checks), both pre-integration and post-integration.

## Integration

All 3 integrated sequentially using `vit integrate --apply`. Each integration:
1. Creates `src/<function>_cpp.cpp` with extern "C" wrapper
2. Adds `USE ISO_C_BINDING` to Functions.f90 (skipped if already present)
3. Adds C interface block above CONTAINS (one per function)
4. Replaces Fortran function body with C++-calling wrapper using RESULT syntax
5. Updates CMakeLists.txt: `enable_language(CXX)`, adds cpp source, links `stdc++`

The second and third integrations correctly detected and skipped already-present items (`enable_language(CXX)`, `USE ISO_C_BINDING`, `stdc++` link).

Build output confirmed all 3 C++ files compiled:
```
Building CXX object CMakeFiles/discon.dir/src/saturate_cpp.cpp.o
Building CXX object CMakeFiles/discon.dir/src/wrap_180_cpp.cpp.o
Building CXX object CMakeFiles/discon.dir/src/wrap_360_cpp.cpp.o
Linking CXX shared library libdiscon.so
```

## Final Validation

`diff baseline_output.txt integrated_output.txt` — empty. All 282 lines of simulation output (both scenarios) are byte-identical between pure-Fortran and C++-integrated builds.

## Files Changed

**ROSCO repo:**
- `Examples/vit_sim.py` — new VIT simulation runner
- `Examples/DISCON_yaw.IN` — generated during scenario 2 (not committed, regenerated each run)
- `vit.yaml` — run_cmd updated, all 3 functions marked as `integrated`
- `translations/Functions/{saturate,wrap_180,wrap_360}.cpp` — restored from canonical copies
- `rosco/controller/src/Functions.f90` — 3 function bodies replaced with C++ wrappers
- `rosco/controller/CMakeLists.txt` — CXX enabled, 3 cpp sources, stdc++ link
- `rosco/controller/src/{saturate,wrap_180,wrap_360}_cpp.cpp` — new C++ wrapper files
- `kernel/{saturate,wrap_180,wrap_360}/` — golden kernel fixtures

**VIT repo:**
- `CLAUDE.md` — wrap_360 in extraction table, vit_sim.py references, updated verification regression commands
- `PROJECT_PLAN.md` — Phase 1 marked complete with notes

## Lessons Learned

1. **Not all functions are reachable from the default simulation.** Future phases will encounter this more frequently as we move to functions in conditional control modes. `vit_sim.py` is designed to be extended with new scenarios as needed.

2. **`vit translate` can overwrite verified translations.** Need a safety mechanism to prevent this.

3. **Source restoration failures are silent and dangerous.** If KGen pragmas are left in source files, subsequent extractions produce kernels that fail verification in a confusing way (all fields appear IDENTICAL except one output field). The diagnostic is to check `git diff` on the source files and/or run the pure-Fortran kernel — if it also fails, the state capture is bad.

4. **Canonical translation copies outside ROSCO are essential.** The flat layout in `vit_translation/translations/` saved us when the ROSCO copies were overwritten.
