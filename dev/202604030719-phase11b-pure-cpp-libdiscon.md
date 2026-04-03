# Phase 11B: Pure C++ libdiscon.so
**Date:** 2026-04-03
**Status:** completed
**Scope:** Phase 11 Stage B — Remove all Fortran from the build

## Summary

Removed all 16 Fortran source files (13 modules + 3 view populators) and the gfortran compiler infrastructure from CMakeLists.txt. ROSCO's `libdiscon.so` is now built entirely from 53 C++ source files using only C and CXX compilers. `ldd` confirms no libgfortran runtime dependency.

This completes Phase 11 — the final phase of the Fortran→C++ translation. Every line of Fortran in ROSCO's controller has been replaced by verified C++ producing byte-identical output.

Verified: 692,000 simulation values IDENTICAL across 10 scenarios. 15,999 debug data rows IDENTICAL.

## What Was Removed

### Fortran source files (16 total)

| File | Why removable |
|------|---------------|
| Constants.f90 | Parameters mirrored in rosco_constants.h |
| ROSCO_Types.f90 | Structs mirrored in vit_types.h |
| Functions.f90 | All 10 functions → C++ _c entry points |
| Filters.f90 | All 7 functions → C++ _c entry points |
| Controllers.f90 | All 15 functions → C++ _c entry points |
| ControllerBlocks.f90 | All 10 functions → C++ _c entry points |
| ReadSetParameters.f90 | All 5 functions → C++ _c entry points |
| ROSCO_IO.f90 | WriteRestartFile/ReadRestartFile/Debug → C++ |
| ROSCO_Helpers.f90 | GetRoot replicated in discon.cpp |
| ZeroMQInterface.f90 | UpdateZeroMQ → C++ |
| ExtControl.f90 | ExtController → C++ |
| SysFiles/SysGnuLinux.f90 | dlopen/dlsym in ext_control.cpp |
| vit_controlparameters_view.f90 | C++ owns ControlParameters data directly |
| vit_performancedata_view.f90 | C++ owns PerformanceData data directly |
| vit_extcontroltype_view.f90 | C++ owns ExtControlType data directly |
| DISCON.F90 | Replaced by discon.cpp in Stage A |

### CMake infrastructure removed

- `LANGUAGES Fortran C` → `LANGUAGES C CXX`
- `enable_language(CXX)` — CXX now in project LANGUAGES
- `CMAKE_Fortran_MODULE_DIRECTORY` — no Fortran modules
- All `CMAKE_Fortran_COMPILER_ID` checks (Intel, Flang, GNU) — no Fortran compiler
- All `CMAKE_Fortran_FLAGS` settings (`-DIMPLICIT_DLLEXPORT`, `-fdefault-real-8`, `-ffp-contract=off`, etc.)
- NWTC_SYS_FILE platform detection block (SysGnuLinux.f90, SysGnuWin.f90, etc.)
- `CMAKE_Fortran_FLAGS` ZMQ linker flag

### What was preserved

- C++ `-ffp-contract=off` — still needed for verification reproducibility
- ZMQ C client support (`zmq_client.c` object library, conditional on `PC_ZeroMQ_FOUND`)
- `target_link_libraries(discon PRIVATE stdc++ dl)` — stdc++ for C++ runtime, dl for ExtController's dlopen
- Install targets and version info
- `target_include_directories` for translations/ headers

## Implementation Approach

Rather than surgically removing lines with fragile regex (which failed on the first attempt due to nested if/elseif/endif blocks and shell→Python→file escaping layers), the Phase 11B block in `integrate_all.sh` extracts the list of .cpp source files from the existing CMakeLists.txt and writes a clean replacement from a Python template. This is robust against any upstream changes to the Fortran sections — they're simply not included in the output.

The first regex attempt failed because:
1. `if(APPLE OR UNIX)...endif()` regex matched the inner `endif()` (closing compiler ID check) instead of the outer `endif()` (closing platform check), leaving `elseif (WIN32)...endif()` dangling
2. NWTC_SYS_FILE block regex used literal `${` which needed different escaping
3. Windows path `C:\Program Files` needed 8 backslashes in the shell→Python→file chain

The template approach eliminated all these issues.

## ldd Output

```
linux-vdso.so.1
libstdc++.so.6 => /lib/aarch64-linux-gnu/libstdc++.so.6
libm.so.6 => /lib/aarch64-linux-gnu/libm.so.6
libgcc_s.so.1 => /lib/aarch64-linux-gnu/libgcc_s.so.1
libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6
/lib/ld-linux-aarch64.so.1
```

No libgfortran. The shared library depends only on standard C/C++ runtime libraries.

## CMakeLists.txt Before and After

**Before (181 lines):** Mixed Fortran/C/C++ with platform-specific compiler detection for 3 Fortran compilers (Intel, Flang, GNU), Fortran module directory, 16 .f90 + 53 .cpp source files, NWTC_SYS_FILE platform selection.

**After (~60 lines):** Pure C/CXX project, `-ffp-contract=off`, 53 .cpp sources, ZMQ support, install targets.

## integrate_all.sh Flow

The script now has 54 integration steps:
1. Steps 1-42: `vit integrate --apply` for KGen-verified functions
2. Steps 43-52: Manual integration for Stage B/C/D/E functions
3. Step 53 (Phase 11A): Copy discon.cpp, swap DISCON.F90→discon.cpp
4. Step 54 (Phase 11B): Rewrite CMakeLists.txt as pure C/C++

Note: Steps 1-52 still modify the .f90 files (creating Fortran wrappers) even though those files are removed from the build in step 54. This is harmless — the wrappers are written but never compiled. Keeping these steps ensures the script remains correct if someone needs to build with Fortran dead code (Stage A configuration) for debugging.

## Baseline Array Issue

An issue discovered during verification: the committed CMakeLists.txt (from Stage A) has `src/discon.cpp` instead of `src/DISCON.F90`. This means `reset_to_clean.sh` + clean build produces a stub DISCON that can't export the `DISCON` symbol, causing `vit_sim.py` to fail with `undefined symbol: DISCON`.

The workaround: use the committed `baseline_arrays/` (captured from a clean Fortran build before Stage A) instead of rebuilding the baseline. These are the canonical reference arrays and are tracked in git.

For future clean build tests, the baseline must be captured before the Stage A CMakeLists.txt change, or CMakeLists.txt must be temporarily restored to include DISCON.F90.

## Files Changed

| Repo | File | Change |
|------|------|--------|
| ROSCO | `scripts/integrate_all.sh` | Added Phase 11B block, TOTAL 53→54 |
| ROSCO | `rosco/controller/CMakeLists.txt` | (via script) Pure C/CXX, no Fortran |
| VIT | `PROJECT_PLAN.md` | Stage B items marked [x] |

## Commits

- ROSCO `7aeac66`: Phase 11B: Remove all Fortran from build — pure C++ libdiscon.so
- VIT `05054f9`: Mark Phase 11 Stage B complete — pure C++ libdiscon.so

## Milestone

This marks the completion of the entire Fortran→C++ translation of ROSCO:

- **53 C++ source files** (52 translated functions + 1 DISCON entry point)
- **0 Fortran source files** in the build
- **692,000 simulation values** byte-identical to the original Fortran
- **10 scenarios** covering all controller modes
- **No Fortran runtime dependency**

The translation was accomplished incrementally over 11 phases, with each function individually verified (42 via KGen kernel verification, 10 via simulation comparison) before integration. The staged approach (Stage A: C++ DISCON with Fortran dead code → Stage B: remove Fortran) isolated the risk of the final assembly from the risk of dead-code removal.
