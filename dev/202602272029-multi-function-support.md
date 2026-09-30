# Multi-Function Extraction, Verification, and Integration
**Date:** 2026-02-27
**Status:** completed
**Scope:** cli.py, integrate.py, tests

## Background

VIT was built around a single function (saturate) as the proof-of-concept
target. Once saturate was working end-to-end, the next step was proving that
VIT can handle multiple functions in the same codebase — extracting, verifying,
and integrating them sequentially without interference.

The second target function was `wrap_180` (angle wrapping to +/-180 degrees,
defined in Functions.f90:462-474). This immediately exposed two architectural
problems: kernel directory collisions during extraction, and broken CMake
patching during integration.

## Part 1: Per-Function Kernel Directories (a605bcc)

### The Problem

KGen extraction dumps all generated files into a flat `kernel/` directory.
When extracting a second function, KGen overwrites the first function's kernel
files. There is no namespacing — `kernel_driver.f90`, `kernel.f90`, and the
state data files all have fixed names.

### The Fix

After KGen extraction completes, VIT now moves the loose files from `kernel/`
into `kernel/{function_name}/`. The move preserves any existing function
subdirectories, so extracting wrap_180 does not disturb the saturate kernel
that was already moved to `kernel/saturate/`.

The `vit verify` and `vit run` commands were updated with fallback path
resolution: if `--kernel-dir` is not specified, they check `kernel/{function_name}/`
before falling back to `kernel/`. This means the common case (`vit verify saturate`)
just works without needing to specify `--kernel-dir kernel/saturate`.

### Finding the Right Call Site for wrap_180

wrap_180 is called from two locations in ROSCO:

1. `Controllers.f90:400` — inside a `Y_ControlMode == 1` branch
2. `Filters.f90:430` — inside the yaw error filter, always active

The default ROSCO simulation configuration (`DISCON.IN`) sets `Y_ControlMode = 0`,
which means the Controllers.f90 call site is never reached during simulation.
KGen would instrument the code, run the simulation, and capture zero
invocations — resulting in an empty kernel with no test cases.

Using Filters.f90:430 as the call site captures 20 invocations (the standard
count for a ROSCO simulation run), giving us the same 40 cases (20 invocations
x 2 directions: input capture + output verification) as saturate.

### wrap_180 Translation

The Fortran function is straightforward:

```fortran
FUNCTION wrap_180(x) RESULT(y)
    REAL(DbKi), INTENT(IN) :: x
    REAL(DbKi) :: y
    y = MODULO(x + 180.0_DbKi, 360.0_DbKi) - 180.0_DbKi
END FUNCTION
```

The C++ translation uses `std::fmod` with an adjustment for Fortran's MODULO
semantics (always non-negative result, unlike C's fmod which preserves sign).

## Part 2: Fix Multi-Function Integration (745a16e)

### The Problem

When running `vit integrate saturate saturate.cpp --apply`, the Fortran source
patches (adding the BIND(C) wrapper, ISO_C_BINDING, INTERFACE block) worked
correctly. But the two CMake patches — adding the .cpp source file to the
target and linking stdc++ — silently did nothing.

The root cause: both CMake patches used `action='insert'` in the integration
plan. The `apply_integration_plan()` function's handling of `action='insert'`
was a no-op — it was defined in the plan schema but never implemented in the
apply logic. The patches appeared in `vit integrate` dry-run output (showing
what would be done) but `--apply` skipped them without error.

This meant that after `vit integrate --apply`, the user had to manually run
sed commands to edit CMakeLists.txt. Not only was this error-prone, but it
completely broke the sequential multi-function workflow: the second function's
integration would not know about the first function's manual edits.

### The Fix

Converted both CMake patches from `action='insert'` to `action='modify'`. A
modify action specifies `original` text (what to find in the file) and
`replacement` text (what to substitute). For the source file addition:

- **Original**: `set(SOURCES src/Functions.f90 ...)`
- **Replacement**: `set(SOURCES src/Functions.f90 src/saturate_cpp.cpp ...)`

For the stdc++ link:

- **Original**: `target_link_libraries(discon ...existing libs...)`
- **Replacement**: `target_link_libraries(discon ...existing libs... stdc++)`

This approach is inherently composable for sequential integration. When
integrating wrap_180 after saturate, the modify action finds the current
CMakeLists.txt text (which already includes `saturate_cpp.cpp` and `stdc++`)
and patches in `wrap_180_cpp.cpp` alongside the existing entries. The stdc++
link patch detects that stdc++ is already present and skips itself.

### Idempotency

Each patch checks whether its modification has already been applied:

- Source file addition: checks if `{function}_cpp.cpp` already appears in the
  SOURCES list.
- stdc++ link: checks if `stdc++` already appears in target_link_libraries.
- `enable_language(CXX)`: checks if the directive is already present.
- `USE ISO_C_BINDING`: checks if the USE statement is already in the module.

This means running `vit integrate saturate --apply` twice is safe — the second
run reports "already present" for each patch and makes no changes.

### Tests

Added 10 new tests covering sequential multi-function integration:

- Integrating saturate alone produces correct CMake and Fortran patches.
- Integrating wrap_180 after saturate produces correct incremental patches.
- CMake patches are idempotent (re-integrating the same function is a no-op).
- Fortran patches are idempotent.
- The combined result after both integrations contains all expected elements.

## Verification

- saturate: 40/40 passed
- wrap_180: 40/40 passed
- Full end-to-end: integrate both, rebuild ROSCO, diff simulation output
  against pure-Fortran baseline = empty diff.

## Files Changed

- `vit/vit/cli.py`: Per-function kernel directory creation and fallback resolution.
  29 insertions(+), 3 deletions(-).
- `vit/vit/integrate.py`: CMake patch generation rewritten from insert to modify.
  Idempotency checks for all patch types. 245 insertions(+), 16 deletions(-).
- `vit/tests/test_integrate.py`: 10 new tests for sequential multi-function
  integration.

## Commits

- a605bcc — Per-function kernel directories and wrap_180 translation
- 745a16e — Fix multi-function integration (~18 minutes later)
