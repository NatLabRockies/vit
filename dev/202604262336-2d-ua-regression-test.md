# 2D UA Regression Test: ad_MHK_RM1_Fixed_UA
**Date:** 2026-04-26
**Status:** completed
**Scope:** Regression test coverage for AFI_ComputeUACoefs 2D interpolation path

## Problem

AFI_ComputeUACoefs was verified 20/20 via KGen kernel, but all 20 cases exercised only the AFITable_1 path (trivial struct copy). The 2D interpolation path — `AFI_ComputeUACoefs2D` → `FindBoundingTables` → `AFI_UA_BL_Type_ExtrapInterp1` → `Angles_ExtrapInterp1` → `AddOrSub2Pi` — was never executed by any test case in the regression suite.

Coverage analysis showed:
- `ad_BAR_SineMotion_UA4_DBEMT3`: UA_Mod=4 but AFTabMod=1 (single table, no 2D interpolation)
- `ad_MHK_RM1_Fixed`: AFTabMod=2Re (multi-Re tables) but UA_Mod=0 (no UA)
- No test case combines AFTabMod=2 with UA_Mod>0

## Solution

Created `ad_MHK_RM1_Fixed_UA` — a copy of `ad_MHK_RM1_Fixed` modified to enable UA with multi-Re interpolation. This is the 17th regression test case.

### Changes from ad_MHK_RM1_Fixed

**AeroDyn config** (`MHK_RM1_Fixed_AeroDyn.dat`):
- `CavitCheck`: True → False (CavitCheck requires UA_Mod=0)
- `UA_Mod`: 0 → 4 (HGM 4-states unsteady aero)
- `UAStartRad`: 4 → 0 (critical fix — see below)
- `AFTabMod`: unchanged at 2 (2D interpolation on AoA and Re)

**9 airfoil files** (NACA6_0240 through NACA6_1000):
- `InclUAdata`: False → True in all 7 Re tables per file (63 table blocks total)
- 10 UA parameters added to each table block: alpha0, alpha1, alpha2, eta_e, C_nalpha, C_lalpha, Cn1, Cn2, Cd0, Cm0
- UA parameters vary with Re index to ensure non-trivial interpolation: `alpha0 = -2.5 + 0.15*i`, `C_nalpha = 6.28 + 0.1*i`, `Cd0 = 0.008 - 0.0005*i` where i is the Re table index (0-6)

### UAStartRad Bug

The original MHK config had `UAStartRad=4` (and `IntegrationMethod=3` on the same line position). With UA_Mod=0 this was irrelevant, but with UA_Mod=4 it means "start dynamic stall at 400% of rotor radius" — effectively disabling UA for all blade stations. The test ran successfully and even produced a baseline, but AFI_ComputeUACoefs was never called.

This was caught by adding temporary diagnostics (fprintf to stderr) at two points:
1. The Fortran wrapper entry (`WRITE(0,*)` with AFTabMod and NumTabs)
2. The C++ 2D interpolation path (after FindBoundingTables, before ExtrapInterp1)

With UAStartRad=4: zero diagnostic output — function never called.
With UAStartRad=0: both diagnostics fired, confirming `AFTabMod=2, NumTabs=7, interpolation between tables 1-2`.

**Lesson:** A test case can run, produce output, and even show differences from the reference — while still not exercising the intended code path. Configuration parameters inherited from a different setup (UA_Mod=0) can silently disable the feature being tested. Always confirm path coverage with direct instrumentation.

### Baseline Generation Process

The baseline must be generated from clean Fortran, not from the integrated C++ build. Generating from C++ and verifying against itself is circular — it only proves determinism, not Fortran equivalence.

Correct process:
1. `bash scripts/reset_to_clean.sh` — restore AirfoilInfo.f90 to pre-integration Fortran
2. Rebuild (`cmake --build`)
3. `bash scripts/generate_aerodyn_baseline.sh ad_MHK_RM1_Fixed_UA` — captures Fortran output
4. `git checkout HEAD -- modules/aerodyn/src/*.cpp modules/aerodyn/src/*.f90` — restore integrated source
5. Rebuild
6. `bash scripts/verify_aerodyn_baselines.sh ad_MHK_RM1_Fixed_UA` — verify C++ ≡ Fortran

Result: bit-identical. The binary `.outb` baseline is the same whether generated from Fortran or C++.

## Verification

- **17/17 baselines PASS** (16 existing + 1 new)
- **2D path confirmed exercised** via temporary diagnostics (removed after confirmation)
- **Fortran baseline** properly generated from clean source

## Files

### r-test submodule (new)
- `modules/aerodyn/ad_MHK_RM1_Fixed_UA/` — complete test case directory (driver, AeroDyn config, 9 airfoil files with UA data, InflowWind, SeaState, TurbSim)

### OpenFAST repo
- `baselines/aerodyn/ad_MHK_RM1_Fixed_UA/ad_driver.baseline.outb` — Fortran-generated baseline
- `baselines/aerodyn/ad_MHK_RM1_Fixed_UA/drift_vs_nrel.txt` — drift report vs original NREL reference (81.48% bit-identical, 18.52% drift from UA effects)

### Commits
| Repo | Commit | What |
|------|--------|------|
| r-test | `8a747fb` | Add ad_MHK_RM1_Fixed_UA test case |
| r-test | `aff69fd` | Fix UAStartRad=0 |
| OpenFAST | `885fb4ab6` | Add test case + baseline |
| OpenFAST | `54a239eec` | Fix UAStartRad, confirm 2D path |
| OpenFAST | `38bb0f1ca` | Regenerate baseline from clean Fortran |
