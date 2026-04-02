# Phase 10B Step 2: ReadCpFile C++ Translation

**Date:** 2026-04-01
**Status:** completed
**Scope:** Translate ReadCpFile to idiomatic C++ — first ALLOCATABLE pre-allocation test for Stage B

## What This Is

ReadCpFile reads rotor performance tables (Cp, Ct, Cq) from a fixed-format text file into `PerformanceData`. It is the second Stage B translation and the first to test the **pre-allocation pattern** — where the Fortran wrapper ALLOCATEs arrays before creating the view struct, so C++ receives valid pointers to fill.

This was the ideal second candidate because:
1. It's small (84 lines Fortran → 145 lines C++) — low risk
2. It exercises the ALLOCATABLE allocation-then-fill pattern needed for Step 3
3. The array sizes are known upfront from `CntrPar%PerfTableSize` (set by ReadControlParameterFileSub before ReadCpFile is called)
4. It does real file I/O (`std::ifstream`), proving C++ can read the same files Fortran does

## The Performance Table File Format

The Cp/Ct/Cq file (e.g., `Cp_Ct_Cq.NREL5MW.txt`) has a fixed structure:

```
# Header comment (line 1)
# Header comment (line 2)
                                        ← blank line
# Pitch angle vector, 36 entries...     ← comment
-5.0  -4.0  -3.0  ...  30.0            ← Beta_vec (PerfTableSize(1) values)
# TSR vector, 26 entries...             ← comment
2.0  2.5  3.0  ...  14.5               ← TSR_vec (PerfTableSize(2) values)
# Wind speed vector                     ← comment (ignored)
11.4                                    ← wind speed (ignored)
                                        ← blank
# Power coefficient                     ← comment
                                        ← blank
0.006673  0.009813  ...                 ← Cp row 1 (TSR=2.0, all pitches)
0.020093  0.026879  ...                 ← Cp row 2 (TSR=2.5, all pitches)
...                                     ← 26 rows total
                                        ← blank
                                        ← blank
# Thrust coefficient                    ← comment
                                        ← blank
...                                     ← Ct matrix (same shape)
                                        ← blank × 2
# Torque coefficient                    ← comment
                                        ← blank
...                                     ← Cq matrix (same shape)
```

For NREL-5MW: 36 pitch angles × 26 TSR values = 936 entries per matrix, 3 matrices = 2,808 total values plus 2 axis vectors.

## The Pre-Allocation Pattern

This is the key architectural pattern that Stage B introduces. During the mixed Fortran/C++ phase:

1. **Fortran owns the memory** — all ALLOCATABLE arrays live in Fortran's `TYPE(PerformanceData)`
2. **The view struct is read/write pointers** — `performancedata_view_t` has `double* Cp_mat` + `int32_t n_Cp_mat_rows` + `int32_t n_Cp_mat_cols`
3. **C++ fills through the pointers** — it writes directly into Fortran-allocated memory

The sequence in the Fortran wrapper:

```fortran
! 1. Pre-allocate all arrays (sizes known from CntrPar%PerfTableSize)
ALLOCATE(PerfData%Beta_vec(CntrPar%PerfTableSize(1)))     ! 36 pitch angles
ALLOCATE(PerfData%TSR_vec(CntrPar%PerfTableSize(2)))      ! 26 TSR values
ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))  ! 26×36
ALLOCATE(PerfData%Ct_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
ALLOCATE(PerfData%Cq_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))

! 2. Populate views (now C_LOC works because arrays are allocated)
CALL vit_populate_controlparameters(CntrPar, CntrPar_view)
CALL vit_populate_performancedata(PerfData, PerfData_view)

! 3. Call C++ to read the file and fill the arrays
CALL readcpfile_c(C_LOC(CntrPar_view), C_LOC(PerfData_view), C_LOC(ErrVar))
```

After `readcpfile_c` returns, the Fortran `PerfData%Cp_mat` etc. contain the values C++ wrote — because the view struct's pointers point directly into the Fortran-allocated memory.

### Why This Pattern Is Needed

The view populator (`vit_populate_performancedata`) calls `C_LOC(src%Cp_mat(1,1))` to get the address of the first element. If the array isn't allocated, there is no element, and `C_LOC` would crash or return garbage. So allocation MUST happen before view population.

For ReadCpFile, this is straightforward because `PerfTableSize` is already set by the config reader. For Step 3 (ReadControlParameterFileSub), it's harder because the config reader itself determines the array sizes — requiring the two-pass protocol.

## Column-Major Matrix Layout

Fortran stores matrices in column-major order. `Cp_mat(i, j)` is stored at offset `(j-1)*n_rows + (i-1)` (0-indexed). The view struct exposes this layout:

```c
typedef struct {
    double* Cp_mat;           // column-major: mat[col * n_rows + row]
    int32_t n_Cp_mat_rows;    // = PerfTableSize(2) = number of TSR values
    int32_t n_Cp_mat_cols;    // = PerfTableSize(1) = number of pitch angles
    ...
} performancedata_view_t;
```

Each line in the performance file is one row (one TSR value, all pitch angles). In C++:

```cpp
// Each file line: n_cols values for one TSR (row)
for (int row = 0; row < n_rows; row++) {
    // read line
    for (int col = 0; col < n_cols; col++) {
        mat[col * n_rows + row] = value;  // column-major
    }
}
```

This is the same pattern used by `interp2d.cpp` and `aerodyntorque.cpp` — the `Z(row,col)` macro from TRANSLATION_TIPS.md.

## C++ Translation Details

**File:** `translations/ReadSetParameters/readcpfile.cpp` (145 lines)

**Key implementation choices:**

