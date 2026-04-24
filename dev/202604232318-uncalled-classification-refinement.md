# Uncalled Classification Refinement — Eliminating Guesswork
**Date:** 2026-04-23
**Status:** completed
**Scope:** Accuracy of uncalled procedure classification in the AeroDyn structural inventory

## Summary

Follow-up to dev note `202604232243`. The initial investigation reduced 210 false "dead code" entries to 67 with 0 unknowns, but relied on heuristic classification for two categories: "likely_framework_interface" (7 entries, based on naming convention) and "test_function" / "driver_entry_point" (29 entries, based on module name). User challenged: what does "likely" mean? Are we sure these aren't actually dead?

Investigation revealed the heuristics were partially wrong. Three rounds of fixes refined the classification from heuristic guesses to source-verified evidence for every entry.

## What was wrong

### Problem 1: Heuristics ran before source evidence

The original code classified by module name / procedure name prefix first:
```python
if "driver" in mod.lower():
    reason = "driver_entry_point"  # Heuristic — never verified
elif "test" in mod.lower():
    reason = "test_function"       # Heuristic — never verified
```

The deep source analysis (scanning for commented-out calls, callbacks, PROGRAM block calls) only ran for procedures classified as "unknown." So driver and test entries were never actually verified.

**Fix:** Run deep source analysis for ALL 67 procedures. Let source evidence override heuristics.

**Result:** 
- All 12 "driver_entry_point" entries reclassified to "program_block" (confirmed called from PROGRAM blocks)
- 11 of 17 "test_function" entries reclassified to "program_block" (DBEMT tests called from `PROGRAM DBEMT_Test`)
- 5 FVW_Tests entries remained "test_function" (genuinely uncalled test utilities)

### Problem 2: String literals caused false callback matches

Procedure names appearing inside error message strings were matching the callback detector:
```fortran
call SetErrStat(ErrID_Fatal, "... call ADI_C_Init ...", ...)  ! name in string
call WrScr("   ADI_C_Init")                                   ! name in string
call SetErrStat(..., 'Dvr_InitCase')                          ! name as label
```

The callback regex matched because it didn't distinguish string content from code.

**Fix:** Strip single- and double-quoted strings before checking for callback usage and live references.

### Problem 3: Substring matching

`print_tree` was matching `print_tree_rec` in live code, causing false `any_live_reference`. Similarly, other short names could match inside longer identifiers.

**Fix:** Use pre-compiled word-boundary regex (`\bname\b`) for all name matching instead of Python `in` operator.

### Problem 4: Definition line detection was whitespace-sensitive

`END SUBROUTINE  print_tree` (two spaces) didn't match `"subroutine " + name` (one space), so the END line counted as a live reference.

**Fix:** Use regex-based definition detection that handles variable whitespace.

### Problem 5: "likely_framework_interface" was a guess

7 procedures had the standard OpenFAST prefix (`AD_`, `BEMT_`) and were PUBLIC, so they were classified as "likely" framework interface. But only scanning the AeroDyn source tree — not the OpenFAST glue code that would actually call them.

**Fix:** Added `--search` flag that scans a broader source tree (e.g., `modules/`) for external callers. For each candidate, grep the broader tree (excluding `--src` to avoid self-matches), verify word-boundary match in non-comment, non-string code. Result is `framework_interface` (confirmed) instead of `likely_framework_interface` (guessed).

**Result of broader search:**

| Procedure | Heuristic said | Broader search found | Actual |
|-----------|---------------|---------------------|--------|
| `AD_JacobianPInput` | likely_framework_interface | Called in `FAST_Funcs.f90:1376` | **framework_interface** |
| `AD_JacobianPContState` | likely_framework_interface | Called in `FAST_Funcs.f90:1486` | **framework_interface** |
| `AD_VarsPackExtInput` | likely_framework_interface | Called in `FAST_Funcs.f90:912` | **framework_interface** |
| `AD_JacobianPDiscState` | likely_framework_interface | No references anywhere | **genuinely_dead** |
| `AD_JacobianPConstrState` | likely_framework_interface | No references anywhere | **genuinely_dead** |
| `AD_CalcConstrStateResidual` | likely_framework_interface | No references anywhere | **genuinely_dead** |
| `BEMT_UpdateDiscState` | likely_framework_interface | No references anywhere | **genuinely_dead** |

