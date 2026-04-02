# Phase 10B Step 1: CheckInputs C++ Translation

**Date:** 2026-04-01
**Status:** completed
**Scope:** First non-KGen ("idiomatic C++") translation — CheckInputs validation logic from ReadSetParameters.f90

## What This Is

CheckInputs is the first Stage B translation — the first function translated to C++ and verified via simulation-level comparison rather than KGen kernel verification. It validates all 225+ controller parameters parsed from DISCON.IN (range checks, mode flag consistency, cross-parameter constraints) and sets `ErrVar%aviFAIL = -1` on any failure.

The function is pure validation logic: no file I/O, no memory allocation, no state mutation beyond error reporting. This made it the ideal first Stage B candidate because it tests the manual integration pattern (Fortran wrapper + view struct) without the complexity of ALLOCATABLE pre-allocation or file I/O.

## Translation Details

**Fortran source:** `ReadSetParameters.f90` lines 950-1806 (857 lines)
**C++ translation:** `translations/ReadSetParameters/checkinputs.cpp` (840 lines)
**Built copy:** `rosco/controller/src/checkinputs.cpp` (copied during integration)

### C++ Signature

```cpp
void CheckInputs(localvariables_t* LocalVar, controlparameters_view_t* CntrPar,
                 float* avrSWAP, errorvariables_t* ErrVar, int32_t size_avcMSG)
```

### Key Translation Patterns

**Error reporting:** Fortran CHARACTER fields are space-padded (not null-terminated). The `setError()` helper fills the 1024-byte `ErrMsg` field with spaces, then memcpy's the message:
```cpp
static void setError(errorvariables_t* ErrVar, const char* msg) {
    ErrVar->aviFAIL = -1;
    std::memset(ErrVar->ErrMsg, ' ', 1024);
    size_t len = std::strlen(msg);
    if (len > 1024) len = 1024;
    std::memcpy(ErrVar->ErrMsg, msg, len);
}
```

**NonDecreasing helper:** Matches Fortran semantics exactly — uses `<=` not `<`:
```cpp
static bool NonDecreasing(const double* arr, int n) {
    for (int i = 0; i < n - 1; i++) {
        if (arr[i + 1] - arr[i] <= 0.0) return false;
    }
    return true;
}
```

**ALLOCATABLE array access:** View struct exposes ALLOCATABLE arrays as pointer+size pairs. C++ accesses via `CntrPar->FieldName[i]` (0-indexed) with size from `CntrPar->n_FieldName`. Fortran intrinsics translated:
- `ANY(arr <= 0)` → loop with early return
- `ALL(arr < 1)` → loop checking all elements
- `MAXVAL(arr)` → `*std::max_element(ptr, ptr + n)`
- `MINVAL(arr)` → `*std::min_element(ptr, ptr + n)`

**AddToList / dynamic array:** Fortran uses `ALLOCATABLE All_OL_Indices(:)` + `AddToList` (append-with-realloc). C++ uses `std::vector<int>` with `push_back`. The initial population uses direct `push_back` instead of the Fortran array constructor:
```cpp
std::vector<int> allOlIndices;
allOlIndices.push_back(CntrPar->Ind_BldPitch[0]);
allOlIndices.push_back(CntrPar->Ind_BldPitch[1]);
allOlIndices.push_back(CntrPar->Ind_BldPitch[2]);
allOlIndices.push_back(CntrPar->Ind_GenTq);
allOlIndices.push_back(CntrPar->Ind_YawRate);
allOlIndices.push_back(CntrPar->Ind_R_Speed);
allOlIndices.push_back(CntrPar->Ind_R_Torque);
allOlIndices.push_back(CntrPar->Ind_R_Pitch);
for (int i = 0; i < CntrPar->n_Ind_CableControl; i++)
    allOlIndices.push_back(CntrPar->Ind_CableControl[i]);
for (int i = 0; i < CntrPar->n_Ind_StructControl; i++)
    allOlIndices.push_back(CntrPar->Ind_StructControl[i]);
```