1. **`std::ifstream` + `std::istringstream`** for line-based parsing. Each line is read with `std::getline`, then values extracted with `>>` from an `istringstream`. This matches Fortran's `READ(unit, *)` free-format behavior.

2. **`skipLines()` helper** replaces Fortran's `ReadEmptyLine` — just reads and discards N lines. No validation needed for skipped lines.

3. **`readMatrix()` helper** reads `n_rows` lines, each with `n_cols` values, into column-major storage. Error handling matches the Fortran pattern (set `ErrVar->aviFAIL = -1` on parse failure).

4. **`trimFortranString()` helper** extracts the filename from the Fortran space-padded `PerfFileName` CHARACTER field. Strips trailing spaces and returns a `std::string`.

5. **Error messages** reproduce the Fortran error text pattern, including the RoutineName prefix.

## Fortran Wrapper

```fortran
SUBROUTINE ReadCpFile(CntrPar, PerfData, ErrVar)
    USE, INTRINSIC :: ISO_C_Binding
    USE ROSCO_Types, ONLY : PerformanceData, ControlParameters, ErrorVariables
    USE vit_controlparameters_view, ONLY: controlparameters_view_t, vit_populate_controlparameters
    USE vit_performancedata_view, ONLY: performancedata_view_t, vit_populate_performancedata
    IMPLICIT NONE
    TYPE(ControlParameters),    INTENT(INOUT), TARGET :: CntrPar
    TYPE(PerformanceData),      INTENT(INOUT), TARGET :: PerfData
    TYPE(ErrorVariables),       INTENT(INOUT), TARGET :: ErrVar
    TYPE(controlparameters_view_t), TARGET :: CntrPar_view
    TYPE(performancedata_view_t), TARGET :: PerfData_view
    INTERFACE
        SUBROUTINE readcpfile_c(CntrPar, PerfData, ErrVar) BIND(C, NAME='readcpfile_c')
            USE ISO_C_BINDING
            TYPE(C_PTR), VALUE :: CntrPar
            TYPE(C_PTR), VALUE :: PerfData
            TYPE(C_PTR), VALUE :: ErrVar
        END SUBROUTINE readcpfile_c
    END INTERFACE
    ! Pre-allocate arrays
    ALLOCATE(PerfData%Beta_vec(CntrPar%PerfTableSize(1)))
    ALLOCATE(PerfData%TSR_vec(CntrPar%PerfTableSize(2)))
    ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
    ALLOCATE(PerfData%Ct_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
    ALLOCATE(PerfData%Cq_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
    ! Populate views and call C++
    CALL vit_populate_controlparameters(CntrPar, CntrPar_view)
    CALL vit_populate_performancedata(PerfData, PerfData_view)
    CALL readcpfile_c(C_LOC(CntrPar_view), C_LOC(PerfData_view), C_LOC(ErrVar))
END SUBROUTINE ReadCpFile
```

Unlike CheckInputs (which only reads existing data), ReadCpFile's wrapper contains **domain-specific allocation logic** — it knows the relationship between `PerfTableSize` and the array dimensions. This is why `vit integrate` can't be used: the standard integration generates a simple pass-through wrapper, but ReadCpFile needs pre-allocation before the view can be created.

## Integration Automation

Added to `integrate_all.sh` as the second Stage B step (after CheckInputs). Uses the same Python `re.sub` pattern to replace the subroutine body:

```bash
pattern = r'(    SUBROUTINE ReadCpFile\(CntrPar\s*,\s*PerfData\s*,\s*ErrVar\)).*?(    END SUBROUTINE ReadCpFile)'
```

The regex uses `\s*,\s*` for the first comma to handle both `ReadCpFile(CntrPar,PerfData` (no space) and `ReadCpFile(CntrPar, PerfData` (with space) — the Fortran source has inconsistent spacing in the signature.

**Total:** integrate_all.sh now handles 43 functions (41 Track A + 2 Stage B).

## Verification

**Method:** Simulation-level comparison (not KGen)
**Result:** ALL SCENARIOS IDENTICAL — 692,000 total values compared
**Build config:** `-ffp-contract=off`

ReadCpFile is called only during initialization (`iStatus == 0`, inside `SetParameters`), and only when `WE_Mode > 0` (wind speed estimator active). It populates the Cp/Ct/Cq lookup tables used by `WindSpeedEstimator` and `AeroDynTorque`. The bit-identical output across all 10 scenarios confirms the tables were read correctly and the column-major layout is right.

## Implications for Step 3 (ReadControlParameterFileSub)

ReadCpFile validates the single-pass pre-allocation pattern: Fortran allocates → view → C++ fills. Step 3 needs a **two-pass** variant because the config reader itself determines the sizes:

1. **Pass 1:** C++ reads the file, extracts all scalars and size-determining integers into the view
2. **Fortran wrapper:** Copies scalars from view to CntrPar, ALLOCATEs all ~68 arrays using the sizes
3. **Pass 2:** C++ fills array values through the now-valid view pointers

The pre-allocation pattern proven here (allocate → populate view → call C++) is the foundation for the Fortran wrapper's "between passes" step.

## Files Modified

| File | Repo | Change |
|------|------|--------|
| `translations/ReadSetParameters/readcpfile.cpp` (new) | ROSCO | Canonical C++ translation (145 lines) |
| `rosco/controller/src/readcpfile.cpp` (new) | ROSCO | Built copy |
| `rosco/controller/CMakeLists.txt` | ROSCO | Add readcpfile.cpp to SOURCES |
| `rosco/controller/src/vit_translated.h` | ROSCO | Add readcpfile_c declaration |
| `scripts/integrate_all.sh` | ROSCO | Update to 43 functions, add ReadCpFile Stage B step |

## Commit

ROSCO `47e071f`: Phase 10B: ReadCpFile C++ translation (Step 2)
