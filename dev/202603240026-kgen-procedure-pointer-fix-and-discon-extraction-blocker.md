# KGen PROCEDURE POINTER Parser Fix and DISCON.F90 Extraction Blocker
**Date:** 2026-03-24
**Status:** completed (parser fix), blocked (DISCON extraction)
**Scope:** KGen parser, VIT extraction pipeline, Phase 7B extraction campaign

## Background

Phase 7B requires extracting 6 functions called from DISCON.F90: PitchControl, VariableSpeedControl, YawRateControl, FlapControl, CableControl, StructuralControl. The earlier extraction campaign found that KGen crashed when parsing DISCON.F90's dependency chain, specifically on `PROCEDURE(BladedDLL_Legacy_Procedure), POINTER` in ExtControl.f90. The initial plan proposed a temporary workaround (commenting out the PROCEDURE POINTER declaration). The user rejected this as fragile and requested fixing KGen directly.

## KGen PROCEDURE POINTER Parser Fix

### Root Cause

KGen's Fortran parser had an **incomplete implementation** of PROCEDURE declarations. Someone at NCAR started implementing support — the `Procedure` class, the `Procedure_Declaration_Stmt` AST class, and all the `# TEMP` comments — but left it commented out. Three gaps:

1. The `Procedure` statement class was commented out in `typedecl_statements.py`
2. `Procedure` was excluded from `declaration_type_spec` lists and `__all__` exports
3. `Proc_Attr_Spec.match()` in `Fortran2003.py` didn't handle `POINTER` (only INTENT/OPTIONAL/SAVE)

When KGen encountered `PROCEDURE(BladedDLL_Legacy_Procedure), POINTER :: DLL_Legacy_Subroutine` in ExtControl.f90:72, no statement class matched the line. The raw `Line` object was stored in the parse tree. Later, `analyze()` was called on all content — `Line` has no `analyze()` method → crash: `'Line' object has no attribute 'analyze'`.

### Fix: 8 Changes Across 3 Files

**File 1: `KGen/kgen/parser/typedecl_statements.py`** (5 changes)

1. Added `'Procedure'` to `__all__` export list (line 16) — without this, `from typedecl_statements import *` in block_statements.py didn't export the class, causing `NameError: name 'Procedure' is not defined`
2. Uncommented the `Procedure` class definition (lines 633-637) — `TypeDeclarationStatement` subclass with `match = re.compile(r'procedure\s*\(', re.I).match` and `f2003_class = Fortran2003.Procedure_Declaration_Stmt`
3. Added `Procedure` to `declaration_type_spec` list (line 749)
4. Fixed `is_intrinsic()` to exclude `Procedure` (line 513): `not isinstance(self,(Type,Class,Procedure))`
5. Fixed `is_procedure()` to check `Procedure` (line 517): `isinstance(self,Procedure)`
6. Fixed two selector string methods (lines 343, 397) to include `Procedure` in `isinstance` checks

**File 2: `KGen/kgen/parser/block_statements.py`** (1 change)

7. Added `Procedure` to `declaration_type_spec` list (line 1599) — this is the version actually used by `declaration_construct` → `specification_part` → subroutine body parsing (block_statements.py redefines the list after importing from typedecl_statements.py)

**File 3: `KGen/kgen/parser/Fortran2003.py`** AND **`KGen/base/Fortran2003.py`** (1 change each)

8. Added POINTER to `Proc_Attr_Spec.match()` (after the SAVE check): `if len(string)==7 and string.upper()=='POINTER': return 'POINTER', None` — KGen has two copies of Fortran2003.py (base/ and kgen/parser/); both needed the fix

### Debugging the Import Chain

The initial fix attempt failed with `NameError: name 'Procedure' is not defined`. Investigation revealed that `block_statements.py` imports from `typedecl_statements.py` via `from typedecl_statements import *`, which only exports names in `__all__`. The `__all__` list had `Procedure` commented out with a `# TEMP` marker — matching the pattern of the incomplete implementation.

### Verification

After the fix, KGen successfully:
- Parses ExtControl.f90 (including the ABSTRACT INTERFACE block and PROCEDURE POINTER declaration)
- Reads all DISCON.F90 dependencies (Controllers.f90, Functions.f90, ExtControl.f90, ROSCO_Types.f90, etc.)
- Generates a complete kernel for VariableSpeedControl (11 .f90 files, Makefile, kernel_driver.f90)

## DISCON.F90 Extraction: New Blocker

### The Assumed-Size Array Problem

With the parser fix, KGen successfully generates the kernel. But the **state capture build fails** with:

```
Error: Actual argument for 'var' cannot be an assumed-size array at (1)
```

DISCON.F90's signature has `REAL(ReKi), INTENT(INOUT) :: avrSWAP(*)` — an assumed-size array. KGen generates state capture code that passes `avrswap` to a write function (`kw_discon_real__reki_dim1`) with an assumed-shape parameter. Fortran forbids passing assumed-size arrays to assumed-shape parameters.

