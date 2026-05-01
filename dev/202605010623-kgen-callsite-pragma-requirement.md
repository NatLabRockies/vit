# KGen Pragma Must Be at CALL SITE, Not Function Definition
**Date:** 2026-05-01
**Status:** completed
**Scope:** KGen extraction infrastructure, DBEMT_ReInit diagnosis

## Discovery

KGen extraction for DBEMT_ReInit failed repeatedly with `INTENT(IN/OUT)` declarations at MODULE scope. Investigation initially attributed this to a KGen bug. The user challenged this assumption, prompting a deeper trace through KGen's source code.

## Root Cause

**VIT placed the `!$kgen begin_callsite` pragma at the subroutine DEFINITION (module level). KGen requires it at a CALL SITE inside another subroutine.**

KGen's `ancestors()` method (`base_classes.py:722`) walks up the AST via `self.parent`. For a module-level subroutine declaration, `parent` is the MODULE. So `ancestors()` returns `[MODULE]`, and `Config.parentblock['stmt']` becomes the MODULE (`kganalyze.py:163`).

KGen's `create_parentblock_parts` (`gen_kernel_callsite_file.py:49-80`) then appends verification parameters (`kgen_unit`, `kgen_measure`, etc.) with `INTENT(IN/OUT)` attributes to the parentblock's `DECL_PART`. When the parentblock is a MODULE, these declarations land at module scope — invalid Fortran (`INTENT` only valid inside a subroutine).

## Evidence: Every Successful Extraction Used a Subroutine-Level Parentblock

| Function | Parentblock (from kernel) | How it got a subroutine parentblock |
|----------|--------------------------|-------------------------------------|
| DBEMT_AB4 | `dbemt_abm4` | ABM4 calls AB4 within DBEMT.f90 |
| DBEMT_ABM4 | `dbemt_updatestates` | UpdateStates calls ABM4 within DBEMT.f90 |
| DBEMT_CalcContStateDeriv | `dbemt_ab4` | AB4 calls CalcContStateDeriv within DBEMT.f90 |
| DBEMT_InitStates | `dbemt_updatestates` | UpdateStates calls InitStates within DBEMT.f90 |
| DBEMT_RK4 | `dbemt_ab4` | AB4 calls RK4 within DBEMT.f90 |
| DBEMT_UpdateStates | `bemt_updatestates` | BEMT_UpdateStates calls DBEMT_UpdateStates (BEMT.f90) |
| **DBEMT_ReInit** | **MODULE dbemt** | **No subroutine caller in the pragma's file** |

These all appeared to work "at the definition" but actually succeeded because KGen found an in-file subroutine caller as the parentblock. ReInit's callers are in other files (BEMT_ReInit in BEMT.f90, DBEMT_Init at module level in DBEMT.f90).

## Fix

Extract from the CALL SITE instead of the definition:

```bash
# WRONG — pragma at function definition (module scope):
vit extract DBEMT_ReInit -f modules/aerodyn/src/DBEMT.f90 -l 236

# CORRECT — pragma at call site (inside BEMT_ReInit subroutine):
vit extract DBEMT_ReInit -f modules/aerodyn/src/BEMT.f90 -l 778
```

Result: 4/4 IDENTICAL. KGen successfully captures state data and the translation passes verification.

## Why Only 4 Cases (Not 20)

The `invocation: 0:0:1-20` config requests up to 20 captures, but `ad_BAR_CombinedCases` only calls DBEMT_ReInit 4 times through the BEMT_ReInit call site (once per combined case for cases 2-4, plus once during case 1 init). The function has no floating-point math — it zeros arrays, sets scalars, and copies structs — so 4 cases covering different DBEMT_Mod values (1, 2, 3) provide full branch coverage.

## Documentation Updated

- **CLAUDE.md** Step 5 in Extraction Workflow: now explicitly states to use a CALL SITE, not the definition, with example
- **Dev note 202605010526**: updated root cause from "KGen instrumentation bug" to "pragma at wrong scope"

## Lesson

What looked like a KGen code generation bug was actually a VIT usage error: placing the pragma where KGen doesn't support it. The successful extractions masked this because they happened to have in-file subroutine callers. The fix is a documentation/workflow change, not a code change.
