# First Successful KGen Extraction on AeroDyn — OQ2 Answered
**Date:** 2026-04-24
**Status:** completed
**Scope:** Phase 1 proof-of-concept — validate VIT extract on AeroDyn's cmake build chain

## Summary

First successful KGen state extraction on AeroDyn: `Calculate_Cn` from AirfoilInfo.f90, 21 cases captured from the `ad_BAR_SineMotion` regression test case. This answers **OQ2: KGen works on AeroDyn's cmake build chain.** Three VIT bugs were found and fixed during the attempt, plus one call-site lesson.

## Target Function

**`Calculate_Cn`** — AirfoilInfo.f90, lines 1154-1170. Pure math leaf function:
```fortran
FUNCTION Calculate_Cn (alpha, Cl, Cd, Cd0) RESULT(Cn)
   REAL(ReKi), intent(in) :: alpha(:), Cl(:), Cd(:)
   REAL(ReKi), intent(in) :: Cd0
   REAL(ReKi) :: Cn(size(alpha))
   do Row=1,NumAlf
      cn(Row) = Cl(Row)*cos(alpha(Row)) + (Cd(Row) - Cd0)*sin(alpha(Row))
   end do
END FUNCTION
```

17 lines, no derived type args, no NWTC calls, no callees. Chosen as the simplest possible test of the pipeline.

## Extraction Command

```bash
cd /workspace/openfast
vit extract Calculate_Cn -f modules/aerodyn/src/AirfoilInfo.f90 -l 974
```

**Call site at line 974** (not 956). Line 956 is in a conditional branch (`if (.not. UA_f_cn)`) that the BAR_SineMotion test case doesn't exercise. Line 974 is in the else branch (common path when Cd is not constant) — 21 invocations captured (one per airfoil table in the test case).

## VIT Configuration

Created `openfast/vit.yaml`:
```yaml
project:
  name: AeroDyn
  source_dir: modules
  build_dir: build
kgen:
  path: /workspace/KGen/bin/kgen
  build_cmd: cd /workspace/openfast/build && cmake --build . --target aerodyn_driver -j$(nproc)
  run_cmd: cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/ad_BAR_SineMotion && /workspace/openfast/build/modules/aerodyn/aerodyn_driver ad_driver.dvr
  clean_cmd: cd /workspace/openfast/build/modules/aerodyn && make clean
  invocation: "0:0:1-5"
build_system:
  type: cmake
  cmake_file: modules/aerodyn/CMakeLists.txt
  target_name: aerodyn_driver
fortran:
  kind_aliases:
    ReKi: "REAL(8)"    # DOUBLE_PRECISION=ON in cmake
    DbKi: "REAL(8)"
    SiKi: "REAL(4)"
    R4Ki: "REAL(4)"
    R8Ki: "REAL(8)"
    IntKi: "INTEGER(4)"
    B4Ki: "INTEGER(4)"
  compiler: gfortran
  flags: -ffp-contract=off
verification:
  tolerance: 1.0e-14
```

