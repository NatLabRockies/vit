# Clean Fortran Reset: Eliminating C++ Artifacts
**Date:** 2026-04-03
**Status:** completed
**Scope:** Script infrastructure — reset_to_clean.sh, integrate_all.sh, prepare_commit.sh

## Summary

After Phase 11 made ROSCO a pure C++ build, `reset_to_clean.sh` was leaving C++ artifacts in the source tree — 53 `.cpp` stubs compiled and linked into the "Fortran baseline," `.h` files left from previous integrations, view populator `.f90` files as dead code, and a CMakeLists.txt that required a C++ compiler. This undermined the claim that the baseline was a clean Fortran build.

Fixed by switching from the intermediate `aef3155` CMakeLists.txt (which had accumulated all C++ infrastructure) to the upstream `e8010f0` CMakeLists.txt (pure Fortran, zero `.cpp` files), adding `-ffp-contract=off` for verification, and removing all `.cpp`/`.h` files from `src/`.

## The 5 Issues

| # | Issue | Resolution |
|---|-------|------------|
| 1 | 53 `.cpp` stubs compiled and linked into baseline | Upstream CMakeLists.txt has zero `.cpp` files |
| 2 | `.h` files (vit_translated.h, restart_fields.h) in `src/` | `rm -f src/*.cpp src/*.h` during reset |
| 3 | Orphaned files (debug_logger.cpp, restart.cpp, discon.cpp) in `src/` not in CMake | Same cleanup |
| 4 | View populator `.f90` files compiled as dead code | Upstream CMakeLists.txt doesn't include them |
| 5 | `ROSCO_Types.f90` has Phase 4A/4C modifications | **Accepted** — bug-fixed .f90 sources reference modified type fields (`restart /= 0`, `AWC_complexangle_re/im`, `ACC_INFILE(1024)`). The types and sources are a coupled set. Modifications don't change behavior. |

## Key Finding: Type-Source Coupling

The investigation revealed that `ROSCO_Types.f90` cannot be reset to upstream (`e8010f0`) because the bug-fixed `.f90` source files depend on the modified type fields:

- `LocalVar%restart` changed from `LOGICAL` to `INTEGER(C_INT)` — all source files use `(LocalVar%restart /= 0)` instead of `LocalVar%restart`
- `LocalVar%AWC_complexangle` changed from `COMPLEX(DbKi)` to `REAL(DbKi) AWC_complexangle_re/im` — Controllers.f90 references `_re`/`_im` fields
- `LocalVar%ACC_INFILE` changed from `CHARACTER(:), ALLOCATABLE` to `CHARACTER(1024)` — ReadSetParameters.f90 uses fixed-length copy loop
- `ErrorVariables%ErrMsg` changed from `CHARACTER(:), ALLOCATABLE` to `CHARACTER(1024)`

These modifications were made in Phase 4A/4C to enable C-interoperability (`BIND(C)` on derived types). The Fortran behavior is unchanged — `INTEGER(C_INT)` holds the same 0/1 values as `LOGICAL`, and `CHARACTER(1024)` is large enough for all use cases. But the source code syntax is different, creating a hard dependency.

## Bug Found: integrate_all.sh Phase 11B

When starting from the upstream Fortran-only CMakeLists.txt, the manual integration blocks (Stage B/C/D/E) copy `.cpp` files to `src/` but don't add them to CMake — they relied on the files already being listed in the `aef3155` CMakeLists.txt. Phase 11B's Python code extracted `.cpp` files from CMakeLists.txt SOURCES, missing the manually-copied files.

Fixed by changing Phase 11B to scan the `src/` directory for `.cpp` files instead of reading CMakeLists.txt. Since all integration steps copy `.cpp` files to `src/` before Phase 11B runs, this catches everything regardless of whether it was added to CMake.

## macOS sed Compatibility

`sed -i` on macOS requires an extension argument (`sed -i '' '...'`) while Linux `sed -i` does not. Since `reset_to_clean.sh` runs on the Mac (not in the container), the `-ffp-contract=off` append uses `sed -i ''`.

## Verified Full Cycle

| Step | Result |
|------|--------|
| `reset_to_clean.sh` | 0 .cpp, 0 .h, `LANGUAGES Fortran C`, -ffp-contract=off |
| Fortran build | Only gfortran invocations, `Linking Fortran shared library` |
| `ldd` Fortran | libgfortran present, no libstdc++ |
| Baseline capture | 10 scenarios |
| `integrate_all.sh` | 54/54 passed (bootstraps C++ from clean Fortran) |
| C++ build | Only g++ invocations, `Linking CXX shared library` |
| `ldd` C++ | libstdc++ present, no libgfortran |
| sim-compare | 692,000 values IDENTICAL |
| debug_test | 15,999 data rows IDENTICAL |
| `prepare_commit.sh` | Clean `git status` |

## Script Workflow (Final State)

```
reset_to_clean.sh          → Pure Fortran (upstream CMake, no C++ artifacts)
  ↓ build + capture baseline
integrate_all.sh           → 54 steps: 52 functions + DISCON swap + Fortran removal
  ↓ build + sim-compare + debug_test
prepare_commit.sh          → Committed state (pure C++ CMake, clean .f90, committed .cpp)
```

The three scripts form a clean cycle: reset produces a buildable Fortran baseline, integrate transitions to pure C++, and prepare_commit restores the committed state. Each step is independently verifiable.

## Commits

- ROSCO `0beeb47`: Initial reset_to_clean.sh update (CMakeLists.txt restoration from aef3155)
- ROSCO `ee45cc8`: Fix git staging side effect
- ROSCO `6207239`: Clean Fortran reset (upstream CMakeLists.txt, remove C++ artifacts, Phase 11B directory scan)
