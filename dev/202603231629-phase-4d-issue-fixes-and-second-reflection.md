# Phase 4D: Issue Fixes and Second Reflection
**Date:** 2026-03-23
**Status:** completed
**Scope:** Phase 4D view type infrastructure — fixing all issues from initial reflection, second-pass review

## Background

After implementing Phase 4D Steps 1-8 (ALLOCATABLE scanner, view struct header, populator, type registry, interface gen, integration, translation), a rigorous reflection surfaced 7 issues (dev note `202603231559`). This note documents the plan to fix them, the fixes applied, a second reflection that found an additional issue, and the final state.

## Plan for Fixing Initial Issues

The initial reflection classified issues by severity:

| # | Issue | Severity |
|---|-------|----------|
| 1 | CLI `_build_registry()` never calls `apply_type_strategies()` | CRITICAL |
| 2 | `kernel_verify.py` doesn't handle view types | CRITICAL |
| 3 | `integrate.py` doesn't generate populator `.f90` or update CMake | CRITICAL |
| 4 | Dimension splitting bug for nested parentheses in ALLOCATE | BUG |
| 5 | INTENT(INOUT) scalar write-back | IMPORTANT |
| 6 | OL_ unmapped fields (11 fields + WE_CP) | IMPORTANT |
| 7 | No unit tests for new code | IMPORTANT |

Two issues were resolved by investigation before coding:
- **Issue #5:** Audit of all Phase 7B target functions found zero CntrPar scalar writes. All ControlParameters fields are read-only. No depopulator needed.
- **Issue #6:** 5 Phase 7B functions use OL_ fields (PitchControl, VariableSpeedControl, YawRateControl, CableControl, PowerControlSetpoints). All read-only interpolation via `interp1d`. WE_CP unused. Deferred to Phase 7B — manual size mappings will be added when those functions are translated.

Execution order: Fix 4 (dim split) → Fix 1 (CLI) → Fix 5 (tests) → Fix 3 (integrate) → Fix 2 (kernel_verify).

## Fixes Applied

### Fix 4: Parenthesis-aware dimension splitting

**Problem:** `dims_str.split(",")` broke on `SIZE(CntrPar%OL_Channels,DIM=1)` — the comma inside `SIZE(...)` caused a false split, reporting 2D arrays as 3D.

**Fix:** Added `_split_dims_aware()` to `analyze_types.py` — iterates chars, tracks paren depth, only splits on commas at depth 0.

**Verification:** OL_CableControl now correctly reports as 2D.

### Fix 1: CLI `_build_registry()` apply_type_strategies

**Problem:** All CLI commands (verify, integrate, translate, run) built the derived type registry but never applied view strategies from config. ControlParameters was silently treated as passthrough.

**Fix:** Added `apply_type_strategies(registry, config.type_strategies)` call to `_build_registry()` in `cli.py`.

**Verification:** `vit interface PitchControl` now generates correct wrapper with `USE vit_controlparameters_view`, local view struct, populator call, and `C_LOC(CntrPar_view)`.

### Fix 5: Unit tests (35 new tests)

**New file:** `tests/test_view_types.py`

Test coverage:
- **Dimension splitting (4 tests):** Simple, single, nested parens, double nested
- **Size expression classification (6 tests):** Types A-E + complex expressions
- **View struct header (6 tests):** Scalar, 1D allocatable, 2D allocatable, CHARACTER, unmapped commented out, `_view_t` suffix
- **Populator generation (7 tests):** Scalar assignment, 1D/2D C_LOC, ALLOCATED check, CHARACTER array form, BIND(C) mirror type, module structure
- **Type registry (3 tests):** Set view strategy, case insensitive, no match leaves None
- **TypeMapping flow (3 tests):** View makes compatible, no strategy stays incompatible, BIND(C) type not view
- **Interface generation (6 tests):** `_view_t*` params, USE statement, local decl, populator call, C_LOC of view, regular type unchanged

All 486 tests pass (451 existing + 35 new).

### Fix 3: `integrate.py` populator file generation + CMake

**Problem:** Integration generated the C++ file and patched Fortran/CMake, but didn't create the populator `.f90` or add it to CMake SOURCES.

**Fix:** Added to `generate_integration_plan()`:
1. For each view-type arg: generate populator content via `generate_view_populator()`, create an `IntegrationPatch` with `action="create"` for `src/vit_{type}_view.f90`
2. Idempotent: skips if populator file already exists (multiple functions may use the same view type)
3. Added `extra_fortran_sources` parameter to `_generate_cmake_patches()` — appends populator `.f90` to CMake SOURCES list

### Fix 2: `kernel_verify.py` view type support

**Problem:** `setup_kernel_cpp()` generated struct headers but didn't handle view types. The kernel needed view struct headers, populator `.f90` modules, and Makefile changes.

