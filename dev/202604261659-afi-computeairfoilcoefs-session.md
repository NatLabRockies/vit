# AFI_ComputeAirfoilCoefs: Full Session Record — Infrastructure + Translation + Debugging
**Date:** 2026-04-26
**Status:** completed
**Scope:** AFI_ComputeAirfoilCoefs translation, view populator enhancement, NWTC utilities, VIT callee bridge improvements

## Overview

AFI_ComputeAirfoilCoefs is the main airfoil coefficient lookup in AeroDyn — called every timestep for every blade element (millions of invocations per simulation). It dispatches to 1D (single-table cubic spline) or 2D (two-table interpolation via FindBoundingTables + linear interp) based on AFTabMod. This was the 13th AirfoilInfo function and the most infrastructure-intensive translation to date.

The session involved four phases: VIT infrastructure (Table array marshalling, callee bridge improvements), NWTC utility translation, the AFI_ComputeAirfoilCoefs translation itself, and debugging a SplineCoefs dimension bug.

## Phase 1: VIT Infrastructure — Table Array Marshalling

### Problem

Previous functions that accessed `p%Table(i)` did so through the Fortran wrapper — the wrapper extracted a single table and passed it as `afi_table_type_view_t*` to C++. The C++ never needed to index the Table array. AFI_ComputeAirfoilCoefs can't use that workaround because the dispatch logic (which table to access) is in C++: the 1D function receives a `TableNum` parameter, the 2D function calls FindBoundingTables to determine bounding table indices.

The view struct had `void* Table; int32_t n_Table;` with the populator setting `Table = C_NULL_PTR` — C++ could count tables but not access them.

### Solution

Three VIT files changed:

**`view_populator.py`:** Detects ALLOCATABLE TYPE() fields whose inner type has `strategy: view` in the registry. For these fields:
- Adds `USE child_populator_module` in the module preamble
- Declares a module-level `SAVE` array of child view structs (persists between calls)
- Generates an allocate+populate loop in the populator subroutine:
  ```fortran
  IF (ALLOCATED(vit_table_views)) DEALLOCATE(vit_table_views)
  ALLOCATE(vit_table_views(SIZE(src%Table)))
  DO vit_i = 1, SIZE(src%Table)
      CALL vit_populate_afi_table_type(src%Table(vit_i), vit_table_views(vit_i))
  END DO
  view%Table = C_LOC(vit_table_views(1))
  ```

**`derived_type_parser.py`:** Changes C header from `void* Table` to `afi_table_type_view_t* Table` when the inner type is a view type.

**`kernel_verify.py`:** Adds inter-populator Makefile dependencies (parent populator .o depends on child populator .o).

Verified: FindBoundingTables kernel 21/21 IDENTICAL with the enhanced populator.

### LBOUND Fix

Also fixed a pre-existing bug: `C_LOC(src%field(1,1,1))` assumed all array dimensions start at 1. Changed to `C_LOC(src%field(LBOUND(src%field,1), LBOUND(src%field,2), LBOUND(src%field,3)))`. This is critical for SplineCoefs which has a 0-based third dimension (`0:3`). Without this fix, `C_LOC(1,1,1)` would skip the entire k=0 coefficient slice.

## Phase 2: VIT Infrastructure — Callee Bridge Improvements

### Problem: Same-File Callees

AFI_ComputeAirfoilCoefs calls 1D and 2D (internal helpers), which call FindBoundingTables, CubicSplineInterpM, MPi2Pi, and AFI_Output_ExtrapInterp1. VIT's callee detector found all of these and tried to generate Fortran bridges — but 1D, 2D, ExtrapInterp1, CubicSplineInterpM, and MPi2Pi are all implemented in C++ (either in the same file or in shared utilities). Only FindBoundingTables needs a bridge.

### Solution: Three New Skip Rules

Added to `_generate_callee_bridges()` in kernel_verify.py:

1. **Same-file skip:** If a callee's `cpp_file` in `vit.yaml` matches the target file being verified, skip the bridge. The C++ calls the function directly (static function in the same translation unit).

2. **Shared-file skip:** If a callee's `cpp_file` is in the `shared_files` list (e.g., `vit_nwtc.cpp`), skip the bridge. The C++ has its own implementation.

3. **Helper callee detection:** After skipping same-file callees, also scan their Fortran bodies for THEIR callees. Without this, FindBoundingTables (called by 2D, which is same-file) wasn't detected as needing a bridge.

These required passing `target_cpp_file` and `shared_files` parameters through the call chain.

## Phase 3: NWTC Utilities

Added three new functions to `vit_nwtc.h/.cpp`:

**`MPi2Pi(angle)`** — inline, normalizes angle to [-Pi, Pi]. Uses `std::fmod` (which can return negative) + conditional add `TwoPi` + conditional subtract `TwoPi`. Matches Fortran's `MODULO` semantics (always non-negative before the Pi check).

**`LocateBin(XVal, XAry, AryLen)`** — binary search returning 1-based lower-bound index. Returns 0 if below range, AryLen if above. Arrays are 0-based in C++ but the return value is 1-based (Fortran convention, matching how CubicSplineInterpM uses it).

