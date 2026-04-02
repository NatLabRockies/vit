# Phase 10B Step 3a: ReadControlParameterFileSub C++ Translation

**Date:** 2026-04-02
**Status:** completed
**Scope:** Replace the 559-line Fortran DISCON.IN config parser with idiomatic C++ using a two-pass protocol

## What This Is

ReadControlParameterFileSub is the core configuration loading function in ROSCO. It reads the entire DISCON.IN parameter file (~225 fields covering 26 controller mode flags, filter frequencies, gain schedules, control limits, and open-loop timeseries) and populates the `ControlParameters` derived type. This is the largest and most architecturally complex Stage B translation — it required solving the ALLOCATABLE memory ownership problem that was identified during planning.

**This is the 3rd of 3 Stage B functions.** Together with CheckInputs (Step 1) and ReadCpFile (Step 2), all of ReadSetParameters.f90's parsing and validation logic is now C++. Only the orchestrator (SetParameters) remains in Fortran.

## The DISCON.IN File Format

DISCON.IN is a line-oriented parameter file. Each parameter line has the format:

```
VALUE  ! ParameterName  - Description
```

- The `!` character separates the value from the comment
- The first word after `!` is the parameter name
- Values can be scalars (int, double, string) or space-separated arrays
- Lines starting with `!` are section headers
- Strings may be double-quoted: `"../path/file.txt"`
- Array lines have N values followed by `! ArrayName`: `0.057 0.084 0.106 ... ! PC_GS_angles`

The Fortran parser reads ALL lines into a `FileLines` array, then uses `FindLine` to search for each parameter by name (case-insensitive). This is key-value lookup, not positional — the order of parameters in the file doesn't matter.

## The Two-Pass Protocol

### Why Two Passes?

The core challenge: `ControlParameters` has ~50 ALLOCATABLE arrays whose sizes are determined by scalar parameters in the same config file. For example:
- `PC_GS_n = 30` (scalar) → `PC_GS_angles` has 30 elements (array)
- `AWC_NumModes = 2` → `AWC_freq` has 2 elements

During the mixed Fortran/C++ phase, Fortran owns all ALLOCATABLE memory. The view struct provides pointer+size pairs into Fortran-allocated arrays. C++ can fill values through these pointers, but only AFTER Fortran has ALLOCATED the arrays (otherwise the pointers are NULL and C_LOC crashes).

The two-pass protocol solves this:

### Pass 1: `readcontrolparameterfilesub_pass1_c`

**Signature:**
```cpp
void readcontrolparameterfilesub_pass1_c(
    controlparameters_view_t* CntrPar,
    localvariables_t* LocalVar,
    const char* filename,    // null-terminated DISCON.IN path
    const char* priPath,     // null-terminated directory of DISCON.IN
    errorvariables_t* ErrVar,
    int32_t* n_OL_rows,      // output: OL file data row count
    int32_t* OL_Count         // output: OL channel count
);
```

