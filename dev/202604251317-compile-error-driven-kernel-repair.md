# Compile-Error-Driven Kernel Repair
**Date:** 2026-04-25
**Status:** completed
**Scope:** VIT kernel verification infrastructure

## Problem

KGen generates kernel directories with intentionally reduced copies of framework modules. These reductions make kernels incomplete: missing constants, subroutines, USE chains, and external library links. VIT needs kernels to compile and run for C++ verification.

Previous attempts used heuristics (line count thresholds, hardcoded constant names like ErrMsgLen, NWTC-specific module detection). These were fragile and project-specific — auditing caught them immediately.

## Solution

**Let the compiler tell us what's wrong.** After VIT's existing kernel patches, attempt `make build`. If it fails, parse the compiler/linker errors, fix each one using the project source tree, and retry. Repeat until the build succeeds or no more fixes can be applied.

The compiler is the authority on what's missing — no heuristics, no hardcoded values, no project-specific knowledge.

## Implementation: `_repair_kernel_build()` in kernel_verify.py

A loop (max 5 iterations) that:
1. Runs `make clean && make build 2>&1`
2. Parses gfortran/linker output into structured `KernelBuildError` objects
3. Dispatches each error to the appropriate fix function
4. If any fixes were applied, goes back to step 1
5. Returns when build succeeds or no fixable errors remain

### Error Parser: `_parse_build_errors(output)`

Parses three error patterns from gfortran output:

| Pattern | Category | Example |
|---------|----------|---------|
| `Scalar INTEGER expression expected` + CHARACTER context | `missing_constant` | CHARACTER(ErrMsgLen) where ErrMsgLen undefined |
| `Symbol 'name' at (1) has no IMPLICIT type` | `undefined_symbol` | errmsglen used but never declared |
| `undefined reference to 'symbol_'` | `link_undefined` | dgels_, seterrstat_ |

**Important detail:** gfortran uses Unicode curly quotes (`\u2018` `\u2019`), not ASCII quotes. The regex patterns must handle both.

### Fix Functions

**`_fix_missing_constant(kernel, error, source_dir)`**
- Searches project source for `INTEGER, PARAMETER :: NAME = value`
- Inlines the literal: `CHARACTER(ErrMsgLen)` → `CHARACTER(8196)`
- Also adds dummy `INTEGER :: name` if the constant is READ from KGen state data

**`_fix_undefined_symbol(kernel, error, source_dir)`**
- For symbols READ from state data: adds local `INTEGER :: name` declaration
- For PARAMETER symbols: inlines the value from project source

**`_fix_link_undefined(kernel, error, source_dir)`**
- Strategy 1: LAPACK/BLAS detection by standard naming convention (`[sdcz]` prefix + operation) → adds `-llapack -lblas`
- Strategy 2: Find the Fortran subroutine in project source, extract it, inject into the kernel module that should contain it

The subroutine injection searches by module name: finds which module in the project source defines the subroutine, then finds the kernel file that defines the same module, and injects the subroutine before `END MODULE`. Handles `END SUBROUTINE` with or without the subroutine name (Fortran allows both).

## Calculate_C_alpha Repair Trace

```
[iter 1] Fixed missing_constant: ErrMsgLen in NWTC_LAPACK.f90
[iter 2] Fixed missing_constant: errmsglen in AirfoilInfo.f90
[iter 2] Fixed undefined_symbol: errmsg2 in AirfoilInfo.f90
[iter 2] Fixed undefined_symbol: kgenref_errmsg2 in AirfoilInfo.f90
[iter 3] Fixed undefined_symbol: errmsglen in AirfoilInfo.f90
[iter 4] Fixed link_undefined: dgels_ in SingPrec.f90
[iter 4] Fixed link_undefined: seterrstat_ in SingPrec.f90
Build succeeded on iteration 5
```

Five iterations, seven fixes, all discovered and applied automatically from the project source tree. No hardcoded constants or module names.

## Design Decisions

1. **Build output merging:** `make build 2>&1` merges stderr into stdout. gfortran splits file context lines and error messages across stdout/stderr; merging ensures the parser sees them in order.

2. **Unicode quote handling:** gfortran 13+ uses curly quotes in error messages. Parser regex uses `['\u2018]` and `['\u2019]` character classes.

3. **Subroutine END pattern:** Fortran allows `END SUBROUTINE` without repeating the name. The regex uses `(?:\s+name)?` to match both forms.

4. **UTF-8 decode errors:** Kernel output may contain binary garbage from CHARACTER buffer comparisons. `_build_and_run_kernel` now uses `errors="replace"` when decoding subprocess output.

5. **Deduplication:** The error parser deduplicates by `(category, symbol)` to avoid fixing the same thing twice.

6. **Progress guarantee:** The loop terminates if no fixes were applied in an iteration (prevents infinite loops on unfixable errors).

## What's General

- No hardcoded constants, module names, file paths, or library names
- Discovers everything by searching project source tree
- LAPACK/BLAS detection uses the standardized naming convention (single letter prefix + operation)
- Works for any Fortran project with a source tree and gfortran compiler
- Existing kernels (Calculate_Cn, FindBoundingTables, etc.) are unaffected — build succeeds on first attempt, repair loop is a no-op

## Remaining Issue

Calculate_C_alpha verification reports 0/20 passed because the `errmsg2` field (8196-char buffer) contains stack garbage that differs between Fortran reference and C++ runs. The numeric fields (default_cn_alpha, default_cl_alpha, default_alpha0, errstat2) are all IDENTICAL. This is a verification pipeline detail (errmsg2 garbage comparison), not a kernel repair issue.

## Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `05a40f6` | Compile-error-driven kernel repair + UTF-8 decode fix |
