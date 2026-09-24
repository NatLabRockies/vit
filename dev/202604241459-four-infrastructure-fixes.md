# Four Infrastructure Fixes for AeroDyn Translation Scaling
**Date:** 2026-04-24
**Status:** completed
**Scope:** Follow-up from Phase 1 proof-of-concept — fix infrastructure gaps before scaling to 37 functions

## Summary

Four infrastructure issues identified during the Phase 1 proof-of-concept (Calculate_Cn) have been fixed. These are one-time changes that enable the pipeline to scale from 1 function to 507.

## Fix 1: `-ffp-contract=off` in CMakeLists.txt

**Problem:** The flag was only passed via `cmake .. -DCMAKE_Fortran_FLAGS='-ffp-contract=off'` — lost on reconfigure, easy to forget, not documented in the build.

**Fix:** Added `FP_CONTRACT_OFF` cmake option (default ON) to `openfast/CMakeLists.txt` (line 42). The flag logic in `cmake/OpenfastFortranOptions.cmake` sets both `CMAKE_Fortran_FLAGS` and `CMAKE_CXX_FLAGS` to `-ffp-contract=off` when the option is ON.

```cmake
option(FP_CONTRACT_OFF "Disable FMA contraction for VIT translation verification" on)
```

Default ON during the translation effort. Set `cmake .. -DFP_CONTRACT_OFF=OFF` for production pure-C++ builds after translation is complete.

**Files:** `openfast/CMakeLists.txt`, `openfast/cmake/OpenfastFortranOptions.cmake`

## Fix 2: Regenerated verification baselines with `-ffp-contract=off`

**Problem:** The committed baselines at `openfast/baselines/aerodyn/` were captured with default FMA (no `-ffp-contract=off`). After Fix 1, all builds use `-ffp-contract=off`, so the baselines didn't match.

**Fix:** Rebuilt aerodyn_driver with Fix 1 applied, regenerated all 16 baselines using `generate_aerodyn_baseline.sh`, verified 16/16 self-consistent with `verify_aerodyn_baselines.sh all`.

**Result:** 16/16 verified. 14 bit-identical, 2 data-identical (header-only binary diffs in ad_BAR_CombinedCases and ad_MultipleHAWT — different binary metadata but identical data arrays).

**Files:** `openfast/baselines/aerodyn/*/ad_driver.baseline.outb` (16 files), `openfast/baselines/aerodyn/*/drift_vs_nrel.txt` (16 files)

## Fix 3: ReKi precision in Registry C++ generator

**Problem:** The Registry defines ReKi with `bit_size=0` (the "default precision" sentinel). Both `DataType::c_type()` in registry.hpp and `cpp_type_str()` in registry_gen_cpp.cpp mapped bit_size=0 → `float`. But OpenFAST builds with `DOUBLE_PRECISION=ON`, making ReKi=REAL(8). Every generated C++ header declared ReKi fields as `float` instead of `double`.

**Fix:** Added `-cppcode-double` CLI flag to the Registry tool. When used, `cpp_type_str()` maps bit_size=0 → `double` instead of `float`. The flag also sets `gen_cpp_code = true` (implies `-cppcode`).

The Registry's data model is unchanged — ReKi stays bit_size=0 as the "default precision" sentinel. The C++ generator just interprets it differently when double precision is requested.

**Verification:** Regenerated all 10 AeroDyn headers with `-cppcode-double`. Zero `float` occurrences for ReKi fields, all `double`. 10/10 compile cleanly.

**Files:** `openfast/modules/openfast-registry/src/registry_gen_cpp.cpp`, `openfast/modules/openfast-registry/src/registry.hpp`, `openfast/modules/openfast-registry/src/main.cpp`

## Fix 4: CMake auto-patching for multi-target builds

**Problem:** VIT's `_generate_cmake_patches()` in integrate.py assumed a flat `set(SOURCES ...)` block (ROSCO's pattern). AeroDyn has multiple `add_library()` targets with explicit source lists (`basicaerolib`, `aerodynlib`, `adilib`, etc.). VIT couldn't determine which target to add the C++ source to.

**Fix:** Rewrote `_generate_cmake_patches()` to handle both patterns:

1. **ROSCO-style:** `set(SOURCES ...) + add_library(target SHARED ${SOURCES})` — unchanged behavior
2. **OpenFAST-style:** `add_library(target STATIC src/file1.f90 ...)` — new: auto-detects which target contains the Fortran source file being integrated

Added `_find_target_for_source()` helper that parses all `add_library()` blocks, extracts their source lists, and finds which block contains the Fortran file. The patching then:
- Adds `enable_language(CXX)` before the target's `add_library`
- Adds the .cpp source to the target's source list
- Adds `stdc++` to the target's `target_link_libraries`

**Verification:** `vit integrate Calculate_Cn ... --apply` on clean AeroDyn correctly identified `basicaerolib` (the target containing `AirfoilInfo.f90`), added `calculate_cn.cpp` to its source list, added `enable_language(CXX)` and `stdc++`. Build succeeded, baseline comparison passed.

**Files:** `vit/vit/integrate.py`

## Commits

- **OpenFAST** `b5422a8c6`: Fixes 1-3 (cmake option, baselines, Registry double precision)
- **VIT** `97c279f`: Fix 4 (multi-target cmake auto-patching)

## Related

- Dev note `202604241415` — full pipeline proven (identified these four gaps)
- Dev note `202604241400` — first full verification
- Dev note `202604241337` — FMA hypothesis confirmed
- `PROJECT_PLAN_AERODYN.md` — Phase 1 infrastructure