This is a **separate KGen limitation** from the parser issue. KGen cannot generate valid Fortran for state capture when the enclosing subroutine has assumed-size array arguments.

### Why Previous Extractions Worked

All 19 successful extractions use call sites in Controllers.f90, Filters.f90, or ControllerBlocks.f90. These subroutines receive standard arrays (not assumed-size). Only DISCON.F90 has `avrSWAP(*)` — it's the Bladed DLL interface subroutine, which uses the assumed-size convention required by the OpenFAST DLL calling protocol.

### Implications for Phase 7B

The 6 functions called directly from DISCON.F90 (PitchControl, VariableSpeedControl, YawRateControl, FlapControl, CableControl, StructuralControl) cannot be extracted from DISCON.F90 due to this limitation. They need alternative call sites — either:

1. **Synthetic call sites in Controllers.f90** — add calls inside a function that IS reachable (e.g., inside PitchControl's body), guarded by an always-true condition. The calls must actually execute during the simulation for KGen to capture state.

2. **Wrapper subroutine** — create a wrapper that receives sized arrays (not assumed-size), calls the target function, and is called from somewhere in the execution path.

3. **Patch KGen's state capture code generation** — fix the `kw_*` write functions to handle assumed-size arrays by passing explicit sizes. This is the most robust but requires understanding KGen's code generation internals.

## VIT Extraction Pipeline Bugs Fixed

Three bugs were discovered and fixed during investigation:

### Bug 1: Stale Pragma Contamination

**Root cause:** `vit extract` only cleaned KGen pragmas from the TARGET source file (e.g., DISCON.F90). But KGen reads ALL source files in the project. A stale `!$kgen begin_callsite saturate` pragma left in Controllers.f90 from a previous extraction caused KGen to find TWO pragmas and use the second one ("saturate") as the kernel name. State data was written to `kernel/saturate.0.0.X` instead of `kernel/VariableSpeedControl.0.0.X`.

**Fix:** `cmd_extract()` in cli.py now cleans pragmas from ALL `.f90` and `.F90` files in the source directory before extraction, not just the target file.

### Bug 2: Stale `elapsedtime/` Directory

**Root cause:** KGen stores ETime profiling data in an `elapsedtime/` directory. This was not cleaned between extractions. KGen would reuse stale profiling data, which could embed wrong function names or timing data.

**Fix:** `clean_kgen_artifacts()` in extract.py now removes the `elapsedtime/` directory.

### Bug 3: Stale `.kgen_org` Files

**Root cause:** KGen's state/Makefile creates `.kgen_org` backup files in the source directory. These persist after extraction and can contain pragmas from previous extractions that contaminate subsequent runs.

**Fix:** `run_kgen_extract()` in extract.py now removes all `.kgen_org` files from the source directory before extraction.

## Source File State for Extraction

The investigation also clarified the exact source file state required for DISCON.F90 extractions (if the assumed-size issue is resolved):

| File | Version | Notes |
|------|---------|-------|
| Controllers.f90 | fde0498 (Phase 4C) | Has restart fix, AWC decomposition, FlapControl bug fix. Do NOT apply restart sed (already fixed). |
| ControllerBlocks.f90 | fde0498 (Phase 4C) | Has restart fix. |
| Functions.f90 | e8010f0 (upstream) + sed restart fix | No integration wrappers. |
| Filters.f90 | e8010f0 (upstream) + sed restart fix + Flp_Mode=2 DO loop fix | No integration wrappers. Needs the uninitialized `n` bug fix from commit dba7738. |
| ROSCO_Types.f90 | fde0498 (Phase 4C) | Has INTEGER(C_INT) restart, AWC decomposition. Do NOT reset. |
| ExtControl.f90 | fde0498 (Phase 4C) | No changes needed (parser fix handles it). |
| DISCON.F90 | e8010f0 (upstream) | No integration wrappers. |
| ReadSetParameters.f90, ROSCO_IO.f90 | fde0498 (Phase 4C) | No changes needed. |

Key lesson: Controllers.f90 at fde0498 **already has** `(LocalVar%restart /= 0)` conversions (30 occurrences). Applying the sed restart fix to it double-converts to `((LocalVar%restart /= 0) /= 0)` which is invalid.

## Commits

**KGen repo:**
- PROCEDURE POINTER parser fix (8 changes across 3 files)

**VIT repo:**
- Stale pragma cleanup (clean all source files, not just target)
- Stale elapsedtime/ cleanup
- Stale .kgen_org cleanup
- Dev note

## What Was Gained

1. **KGen can now parse Fortran 2003 PROCEDURE POINTER declarations.** This removes a class of parse failures for any project using procedure pointers — not just ROSCO's ExtControl.f90.

2. **VIT extraction is more robust.** Three stale-artifact bugs that could cause silent extraction failures are now fixed. The pragma contamination bug was particularly insidious — it caused state data to be written to the wrong path with no error message.

3. **The assumed-size array limitation is clearly identified.** The next step is to find alternative call sites or fix KGen's code generation for assumed-size arrays. The parser is no longer the blocker.
