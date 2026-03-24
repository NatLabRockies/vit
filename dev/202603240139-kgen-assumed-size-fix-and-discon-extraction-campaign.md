# KGen Assumed-Size Array Fix and DISCON.F90 Extraction Campaign
**Date:** 2026-03-24
**Status:** in-progress (4 of 6 DISCON functions extracted, 3 functions need investigation)
**Scope:** KGen state capture, VIT extraction pipeline, Phase 7B extraction

## Background

The previous session fixed KGen's PROCEDURE POINTER parser (8 changes across 3 files, committed to KGen repo). KGen could now parse ExtControl.f90 and generate kernels from DISCON.F90 call sites. But state capture failed with a Fortran compile error: `Actual argument for 'var' cannot be an assumed-size array`.

## The Assumed-Size Array Problem

### Root Cause

DISCON.F90's signature has `REAL(ReKi), INTENT(INOUT) :: avrSWAP(*)` — an assumed-size array. KGen generates state capture write subroutines with `DIMENSION(:)` (assumed-shape) parameters. Fortran forbids passing assumed-size `(*)` to assumed-shape `(:)`.

The code generation happens in `KGen/kgen/extractor/plugins/gencore/gencore_subr.py`, lines 33 and 208:
```python
if var.is_array(): attrspec.append('DIMENSION(%s)'% ','.join(':'*var.rank))
```

This always generates `DIMENSION(:)` regardless of whether the original is assumed-size or assumed-shape. KGen already has `var.is_assumed_size_array()` (base_classes.py:459) but `gencore_subr.py` doesn't use it.

### The Generated Code

**Instrumented state/DISCON.F90:**
```fortran
! Call site — passes assumed-size array to assumed-shape parameter
CALL kw_discon_real__reki_dim1(avrswap, kgen_unit, "avrswap", .FALSE.)

! Write subroutine — declares assumed-shape parameter
SUBROUTINE kw_discon_real__reki_dim1(var, kgen_unit, printname, printvar)
    REAL(KIND=reki), INTENT(IN), DIMENSION(:) :: var  ! ← can't accept (*)
```

### Fix: VIT Post-Processing Patch

Rather than modifying KGen's code generator (which can't infer array sizes), VIT now patches the KGen-generated instrumented source during the build wrapper execution. The patch replaces bare assumed-size array arguments with explicit array sections at `kw_*` call sites:

**Before:** `CALL kw_discon_real__reki_dim1(avrswap, kgen_unit, ...)`
**After:** `CALL kw_discon_real__reki_dim1(avrswap(1:2000), kgen_unit, ...)`

The write subroutine stays unchanged — `DIMENSION(:)` correctly accepts an explicit section `(1:2000)`. The kernel's read side allocates based on saved bounds (LBOUND=1, UBOUND=2000), so write/read stays compatible.

### Configuration

New `vit.yaml` option:
```yaml
kgen:
  assumed_size_arrays:
    avrSWAP: 2000
```

The size 2000 comes from ExtControl.f90's `max_avr_entries = 2000` — the Bladed DLL protocol's swap array size.

### Implementation Details

**`vit/vit/config.py`:** Added `assumed_size_arrays: dict` field, parsed from `kgen.assumed_size_arrays`.

