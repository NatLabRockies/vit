# DBEMT ComputeTau2 Integration Failure — Root Cause Analysis
**Date:** 2026-04-28
**Status:** in-progress
**Scope:** Integration architecture, AirfoilInfo audit finding

## Summary

Attempting to integrate ComputeTau2 (first DBEMT function, 20/20 KGen IDENTICAL) into DBEMT.f90 caused ad_MHK_RM1_Fixed_UA to diverge catastrophically (max_abs=5.73e+06). Investigation revealed the root cause is NOT a translation bug — it's a fundamental misunderstanding of what the AirfoilInfo "integration" actually did.

## Key Finding: AirfoilInfo Leaf Functions Were Never Replaced

The current AirfoilInfo.f90 has **original Fortran bodies** for all 11 early leaf functions:

- `FindBoundingTables` (line 1194): original binary search, no C++ call
- `AFI_ComputeAirfoilCoefs` (line 1402): original, delegates to 1D/2D in Fortran
- `AFI_ComputeAirfoilCoefs1D` (line 1320): original
- `AFI_ComputeAirfoilCoefs2D` (line 1271): original
- `ComputeUA360_CnOffset`, `Calculate_Cn`, etc.: not even present in AirfoilInfo.f90 (defined only in .cpp files)

Only **public entry points called from outside the module** have C++ wrappers:
- `AFI_ComputeUACoefs` (line 1437) → `afi_computeuacoefs_c`
- `AFI_Init` (~line 200) → `afi_init_pass1_c` / `afi_init_pass2_c`
- `ReadAFfile` (~line 370) → `readaffile_pass1_c` / `readaffile_fill_c`
- `AFI_WrHeader/WrData/WrTables` → respective `_c` functions

The early leaf functions' C++ code is compiled (in basicaerolib .cpp files) but called only via C++-to-C++ from the orchestrator wrappers. The Fortran bodies are dead code.

## How This Happened

The `reset_to_clean.sh` extraction workflow caused it:

1. Integrate Calculate_Cn → wrapper replaces body in AirfoilInfo.f90
2. Need to extract FindBoundingTables → `reset_to_clean.sh` reverts AirfoilInfo.f90
3. Integrate FindBoundingTables → wrapper replaces body
4. Need to extract next function → `reset_to_clean.sh` reverts again
5. ... repeat for all leaf functions
6. Finally integrate orchestrators (AFI_ComputeUACoefs, AFI_Init, etc.) → wrappers remain because no subsequent extractions needed them reverted

The final state has only the LAST integrations (orchestrators/public functions), with all early leaf wrapper insertions reverted by `reset_to_clean.sh`.

## Why 17/17 Baselines Passed for AirfoilInfo

The early "integrations" didn't change any live Fortran code path. The .cpp files were compiled and linked, but the Fortran ran unchanged. Baselines matched trivially because nothing changed in the executable code path.

The live call paths are:
- **Fortran path:** BEMT → `AFI_ComputeAirfoilCoefs` (original Fortran) → `AFI_ComputeAirfoilCoefs1D` (original) → `FindBoundingTables` (original) → all Fortran
- **C++ path:** BEMT → `AFI_ComputeUACoefs` (C++ wrapper) → `afi_computeuacoefs_c` → `calculateuacoeffs_c` → `computeua360_attachedflow_c` → all C++

## Why ComputeTau2 Integration Failed

ComputeTau2 is the **first time we actually replaced a PRIVATE function body** inside a module where the CALLER (DBEMT_UpdateStates) is still Fortran. This changes the module's compiled code.

Debugging confirmed: adding ANY subroutine call (even empty) inside ComputeTau2 causes ad_MHK_RM1_Fixed_UA to diverge. The change prevents gfortran from optimizing DBEMT_UpdateStates the same way, and the exponential expressions (lines 381-385) are extremely sensitive to evaluation-order changes. The UA feedback loop amplifies sub-ULP differences into full divergence.

## Implications

1. **AirfoilInfo "integration" for early functions is incomplete** — the C++ code exists but is only exercised via C++-to-C++ paths, not via Fortran wrappers
2. **The integration status in vit.yaml is misleading** — `status: integrated` for early leaf functions means "compiled and linked" not "actively called from Fortran"
3. **The correct integration pattern is orchestrator-level only**: replace PUBLIC entry points with C++ wrappers, call leaf _c functions from C++ via vit_translated.h
4. **Private leaf functions should NOT have their Fortran bodies replaced individually** — this changes the module's compilation landscape

## Open Questions

1. Should we fix AirfoilInfo by re-integrating the early leaf function wrappers? Or is the current state (dead Fortran code) acceptable since the C++ IS exercised via the orchestrator paths?
2. For DBEMT, should we translate all functions first and integrate only at the public orchestrator level?
3. Does this affect the PROJECT_PLAN's per-function integration milestones?
