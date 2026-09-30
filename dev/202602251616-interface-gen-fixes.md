# Interface Generator Fixes: Case Preservation and KGen Pragma Format
**Date:** 2026-02-25
**Status:** completed
**Scope:** fortran_parser.py, interface_gen.py, extract.py, cli.py

## Summary

Two quick-succession fixes (197759f and dc048ee, roughly 45 minutes apart) addressed issues discovered during the first real attempt to run VIT against ROSCO. These are classic "works in unit tests, breaks in production" bugs — the initial implementation made assumptions about Fortran type representation and KGen pragma format that didn't hold against the real codebase.

## Background

After the initial commit landed the full architecture, the next step was to actually run VIT against ROSCO's `saturate` function. This immediately surfaced two problems:

1. Kind-aliased types like `DbKi` were being mangled to `DBKI`, breaking the generated interface code.
2. KGen pragmas were missing the function name, causing KGen to reject them.

Both bugs lived at the boundary between VIT's internal representation and external tools (the Fortran compiler and KGen), which is why unit tests with synthetic snippets didn't catch them.

## Fix 1: Preserve Type Case in Fortran Parser (197759f)

### The Problem

VIT's Fortran parser uppercases the entire source line before parsing to handle Fortran's case insensitivity. This is correct for keywords (`FUNCTION`, `INTENT`, `REAL`) but destructive for kind aliases. ROSCO defines custom kinds:

```fortran
REAL(DbKi) :: saturate
REAL(DbKi), INTENT(IN) :: inputValue, minValue, maxValue
```

When the parser uppercased the line, `DbKi` became `DBKI`. This propagated through to the generated interface code, which would emit `REAL(DBKI)` — a kind that doesn't exist because Fortran kind aliases are case-sensitive at the module level (the `USE` statement imports `DbKi`, not `DBKI`).

### The Fix

The parser was changed to extract type strings from the **original source line** using the character positions identified during parsing of the uppercased copy, rather than extracting from the uppercased copy itself. Specifically:

- `result_name` extraction: use original line positions
- `result_type` extraction: use original line positions
- `_parse_type_declaration`: match on uppercased copy for keyword detection, but extract the type string (including kind parameter) from the corresponding position in the original line

This preserves the user's original casing for kind parameters while still allowing case-insensitive keyword matching.

### Why This Matters

Fortran is case-insensitive for keywords but module-defined kind constants retain their declared casing in the symbol table. When VIT generates interface code with `REAL(DBKI)`, the Fortran compiler can't resolve the kind because the module exported `DbKi`. The generated code must use the exact same casing as the original source.

## Fix 2: Add Function Name to KGen Pragmas (dc048ee)

### The Problem

VIT was inserting KGen extraction pragmas like:

```fortran
!$kgen begin_callsite
```

But KGen's parser expects the function name in the pragma:

```fortran
!$kgen begin_callsite saturate
```

Without the function name, KGen silently ignores the pragma or generates incomplete instrumentation.

### The Fix

Added a `function_name` parameter to `insert_kgen_pragma()` in `extract.py` and updated the CLI layer to pass the function name through from the user's command-line arguments. The pragma format became:

```fortran
!$kgen begin_callsite <function_name>
```

### Why This Was Missed

The initial implementation was based on KGen documentation that didn't clearly specify the function name requirement in the pragma. The actual requirement was discovered by examining KGen's source code and successful pragma examples after the first extraction attempt failed.

## Broader Lesson

These two fixes illustrate a pattern that recurred throughout VIT's development: the gap between documented behavior and actual behavior of external tools (KGen, gfortran) can only be closed by running against real code. The initial architecture was sound, but the interface details required empirical debugging. This is why the development notes from 2026-02-25 show such rapid iteration — each fix was discovered by attempting a real extraction/verification run and observing what broke.
