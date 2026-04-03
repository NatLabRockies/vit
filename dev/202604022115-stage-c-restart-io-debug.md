# Stage C Complete: Restart I/O + Debug Logger

**Date:** 2026-04-02
**Status:** completed
**Scope:** Translate WriteRestartFile, ReadRestartFile, and Debug (ROSCO_IO.f90) — the final 3 functions in the ROSCO_IO module

## Overview

Stage C translates all three functions in ROSCO_IO.f90, completing the module. These are I/O functions that cannot use KGen extraction (file I/O, SAVE variables, variable-length CHARACTER arguments). They follow the Stage B pattern: manual integration in `integrate_all.sh`, verified via 10-scenario sim-compare.

After Stage C: **50 functions** translated and integrated, **692,000 values IDENTICAL** across 10 scenarios.

## C1: WriteRestartFile (ROSCO_IO.f90:14-355, 342 lines)

**What it does:** Opens a binary checkpoint file `{RootName}{timestep}.RO.chkp` and sequentially writes ~310 fields: ~130 LocalVar scalars, ~84 manually-unrolled array elements (rootMOOP(1..3), CC_DesiredL(1..12), etc.), 7 WE nested struct fields (including 3x3 P matrix), 46 FilterParameters DIMENSION(1024) arrays (8KB each), 5 piParams arrays, 4 resParams arrays, 1 rlParams array, and 9 objInst instance counters.

**Translation:** `translations/IO/restart.cpp` — combined with ReadRestartFile.

**Key design:** Uses a shared `checkpoint_fields` template function that accepts a stream and a field operation (write or read). Both WriteRestartFile and ReadRestartFile call this same function, guaranteeing identical field ordering. This eliminates the highest risk in restart I/O translation — a single field written out of order would silently corrupt the checkpoint.

```cpp
template<typename Stream, typename FieldOp>
void checkpoint_fields(Stream& f, localvariables_t* LocalVar,
                       objectinstances_t* objInst, FieldOp field_op) {
    field_op(f, LocalVar->iStatus);
    field_op(f, LocalVar->AlreadyInitialized);
    // ... 300+ fields in exact Fortran order
}
```

Called as:
```cpp
// Write
checkpoint_fields(f, LocalVar, objInst, [](std::ofstream& s, auto& val) { write_field(s, val); });
// Read
checkpoint_fields(f, LocalVar, objInst, [](std::ifstream& s, auto& val) { read_field(s, val); });
```

**CntrPar argument:** Present in the Fortran signature but never accessed in the function body. The C++ entry point omits it; the Fortran wrapper accepts it for signature compatibility but doesn't pass it through.

## C2: ReadRestartFile (ROSCO_IO.f90:358-707, 350 lines)

**What it does:** Mirror of WriteRestartFile — reads the same checkpoint fields in the same order, then calls `ReadControlParameterFileSub` and `ReadCpFile` to re-parse the DISCON.IN config and Cp performance table.

**Callee dispatch design:** The C++ `readrestartfile_c` only handles binary checkpoint I/O. The callee calls (ReadControlParameterFileSub and ReadCpFile) are handled by the Fortran wrapper:

```fortran
CALL readrestartfile_c(avrSWAP, C_LOC(LocalVar), C_LOC(ErrVar), C_LOC(objInst), ...)

! Callee dispatch (same as original Fortran)
CALL ReadControlParameterFileSub(CntrPar, LocalVar, LocalVar%ACC_INFILE, ...)
IF (CntrPar%WE_Mode > 0) THEN
    CALL ReadCpFile(CntrPar, PerfData, ErrVar)
ENDIF
```

This avoids the C++ needing to handle ReadControlParameterFileSub's complex two-pass ALLOCATE protocol (which requires Fortran-side ALLOCATEs between passes). The callee calls go through their own already-integrated Fortran wrappers.

**Verification:** Restart I/O is only triggered by iStatus==-8/-9, which is not exercised in the standard 10-scenario sim-compare. Verified indirectly: sim-compare proves no regression from wrapper plumbing (692K values IDENTICAL).

## C3: Debug (ROSCO_IO.f90:710-1203, 494 lines)

**What it does:** The largest remaining function. Writes three debug output files controlled by `CntrPar%LoggingLevel`:
- `.RO.dbg`: 26 DebugVar fields (LoggingLevel > 0)
- `.RO.dbg2`: 159 LocalVar fields (LoggingLevel > 1)
- `.RO.dbg3`: avrSWAP indices — base 85 + CC_GroupIndex + StC_GroupIndex (LoggingLevel > 2)

