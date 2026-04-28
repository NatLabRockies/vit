# integrate_all.sh and AirfoilInfo Integration Gap Fix
**Date:** 2026-04-28
**Status:** completed
**Scope:** Integration infrastructure, AirfoilInfo module

## Background

During DBEMT ComputeTau2 integration, we discovered that 14 of 20 AirfoilInfo functions had original Fortran bodies despite `status: integrated` in vit.yaml. Only 6 had C++ wrappers (AFI_Init, ReadAFfile, AFI_ComputeUACoefs, AFI_WrHeader/WrData/WrTables). The 14 early leaf function wrappers were lost during extraction cycles: each `reset_to_clean.sh` reverted AirfoilInfo.f90, and no script existed to re-apply them.

This is the same problem ROSCO solved with `integrate_all.sh`.

## Root Cause

The extraction workflow requires clean source (`reset_to_clean.sh`), which restores AirfoilInfo.f90 to upstream clean. After extraction, `git checkout` restored the committed version — but the committed version only had the LATEST integrations (hand-integrated orchestrators), not the early leaf wrappers (which were reverted by previous resets and never recommitted).

The 17/17 baselines passed trivially because the Fortran code was unchanged — the early C++ translations were compiled and linked but never called from Fortran. They were only called via C++-to-C++ from the orchestrator wrappers (e.g., `afi_computeuacoefs_c` → `calculateuacoeffs_c`).

## Fix: integrate_all.sh

Created `openfast/scripts/integrate_all.sh` following the ROSCO pattern:

1. Restores committed source files from git (hand-integrated wrappers + view populators + vit_nwtc.cpp)
2. Runs `vit integrate` for 13 functions via `docker exec` (generates Fortran wrappers)
3. Restores committed .cpp files (VIT creates duplicates; committed versions are correct)

The script runs on the Mac (needs git + docker exec). Git was also installed in the container for future convenience.

**Functions integrated via `vit integrate` (13):**
Calculate_Cn, FindBoundingTables, Compute_iLoweriUpper, ComputeUA360_CnOffset, Calculate_C_alpha, ComputeUASeparationFunction_zero, ComputeUA360_updateCnSeparated, ComputeUA360_updateSeparationF, ComputeUASeparationFunction_onCl, ComputeUA360_AttachedFlow (--reverse-copy), CalculateUACoeffs (--reverse-copy), AFI_ValidateInitInput, AFI_ComputeAirfoilCoefs

**Functions from committed source (6, hand-integrated):**
AFI_Init, ReadAFfile, AFI_ComputeUACoefs, AFI_WrHeader, AFI_WrData, AFI_WrTables

## Verification

Full cycle tested: `reset_to_clean.sh` → `integrate_all.sh` (13/13 OK) → build → **17/17 baselines PASS** (including ad_MHK_RM1_Fixed_UA which was the failing case during DBEMT debugging).

## VIT Bug: Duplicate extern "C" Wrappers

`vit integrate` creates .cpp files by wrapping the user's translation with a VIT header + extern "C" block. But the early scaffold-pattern translations already have an extern "C" wrapper. This causes duplicate symbol errors. The workaround in integrate_all.sh: restore the committed .cpp files after running `vit integrate` (which only needs the Fortran wrapper changes). A proper fix would be for VIT to detect existing extern "C" blocks and skip generation.

## Related: DBEMT ComputeTau2

The DBEMT integration failure that prompted this investigation was caused by replacing a PRIVATE module-internal function body with a C++ wrapper — changing gfortran's optimization of the caller (DBEMT_UpdateStates). This is a separate issue from the missing integrate_all.sh, but was discovered during the same debugging session. ComputeTau2 remains verified (20/20 IDENTICAL) but not integrated. See dev note `202604281032` for the full analysis.