**Fix:** Added to `setup_kernel_cpp()` (after struct header loop):
1. For each view-type arg: generate view struct header (`_view_t.h`), generate populator Fortran module (`vit_{type}_view.f90`), write both to kernel directory
2. Pass populator source list to `_patch_kernel_makefile()`
3. Makefile patcher adds populator `.o` to `ALL_OBJS` and inserts Fortran compilation rules before the `build:` target

## Second Reflection: Additional Issue Found

After fixing all 7 issues, a second rigorous review traced every code path end-to-end. Found one additional issue:

### Kernel Makefile build ordering (found and fixed)

**Problem:** The populator `.f90` USEs `ROSCO_Types`, and the wrapper `.f90` (e.g., `Controllers.f90`) USEs the populator module. Without explicit Make dependencies, parallel build or unlucky ordering could compile them in the wrong order, producing "Cannot open module file" errors.

The initial populator rule was:
```makefile
vit_controlparameters_view.o: vit_controlparameters_view.f90
```

Missing: dependency on `ROSCO_Types.o` (provides the .mod the populator needs) and dependency of the wrapper `.o` on the populator `.o`.

**Fix:** Two dependency additions in `_patch_kernel_makefile()`:
1. Populator rule: `vit_controlparameters_view.o: vit_controlparameters_view.f90 ROSCO_Types.o`
2. Wrapper rule: Modified existing `Controllers.o: Controllers.f90 ROSCO_Types.o ...` to also depend on `vit_controlparameters_view.o`

The wrapper's Fortran `.o` file is identified from `sig.source_file` (e.g., `Controllers.f90` → `Controllers.o`).

## Items Examined and Confirmed Safe

### CMake SOURCES double-patch
Suspected that C++ source and Fortran populator patches would conflict because both append after the same `last_entry` in the SOURCES block. After tracing `apply_integration_plan()`: patches are applied sequentially, each re-reads the file, and `str.replace()` finds the first occurrence of `last_entry` (still present after prior patches). Both files end up in SOURCES correctly. No bug.

### Fortran declaration/executable ordering
The view preamble generates declarations (`TYPE(...) :: CntrPar_view`) before executables (`CALL vit_populate_...`). All declarations precede all executables in the generated wrapper. Standard-compliant Fortran.

### Config round-tripping
`save_config()` writes the `types:` section; `load_config()` reads it via `_parse_type_strategies()`. Round-trip verified.

### Stack allocation for view struct
The wrapper declares a local `controlparameters_view_t` on the stack (~2KB for ControlParameters). Well within Fortran stack conventions. `C_LOC` is valid because the local has the TARGET attribute.

### Existing functions unaffected
View type changes only activate when `view_strategy == "view"` is set in the registry. Existing 18 functions use either BIND(C) types or passthrough — unaffected. Confirmed with regression tests: saturate, PIController, ResController all 40/40.

## Remaining Known Limitations (Not Bugs)

1. **OL_ unmapped fields (11 fields):** Open-loop timeseries read from CSV files. Not scannable. 5 Phase 7B functions use them. Will need manual size mappings when those functions are translated. Deferred to Phase 7B.

2. **Populator source module fallback:** `generate_view_populator()` defaults to `source_module="ROSCO_Types"` when `type_info.source_module` is None. Correct for ROSCO, not general-purpose. Phase 12 concern.

3. **No depopulator (write-back):** View pattern is read-only for scalars. Audit confirmed no Phase 7B function writes CntrPar scalars. If a future function does, a depopulator step would be needed.

4. **View strategy on already-compatible types:** Configuring `strategy: view` on a BIND(C) type generates a view struct instead of a direct struct. Configuration error, not code error. No defensive check, not needed for ROSCO.

## Final State

### Commits (this session, VIT repo)

1. `5b51c21` — Fixes 1, 4, 5: dimension split bug, CLI apply_type_strategies, 35 unit tests
2. `f06eee9` — Fixes 2, 3: integrate.py populator generation, kernel_verify.py view support
3. `5689777` — Build ordering fix: populator and wrapper Make dependencies

### Test Results

- 486 unit tests pass (451 existing + 35 new)
- Verification regression: saturate 40/40, PIController 40/40, ResController 40/40
- All view type infrastructure validated against PitchControl signature (interface, wrapper, scaffold)

### What's Next

Step 9: end-to-end validation with a real ControlParameters function. Requires:
1. Clean ROSCO source (no integration wrappers)
2. Extract a function that takes ControlParameters
3. Translate, verify 40/40, integrate
4. Rebuild, diff against baseline

The one unknown is whether the KGen kernel's `ROSCO_Types.mod` is binary-compatible with the populator's `USE ROSCO_Types` — same compiler and flags, so it should be, but can only be verified by actually building a kernel with a view-type function.
