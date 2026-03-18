# Phase 3: Derived Type Support, OPTIONAL Args, and ratelimit
**Date:** 2026-03-18
**Status:** completed
**Scope:** interface_gen.py, derived_type_parser.py, kernel_verify.py, integrate.py, translate.py, cli.py, test_core.py

## Summary

Phase 3 adds three new VIT capabilities needed for `ratelimit` — the first function with a derived type argument (`TYPE(rlParams)`), an OPTIONAL parameter, and scalar INTENT(INOUT) in a FUNCTION:

1. **Derived type struct passing** — C-compatible derived types are auto-discovered from source, mapped to C struct pointers (`rlparams_t*`), and passed via `TYPE(C_PTR) VALUE` / `C_LOC()` with `TARGET` attribute.
2. **OPTIONAL argument handling** — OPTIONAL args split into flag+value pair (`int has_ResetValue`, `double ResetValue`). Fortran wrapper generates `PRESENT()` preamble with local variables.
3. **Scalar INOUT in functions** — removed `sig.is_subroutine` guard; scalar INTENT(OUT/INOUT) args always become pointers regardless of function vs subroutine.

## Technical Details

### Derived Type Auto-Discovery

New functions in `derived_type_parser.py`:
- `find_and_parse_derived_type(type_name, source_dir)` — globs .f90 files, parses TYPE definition
- `build_derived_type_registry(sig, source_dir)` — scans sig.args for TYPE(...), builds registry
- `generate_c_struct_header(type_info, kind_aliases)` — emits C typedef with include guard

For `rlParams`:
```c
typedef struct {
    double LastSignal[1024];
} rlparams_t;
```

### Pipeline Threading

`derived_types` registry flows through: CLI → `build_derived_type_registry()` → every `generate_*` and `build_*` function → every `get_type_mapping()` call. Without registry, TYPE(...) args default to passthrough (backward compatible).

### OPTIONAL Preamble

```fortran
INTEGER(C_INT) :: has_ResetValue_flag
REAL(C_DOUBLE) :: ResetValue_val
has_ResetValue_flag = 0
ResetValue_val = 0.0D0
IF (PRESENT(ResetValue)) THEN
    has_ResetValue_flag = 1
    ResetValue_val = REAL(ResetValue, C_DOUBLE)
END IF
```

### LOGICAL Handling

Added explicit `LOGICAL(x, C_BOOL)` conversion in wrapper call args. LOGICAL maps to `int` in C and `LOGICAL(C_BOOL)` in Fortran BIND(C) interface. Without explicit conversion, gfortran might silently truncate LOGICAL(4) to LOGICAL(1).

## Workflow Discovery: Extraction Requires Clean Source

KGen captures the current state of source files. If Functions.f90 already has integrated C++ wrappers (e.g., `saturate_c` calls), the kernel includes those unresolved references and fails to build.

**Solution:** Extraction and integration are separate phases:
1. Reset source to upstream (`git checkout e8010f0 -- <files>`)
2. Extract all kernels → golden fixtures persist
3. Re-integrate all functions from clean → commit integrated state

Golden fixtures only need re-extraction when extraction logic changes.

## Verification Results

- 280/280 unit tests pass
- All 7 functions verified 40/40: saturate, wrap_180, wrap_360, ratelimit, ColemanTransform, ColemanTransformInverse, identity
- Full clean build test: extract all 7, verify all 7, integrate all 7, rebuild, empty diff

## Files Changed

### VIT repo
- `vit/derived_type_parser.py` — 3 new functions
- `vit/interface_gen.py` — rewritten: INOUT fix, derived type handling, OPTIONAL, LOGICAL, wrapper refactored into helper functions
- `vit/kernel_verify.py` — thread derived_types, write struct headers to kernel dir
- `vit/integrate.py` — thread derived_types, embed struct typedefs in standalone C++
- `vit/translate.py` — thread derived_types, include struct in scaffold and prompt
- `vit/cli.py` — build registry via `_build_registry()` helper, thread through all commands
- `tests/test_core.py` — 6 new test classes with 20+ new tests
- `PROJECT_PLAN.md` — Phase 3 marked complete
- `CLAUDE.md` — ratelimit added to tables, clean build test updated, extraction prereqs documented

### ROSCO repo
- `translations/Functions/ratelimit.cpp` — C++ translation
- `kernel/ratelimit/` — golden kernel fixture (new)
- `kernel/saturate/`, `kernel/wrap_180/`, `kernel/wrap_360/`, etc. — re-extracted from clean source