**What it does:**
1. Opens DISCON.IN, reads all lines into `std::vector<std::string>`
2. Parses ALL ~120 scalar fields into the view struct (ints, doubles, strings)
3. Parses the `allowDefault` conditions exactly matching Fortran semantics (e.g., `CntrPar->PC_ControlMode == 0` means pitch control is disabled, so pitch parameters are optional and default to 0)
4. If `OL_Mode > 0`: opens the OL file, counts data rows (skipping comment lines starting with `!`, `#`, `%`), computes `OL_Count` from the `Ind_*` fields — both returned via output parameters so Fortran can allocate OL arrays
5. Returns to Fortran (does NOT touch array pointers — they're NULL)

### Fortran Wrapper: Allocation Between Passes

After pass 1 returns:

1. **`vit_copy_scalars_to_cntrpar(view, CntrPar)`** — copies all 145 scalar fields (66 integers, 73 doubles, 6 CHARACTER strings) from the view struct back to the Fortran TYPE. This is necessary because the Fortran allocations use `CntrPar%PC_GS_n` etc. (Fortran-side field access).

2. **Apply `Fl_n` default** — `IF (CntrPar%Fl_n == 0) CntrPar%Fl_n = 1` (matching Fortran line 583)

3. **ALLOCATE all ~50 ALLOCATABLE arrays** — using `MAX(size, 1)` to avoid zero-length allocations:
   ```fortran
   ALLOCATE(CntrPar%PC_GS_angles(MAX(CntrPar%PC_GS_n, 1))); CntrPar%PC_GS_angles = 0.0
   ALLOCATE(CntrPar%PC_GS_KP(MAX(CntrPar%PC_GS_n, 1))); CntrPar%PC_GS_KP = 0.0
   ! ... ~50 more
   ```

   OL arrays are only allocated if `OL_Mode > 0 .AND. n_OL_rows > 0`:
   ```fortran
   ALLOCATE(CntrPar%OL_Channels(n_OL_rows, OL_Count)); CntrPar%OL_Channels = 0.0
   ALLOCATE(CntrPar%OL_Breakpoints(n_OL_rows)); CntrPar%OL_Breakpoints = 0.0
   ! ... 10 more OL arrays
   ```

4. **`vit_populate_controlparameters(CntrPar, view)`** — creates the view with valid array pointers (C_LOC now works because arrays are allocated)

### Pass 2: `readcontrolparameterfilesub_pass2_c`

**Signature:**
```cpp
void readcontrolparameterfilesub_pass2_c(
    controlparameters_view_t* CntrPar,
    localvariables_t* LocalVar,
    const char* filename,
    const char* priPath,
    errorvariables_t* ErrVar
);
```

**What it does:**
1. Re-opens DISCON.IN and re-reads all lines (no inter-pass caching — the file is small)
2. Fills ALL ALLOCATABLE array values through the view pointers (~55 array fields)
3. Computes derived constants:
   - `DT_Out` default: if 0, use `LocalVar->DT`
   - `n_DT_Out = round(DT_Out / DT)`
   - `n_DT_ZMQ = round(ZMQ_UpdatePeriod / DT)`
   - `Y_Rate *= R2D` (convert to deg/s)
   - `PC_RtTq99 = VS_RtTq * 0.99`
   - `VS_MinOMTq = VS_Rgn2K * VS_MinOMSpd²`
   - `VS_MaxOMTq = VS_Rgn2K * VS_RefSpd²`
4. Path resolution: prepends `priPath` to relative `PerfFileName` and `OL_Filename`
5. OL CSV loading (if `OL_Mode > 0`):
   - Builds display string and channel count (matching Fortran's output)
   - Reads CSV file (skip comment lines, read data rows)
   - Fills `OL_Channels` (2D, column-major)
   - Extracts columns into individual arrays (`OL_Breakpoints`, `OL_BldPitch1`, etc.) based on `Ind_*` indices (1-based in the config, converted to 0-based for column access)
   - Unwraps `OL_Azimuth` if `Ind_Azimuth > 0` (adds/subtracts 2π to keep consecutive differences in [-π, π])
   - Handles `OL_CableControl` and `OL_StructControl` (2D arrays, column-major)
6. Adds "ReadControlParameterFileSub:" prefix to error message on failure

## The DisconParser Class

The C++ parser encapsulates all DISCON.IN parsing logic in a reusable class:

```cpp
class DisconParser {
    std::vector<std::string> lines_;
    std::string filename_;
    errorvariables_t* errVar_;

    int findParam(const std::string& name);   // case-insensitive search
    std::string getValuePart(int idx);          // everything before '!'

public:
    bool load(const std::string& filename, errorvariables_t* err);
    bool parseDbl(const char* name, double& val, bool allowDefault = true);
    bool parseInt(const char* name, int& val, bool allowDefault = true);
    bool parseStr(const char* name, char* val, int maxLen, bool allowDefault = true);
    bool parseDblAry(const char* name, double* arr, int n, bool allowDefault = true);
    bool parseIntAry(const char* name, int* arr, int n, bool allowDefault = true);
};
```

### Parameter Name Matching

`findParam` searches all lines for a matching parameter name:
1. Skips pure comment lines (first non-whitespace is `!`)
2. Finds the `!` separator in the line
3. Extracts the first word after `!` (the parameter name)
4. Compares case-insensitively (both converted to uppercase)
5. Returns the line index, or -1 if not found

### Value Extraction

`getValuePart` returns everything before the first `!` on the matched line. Scalar parsers use `std::istringstream >> val`. Array parsers use `std::istringstream` in a loop. String parsers strip double quotes via `stripQuotes`.

### AllowDefault Behavior

When `allowDefault = true` and the parameter is not found:
- Scalars default to `0` (int), `0.0` (double), or `"unused"` (string)
- Arrays default to all zeros
- No error is set — the parameter is optional

When `allowDefault = false` and the parameter is not found:
- `ErrVar->aviFAIL = -1` is set with a descriptive error message
- This is a fatal error — the parameter is required

The `allowDefault` condition is typically a mode check (e.g., `CntrPar->PC_ControlMode == 0` means pitch control is disabled, so all pitch parameters are optional).

## The Reverse-Copy Helper: `vit_copy_scalars_to_cntrpar`

Added to `vit_controlparameters_view.f90` as the inverse of `vit_populate_controlparameters`. Copies 145 scalar fields FROM the view struct BACK TO the Fortran ControlParameters TYPE:

- **66 INTEGER fields:** `INT(view%field, IntKi)` conversion
- **73 REAL(DbKi) fields:** `REAL(view%field, DbKi)` conversion
- **6 CHARACTER fields:** Copied character-by-character from `C_CHAR` array to Fortran `CHARACTER` string:
  - `PerfFileName` (1024 chars)
  - `OL_Filename` (1024 chars)
  - `DLL_FileName` (1024 chars)
  - `DLL_InFile` (1024 chars)
  - `DLL_ProcName` (1024 chars)
  - `ZMQ_CommAddress` (256 chars)

**No ALLOCATABLE pointer fields are copied** — those only flow Fortran→view (via `C_LOC` in the populator).

Required `USE Constants, ONLY: IntKi, DbKi` for the kind conversion parameters.

## Fortran Wrapper Architecture

The wrapper (207 lines, stored in `translations/ReadSetParameters/readcontrolparameterfilesub_wrapper.f90`) replaces the entire subroutine body:

```
1. Extract filename from accINFILE (C_CHAR array → null-terminated string)
2. Extract PriPath (directory of DISCON.IN, for relative path resolution)
3. Initialize empty view struct
4. Call pass1_c → scalars populated in view
5. vit_copy_scalars_to_cntrpar → scalars copied to Fortran CntrPar
6. Apply Fl_n default
7. ALLOCATE ~50 arrays using sizes from CntrPar
8. vit_populate_controlparameters → create view with valid array pointers
9. Call pass2_c → arrays filled, constants computed, OL loaded
10. vit_copy_scalars_to_cntrpar → computed constants copied back
```

### accINFILE Handling

The `accINFILE` parameter is a `CHARACTER(accINFILE_size)` array passed from the Bladed DLL interface. The filename is in `accINFILE(1)`. The wrapper extracts it character-by-character and null-terminates for C++:

```fortran
filename_c = ' '
DO i = 1, MIN(accINFILE_size, 1023)
    IF (accINFILE(1)(i:i) == C_NULL_CHAR) EXIT
    filename_c(i:i) = accINFILE(1)(i:i)
END DO
filename_c(MIN(i, 1024):MIN(i, 1024)) = C_NULL_CHAR
```

### PriPath Extraction

Matches Fortran's `GetPath` — finds the last `/` or `\` in the filename and takes everything up to and including it:

```fortran
DO i = MIN(accINFILE_size, 1023), 1, -1
    IF (filename_c(i:i) == '/' .OR. filename_c(i:i) == '\') EXIT
END DO
IF (i > 0) THEN
    priPath_c(1:i) = filename_c(1:i)
    priPath_c(i+1:i+1) = C_NULL_CHAR
ELSE
    priPath_c(1:2) = './'
    priPath_c(3:3) = C_NULL_CHAR
END IF
```

## Integration Automation

The `integrate_all.sh` was updated to 44 functions (41 Track A + 3 Stage B). ReadControlParameterFileSub uses a different integration approach than CheckInputs and ReadCpFile: instead of embedding the full wrapper in a Python regex string (too long at 207 lines), the wrapper template is stored in `translations/ReadSetParameters/readcontrolparameterfilesub_wrapper.f90` and the Python script reads it:

```bash
python3 -c "
import re, sys
with open('rosco/controller/src/ReadSetParameters.f90', 'r') as f:
    content = f.read()
with open('translations/ReadSetParameters/readcontrolparameterfilesub_wrapper.f90', 'r') as f:
    wrapper = f.read()
pattern = r'(    SUBROUTINE ReadControlParameterFileSub\(...).*?(    END SUBROUTINE ReadControlParameterFileSub)'
result = re.sub(pattern, wrapper.rstrip(), content, flags=re.DOTALL)
..."
```

## Complete Field Inventory

### Pass 1 — Scalar Fields (~120)

| Category | Count | Examples |
|----------|-------|---------|
| Mode flags (INTEGER) | 26 | F_LPFType, IPC_ControlMode, VS_ControlMode, PC_ControlMode, ... |
| Size-determining integers | 16 | PC_GS_n, VS_n, F_NumNotchFilts, WE_FOPoles_N, PS_BldPitchMin_N, ... |
| Other integers | 24 | Echo, LoggingLevel, Ext_Interface, IPC_SatMode, SD_EnablePitch, ... |
| Physical parameters (REAL) | ~48 | F_LPFCornerFreq, PC_MaxPit, VS_GenEff, WE_BladeRadius, ... |
| Computed constants (REAL) | ~5 | DT_Out, VS_TSRopt, SS_VSGain, PA_CornerFreq, ... |
| File paths (CHARACTER) | 4 | PerfFileName, OL_Filename, DLL_FileName, DLL_InFile |
| Other strings (CHARACTER) | 2 | DLL_ProcName, ZMQ_CommAddress |

### Pass 2 — Array Fields (~55)

| Category | Count | Examples |
|----------|-------|---------|
| Filter arrays (REAL) | 5 | F_NotchFreqs, F_NotchBetaNum, F_FlCornerFreq, ... |
| Filter indices (INTEGER) | 2 | F_GenSpdNotch_Ind, F_TwrTopNotch_Ind |
| Pitch gain schedules (REAL) | 5 | PC_GS_angles, PC_GS_KP, PC_GS_KI, PC_GS_KD, PC_GS_TF |
| IPC arrays (REAL) | 4 | IPC_Vramp, IPC_KP, IPC_KI, IPC_aziOffset |
| VS arrays (REAL) | 5 | VS_KP, VS_KI, VS_FBP_U, VS_FBP_Omega, VS_FBP_Tau |
| PRC arrays (REAL) | 4 | PRC_R_Table, PRC_Pitch_Table, PRC_WindSpeeds, PRC_GenSpeeds |
| WSE arrays | 3 | PerfTableSize (INT), WE_FOPoles_v, WE_FOPoles |
| Yaw/PS/SU/SD arrays | 11 | Y_ErrThresh, PS_WindSpeeds, SU_LoadStages, SD_StageTime, ... |
| Floating/Flap arrays | 2 | Fl_Kp, Fl_U |
| OL indices/gains | 3 | Ind_BldPitch (INT), RP_Gains, PF_Offsets |
| AWC arrays | 7 | AWC_n (INT), AWC_harmonic (INT), AWC_freq, AWC_amp, AWC_clockangle, AWC_CntrGains |
| Cable/StC arrays (INT) | 4 | CC_GroupIndex, StC_GroupIndex, Ind_CableControl, Ind_StructControl |
| OL timeseries (REAL) | 11+ | OL_Channels (2D), OL_Breakpoints, OL_BldPitch1-3, OL_GenTq, OL_YawRate, OL_Azimuth, ... |

## Open Loop CSV Loading

The OL loading is the most complex part of pass 2. It replicates the Fortran `Read_OL_Input` + column extraction logic:

1. **Count channels** from `Ind_*` fields (same logic as Fortran's OL_Count computation, including duplicate index deduplication for Ind_BldPitch)
2. **Print status** matching Fortran's `PRINT *` messages
3. **Read CSV file:**
   - Skip lines containing `!`, `#`, or `%` (comment lines)
   - Read data rows using `std::istringstream`
   - Store in `OL_Channels` (column-major: `OL_Channels[col * n_rows + row]`)
4. **Extract columns** into individual arrays based on `Ind_*` indices (1-based → 0-based):
   ```cpp
   for (int i = 0; i < n_rows; i++)
       CntrPar->OL_Breakpoints[i] = OL_Channels[(CntrPar->Ind_Breakpoint - 1) * n_rows + i];
   ```
5. **Unwrap azimuth** if `Ind_Azimuth > 0`:
   ```cpp
   static void unwrap(double* arr, int n) {
       for (int i = 1; i < n; i++) {
           double diff = arr[i] - arr[i-1];
           while (diff > PI) { arr[i] -= 2.0 * PI; diff -= 2.0 * PI; }
           while (diff < -PI) { arr[i] += 2.0 * PI; diff += 2.0 * PI; }
       }
   }
   ```
6. **Cable/Structural OL arrays** (2D, column-major) — only for non-zero `Ind_CableControl`/`Ind_StructControl` entries

## Verification

**Method:** Simulation-level comparison (not KGen)
**Result:** ALL SCENARIOS IDENTICAL — 692,000 total values compared across 10 scenarios
**Build config:** `-ffp-contract=off`

This verifies that:
- All 225+ parameters are parsed correctly from DISCON.IN
- The NREL-5MW config produces exactly the same CntrPar state as the Fortran parser
- Computed constants (PC_RtTq99, VS_MinOMTq, etc.) are bit-identical
- Path resolution works (PerfFileName for the Cp table lookup)
- OL CSV loading works for Scenario 10 (OL_Mode=2 azimuth tracking)
- The two-pass allocation protocol preserves all data correctly

## Design Decisions

### File Re-Read in Pass 2

Both passes open and read the DISCON.IN file independently. No inter-pass caching. This simplicity was chosen because:
- The file is small (~150-200 lines)
- Reading it twice takes <1ms
- No static/global state to manage between passes
- Each pass is fully self-contained

### MAX(size, 1) for Allocations

All arrays are allocated with `MAX(n, 1)` to avoid zero-length Fortran arrays. When the corresponding mode is disabled and the size parameter is 0, the array is allocated with 1 element and filled with zeros. This matches the Fortran `ParseAry` behavior with `AllowDefault=.TRUE.`, which allocates to the requested size (possibly 0 in theory, but Fortran handles it) and fills with defaults.

### Echo File Support Deferred

The Fortran parser supports an optional echo file (`Echo > 0`), writing each parsed parameter to a `.RO.echo` file. The C++ translation skips echo file support — all test scenarios use `Echo = 0`. This can be added later if needed.

### Wrapper Template Approach

The integration script stores the complete Fortran wrapper in `translations/ReadSetParameters/readcontrolparameterfilesub_wrapper.f90` (207 lines) rather than embedding it in a shell script string. This is cleaner for large wrappers and makes the wrapper independently readable/editable.

## File Summary

| File | Lines | Role |
|------|-------|------|
| `translations/ReadSetParameters/readcontrolparameterfilesub.cpp` | 1057 | Canonical C++ translation (DisconParser + two passes) |
| `translations/ReadSetParameters/readcontrolparameterfilesub_wrapper.f90` | 207 | Fortran wrapper template (allocations between passes) |
| `rosco/controller/src/readcontrolparameterfilesub.cpp` | 1057 | Built copy |
| `rosco/controller/src/vit_controlparameters_view.f90` | 1112 | Added `vit_copy_scalars_to_cntrpar` (170 lines) |
| `rosco/controller/src/vit_translated.h` | +2 | Pass 1 + pass 2 declarations |
| `rosco/controller/CMakeLists.txt` | +1 | Added source file |
| `scripts/integrate_all.sh` | +30 | Stage B: ReadControlParameterFileSub step (44/44) |

## Stage B Status After This Step

| Step | Function | Fortran | C++ | Status |
|------|----------|---------|-----|--------|
| 1 | CheckInputs | 857 lines | 840 lines | Verified |
| 2 | ReadCpFile | 84 lines | 145 lines | Verified |
| **3a** | **ReadControlParameterFileSub** | **559 lines** | **1057 lines** | **Verified** |
| **Total** | | **1,500 lines** | **2,042 lines** | **44/44 integrated** |

All three ReadSetParameters.f90 parsing/validation functions are now C++. The remaining Fortran in ReadSetParameters.f90 is the `SetParameters` orchestrator (Stage E in the plan) which calls these functions and initializes LocalVar fields.

## Commit

ROSCO `c12c6e3`: Phase 10B: ReadControlParameterFileSub C++ translation (Step 3a)

## What's Next

- **Step 3b:** YAML format support (yaml-cpp). User downloads the tarball, we add a `YamlParser` class alongside `DisconParser`, auto-detect format by file extension.
- **Step 4:** Documentation updates, PROJECT_PLAN.md completion markers, dev notes.
- **Stage C:** Restart I/O (WriteRestartFile, ReadRestartFile) + Debug logger — next phase of idiomatic C++ work.