**`vit/vit/extract.py`:** `generate_state_capture_build_script()` now accepts `assumed_size_arrays` and generates sed commands in the wrapper that patch each configured variable. The sed runs on all `.f90`/`.F90` files in the source directory (where instrumented files are placed by state/Makefile's `replace` step).

**`vit/vit/cli.py`:** Threads `config.assumed_size_arrays` through to `run_kgen_extract()`.

## Build Wrapper Idempotency Fix

During investigation, discovered that the build wrapper had two bugs:

1. **Backup overwrite:** `cp "$CMAKE_FILE" "${CMAKE_FILE}.vit_backup"` ran on EVERY wrapper call, overwriting the clean backup with an already-modified file. The wrapper runs multiple times per extraction (profiling build + state capture build). Fixed: `if [ ! -f "${CMAKE_FILE}.vit_backup" ]; then cp ...`.

2. **Duplicate kgen_utils.f90:** The sed that adds `src/kgen_utils.f90` to CMakeLists.txt ran on every call, creating duplicate entries. Fixed: `if ! grep -q "kgen_utils.f90" "$CMAKE_FILE"; then sed ...`.

Without these fixes, repeated extractions corrupted CMakeLists.txt with multiple `src/kgen_utils.f90` entries that persisted across sessions (backup was already corrupted).

## Scenario Order Fix

### Problem

vit_sim.py ran Scenario 4 first (Flp_Mode=2 for PIIController extraction), then Scenario 5, then Scenario 3. KGen's invocation range `0:0:1-20` captures the first 20 DISCON calls. With Scenario 4 first, the first 20 invocations had CC_Mode=0 and StC_Mode=0 — CableControl and StructuralControl were never called during the capture window.

### Fix

Changed vit_sim.py to run Scenario 3 first. Scenario 3 now has ALL mode-gated functions enabled: CC_Mode=1, StC_Mode=1, Fl_Mode=1, Flp_Mode=1 (new), Y_ControlMode=1, TD_Mode=1. This ensures all functions are called during invocations 1-20.

Also added `Flp_Mode=1` to Scenario 3 for FlapControl extraction.

## Source File State for Extraction

The correct source state for DISCON.F90 extractions (documented more precisely than before):

| File | Version | Notes |
|------|---------|-------|
| Controllers.f90 | fde0498 (Phase 4C) | Has restart fix + AWC decomposition + FlapControl bug fix. Do NOT apply sed restart fix (already has it). |
| ControllerBlocks.f90 | fde0498 (Phase 4C) | Has restart fix. |
| Functions.f90 | e8010f0 (upstream) + `sed 's/LocalVar%restart/(LocalVar%restart \/= 0)/g'` | No wrappers. Needs restart fix. |
| Filters.f90 | e8010f0 (upstream) + sed restart fix + Flp_Mode=2 DO loop fix (dba7738) | No wrappers. Needs both fixes. |
| ROSCO_Types.f90 | fde0498 (Phase 4C) | Has INTEGER(C_INT) restart. Do NOT reset. |
| ExtControl.f90 | fde0498 (Phase 4C) | KGen parser fix handles PROCEDURE POINTER. |
| DISCON.F90 | e8010f0 (upstream) | No wrappers. |
| ReadSetParameters.f90, ROSCO_IO.f90 | fde0498 (Phase 4C) | No changes needed. |

**Critical:** Controllers.f90 at fde0498 ALREADY has `(LocalVar%restart /= 0)` conversions (30 occurrences). Applying the sed restart fix to it creates invalid double-conversions `((LocalVar%restart /= 0) /= 0)`.

## Extraction Results

| Function | Call site | Line | Cases | Status |
|----------|-----------|------|-------|--------|
| VariableSpeedControl | DISCON.F90 | 117 | **20** ✓ | No mode gate (always called) |
| PitchControl | DISCON.F90 | 119 | **20** ✓ | PC_ControlMode > 0 (default) |
| YawRateControl | DISCON.F90 | 123 | **20** ✓ | Y_ControlMode=1 in Scenario 3 |
| FlapControl | DISCON.F90 | 127 | **20** ✓ | Flp_Mode=1 in Scenario 3 |
| CableControl | DISCON.F90 | 132 | **0** ✗ | CC_Mode=0 at runtime (see below) |
| StructuralControl | DISCON.F90 | 137 | **0** ✗ | StC_Mode=0 at runtime (same issue) |
| FloatingFeedback | Controllers.f90 | 96 | **0** ✗ | Fl_Mode gate issue |

## Remaining Issue: CC_Mode=0 at Runtime

### Symptoms

CableControl and StructuralControl extract 0 state files despite:
- CC_Mode=1 and StC_Mode=1 set in Scenario 3's DISCON_filters.IN (verified with grep)
- Scenario 3 running first (verified with simulation output)
- The instrumented code compiling successfully with kgen_utils.f90

A debug PRINT added to the instrumented DISCON.F90 before the `IF (CntrPar%CC_Mode > 0)` block showed `CC_Mode=0` for ALL 103,995 invocations across all scenarios — including Scenario 3 which should have CC_Mode=1.

### What This Means

The DISCON input file has CC_Mode=1, but at runtime CntrPar%CC_Mode=0. This suggests:
1. ROSCO's SetParameters subroutine isn't reading CC_Mode from the input file correctly in the instrumented build context
2. Or the instrumented DISCON.F90's initialization path differs from the original (KGen may insert instrumentation code that changes the execution flow during initialization)
3. Or the DLL reload between scenarios resets CntrPar but the reinitializaton doesn't re-read CC_Mode

### Why VariableSpeedControl/PitchControl/YawRateControl/FlapControl Work

These functions are either:
- Always called (VariableSpeedControl — no IF guard)
- Gated by modes that are DEFAULT on (PC_ControlMode=1 is the default)
- Gated by modes set in EARLIER scenarios that happened to work with the old scenario order

The fact that CC_Mode=0 for ALL invocations (not just non-Scenario-3 ones) suggests the issue is fundamental — ROSCO's parameter reader may not be parsing CC_Mode correctly in the instrumented build, or the CntrPar struct is not being populated correctly for CC_Mode.

### Investigation Needed

1. Check if CntrPar%CC_Mode is set ANYWHERE in the instrumented simulation path
2. Compare SetParameters behavior between instrumented and non-instrumented builds
3. Check if CC_Mode is read during initialization (iStatus=0) only, and whether the instrumented code's initialization differs
4. Test: run the non-instrumented build and check if CC_Mode=1 during Scenario 3

## Commits

**VIT repo:**
- `10b4ab7` — Add assumed-size array patching for DISCON.F90 extraction
- `54fd7bb` — Fix stale artifact contamination in extraction pipeline (from previous session)

**ROSCO repo:**
- `0153ecd` — Add assumed_size_arrays config, Flp_Mode=1, fix scenario order

**KGen repo:**
- `31d653c` — Fix PROCEDURE POINTER parsing for Fortran 2003+ code (from previous session)

## What Was Gained

1. **DISCON.F90 extraction is unblocked.** The combination of the KGen parser fix (PROCEDURE POINTER) and the assumed-size array patching enables extraction from DISCON.F90 for the first time. 4 of 6 target functions extracted successfully.

2. **The assumed-size fix is general.** Any project with assumed-size array arguments in DLL interfaces can configure `kgen.assumed_size_arrays` in vit.yaml. The fix is a simple sed in the build wrapper — no KGen changes needed.

3. **Build wrapper is idempotent.** The backup and injection guards prevent CMakeLists.txt corruption across repeated extractions.

4. **Source state for DISCON extraction is precisely documented.** The exact git commits, sed commands, and bug fixes needed for each file are recorded. The key lesson: Controllers.f90 at fde0498 already has the restart fix — applying sed to it creates double-conversions.

## Next Steps

1. Investigate the CC_Mode=0 runtime issue for CableControl/StructuralControl
2. Investigate the FloatingFeedback 0-case issue (may be related)
3. Once all 7 remaining functions are extracted, proceed to translation phase
4. IPC and ActiveWakeControl golden fixtures from the earlier campaign are still valid (20 cases each)