**avrSWAP indexing:** Fortran 1-based → C++ 0-based. `NINT(avrSWAP(10))` → `static_cast<int>(std::round(avrSWAP[9]))`.

**Constants from rosco_constants.h:** `VS_Mode_WSE_TSR`, `VS_Mode_Power_TSR` — used in the TRA_Mode frequency avoidance check. Not redefined in the .cpp file.

**PRINT statements:** Translated to `printf()` for informational messages (PRC_Mode note, TRA_Mode warning, AWC/IPC warning).

**RoutineName prefix:** At the end of the function, if `aviFAIL < 0`, the error message gets "CheckInputs:" prepended:
```cpp
if (ErrVar->aviFAIL < 0) {
    char tmp[1024];
    int msgLen = 1024;
    while (msgLen > 0 && ErrVar->ErrMsg[msgLen - 1] == ' ') msgLen--;
    std::snprintf(tmp, sizeof(tmp), "CheckInputs:%.*s", msgLen, ErrVar->ErrMsg);
    setError(ErrVar, tmp);
}
```

### Fortran Bugs Faithfully Reproduced

1. **IPC_KP check uses IPC_KI (lines 1254-1262):** The Fortran source checks `CntrPar%IPC_KI(1)` and `CntrPar%IPC_KI(2)` but prints error messages about `IPC_KP(1)` and `IPC_KP(2)`. The C++ faithfully reproduces this bug.

2. **StC_Mode error message says CC_Mode (line 1630):** The Fortran error message for `Ind_StructControl` says "CC_Mode must be 2" instead of "StC_Mode must be 2". Reproduced in C++.

## Fortran Wrapper

The wrapper replaces the CheckInputs subroutine body in `ReadSetParameters.f90`:

```fortran
SUBROUTINE CheckInputs(LocalVar, CntrPar, avrSWAP, ErrVar, size_avcMSG)
    USE, INTRINSIC :: ISO_C_Binding
    USE ROSCO_Types, ONLY : LocalVariables, ControlParameters, ErrorVariables
    USE vit_controlparameters_view, ONLY: controlparameters_view_t, vit_populate_controlparameters
    IMPLICIT NONE
    TYPE(ControlParameters),    INTENT(INOUT), TARGET :: CntrPar
    TYPE(LocalVariables),       INTENT(INOUT), TARGET :: LocalVar
    TYPE(ErrorVariables),       INTENT(INOUT), TARGET :: ErrVar
    INTEGER(IntKi),             INTENT(IN   )         :: size_avcMSG
    REAL(ReKi),                 INTENT(IN   )         :: avrSWAP(*)
    TYPE(controlparameters_view_t), TARGET :: CntrPar_view
    INTERFACE
        SUBROUTINE checkinputs_c(LocalVar, CntrPar, avrSWAP, ErrVar, size_avcMSG) BIND(C, NAME='checkinputs_c')
            USE ISO_C_BINDING
            TYPE(C_PTR), VALUE :: LocalVar
            TYPE(C_PTR), VALUE :: CntrPar
            REAL(C_FLOAT), INTENT(IN) :: avrSWAP(*)
            TYPE(C_PTR), VALUE :: ErrVar
            INTEGER(C_INT32_T), VALUE :: size_avcMSG
        END SUBROUTINE checkinputs_c
    END INTERFACE
    CALL vit_populate_controlparameters(CntrPar, CntrPar_view)
    CALL checkinputs_c(C_LOC(LocalVar), C_LOC(CntrPar_view), avrSWAP, C_LOC(ErrVar), INT(size_avcMSG, C_INT32_T))
END SUBROUTINE CheckInputs
```

### avrSWAP Passing Issue

`avrSWAP(*)` is an assumed-size array and cannot be passed via `C_LOC` (Fortran requires TARGET attribute, which assumed-size arrays cannot have). The solution is to pass `avrSWAP` directly in the BIND(C) interface as `REAL(C_FLOAT), INTENT(IN) :: avrSWAP(*)` rather than as a `C_PTR`. This works because Fortran passes arrays by reference, which is compatible with C's `float*` pointer. Other functions with assumed-size avrSWAP (e.g., structuralcontrol_c, variablespeedcontrol_c) use the same pattern.

