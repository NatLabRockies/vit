# Stage E Complete: PreFilterMeasuredSignals + SetParameters

**Date:** 2026-04-02
**Status:** completed
**Scope:** Stage E translation — final two non-DISCON orchestrator functions (50→52)

## What We Did

Translated the last two non-DISCON functions: PreFilterMeasuredSignals (filter cascade) and SetParameters (initialization + per-timestep orchestrator). Both are called every timestep from DISCON.F90. After Stage E, every function in ROSCO except DISCON itself is C++.

## PreFilterMeasuredSignals

Filters.f90:325-439 (115 lines). Pure filter orchestrator — conditionally applies LP, SecLP, HP, Notch, and NotchFilterSlopes filters to sensor signals based on CntrPar mode flags.

### Translation approach

Standard `vit translate` + `vit integrate --apply`. CntrPar is the only view type; LocalVar, DebugVar, objInst, ErrVar are all bind_c. All 6 callee filter types were already translated and available via `_c` entry points.

The translation was straightforward — a line-by-line mapping of filter calls to their `_c` equivalents. The main complexity was the double-indexed CntrPar ALLOCATABLE array pattern:

```fortran
! Fortran (1-based everything):
CntrPar%F_NotchFreqs(CntrPar%F_GenSpdNotch_Ind(n))

! C++ (0-based everything):
CntrPar->F_NotchFreqs[CntrPar->F_GenSpdNotch_Ind[n-1] - 1]
```

Both the loop variable `n` and the stored index value are 1-based in Fortran, so both need adjustment for C++.

### Field name case trap

The build initially failed with two case mismatches:

1. `LocalVar->We_Vw_F` → should be `LocalVar->WE_Vw_F` (TYPE definition uses `WE_Vw_F`)
2. `LocalVar->NacIMU_FA_AccF` → should be `LocalVar->NACIMU_FA_AccF` (TYPE definition uses `NACIMU_FA_AccF`)

The second one is particularly insidious: ROSCO_Types.f90 defines the LocalVariables field as `NACIMU_FA_AccF` (all caps NACIMU), but the Fortran usage code in Filters.f90 writes `NacIMU_FA_AccF` (mixed case). Meanwhile, the DebugVariables type defines its field as `NacIMU_FA_AccF` (mixed case). So the same conceptual field has different casing in the two struct TYPE definitions. Fortran doesn't care (case-insensitive), but C++ struct field names must match the TYPE definition exactly.

This is the exact scenario that TRANSLATION_TIPS.md warns about ("C struct field names use TYPE definition case"), but it still caught us. The Fortran source code is misleading — the actual struct definitions are authoritative.

## SetParameters

ReadSetParameters.f90:143-298 (156 lines). Initialization + per-timestep orchestrator that manages the DLL's first-call setup and per-timestep bookkeeping.

### The ALLOCATE protocol challenge

SetParameters calls ReadControlParameterFileSub on the first timestep (iStatus==0). ReadControlParameterFileSub's Fortran wrapper implements a two-pass protocol: pass1_c reads scalars and counts array sizes, Fortran ALLOCATEs the CntrPar arrays, then pass2_c fills them. C++ cannot allocate Fortran ALLOCATABLE arrays. The CntrPar view struct can't be populated until the arrays exist.

This means a fully-translated SetParameters C++ function can't call ReadControlParameterFileSub — there's no single `_c` entry point that does the full operation, and the ALLOCATE step between passes must happen in Fortran.

### Solution: Manual integration with callee dispatch

Same pattern as ReadRestartFile (Stage C). The Fortran wrapper handles:
- Per-timestep init: ErrVar clear, 9 objInst counter resets, 11 avrSWAP defaults (~23 lines of simple assignments, must precede any callee calls to clear error flags)
- iStatus==0 callee dispatch: banner print (uses `rosco_version`), accINFILE→LocalVar save, ReadControlParameterFileSub call (two-pass wrapper), ReadCpFile call
- View population (CntrPar only — PerfData not needed by C++)
- Error message prepend for both ReadControlParameterFileSub and CheckInputs errors

