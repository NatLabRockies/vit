# Phase 10A: ReadAvrSWAP — First ReadSetParameters Function

**Date:** 2026-04-01
**Status:** completed
**Scope:** Extract, translate, verify, and integrate ReadAvrSWAP (function #40)

## Context

Phase 10 introduces a strategic shift: KGen where it works, idiomatic C++ where it doesn't. ReadAvrSWAP is the first Phase 10 function — a pure transformer (no file I/O) that unpacks the Bladed-interface `avrSWAP` float array into `LocalVariables` struct fields. It belongs to Track A (KGen, mirror Fortran).

This is the first function from `ReadSetParameters.f90`, a new module for VIT. All 39 previous functions came from Functions.f90, Filters.f90, Controllers.f90, ControllerBlocks.f90, or DISCON.F90.

## KGen Parser Issue: Backslash in ROSCO_Helpers.f90

The extraction initially failed — KGen's Python 2.7 parser crashed on `ROSCO_Helpers.f90`.

**Root cause chain:**
1. ReadAvrSWAP is defined in MODULE ReadSetParameters
2. ReadSetParameters has `USE ROSCO_Helpers` (module-level)
3. ReadAvrSWAP's subroutine-level `USE ROSCO_Types, ONLY: LocalVariables` imports only LocalVariables — ControlParameters is not in the ONLY list
4. KGen tries to resolve ControlParameters (used in ReadAvrSWAP's signature) through other USE'd modules
5. KGen follows the module-level `USE ROSCO_Helpers` and parses that file
6. ROSCO_Helpers.f90 contains `PathIsRelative` and `GetPath` functions with backslash characters in string literals:
   ```fortran
   I = INDEX( GivenFil, '\', BACK=.TRUE. )
   IF ( INDEX( GivenFil, ":\") == 0 )
   ```
7. KGen's f2py-based parser can't handle backslashes — it misinterprets `'\'` as an unterminated string continuation
8. Parser throws an unrecoverable exception: `'Line' object has no attribute 'analyze'`

**Why previous extractions from DISCON.F90 didn't hit this:** Functions in Controllers.f90, ControllerBlocks.f90, etc. don't `USE ROSCO_Helpers`. Even though DISCON.F90 uses ReadSetParameters (which uses ROSCO_Helpers), KGen's lazy resolution only follows USE chains relevant to the target function's type dependencies. For VariableSpeedControl et al., ControlParameters was resolved through a different path that didn't require ROSCO_Helpers.f90.

**Fix:** Temporarily patch the three backslash-containing lines before extraction, replacing backslash characters with forward slashes. This changes GetPath/PathIsRelative behavior on Windows paths but is harmless on Linux (where the simulation runs). The original file is backed up and restored after extraction.

This patch is now codified in `scripts/extract_all.sh` — it backs up ROSCO_Helpers.f90, patches the backslash lines, runs the ReadAvrSWAP extraction, and restores the original.

## Translation

ReadAvrSWAP is ~110 lines of mostly array index assignments:

```fortran
LocalVar%iStatus = NINT(avrSWAP(1))    ! → static_cast<int32_t>(std::round(avrSWAP[0]))
LocalVar%Time    = avrSWAP(2)          ! → LocalVar->Time = avrSWAP[1]
LocalVar%NacVane = avrSWAP(24) * R2D   ! → avrSWAP[23] * R2D (float→double promotion)
```

Key translation details:
- **avrSWAP is `float*`** (REAL(ReKi) = REAL(4)), not double. LocalVar fields are double. The float→double assignment is implicit and exact.
- **Fortran 1-based → C++ 0-based** array indexing throughout (avrSWAP(1) → avrSWAP[0], etc.)
- **R2D and D2R constants** from `rosco_constants.h`
- **NINT** → `static_cast<int32_t>(std::round(...))`
- **Error message string** uses `memset(' ')` + `memcpy` to match Fortran's space-padding behavior (though the error path is never exercised during verification)
- **COS/SIN** for nacelle frame conversion — `std::cos`/`std::sin` with `D2R` conversion
- **PF_Offsets** accessed through view struct pointer (`CntrPar->PF_Offsets[K-1]`)

## Verification Results

- **Extraction:** 21 cases captured (Scenario 3)
- **Kernel verification:** 21/21 passed (4,935 field entries)
- **Simulation verification:** ALL SCENARIOS IDENTICAL — 672,000 values across 9 scenarios (40 functions integrated)

## Script Updates

All batch scripts updated to reflect function #40:

| Script | Changes |
|--------|---------|
| `reset_to_clean.sh` | Added `git checkout -- ReadSetParameters.f90`. Added ReadSetParameters.f90 to verification grep. Updated comments. |
| `extract_all.sh` | Added ReadAvrSWAP extraction with ROSCO_Helpers.f90 backslash patch/restore. Total: 39→40. |
| `verify_all.sh` | Added ReadAvrSWAP to Batch 1 (now 14 functions). Total: 39→40. |
| `integrate_all.sh` | Added ReadAvrSWAP section. Total: 39→40. Fixed pre-existing comment/count mismatch. |

## Baseline Arrays Issue

During the simulation verification step, we discovered that `baseline_arrays/` didn't exist (they're transient artifacts, not committed). We also hit an `undefined symbol: readavrswap_c` error when trying to capture baseline arrays — `reset_to_clean.sh` had reset Controllers.f90 etc. but NOT ReadSetParameters.f90, which still had the integration wrapper calling `readavrswap_c`. The C++ stub (`// stub`) didn't define that symbol.

This confirmed that `reset_to_clean.sh` needed the ReadSetParameters.f90 reset line. After adding it, the full clean build test flow worked correctly:
1. Reset all source (including ReadSetParameters.f90)
2. Build clean Fortran baseline → capture baseline arrays
3. Integrate all 40 functions → rebuild → capture integrated arrays
4. Compare: 672,000 values identical

## Files Modified

| File | Repo | Change |
|------|------|--------|
| `translations/ReadSetParameters/readavrswap.cpp` (new) | ROSCO | C++ translation |
| `kernel/ReadAvrSWAP/` (new) | ROSCO | Golden kernel fixture (21 state files) |
| `scripts/reset_to_clean.sh` | ROSCO | Reset ReadSetParameters.f90, verify in grep |
| `scripts/extract_all.sh` | ROSCO | Add ReadAvrSWAP + ROSCO_Helpers backslash patch |
| `scripts/verify_all.sh` | ROSCO | Add ReadAvrSWAP to Batch 1, total 40 |
| `scripts/integrate_all.sh` | ROSCO | Add ReadAvrSWAP section, total 40 |

## Next Steps

- A.2: PIDController — requires adding OL_Mode=2 scenario to vit_sim.py
- Then Stage B: idiomatic C++ config loading (ReadCpFile, config reader, CheckInputs)