## Integration Approach: Manual, Not `vit integrate`

This is the first function that uses manual integration rather than `vit integrate`. The reasons:

1. **No KGen kernel:** `vit integrate` is designed for KGen-verified functions. CheckInputs has no kernel.
2. **Custom wrapper:** The wrapper needs to create a view struct, but doesn't need the pre-allocation logic that ReadCpFile and ReadControlParameterFileSub will need.
3. **ReadSetParameters.f90 complexity:** This file already has two other integrated functions (ReadAvrSWAP via `vit integrate`), and the CheckInputs integration needs to work alongside them.

### How Manual Integration Works in integrate_all.sh

The `integrate_all.sh` script was updated from 41 to 42 functions. After all 41 Track A `vit integrate` steps, a "Stage B" section runs:

1. Copies `translations/ReadSetParameters/checkinputs.cpp` → `rosco/controller/src/checkinputs.cpp`
2. Uses Python regex (`re.sub`) to replace the CheckInputs subroutine body in ReadSetParameters.f90 with the Fortran wrapper
3. The regex matches from `SUBROUTINE CheckInputs(...)` to `END SUBROUTINE CheckInputs` (with `re.DOTALL` for multi-line matching)

This approach ensures the integration works regardless of whether ReadSetParameters.f90 has its original Fortran or has already been partially modified by prior Track A integrations (ReadAvrSWAP).

## Build System Changes

### CMakeLists.txt

Two changes:
1. Added `src/checkinputs.cpp` to the SOURCES list
2. Added `target_include_directories(discon PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../translations)` — this resolves `#include "vit_types.h"` and `#include "rosco_constants.h"` in all .cpp files. Previously these headers were found via relative paths (e.g., `../../translations/vit_types.h`) but this was inconsistent across files. The include directory approach is cleaner and fixes readavrswap.cpp and pidcontroller.cpp which used bare `#include "vit_types.h"`.

### vit_translated.h

Added declaration:
```c
void checkinputs_c(localvariables_t* LocalVar, controlparameters_view_t* CntrPar, float* avrSWAP, errorvariables_t* ErrVar, int32_t size_avcMSG);
```

**Important caveat:** `prepare_commit.sh` restores `vit_translated.h` to its committed state via `git checkout`. After committing the CheckInputs declaration, future runs of `prepare_commit.sh` will preserve it. But when adding the NEXT Stage B function, the same issue will occur — the new declaration must be added to `vit_translated.h` AND committed, or prepare_commit.sh will strip it.

## prepare_commit.sh Glob Bug Fix (Second Instance)

### The Bug

`git checkout -- rosco/controller/src/*.cpp` expands the glob to include ALL .cpp files, including UNTRACKED ones (like `checkinputs.cpp` before it was committed). When git encounters an untracked file in the glob expansion, it fails with "error: pathspec 'checkinputs.cpp' did not match any file(s) known to git" and **aborts the entire checkout** — meaning NONE of the tracked .cpp files get restored.

The `2>/dev/null || true` suppressed the error but didn't fix it. The tracked files remained in their stale state (stubs from reset_to_clean.sh), which caused 30,000+ line diffs in git status.

### The Fix

Same pattern as the DISCON.IN glob fix from earlier today:
```bash
tracked_cpp=$(git ls-files 'rosco/controller/src/*.cpp' 'rosco/controller/src/vit_translated.h')
if [ -n "$tracked_cpp" ]; then
    echo "$tracked_cpp" | xargs git checkout --
fi
```

`git ls-files` returns only tracked files, so `git checkout` never sees untracked files.

### Also Fixed: baseline_arrays Preservation

`prepare_commit.sh` previously deleted `baseline_arrays/` as a transient artifact. But baseline arrays are now committed as the canonical reference for simulation comparisons. Updated to only delete `integrated_arrays/` (which IS transient).

## Baseline Arrays