Also prints console status every 10 seconds and clamps values to [1E-99, 1E+99] to prevent Fortran `********` overflow in formatted output.

**Translation:** `translations/IO/debug_logger.cpp`

**SAVE variable mapping:**
- `INTEGER(IntKi), SAVE :: UnDb, UnDb2, UnDb3` (Fortran file unit numbers) → `static std::ofstream dbg_file, dbg2_file, dbg3_file`
- `INTEGER(IntKi), SAVE, DIMENSION(:), ALLOCATABLE :: avrIndices` → `static std::vector<int32_t> avr_indices`

**Absorbed ROSCO_Helpers:**
- `GetNewUnit` → `std::ofstream` (no unit numbers needed)
- `CurDate`/`CurTime` → `<ctime>` with `strftime`
- `AddToList` → `std::vector::push_back`
- `Int2LStr` → `std::to_string`

**Verification — `scripts/debug_test.sh`:** Created a dedicated test script that compares `.RO.dbg` data rows between baseline and integrated builds. Result: **15,999 data rows byte-identical**. The only differences are in headers:
- Fortran `CHARACTER(15)` truncates column names (e.g., "NacHeadingTarge" vs "NacHeadingTarget")
- Minor spacing differences from different format string implementations

The script supports `baseline`, `integrated`, `compare`, `all`, and `clean` subcommands.

## Integration Approach

All three functions required **manual integration** in `integrate_all.sh` (same pattern as Stage B and Stage D) for two reasons:

1. **`CHARACTER(size_avcOUTNAME)` argument type:** VIT's `vit translate` and `vit integrate` cannot parse variable-length CHARACTER arguments where the length comes from another parameter. The Fortran wrapper manually converts the CHARACTER to a `C_CHAR` array before calling the `_c` function.

2. **ReadRestartFile callee dispatch:** Needs to call ReadControlParameterFileSub and ReadCpFile from the Fortran side after the C++ returns.

The Fortran wrappers follow a consistent pattern:
```fortran
! Convert CHARACTER to C_CHAR array
DO i = 1, size_avcOUTNAME
    RootName_c(i) = RootName(i:i)
END DO
! Call C++ function
CALL writerestartfile_c(C_LOC(LocalVar), C_LOC(ErrVar), C_LOC(objInst), &
                        RootName_c, INT(size_avcOUTNAME, C_INT32_T))
```

## Bug Fix: reset_to_clean.sh

During debug testing, discovered that `reset_to_clean.sh` didn't restore ExtControl.f90, ZeroMQInterface.f90, or ROSCO_IO.f90. These files were originally documented as "Phase 4A/4C fixes, no wrappers" and skipped by reset. But after Stage C/D integration was committed, `integrate_all.sh` now modifies them during integration, so they need to be restored to HEAD during reset.

**Fix:** Added `git checkout -- rosco/controller/src/ExtControl.f90 rosco/controller/src/ZeroMQInterface.f90 rosco/controller/src/ROSCO_IO.f90` to `reset_to_clean.sh`.

## Files Changed

### ROSCO repo
| File | Change |
|------|--------|
| `translations/IO/restart.cpp` | New — WriteRestartFile + ReadRestartFile C++ (shared checkpoint_fields template) |
| `translations/IO/debug_logger.cpp` | New — Debug C++ (static file handles, formatted output, value clamping) |
| `scripts/integrate_all.sh` | Added Stage C entries (3 functions), TOTAL=50 |
| `scripts/prepare_commit.sh` | Added ROSCO_IO.f90 restoration |
| `scripts/reset_to_clean.sh` | Fixed: now restores ExtControl.f90, ZeroMQInterface.f90, ROSCO_IO.f90 |
| `scripts/debug_test.sh` | New — debug output verification script |
| `vit.yaml` | Added WriteRestartFile, ReadRestartFile, Debug translation entries |

### VIT repo
| File | Change |
|------|--------|
| `PROJECT_PLAN.md` | Stage C marked complete, counts updated to 50 |
| `CLAUDE.md` | Added Stage C verification docs, debug_test.sh usage, updated translations table |

## Updated Counts

| Metric | Before Stage C | After Stage C |
|--------|---------------|---------------|
| Total translated | 47 | 50 |
| KGen-verified | 42 | 42 |
| Simulation-verified | 5 | 8 |
| integrate_all.sh total | 47 | 50 |

## Remaining

- **Stage E:** PreFilterMeasuredSignals + SetParameters (orchestrators)
- **Phase 11:** DISCON entry point → pure C++ libdiscon.so