**Key differences from ROSCO vit.yaml:**
- `source_dir: modules` — broad, covering all OpenFAST modules (KGen needs NWTC Library source paths for USE resolution, not just AeroDyn)
- `build_cmd` uses cmake `--target aerodyn_driver` (multi-target cmake build vs ROSCO's single-library cmake)
- `run_cmd` runs `aerodyn_driver` directly with a regression test case `.dvr` file (vs ROSCO's Python simulation)
- No `install_cmd` — the executable is the simulation (vs ROSCO where the .so needed copying)
- `kind_aliases`: ReKi → REAL(8) because OpenFAST uses `DOUBLE_PRECISION=ON` (confirmed from cmake cache). ROSCO used `-fdefault-real-8` instead.
- No `assumed_size_arrays` or `dll_persistence` needed (Calculate_Cn has no assumed-size args, AeroDyn has no DLL persistence issue)

## Bugs Found and Fixed

### Bug 1: CMakeLists.txt sed pattern matches wrong location

**Problem:** The sed pattern `0,/\(src\/[A-Za-z_]*\.f90\)/s//...` matched the first `src/*.f90` in the file, which was inside a `generate_f90_types()` call (line 18: `${CMAKE_CURRENT_LIST_DIR}/src/AeroAcoustics_Types.f90`) — not inside a library source list. This broke the CMakeLists.txt instead of patching it.

ROSCO didn't have this issue because its CMakeLists.txt had no `generate_f90_types` calls — the source list was the first occurrence of `src/*.f90`.

**Fix:** Changed sed pattern to `0,/^\( *src\/[A-Za-z_]*\.f90\)$/s//...` — anchored to match only lines that are JUST an indented source file entry (no surrounding text). The `^` start anchor and `$` end anchor exclude `generate_f90_types(...)` lines.

**File:** `vit/vit/extract.py`, line 418

### Bug 2: kgen_utils.f90 copied to wrong directory

**Problem:** `generate_state_capture_build_script()` placed kgen_utils.f90 at `source_dir/kgen_utils.f90`. With `source_dir: modules`, this was `modules/kgen_utils.f90` — but CMakeLists.txt references `src/kgen_utils.f90` (relative to `modules/aerodyn/`). The file existed but cmake couldn't find it.

ROSCO didn't have this issue because `source_dir: rosco/controller/src` WAS the cmake source directory.

**Fix:** Derive the kgen_utils destination from `cmake_file` path instead of `source_dir`. If `cmake_file` has a `src/` subdirectory, place kgen_utils.f90 there: `dirname(cmake_file)/src/kgen_utils.f90`.

**File:** `vit/vit/extract.py`, lines 371-381

### Bug 3: Docker filesystem timestamps prevent cmake recompilation

**Problem:** KGen's state Makefile copies instrumented source over the original (`cp -f`), but on Docker-mounted filesystems (Colima on Apple Silicon), the file timestamps may not change as expected. cmake's incremental build compares source timestamps against object file timestamps — if the source appears older, cmake skips recompilation. Result: the binary runs with the ORIGINAL (uninstrumented) AirfoilInfo.f90 code, and no state is captured.

ROSCO didn't have this issue because its cmake build was simpler and always detected source changes (or the clean step was sufficient).

**Diagnosis:** Confirmed by checking build output — `kgen_utils.f90.o` was compiled (new file, always detected), but `AirfoilInfo.f90.o` was NOT recompiled. After adding `touch`, AirfoilInfo.f90.o was recompiled and the binary contained the state capture code (confirmed via `strings`).

**Fix:** Added "Step 2d" to the build wrapper script: `touch` all `.f90` and `.F90` files in the cmake source directory before running `cmake --build`. This forces cmake to recompile all instrumented source files.

**File:** `vit/vit/extract.py`, lines 494-503

### Also fixed: kgen_utils.f90 cleanup path

The `cleanup_kgen_utils_injection()` function tried to remove kgen_utils.f90 from `source_dir` (which no longer matched the actual destination after Bug 2 fix). Updated to check the cmake src/ directory first, then fall back to source_dir.

**File:** `vit/vit/extract.py`, lines 572-584

## Call-Site Lesson

**Line 956 vs line 974:** Calculate_Cn is called at two locations in CalculateUACoeffs:
- Line 956: inside `if (.not. UA_f_cn) then ... else ... Cn = Calculate_Cn(...)` — the UA_f_cn branch for 360-degree models
- Line 974: in the outer else branch — the common path when Cd varies with alpha

The BAR_SineMotion test case doesn't exercise the UA_f_cn branch (line 956), so KGen captures zero invocations there. Line 974 captures 21 invocations (one per airfoil table).

This parallels the ROSCO experience where each function needed the right scenario to exercise its code path. For AeroDyn, the lesson is: always verify the call site is on a taken branch before extracting.

## OpenFAST Precision Discovery

OpenFAST's `ReKi` maps to `REAL(8)` (double precision) when `DOUBLE_PRECISION=ON` (the default cmake setting, confirmed from `build/CMakeCache.txt`). This differs from the Registry C++ generator's current mapping of `ReKi → float` — the generated headers need to be updated to use `double` for ReKi. This is a Phase 0 follow-up task.

**Precision chain:** `SingPrec.f90` defines ReKi via preprocessor:
```fortran
#ifdef OPENFAST_DOUBLE_PRECISION
  INTEGER, PARAMETER :: ReKi = R8Ki   ! REAL(8)
#else
  INTEGER, PARAMETER :: ReKi = R4Ki   ! REAL(4)
#endif
```

## Extraction Results

- **Kernel directory:** `kernel/Calculate_Cn/` (18 files + 21 state data files)
- **State cases:** 21 (invocations 1-22, with invocation 15 skipped by ElapsedTime selection)
- **Kernel files:** AirfoilInfo.f90, AirfoilInfo_Types.f90, NWTC_Library.f90, NWTC_Library_Types.f90, SingPrec.f90, kernel_driver.f90, kgen_utils.f90, tprof_mod.f90, Makefile

## What's Next

1. Write a C++ translation of Calculate_Cn (trivial — 17 lines of cos/sin math)
2. Run `vit verify` to confirm the kernel works
3. Run `vit integrate` to test integration on AeroDyn's cmake structure
4. Commit VIT fixes and vit.yaml
5. Proceed with remaining AirfoilInfo functions

## Related

- `PROJECT_PLAN_AERODYN.md` — Phase 1 (AirfoilInfo + DBEMT)
- Dev note `202604240622` — Phase 0 retrospective (identified OQ2 as key Phase 1 risk)
- Dev note `202604240640` — MeshType investigation (confirmed Phases 1-3 have no mesh access)
- `openfast/vit.yaml` — the AeroDyn VIT configuration
