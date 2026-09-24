# DLL SAVE Variable Persistence: Root Cause and Fix for DISCON.F90 Extraction
**Date:** 2026-03-24
**Status:** completed (6 of 7 remaining functions extracted; FloatingFeedback needs follow-up)
**Scope:** KGen state capture, DLL lifecycle, ROSCO parameter validation, Phase 7B extraction

## The Problem

After fixing KGen's PROCEDURE POINTER parser and the assumed-size array limitation, 4 of 6 DISCON.F90 functions extracted successfully (VariableSpeedControl, PitchControl, YawRateControl, FlapControl — 20 cases each). But CableControl, StructuralControl, and FloatingFeedback consistently captured 0 state files despite mode flags being correctly set in vit_sim.py's Scenario 3.

A debug print showed `CntrPar%CC_Mode = 0` at runtime for ALL invocations, even though `DISCON_filters.IN` had `CC_Mode = 1`.

## Investigation: Three Interleaved Root Causes

The 0-case failure had three interleaved causes that masked each other. Fixing any one alone was insufficient — all three had to be resolved simultaneously.

### Root Cause 1: ROSCO Parameter Validation Errors (Silent aviFAIL=-1)

**Discovery method:** Added `IF (ErrVar%aviFAIL < 0) WRITE(*,*) TRIM(ErrVar%ErrMsg)` after the `CALL SetParameters` line in the instrumented state/DISCON.F90 and ran `make -C state/ run`.

**Findings:** Scenario 3 enabled many modes simultaneously: CC_Mode=1, StC_Mode=1, Fl_Mode=1, Flp_Mode=1, Y_ControlMode=1, TD_Mode=1, IPC_ControlMode=1. Three validation errors occurred:

1. **`StC_GroupIndex must be >= 2801`** — We had `StC_GroupIndex = 2601`. ROSCO's StructuralControl validation (ReadSetParameters.f90:1735) requires StC_GroupIndex >= 2801 (not 2601 as documented in some places). The CC_GroupIndex range starts at 2601, but StC_GroupIndex starts at 2801.

2. **`ROSCO does not currently support IPC_ControlMode and Flp_Mode > 0`** — We had both `IPC_ControlMode=1` and `Flp_Mode=1`. ROSCO explicitly rejects this combination (ReadSetParameters.f90). IPC and flap control both modify blade pitch and are mutually exclusive in ROSCO v2.10.

3. **`F_FlCornerFreq(1) must be greater than zero`** — When `Fl_Mode=1`, ROSCO requires the floating feedback filter corner frequency to be positive. The default from the controller tuning was 0 (disabled). Similarly, `F_FlpCornerFreq(1)` is required when `Flp_Mode > 0`.

**Why these errors were silent:** SetParameters sets `ErrVar%aviFAIL = -1` on any validation failure. DISCON's main control block is gated by `IF (... .AND. ErrVar%aviFAIL >= 0)`. With aviFAIL=-1, the ENTIRE control block is skipped — VariableSpeedControl, PitchControl, CableControl, everything. No error is printed to the user because DISCON clears ErrMsg at the end of each call: `ErrVar%ErrMsg = ''`.

**Why VariableSpeedControl/PitchControl/YawRateControl/FlapControl still worked:** These were extracted in EARLIER runs when fewer modes were enabled (no StC_Mode, Fl_Mode, or Flp_Mode). The validation errors only appeared after we added ALL modes to Scenario 3 for the full extraction campaign.

