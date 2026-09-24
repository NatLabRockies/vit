# Full End-to-End Test: Extraction, Verification, Integration — ALL PASS
**Date:** 2026-03-27
**Status:** completed
**Scope:** Complete pipeline validation after verification pipeline investigation and fixes

## What Was Tested

A complete end-to-end test of the VIT pipeline following INTEGRATION_TEST.md, incorporating all fixes from this session:

1. **Extraction** (28 functions): Fresh extraction with per-function scenario mapping, statefile deduplication, and ElapsedTime CMake cache fix
2. **Verification** (28 functions): All C++ translations verified against freshly extracted golden fixtures
3. **Integration** (27 functions): All translations integrated into ROSCO codebase (PitchControl excluded — awaits PitchSaturation in Phase 8)
4. **Simulation comparison**: 612,000 float64 values across 8 scenarios, byte-identical between pure-Fortran baseline and integrated Fortran/C++ build

## Results

### Extraction: 28/28 passed
All functions extracted with explicit `--run-args '--scenario N'` using `scripts/extract_all.sh`. Case counts 20-23 per function. Statefile lists deduplicated (no more 40-entry duplicates).

### Verification: 28/28 passed
All functions verified using `scripts/verify_all.sh`. Each function reports `N/N passed` matching its extraction case count.

### Integration: 612,000 values ALL IDENTICAL

```
scenario_1: 5 arrays × 40,000 values = 200,000  IDENTICAL
scenario_2: 3 arrays × 4,000 values  =  12,000  IDENTICAL
scenario_3: 5 arrays × 16,000 values =  80,000  IDENTICAL
scenario_4: 5 arrays × 4,000 values  =  20,000  IDENTICAL
scenario_5: 5 arrays × 16,000 values =  80,000  IDENTICAL
scenario_6: 5 arrays × 4,000 values  =  20,000  IDENTICAL
scenario_7: 5 arrays × 24,000 values = 120,000  IDENTICAL
scenario_8: 5 arrays × 16,000 values =  80,000  IDENTICAL
                                TOTAL: 612,000  ALL IDENTICAL
```

## What Changed Since the Last Full Test (2026-03-26)

This session began with a Q&A about what the verification pipeline actually tests and evolved into a deep investigation of KGen's extraction internals. The changes made and validated by this test:

### VIT code changes
- **Statefile deduplication** (`extract.py`): `kgen_statefile.lst` is deduplicated after extraction, fixing the ElapsedTime append bug that doubled entries (40 → 20)
- **CMake cache cleanup** (`extract.py`): `cleanup_kgen_utils_injection()` reconfigures CMake after extraction, preventing stale `DependInfo.cmake` from breaking subsequent builds
- **model/ directory cleanup** (`extract.py`): `clean_kgen_artifacts()` now removes `model/` between extractions so ElapsedTime profiles the correct function
- **4 new tests** for statefile deduplication logic (557 total)

### Extraction changes
- **Per-function scenario mapping**: Every function now extracts with the specific scenario that exercises its call site with non-zero inputs (Scenarios 2, 3, 4, 6, 7, or 8)
- **No more default (all-scenario) runs**: Eliminates wasteful multi-scenario extraction and ensures deterministic invocation capture

### Automation
- **`scripts/extract_all.sh`**: Extracts all 28 functions sequentially with correct scenarios
- **`scripts/verify_all.sh`**: Verifies all 28 translations against golden fixtures

### Documentation
- **CLAUDE.md**: Updated extraction table with explicit scenarios, replaced 90+ lines of individual commands with script references, fixed stale 40/40 references
- **PROJECT_PLAN.md**: Updated current state to reflect variable case counts and per-function scenarios
- **INTEGRATION_TEST.md**: Added "Functions Exercised (non-zero inputs)" column to scenario table, fixed stale "6 scenarios" reference

## Session Commits

**VIT repo:**
| Hash | Description |
|------|-------------|
| `19e3dbd` | Dev note: kgen_statefile.lst duplication root cause |
| `f49a789` | Update dev note: confirmed with instrumented extraction |
| `ab47db6` | Fix ElapsedTime build: reconfigure CMake, clean model/ |
| `3d196f8` | Deduplicate kgen_statefile.lst after extraction |
| `614ad7b` | Per-function scenario mapping, extract/verify scripts |
| `115e50e` | Dev note: 28/28 re-extraction and verification pass |
| `6d792d2` | Update docs: variable case counts, per-function scenarios |

**ROSCO repo:**
| Hash | Description |
|------|-------------|
| `30605ee` | Add extract_all.sh and verify_all.sh scripts |
| `c3905ce` | Re-extract all 28 golden fixtures with correct scenarios |
| `ec483e1` | Update vit.yaml case counts |
| `f1d5248` | Update INTEGRATION_TEST.md: called vs exercised functions |
