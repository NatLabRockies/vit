# WE_FOPoles State File Data Error Investigation
**Date:** 2026-03-25
**Status:** root cause narrowed but not fully resolved
**Scope:** KGen state capture, CableControl kernel, ControlParameters ALLOCATABLE fields

## The Problem

CableControl verification shows `WE_FOPoles` as OUT_TOL in case 0.0.1. The two-run Fortran-vs-C++ comparison correctly handles this (both Fortran and C++ produce the same result), so verification passes. But the underlying data error in the state file was previously dismissed as a "state file artifact" without understanding the mechanism.

## What the Diagnostic Shows

Added diagnostic code to the kernel's DISCON.F90 to print the exact differing element before the kv_ comparison:

```
DIAG we_fopoles sizes: 60 60
DIAG we_fopoles diff at 3: entry= 0.0000000000000000 exit= -1.9650000000000001E-002 diff= 1.9650000000000001E-002
```

- **Element:** 3 (of 60)
- **Entry value (from state file):** `0.0` (zero)
- **Exit reference (from state file):** `-0.01965`
- **DISCON.IN file value:** `-1.965e-02` = `-0.01965`

The exit reference matches the DISCON.IN file value. The entry value is zero. Something caused element 3 to be `0.0` in the entry capture despite `SetParameters` reading it as `-0.01965` from the file earlier in the same DISCON call.

## What We Confirmed

### CableControl does NOT write to WE_FOPoles

Searched all ROSCO source files. `WE_FOPoles` is:
- **Written** only in `ReadSetParameters.f90:518` via `ParseAry` during initialization (`iStatus == 0`)
- **Read** only in `ControllerBlocks.f90:421` as `interp1d` input in `WindSpeedEstimator`
- **Never written** by CableControl or any function between SetParameters and CableControl

### No code between SetParameters and CableControl modifies WE_FOPoles

The DISCON.F90 call sequence between `SetParameters` (line 85) and `CableControl` (line 132):
- `ExtController` (line 90) — only if `Ext_Mode > 0`
- `PreFilterMeasuredSignals` (line 96)
- `WindSpeedEstimator` (line 109) — READS `WE_FOPoles`, does not write
- `PowerControlSetpoints` (line 110)
- `ComputeVariablesSetpoints` (line 114)
- `StateMachine` (line 115)
- `SetpointSmoother` (line 116)
- `VariableSpeedControl` (line 117)
- `PitchControl` (line 119)
- `YawRateControl` (line 123)
- `FlapControl` (line 127)
- `CableControl` (line 132)

None of these write to `CntrPar%WE_FOPoles`.

### The difference is NOT a floating-point precision issue

Entry is `0.0` (exact zero). Exit is `-0.01965` (matches the DISCON.IN file value). This is a **2% absolute difference** — orders of magnitude larger than any floating-point rounding. It's a zero vs. nonzero discrepancy for a field that should have the file value at both entry and exit capture points.

### Same pattern in StructuralControl

StructuralControl shows similar OUT_TOL for `pc_gs_ki` (element unknown, rms=1.3e-9) and `vs_kp` (element unknown, rms=3.9e-6). These are also ControlParameters ALLOCATABLE fields. The pattern — specific elements of specific ALLOCATABLE fields having wrong values in the entry capture — suggests a systematic KGen issue.

## Hypotheses

### 1. KGen field ordering mismatch
KGen's `kw_` (write) and `kr_` (read) routines iterate through ControlParameters fields in a specific order. If the order doesn't match the actual TYPE layout for certain ALLOCATABLE fields, the binary state file could have misaligned data — one field's values written at another field's position.

### 2. Binary format byte-level misalignment
Each ALLOCATABLE field is preceded by metadata (LOGICAL flag, REAL(8) checksum, 2 INTEGER bounds). If one field's metadata consumes a different number of bytes than expected (e.g., from LOGICAL size differences with `-fdefault-real-8`), all subsequent fields in the file would be shifted.

### 3. DLL persistence during extraction
KGen runs a profiling phase then a capture phase. If `CntrPar` persists between phases (DLL not truly unloaded), and the profiling phase leaves `WE_FOPoles(3)` at `0.0` (e.g., from a timestep where `WE_Mode != 2` skipped the ParseAry read), the capture phase might capture this stale value as the entry state.

VIT's `dll_persistence` configuration resets `LocalVar%AlreadyInitialized` but does NOT reset `CntrPar` fields. If CntrPar persists with stale ALLOCATABLE data from a previous phase, the entry capture would see stale data.

### 4. ParseAry skip behavior
`ParseAry` for `WE_FOPoles` has a skip flag: `CntrPar%WE_Mode .NE. 2`. If `WE_Mode != 2` on any intermediate call, ParseAry might reallocate the array to zeros without reading values. However, `WE_Mode` should be `2` throughout Scenario 3.

## Current State

The two-run Fortran-vs-C++ comparison correctly handles this issue: both Fortran and C++ produce the same result for `WE_FOPoles` (neither modifies it), so the OUT_TOL appears identically in both field logs and is treated as a matching result. Verification passes.

The data error is in the KGen state file, not in VIT or the C++ translation. It affects 3 specific ALLOCATABLE fields across 2 functions (CableControl: `we_fopoles`; StructuralControl: `pc_gs_ki`, `vs_kp`). The exact KGen mechanism that produces these errors is not yet identified.

## What Would Be Needed to Resolve

1. **Examine the KGen instrumented DISCON.F90** during extraction (before VIT restores the original) to see exactly where captures are placed relative to the call and the surrounding code
2. **Add diagnostics to the extraction** to print `WE_FOPoles(3)` at the entry capture point during the actual simulation run
3. **Check the binary state file** with a hex editor to verify the field ordering matches expectations
4. **Test DLL persistence** by checking if the entry value changes when extraction is run with `--run-args '--scenario 3'` (single scenario) vs. multi-scenario