The C++ `setparameters_c` handles:
- iStatus==0: LocalVar field initialization (~25 fields), interp1d_c call (for VS_FBP_Tau lookup), checkinputs_c call
- Per-timestep: OL_Index computation (conditional lpfilter_c call)

The C++ signature is clean — no CHARACTER args, no expression-based types:
```cpp
void SetParameters(controlparameters_view_t* CntrPar, localvariables_t* LocalVar,
                   float* avrSWAP, objectinstances_t* objInst,
                   errorvariables_t* ErrVar, int size_avcMSG)
```

accINFILE and RootName are handled entirely by the wrapper (only used for ReadControlParameterFileSub and the accINFILE save). PerfData is not passed to C++ — it's only used by ReadCpFile, which has its own Fortran wrapper.

### Why per-timestep init stays in the wrapper

The per-timestep init (ErrVar%aviFAIL = 0, objInst counters, avrSWAP defaults) must execute BEFORE ReadControlParameterFileSub on the first call — clearing aviFAIL before any callee that might set it. If this init were in C++ (which runs after the file reading), the ordering would be wrong: ReadControlParameterFileSub could set an error, then the C++ init would clear it.

In Phase 11, when DISCON becomes C++ and calls SetParameters directly, all of this moves to C++ (including the banner, accINFILE save, and file reading — because in a pure C++ world, CntrPar is a C++ struct with std::vector fields, not Fortran ALLOCATABLE).

## Verification

- **52/52 integration** via integrate_all.sh
- **692,000 values IDENTICAL** across 10 scenarios (sim-compare)
- Both functions are called every timestep, so sim-compare provides comprehensive coverage — every filter coefficient, every initialization value, every OL_Index computation is verified

## What This Means

Every function in ROSCO except DISCON itself is now C++. The function count:
- 42 KGen-verified (kernel-level, IDENTICAL)
- 10 simulation-verified (3 Stage B + 2 Stage D + 3 Stage C + 2 Stage E)

The remaining work is Phase 11: DISCON becomes a pure C++ entry point. Every Fortran wrapper becomes dead code. The two-pass ALLOCATE protocol for ReadControlParameterFileSub goes away because CntrPar becomes a C++ struct with std::vector fields. The mixed Fortran/C++ binary disappears entirely.

## Files Changed

### ROSCO repo
| File | Change |
|------|--------|
| `translations/Filters/prefiltermeasuredsignals.cpp` | New — PreFilterMeasuredSignals C++ translation |
| `translations/ReadSetParameters/setparameters.cpp` | New — SetParameters C++ translation (partial — wrapper handles callee dispatch) |
| `scripts/integrate_all.sh` | TOTAL 50→52, added PreFilterMeasuredSignals integrate() call, added SetParameters manual integration block |
| `vit.yaml` | Added PreFilterMeasuredSignals and SetParameters entries |

### VIT repo
| File | Change |
|------|--------|
| `PROJECT_PLAN.md` | Stage E marked complete, count updated 50→52 |

## Session Notes

- Stage E was clean — no VIT tool changes needed. The existing CHARACTER(variable) support from Stage C wasn't even needed (PreFilterMeasuredSignals has no CHARACTER args; SetParameters' CHARACTER args are handled by the wrapper).
- The field name case trap (`NACIMU_FA_AccF` vs `NacIMU_FA_AccF`) reinforces the TRANSLATION_TIPS guidance. It's the kind of bug that's invisible in Fortran but breaks C++ compilation.
- The manual integration pattern (callee dispatch in wrapper) is now used by 3 functions: ReadRestartFile, SetParameters, and (indirectly) ReadControlParameterFileSub. All three need Fortran-side ALLOCATE operations that can't happen in C++. Phase 11 eliminates this pattern entirely.