**`CubicSplineInterpM(X, XAry, YAry, Coef, Res, NumPts, nCols, nCoefRows)`** — multi-column cubic spline interpolation. Uses LocateBin for segment search, then evaluates `C0 + C1*x + C2*x^2 + C3*x^3` for each column. The `nCoefRows` parameter is critical — see Phase 4.

## Phase 4: The SplineCoefs Dimension Bug

### Symptom

Kernel verification: 1/20 pass (case 1 = ConstData path, no spline). Cases 2-20 fail with moderate differences (Cl off by ~12%, Cd off by ~0.05%). The errors were consistent, not random — characteristic of systematic indexing error, not floating-point precision.

### Investigation

The differences were in spline-interpolated values (Cl, Cd, Cm, fullySeparate, fullyAttached) but NOT in scalar values from the table struct (Cd0, Cm0 — both IDENTICAL). This confirmed the Table array marshalling and view struct access were correct; the bug was specifically in CubicSplineInterpM.

### Root Cause

Fortran allocates SplineCoefs as:
```fortran
allocate(p%Table(i)%SplineCoefs(p%Table(i)%NumAlf-1, nCols, 0:3))
```

The first dimension is `NumAlf-1`, **not** `NumAlf`. The spline has `NumAlf-1` segments between `NumAlf` knot points. Each segment has its own set of polynomial coefficients.

The C++ CubicSplineInterpM was using `NumPts` (= `NumAlf` = number of knots) as the column stride for the coefficient array:
```c
int slice = NumPts * nCols;  // WRONG: should be (NumPts-1) * nCols
```

This caused every column access to be offset by `nCols` extra elements — reading coefficients from the wrong segment.

### Fix

Added `nCoefRows` parameter to `CubicSplineInterpM`:
```c
void CubicSplineInterpM(double X, const double* XAry, const double* YAry,
                        const double* Coef, double* Res,
                        int NumPts, int nCols, int nCoefRows);
```

Caller passes `tab->n_SplineCoefs_dim1` (which is `NumAlf-1`). The implementation uses `nCoefRows` for the coefficient stride:
```c
int slice = nCoefRows * nCols;  // Correct stride for 3rd dimension
for (int c = 0; c < nCols; c++) {
    int base = c * nCoefRows + iLo0;
    Res[c] = Coef[0*slice + base] + Coef[1*slice + base]*XOff + ...;
}
```

Result: 20/20 IDENTICAL.

### Lesson

Don't assume the coefficient array has the same dimensions as the data array. In cubic spline interpolation, there are N knot points but only N-1 segments — and each segment has its own polynomial coefficients. The Fortran source makes this explicit in the ALLOCATE statement, but it's easy to miss when writing the C++ translation. The ROSCO scipy/FITPACK bug was different in mechanism (uninitialized variable vs dimension mismatch), but the user's instinct to check for "old Fortran spline code bugs" pointed in the right direction — spline implementations have subtle dimension assumptions.

## Phase 5: Integration

Integration hit two additional issues:

1. **Stale production `vit_types.h`:** The existing file had `void* Table` and was missing `afi_outputtype_t`. The `generate_consolidated_types_header` skips types already present (via include guard check), so regeneration didn't fix it. Solution: manually edited the file — changed `void*` to `afi_table_type_view_t*` and added the AFI_OutputType struct.

2. **Stub .cpp files from extraction:** `reset_to_clean.sh` replaced all `.cpp` files with stubs for KGen extraction, and they weren't restored before integration/rebuild. Caused linker errors (`undefined reference to 'findboundingtables_c'`). Solution: `git checkout HEAD -- modules/aerodyn/src/*.cpp`.

Both issues are procedural — the extraction workflow modifies the source tree and the developer must restore it. The second issue suggests `reset_to_clean.sh` should have a companion `restore_integrated.sh` or the extraction should save/restore automatically.

## Final Verification

- **Kernel:** 20/20 IDENTICAL (ad_BAR_SineMotion, AFTabMod=1 path)
- **Baselines:** 16/16 PASS (all test cases including AFTabMod=2 Re-interpolation paths)
- **Infrastructure regression:** FindBoundingTables 21/21 IDENTICAL (Table array now populated, not NULL)

## Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `bf92f1c` | View populator: ALLOCATABLE TYPE() array marshalling |
| VIT | `4930998` | LBOUND fix, same-file/shared callee skip, helper callee detection |
| VIT | `9166757` | Dev note update |
| OpenFAST | `dba3ba849` | Translation + NWTC utilities + kernel (1/20 initial) |
| OpenFAST | `baf50e071` | Integration: 20/20 + 16/16, SplineCoefs fix, vit_types.h fix |

## VIT Infrastructure Additions (Summary)

| Capability | Where | What it enables |
|------------|-------|----------------|
| ALLOCATABLE TYPE() view arrays | view_populator.py, derived_type_parser.py | C++ can index `p->Table[i]` for arbitrary i |
| LBOUND-based C_LOC | view_populator.py | Correct pointer for 0-based dimensions |
| Same-file callee skip | kernel_verify.py | Multi-function translations in one .cpp |
| Shared-file callee skip | kernel_verify.py | NWTC utility calls don't generate bridges |
| Helper callee detection | kernel_verify.py | Callees of same-file helpers get bridges |
| MPi2Pi, LocateBin, CubicSplineInterpM | vit_nwtc.h/.cpp | Angle normalization, binary search, cubic spline |