**Fix:**
- `StC_GroupIndex`: 2601 → 2801
- Removed `IPC_ControlMode=1` (use default; IPC is exercised via PitchControl's internal IPC call when IPC_ControlMode > 0 in the default tuning)
- Added `F_FlpCornerFreq: '1.0 0.7'` and `F_FlCornerFreq: '1.0 0.7'`

### Root Cause 2: DLL SAVE Variable Persistence Across Loads

**Discovery method:** Added debug prints at the top of the instrumented DISCON body showing `iStatus`, `AlreadyInitialized`, and `CC_Mode` on each call. Key output:

```
DBG_ENTER: iStatus=0  AlreadyInit=1  CC_Mode=0
DBG_SETPARAM: aviFAIL=-1  CC_Mode=1
```

The FIRST call had `AlreadyInitialized = 1` — but this was supposed to be 0 (fresh DLL load).

**Root cause:** Python's `ctypes.CDLL.LoadLibrary()` and the subsequent `dlclose()` in ROSCO's `kill_discon()` don't guarantee the shared library is truly unloaded from memory. When ctypes maintains an internal reference, `dlclose` decrements the reference count but doesn't unmap the library. On the next `cdll.LoadLibrary()`, the same memory is reused — and all Fortran `SAVE` variables retain their values from the previous load.

KGen's extraction runs the simulation TWICE: once for ETime profiling, once for state capture. Both phases call `vit_sim.py`, which loads/unloads the DLL for each scenario. If the DLL isn't truly unloaded between the profiling phase and the state capture phase, SAVE variables from profiling persist into state capture:

- `LocalVar%AlreadyInitialized = 1` → SetParameters rejects reinitialization (line 87-94 of ReadSetParameters.f90):
  ```fortran
  IF (LocalVar%iStatus == 0) THEN
      IF (LocalVar%AlreadyInitialized == 0) THEN
          LocalVar%AlreadyInitialized = 1
      ELSE
          ErrVar%aviFAIL = -1
          ErrVar%ErrMsg = 'ERROR: This ROSCO dynamic library has already been loaded.'
          RETURN
      ENDIF
  ENDIF
  ```
- `kgen_invoke`, `kgen_islast`, `kgen_isstop` — stale from profiling, causing invocation counter to be wrong and stop flags to be set

**The deadlock:** Once `aviFAIL = -1`, it NEVER recovers:
1. SetParameters is the only code that resets `aviFAIL` to 0 (line 172: `ErrVar%aviFAIL = 0`)
2. SetParameters is only called when `aviFAIL >= 0` (DISCON's check: `IF (ErrVar%aviFAIL >= 0) THEN CALL SetParameters(...)`)
3. Result: aviFAIL=-1 forever, all control code skipped, 0 state files

**Fix:** VIT's build wrapper now patches the instrumented source files with two resets:

```fortran
! Before CALL ReadAvrSWAP (runs every call):
LocalVar%AlreadyInitialized = 0

! Before CALL ReadAvrSWAP, only on init call (iStatus=0):
IF (NINT(avrSWAP(1)) == 0 .AND. ALLOCATED(kgen_isstop)) THEN
    DEALLOCATE(kgen_isstop, kgen_issave, kgen_islast, kgen_ischecked)
    DEALLOCATE(kgen_invoke, kgen_last_invoke, kgen_filepath, kgen_lockpath)
END IF
```

### Root Cause 3: KGen Array Deallocation Scope

**Discovery method:** After applying the AlreadyInitialized and DEALLOCATE fixes, extraction consistently produced exactly 1 state file (not 20).

**Root cause:** The initial DEALLOCATE patch ran on EVERY call to DISCON (not just iStatus=0). This caused the KGen init block (`IF (.NOT. ALLOCATED(kgen_isstop))`) to re-run every call, resetting `kgen_invoke = 1`. Every call captured invocation 1, overwriting `CableControl.0.0.1` repeatedly. Result: exactly 1 state file.

**Fix:** Condition the DEALLOCATE on `NINT(avrSWAP(1)) == 0` (iStatus=0, init call only). This way:
- On the first call (iStatus=0): DEALLOCATE runs → KGen init re-runs → fresh counters
- On subsequent calls (iStatus=1): DEALLOCATE skipped → counters increment normally → 20 captures

### Root Cause 4: Multi-Scenario DLL Reload Contamination

**Discovery method:** Even with the above fixes, running all 5 scenarios caused CntrPar to be overwritten by later scenarios (which have different mode settings). With DLL persistence, each scenario's init_discon (iStatus=0) triggers SetParameters to re-read CntrPar from a DIFFERENT DISCON input file, overwriting CC_Mode=1 with CC_Mode=0 from a later scenario.

**Fix:** Added `--run-args` CLI option to `vit extract`:
```bash
vit extract CableControl -f rosco/controller/src/DISCON.F90 -l 132 --run-args '--scenario 3'
```

This appends `--scenario 3` to the run_cmd, causing vit_sim.py to run ONLY Scenario 3. With a single scenario, the DLL is loaded once (iStatus=0), params are read once (CC_Mode=1), and 20 invocations are captured before KGen's STOP.

## The Fix Chain (All Required Simultaneously)

For CableControl/StructuralControl extraction from DISCON.F90 to produce 20 state files, ALL of the following were needed:

1. **Assumed-size array patch** (from previous session): `avrswap` → `avrswap(1:2000)` at kw_* call sites
2. **Validation fixes**: StC_GroupIndex=2801, remove IPC+Flp conflict, add F_FlpCornerFreq/F_FlCornerFreq
3. **AlreadyInitialized=0 reset** before ReadAvrSWAP (every call)
4. **KGen SAVE array DEALLOCATE** on iStatus=0 only (not every call)
5. **Single-scenario execution** via `--run-args '--scenario 3'`

Removing ANY one of these causes 0 state files. The fixes interact:
- Without #2, aviFAIL=-1 from validation → control block skipped
- Without #3, AlreadyInitialized=1 from profiling → aviFAIL=-1 from already-loaded check
- Without #4, kgen_invoke resets to 1 every call → only 1 state file
- Without #5, multi-scenario DLL reloads overwrite CntrPar with wrong mode settings

## Implementation Details

### VIT Changes (`vit/vit/extract.py`)

The `generate_state_capture_build_script()` function now generates wrapper script code for three patches:

**Step 2b — Assumed-size arrays** (from previous session):
```bash
sed -i 's/(avrswap, kgen_unit/(avrswap(1:2000), kgen_unit/gI' "$f90"
```

**Step 2c — DLL SAVE persistence workaround:**
```bash
# Reset AlreadyInitialized (every call)
sed -i '/CALL ReadAvrSWAP/i\    LocalVar%AlreadyInitialized = 0' "$f90"

# Deallocate KGen arrays (init call only)
sed -i '/CALL ReadAvrSWAP/i\    IF (NINT(avrSWAP(1)) == 0 .AND. ALLOCATED(kgen_isstop)) THEN\n        DEALLOCATE(kgen_isstop, kgen_issave, kgen_islast, kgen_ischecked)\n        DEALLOCATE(kgen_invoke, kgen_last_invoke, kgen_filepath, kgen_lockpath)\n    END IF' "$f90"
```

These sed commands run on ALL `.f90`/`.F90` files in the source directory during the build wrapper execution (between source replacement and cmake build).

### VIT Changes (`vit/vit/cli.py`)

New `--run-args` option for `vit extract`:
```python
p_extract.add_argument("--run-args", help="Extra arguments to append to the run command")
```

Appended to `config.run_cmd` before passing to `run_kgen_extract`.

### ROSCO Changes (`Examples/vit_sim.py`)

Scenario 3 patches fixed:
- `StC_GroupIndex`: '2601' → '2801'
- Removed `IPC_ControlMode: 1` (conflicts with Flp_Mode)
- Added `F_FlpCornerFreq: '1.0 0.7'` (required when Flp_Mode > 0)
- Added `F_FlCornerFreq: '1.0 0.7'` (required when Fl_Mode > 0)

## Extraction Results

| Function | Cases | Call site | Notes |
|----------|-------|-----------|-------|
| VariableSpeedControl | 20 | DISCON.F90:117 | No mode gate |
| PitchControl | 20 | DISCON.F90:119 | PC_ControlMode > 0 (default) |
| YawRateControl | 20 | DISCON.F90:123 | Y_ControlMode=1 |
| FlapControl | 20 | DISCON.F90:127 | Flp_Mode=1 |
| **CableControl** | **20** | DISCON.F90:132 | CC_Mode=1 + `--run-args '--scenario 3'` |
| **StructuralControl** | **20** | DISCON.F90:137 | StC_Mode=1 + `--run-args '--scenario 3'` |
| FloatingFeedback | 0 | Controllers.f90:96 | Needs follow-up (see below) |

## FloatingFeedback: Remaining Issue

FloatingFeedback is extracted from Controllers.f90:96 (inside PitchControl), not from DISCON.F90. The DLL persistence patches (AlreadyInitialized reset and DEALLOCATE) are applied via sed targeting `CALL ReadAvrSWAP`, which exists only in DISCON.F90. Controllers.f90 doesn't have this line, so the patches don't apply.

The KGen SAVE variables in Controllers.f90 (kgen_isstop, kgen_invoke, etc.) persist from the profiling phase just like in DISCON.F90. The fix needs to be generalized: instead of targeting `CALL ReadAvrSWAP` (DISCON-specific), the DEALLOCATE should target the KGen init check `IF (.NOT. ALLOCATED(kgen_isstop))` directly, with a condition that only runs once per DLL "load."

**Proposed fix:** Change the DEALLOCATE sed to target the KGen region directly:
```bash
sed -i '/IF (.NOT. ALLOCATED(kgen_isstop))/i\    IF (LocalVar%iStatus == 0 .AND. ALLOCATED(kgen_isstop)) THEN\n        DEALLOCATE(...)\n    END IF' "$f90"
```

This would work for any instrumented file (DISCON.F90 or Controllers.f90) because `LocalVar%iStatus` is available in both contexts. The AlreadyInitialized reset would remain DISCON-specific (only DISCON calls ReadAvrSWAP/SetParameters).

## Debugging Methodology

The investigation used a systematic approach of adding Fortran WRITE statements to the instrumented state/DISCON.F90 at strategic points:

1. **Entry point:** `WRITE(*,*) "DBG_ENTER: iStatus=", NINT(avrSWAP(1)), " AlreadyInit=", LocalVar%AlreadyInitialized` — confirmed AlreadyInitialized=1 persistence
2. **After SetParameters:** `WRITE(*,*) "DBG_SETPARAM: aviFAIL=", ErrVar%aviFAIL, " CC_Mode=", CntrPar%CC_Mode` — confirmed aviFAIL=-1 deadlock, CC_Mode=1 read correctly
3. **Error message:** `IF (ErrVar%aviFAIL < 0) WRITE(*,*) TRIM(ErrVar%ErrMsg)` — revealed each validation error in sequence
4. **Inside mode block:** `WRITE(*,*) "DBG_CC_BLOCK: entered"` — confirmed the block was never reached

Each debug point was added to state/DISCON.F90 (preserved by using the Python extraction script instead of CLI), then `make -C state/ run` was executed to see the output. The state/Makefile handles source replacement, build, restore, and simulation execution.

## Key Lessons

### DLL SAVE Persistence Is a Fundamental Issue

The ctypes/dlclose behavior is well-known in the Python community but rarely encountered in scientific computing. Fortran's SAVE attribute (implicit in modules, explicit in subroutines) creates global state that was never designed for dynamic loading/unloading cycles. ROSCO's `AlreadyInitialized` check is a reasonable safeguard against double-loading, but it becomes a trap when the DLL isn't truly unloaded.

This affects ANY project that uses `ctypes.CDLL` with Fortran shared libraries that have SAVE variables. The KGen profiling → state capture workflow inherently triggers this because both phases load the same library via the same Python process.

### Validation Errors Are Silent and Cascading

ROSCO's parameter validation sets `aviFAIL=-1` but the error message is cleared at the end of each DISCON call. On subsequent calls, `aviFAIL=-1` persists but the message is gone. The only way to see the error is to print ErrMsg IMMEDIATELY after SetParameters, within the same call where the error occurs.

The cascading effect: one validation failure (e.g., StC_GroupIndex) disables ALL control code for ALL subsequent calls. Functions with no validation issues (e.g., CableControl, VariableSpeedControl) are still blocked because they share the same aviFAIL flag.

### Single-Scenario Extraction Is More Robust

Running all 5 scenarios during extraction introduces multi-scenario DLL reload issues that are impossible to fix cleanly (CntrPar gets overwritten by different DISCON files). Restricting to a single scenario (`--run-args '--scenario 3'`) eliminates this entirely. The cost is that each extraction must specify which scenario provides the right mode configuration.

## Commits

**VIT repo:**
- `6d86620` — Fix DLL SAVE persistence for DISCON.F90 extraction, add --run-args

**ROSCO repo:**
- `5827f49` — Fix Scenario 3 validation errors for DISCON.F90 extraction
