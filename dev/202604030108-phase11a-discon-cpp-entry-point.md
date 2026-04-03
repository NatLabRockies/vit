# Phase 11A: C++ DISCON Entry Point
**Date:** 2026-04-03
**Status:** completed
**Scope:** Phase 11 Stage A — DISCON.F90 → C++

## Summary

Replaced the 162-line Fortran DISCON.F90 entry point with a ~530-line C++ equivalent (`translations/discon.cpp`). The C++ DISCON orchestrates all 52 translated functions directly via `_c` entry points, eliminating the Fortran interop layer from the call path. Fortran modules remain in the build as dead code (Stage B will remove them).

Verified: 692,000 simulation values IDENTICAL across 10 scenarios. 15,999 debug data rows IDENTICAL.

## Architecture Change

### Before (Fortran DISCON)
```
OpenFAST → DISCON() [Fortran, BIND(C)]
  → Fortran SAVE variables (7 derived types, Fortran-owned ALLOCATABLE arrays)
  → Each function call: Fortran wrapper → vit_populate_*() → _c() entry point
  → View populators run on every call to copy pointers from Fortran ALLOCATABLE to C view struct
```

### After (C++ DISCON)
```
OpenFAST → DISCON() [C++, extern "C", __attribute__((visibility("default")))]
  → static C structs (7 types) + static AllocOwner (raw pointers for 50 ALLOCATABLE fields)
  → Each function call: _c() entry point directly (no Fortran wrapper, no view populator)
  → View struct IS the authoritative storage — pointers set once at allocation time
```

The key architectural insight: in the Fortran system, the view struct was a transient window into Fortran-owned data, repopulated on every call. In the C++ system, the view struct IS the data. `readcontrolparameterfilesub_pass1_c` and `pass2_c` read and write scalar fields directly in the `controlparameters_view_t` struct. No reverse-copy (`vit_copy_scalars_to_cntrpar`) needed.

## ALLOCATABLE Ownership Transition

The hardest part of Stage A was transitioning 50 ALLOCATABLE fields from Fortran ownership to C++ ownership:

- **44 ControlParameters fields**: Allocated via `calloc` after `readcontrolparameterfilesub_pass1_c` returns parsed scalar sizes. Each allocation sets both the raw pointer in the AllocOwner struct (for lifetime management) and the pointer+size pair in the `controlparameters_view_t` struct (for `_c` function access). 65 allocation calls total, mechanically translated from `readcontrolparameterfilesub_wrapper.f90` lines 91-196.

- **5 PerformanceData fields**: Allocated before `readcpfile_c` call. Sizes from `CntrPar.PerfTableSize[0]` (nBeta) and `PerfTableSize[1]` (nTSR). 2D matrices are column-major (Fortran layout preserved): dimensions are `(nTSR, nBeta)`.

- **1 ExtControlType field**: `ExtDLL.avrSWAP` guard-allocated to 2000 floats before first `extcontroller_c` call.

The allocation uses `calloc` (zero-initialized), matching Fortran's ALLOCATE behavior where the wrapper immediately zeros the arrays. The AllocOwner struct persists as a static variable alongside the view structs for the lifetime of the DLL.

## ReadRestartFile Callee Dispatch

The Fortran ReadRestartFile wrapper previously handled callee dispatch — after reading the checkpoint, it called `ReadControlParameterFileSub` and `ReadCpFile` from the Fortran side. In the C++ DISCON, this dispatch moves into `discon.cpp`:

```
readrestartfile_c(...)  // reads checkpoint into LocalVar/objInst
read_config_files(...)  // re-runs full ALLOCATE protocol + file reads
```

The `read_config_files()` helper was factored out to be shared between the iStatus==0 (first call) and iStatus==-9 (restart) paths, avoiding code duplication.

## What Became Dead Code

With the C++ DISCON calling `_c` entry points directly, the following are now compiled but never executed:

- All Fortran wrappers in .f90 module files (the `SUBROUTINE X(...)` bodies that were replaced by `integrate_all.sh` with wrappers calling `_c` entry points via `C_LOC`)
- View populator subroutines (`vit_populate_controlparameters`, `vit_populate_performancedata`, `vit_populate_extcontroltype`)
- Reverse-copy subroutines (`vit_copy_scalars_to_cntrpar`, `vit_copy_scalars_to_extcontroltype`)
- Kernel stash pointers (`vit_original_controlparameters`, etc.)
- `ROSCO_Helpers.f90` (only `GetRoot` was used, now replicated in C++)
- `SysGnuLinux.f90` (dlopen/dlsym already in C++ `ext_control.cpp`)
- `Constants.f90` (mirrored in `rosco_constants.h`)

Stage B will remove all of this.

## Implementation Details

### String Handling

DISCON.F90 uses Fortran CHARACTER variables with `TRANSFER` for C string conversion. In C++, strings are already `char*`:

- **accINFILE**: Passed directly to `readcontrolparameterfilesub_pass1_c` after null-termination and priPath extraction (directory path for relative file resolution).
- **avcOUTNAME → RootName**: `GetRoot()` replicated in C++ — scans backward for last '.' not followed by '/' or '\', truncates there. RootName is space-padded (matching Fortran CHARACTER semantics) for compatibility with `writerestartfile_c`, `readrestartfile_c`, and `debug_c` which call `trim_fortran_string()`.
- **ErrMsg → avcMSG**: `memcpy` with null termination, matching the Fortran `TRANSFER(ErrMsg//C_NULL_CHAR, avcMSG, SIZE(avcMSG))` pattern.

### Banner Output

The Fortran banner uses `write(*,*)` with `NEW_LINE("A")` concatenation. The C++ banner uses `printf` with `\n`. The output format differs slightly (Fortran adds leading space from list-directed output), but this is cosmetic and doesn't affect simulation results.

### DLL Symbol Export

Fortran: `BIND(C, NAME='DISCON')` + `!GCC$ ATTRIBUTES DLLEXPORT :: DISCON` + `-DIMPLICIT_DLLEXPORT` preprocessor flag.

C++: `extern "C" __attribute__((visibility("default"))) void DISCON(...)`. The `-DIMPLICIT_DLLEXPORT` flag is still passed (for remaining Fortran dead code) but the C++ function uses the visibility attribute directly.

### integrate_all.sh Update

Total updated from 52 to 53. Phase 11A block added at the end:
1. Copies `translations/discon.cpp` to `rosco/controller/src/discon.cpp`
2. Replaces `src/DISCON.F90` with `src/discon.cpp` in CMakeLists.txt SOURCES via Python string replacement

### Fl_n Default

The Fortran wrapper applied `IF (CntrPar%Fl_n == 0) CntrPar%Fl_n = 1` between pass1 and allocation. In the C++ path, this default is already applied inside `readcontrolparameterfilesub_pass1_c` (line 492 of the C++ translation). No redundant fixup needed in `discon.cpp`.

## Files Changed

| Repo | File | Change |
|------|------|--------|
| ROSCO | `translations/discon.cpp` | NEW — C++ DISCON entry point (530 lines) |
| ROSCO | `rosco/controller/src/discon.cpp` | NEW — stub for clean builds |
| ROSCO | `rosco/controller/CMakeLists.txt` | DISCON.F90 → discon.cpp in SOURCES |
| ROSCO | `scripts/integrate_all.sh` | Added Phase 11A block, TOTAL 52→53 |
| VIT | `PROJECT_PLAN.md` | Stage A items marked [x] with implementation details |

## Commits

- ROSCO `57fd56f`: Phase 11A: Replace DISCON.F90 with C++ entry point
- VIT `0083417`: Mark Phase 11 Stage A complete in PROJECT_PLAN.md

## What's Next

Stage B: Remove all 13 Fortran modules from CMakeLists.txt, remove `enable_language(Fortran)`, remove gfortran-specific flags. Verify `ldd libdiscon.so` shows no libgfortran dependency. This is pure dead-code removal — low risk since Stage A already proved the C++ orchestration is correct.
