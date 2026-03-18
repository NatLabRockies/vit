# Phase 2: Extract, Verify, Integrate ColemanTransform, ColemanTransformInverse, identity
**Date:** 2026-03-18
**Status:** completed
**Scope:** Phase 2 execution — first subroutines and first array-valued function through the full VIT pipeline

## Overview

Pushed 3 new functions through extract → verify → integrate → rebuild → diff. These are the first subroutines (ColemanTransform, ColemanTransformInverse) and the first array-valued function result (identity) to go through the pipeline. All verified 40/40, byte-identical simulation output confirmed. 6 total functions now running as C++.

## Functions

| Function | Type | Call site | Line | Scenario | Verify |
|----------|------|-----------|------|----------|--------|
| `ColemanTransform` | SUBROUTINE | Controllers.f90 | 510 | 2 (IPC) | 40/40 |
| `ColemanTransformInverse` | SUBROUTINE | Controllers.f90 | 561 | 2 (IPC) | 40/40 |
| `identity` | FUNCTION (array result) | ControllerBlocks.f90 | 454 | 1 (EKF) | 40/40 |

`matinv3` was originally planned for Phase 2 but is dead code (zero call sites in ROSCO). Deferred like `CPfunction`.

## Translation Process

Used `vit translate` for the first time in the pipeline to generate scaffolds with correct signatures and metadata headers. This caught a bug: the scaffold generator returned `double` instead of `void` for array-valued function results. Fixed in `translate.py` by adding a `sig.result_is_array` check to `_get_c_return_type()`.

### ColemanTransform / ColemanTransformInverse

Both are pure trig functions implementing the Coleman (d-q axis) transform and its inverse. Key translation detail: both use `PI = 3.14159265359` from Constants.f90 — only 12 significant digits. The C++ must define `const double PI = 3.14159265359` and NOT use `M_PI` (which has ~16 digits of precision and would break bit-identical verification).

The scalar INTENT(OUT) args (`axTOut`, `axYOut`) become `double*` pointers in C++. The C++ writes via `*axTOut = value`. Fixed-size array args (`rootMOOP(3)`, `PitComIPC(3)`) also pass as `double*` pointers — the size is known from context, no `n_` size parameter generated (correct behavior for fixed-size arrays).

### identity

Returns an n×n identity matrix. The result is a runtime-sized 2D array (`DIMENSION(n, n)`). VIT's new array-result support transforms this into a void C function with an output pointer: `void identity(int n, double* identity_result)`. The Fortran wrapper remains a FUNCTION (drop-in replacement) but internally CALLs the BIND(C) SUBROUTINE.

For identity matrices, column-major vs row-major layout doesn't matter (I^T = I). The implementation uses column-major order (outer loop over columns) for correctness with future non-symmetric matrices.

## Bugs Found During Execution

### 1. translate.py: scaffold return type for array results

`_get_c_return_type()` returned the scalar element type (`double`) for array-valued functions instead of `void`. The scaffold generated `double identity(int n, double* identity_result) { return 0; }` instead of `void identity(...)`.

**Fix:** Added `if sig.result_is_array: return "void"` before the `sig.result_type` check.

### 2. interface_gen.py: INTEGER kind conversion function

The Fortran wrapper generated `INTEGER(nHarmonic, C_INT)` for integer kind conversion. In Fortran, `REAL(x, kind)` works because `REAL` is both a type name and a conversion intrinsic. But `INTEGER` is NOT a conversion function — the correct intrinsic is `INT(x, kind)`. The generated code failed to compile with:
```
Error: Function 'integer' at (1) has no IMPLICIT type
```

**Fix:** Added an `elif mapping.iso_c_type.startswith("INTEGER")` branch in `_build_wrapper_call_args()` that generates `INT(x, kind)` instead of the generic `base(x, kind)` pattern.

This bug was latent since Phase 0 — it never surfaced because Phase 1 functions had no INTEGER arguments with kind mismatches. ColemanTransform's `nHarmonic :: INTEGER(IntKi)` was the first to trigger it.

### 3. KGen pragma residue (recurring)

Controllers.f90 still had `!$kgen begin_callsite saturate` / `!$kgen end_callsite` pragmas from Phase 1 extractions. The pragma restoration fix (backup-after-clean) was committed but the file in the ROSCO working tree predated the fix. The pragmas propagated through extraction backup/restore cycles because each extraction backed up the already-dirty file.

Cleaned with `git checkout -- rosco/controller/src/Controllers.f90` before committing. This won't recur: all future extractions clean pragmas before backing up, so the backup is always pragma-free.

## KGen Behavior with Subroutines

First time KGen extracted a CALL site (vs function call). Worked identically — KGen instruments CALL sites the same way. The kernel driver reads state, calls the containing subroutine, and compares output state. No issues.

## KGen Behavior with Embedded Function Calls

The `identity` call site is deeply embedded in a MATMUL expression:
```fortran
LocalVar%WE%P = MATMUL(identity(3) - MATMUL(LocalVar%WE%K,H), LocalVar%WE%P)
```

KGen successfully isolated the `identity(3)` call and captured 20 invocations. No issues with the expression context.

## Extraction Reachability

Both Coleman functions are called from the IPC subroutine (`IndividualPitchControl`), which runs when `IPC_ControlMode >= 1 OR Y_ControlMode == 2`. vit_sim.py scenario 2 sets `Y_ControlMode=2`, reaching both call sites. The calls at lines 510-511 and 561-562 are unconditional within the IPC body.

`identity` is called from `WindSpeedEstimator` when `WE_Mode == 2 AND WE_Op > 0`. The DISCON.IN sets `WE_Mode = 2` (Extended Kalman Filter). The WSE warning in the simulation output confirms `WE_Op` transitions to 1 during scenario 1, so `identity(3)` is called.

## Files Changed

**ROSCO repo:**
- `translations/Functions/{colemantransform,colemantransforminverse,identity}.cpp` — new translations
- `rosco/controller/src/Functions.f90` — 3 function/subroutine bodies replaced with C++ wrappers
- `rosco/controller/CMakeLists.txt` — 3 new cpp sources added
- `rosco/controller/src/{colemantransform,colemantransforminverse,identity}_cpp.cpp` — new C++ wrapper files
- `kernel/{ColemanTransform,ColemanTransformInverse,identity}/` — golden kernel fixtures
- `vit.yaml` — 3 new functions marked as integrated

**VIT repo:**
- `vit/translate.py` — void return for array-valued results
- `vit/interface_gen.py` — INT() for integer kind conversion
- `CLAUDE.md` — 3 new extraction params
- `PROJECT_PLAN.md` — Phase 2 marked complete