Committed `baseline_arrays/` with all 10 scenarios. These are the canonical reference for simulation-level verification:

| Scenario | Description | Values |
|----------|-------------|--------|
| 1 | Basic (all modes default) | 200,000 (5 arrays × 40,000) |
| 2 | Wrap_360 exercise | 12,000 (3 arrays × 4,000) |
| 3 | Mode coverage (notch + cable + flap + structural) | 80,000 |
| 4 | PIIController (Flp_Mode=2) | 20,000 |
| 5 | Extended scenario 3 | 80,000 |
| 6 | NotchFilterSlopes | 20,000 |
| 7 | Synthetic non-zero inputs | 120,000 |
| 8 | IPC + AWC with blade moments | 80,000 |
| 9 | SD_Mode=1, SU_Mode=1 | 60,000 |
| 10 | OL_Mode=2 azimuth tracking | 20,000 |
| **Total** | | **692,000** |

The baseline is built from clean Fortran (no C++ integrations). Verification confirms that integrated builds (41 Track A + N Stage B) produce bit-identical output.

## Verification

**Method:** Simulation-level comparison (not KGen)
**Result:** ALL SCENARIOS IDENTICAL — 692,000 total values compared
**Build config:** `-ffp-contract=off` (as always during verification phase)

The verification workflow:
1. `reset_to_clean.sh` → build clean Fortran → `vit_sim.py --output-dir baseline_arrays`
2. `integrate_all.sh` (42 functions: 41 Track A + CheckInputs) → rebuild → `vit_sim.py --output-dir integrated_arrays`
3. Compare all `.npz` files: `np.array_equal(baseline[key], integrated[key])` for every array in every scenario

## Why CheckInputs Verification Is Guaranteed

CheckInputs is pure validation. It only reads CntrPar/LocalVar/avrSWAP fields and writes to ErrVar. If no validation error triggers (aviFAIL remains 0), the simulation proceeds identically regardless of whether the validation was done in Fortran or C++. The simulation output can only differ if:
1. C++ triggers a false error (aviFAIL set to -1 when Fortran wouldn't) — simulation aborts
2. C++ misses a real error (aviFAIL stays 0 when Fortran would set -1) — simulation continues but potentially with invalid parameters

Since all 10 scenarios completed successfully and produced identical output, neither case occurred.

## Files Modified

| File | Repo | Change |
|------|------|--------|
| `translations/ReadSetParameters/checkinputs.cpp` (new) | ROSCO | Canonical C++ translation (840 lines) |
| `rosco/controller/src/checkinputs.cpp` (new) | ROSCO | Built copy (stub on main, real content during integration) |
| `rosco/controller/CMakeLists.txt` | ROSCO | Add checkinputs.cpp to SOURCES, add translations include dir |
| `rosco/controller/src/vit_translated.h` | ROSCO | Add checkinputs_c declaration |
| `scripts/integrate_all.sh` | ROSCO | Update to 42 functions, add Stage B manual integration |
| `scripts/prepare_commit.sh` | ROSCO | Fix .cpp glob bug (use git ls-files), preserve baseline_arrays |
| `baseline_arrays/*.npz` (new) | ROSCO | 10 scenario baseline arrays (692,000 values) |
| `vit.yaml` | ROSCO | Remove stale ReadAvrSWAP metadata fields |

## Commit

ROSCO `f93641d`: Phase 10B: CheckInputs C++ translation + baseline arrays + script fixes

## What's Next

**Step 2: ReadCpFile** (84 lines) — first ALLOCATABLE pre-allocation test. The C++ function reads Cp/Ct/Cq performance tables from a fixed-format text file. The Fortran wrapper must pre-allocate PerfData arrays (sizes known from CntrPar%PerfTableSize) before creating the view and calling C++. This tests the two-step allocation pattern needed for Step 3.

**Step 3: ReadControlParameterFileSub** (559 lines) — the big one. Two-pass protocol: C++ parses sizes → Fortran allocates → C++ fills values. Includes DISCON.IN parser + YAML support (yaml-cpp).