The heuristic was right for 3 of 7 and wrong for 4. Without the broader search, we would have incorrectly included 4 dead procedures in translation scope.

## Final classification

All 67 uncalled procedures, 0 unknown, 0 heuristic guesses:

| Category | Count | Evidence basis |
|----------|-------|----------------|
| program_block | 24 | Source scan: `call name` inside `PROGRAM` block |
| genuinely_dead | 14 | No reference in AeroDyn source OR full OpenFAST tree |
| c_binding_entry_point | 10 | Module name contains "c_binding" (structural, not heuristic) |
| commented_out | 9 | Source scan: `!call name` or `!name(` in comments |
| test_function | 5 | FVW_Tests utilities — module name + confirmed no callers |
| framework_interface | 3 | `--search` confirmed: called from `FAST_Funcs.f90` |
| callback | 2 | Source scan: name appears as non-string argument in CALL |

### The 14 genuinely dead procedures (660 lines)

| Procedure | Module | Lines | Why it's dead |
|-----------|--------|-------|---------------|
| `Test_LatticeToSegment` | FVW_Tests | 170 | FVW lattice test, never called from any harness |
| `Compute_dX` | AeroDyn | 79 | Linearization helper, never wired into Jacobian routines |
| `BEMT_End` | BEMT | 64 | Standard interface stub, but glue code never calls it (calls `AD_End` instead) |
| `AD_JacobianPDiscState` | AeroDyn | 55 | Discrete-state Jacobian stub, not used by any linearization path |
| `AD_JacobianPConstrState` | AeroDyn | 55 | Constraint-state Jacobian stub, same |
| `BEMTU_Test_ACT_Relationship` | BEMTUnCoupled | 53 | Orphaned test of actuator-theory relationship |
| `AD_CalcConstrStateResidual` | AeroDyn | 48 | Constraint state residual, not used |
| `Aero_Tests` | AeroAcoustics | 28 | Orphaned acoustic test |
| `find_nan_3D` | FVW_SUBS | 25 | Debug NaN detector, never wired up |
| `find_nan_1D` | FVW_SUBS | 22 | Debug NaN detector, never wired up |
| `getAirfoilOrientationMatrix` | BEMTUnCoupled | 19 | Superseded orientation utility |
| `ComputePhiFromInductions` | BEMT | 18 | Unused alternative phi computation |
| `computeAirfoilAOA` | BEMTUnCoupled | 12 | Unused AoA utility |
| `GetEulerAnglesFromOrientation` | BEMTUnCoupled | 12 | Unused Euler angle utility |

### Translation scope impact

- **14 genuinely dead procedures (660 lines) excluded from scope.** These are confirmed dead across the full OpenFAST tree — not just within AeroDyn.
- **3 framework interface procedures must be translated.** Confirmed called from `FAST_Funcs.f90` — part of the linearization pathway.
- **10 C-binding entry points must be translated.** Called from C/C++ (Exawind).
- **24 program_block procedures: scope TBD.** These are driver and test infrastructure. The AeroDyn Driver specifically is needed during translation for KGen extraction. Whether drivers themselves get translated is a plan-level decision.

## Script changes

1. `--search DIR` flag: scans broader source tree to confirm/reject framework interface candidates
2. Source-first classification: deep analysis runs for ALL entries, not just unknowns
3. String-literal stripping: removes quoted strings before callback and reference checks
4. Word-boundary regex: prevents substring false positives throughout
5. Whitespace-tolerant definition skip: handles `END SUBROUTINE  name` with variable spacing

## Usage

```bash
docker exec vit-dev-openfast python3 /workspace/vit/scripts/fortran_inventory.py \
  --src /workspace/openfast/modules/aerodyn/src \
  --deps /workspace/openfast/modules/nwtc-library/src \
  --search /workspace/openfast/modules \
  --output /workspace/vit/analysis/aerodyn
```

## Related

- Dev note `202604232243-uncalled-procedure-investigation.md` — first round of investigation
- Dev note `202604232137-aerodyn-structural-inventory.md` — initial inventory
- `vit/scripts/fortran_inventory.py` — the inventory script
- `vit/analysis/aerodyn/uncalled_procedures.json` — the final classified list
