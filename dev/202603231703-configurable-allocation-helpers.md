# Configurable Allocation Helpers and Pre-Computed Field Mappings
**Date:** 2026-03-23
**Status:** completed
**Scope:** Phase 4D — ALLOCATABLE scanner generalization and code quality

## The Problem

The ALLOCATABLE size scanner had a ROSCO-specific subroutine name (`ParseAry`) hardcoded in VIT's `analyze_types.py`. ParseAry is a ROSCO utility that reads array data from configuration files and allocates internally. Other Fortran projects would have their own allocation helpers (or just use `ALLOCATE` directly), making the hardcoded regex useless or misleading.

## What Was Done

### 1. Configurable allocation helpers (`vit.yaml`)

Allocation helpers are now defined in `vit.yaml`:

```yaml
allocatable:
  helpers:
    - name: ParseAry
      array_arg: 3
      size_arg: 4
```

Each helper specifies:
- `name` — the subroutine name to match in `CALL` statements
- `array_arg` — which positional argument (1-indexed) is the array being allocated
- `size_arg` — which positional argument is the size expression

The scanner generates regexes dynamically from these definitions. For the common case where `size_arg == array_arg + 1` (size is the next argument after the array), it uses a simple "match field, capture next arg" regex. For non-adjacent positions, it falls back to full positional argument parsing using the parenthesis-aware splitter.

The `ALLOCATE` scanner remains as the universal fallback — it's standard Fortran and works on any codebase without configuration.

A project with no allocation helpers simply omits the `allocatable:` section and relies entirely on `ALLOCATE` patterns.

### 2. Pre-computed field mappings (eliminated parameter threading)

The initial implementation threaded `allocatable_helpers` as a parameter through 10+ function signatures across 6 files — every function in the chain from CLI to code generation gained an irrelevant parameter just to carry a config value to the scanner.

This was identified during review as sloppy design. The fix: pre-compute field mappings once in `_build_registry()` (where config is available) and store them on `DerivedTypeInfo.field_mappings`. All downstream code reads the cached results directly.

**Before (parameter threading):**
```
cli.py → cmd_integrate(config)
  → generate_integration_plan(allocatable_helpers=config.allocatable_helpers)
    → _generate_standalone_cpp(allocatable_helpers=allocatable_helpers)
      → scan_allocatable_sizes(allocatable_helpers=allocatable_helpers)
```

**After (pre-computed):**
```
cli.py → _build_registry(config)
  → scan_allocatable_sizes(allocatable_helpers=config.allocatable_helpers)
  → info.field_mappings = results  # cached on registry

cli.py → cmd_integrate()
  → _generate_standalone_cpp()
    → type_info.field_mappings  # already computed
```

Net effect:
- `allocatable_helpers` removed from 5 function signatures and 7 CLI call sites
- 5 inline `scan_allocatable_sizes()` calls eliminated from code generation paths
- Scan runs once per registry build, not repeatedly
- 35 net lines removed (20 added, 55 deleted)
- `allocatable_helpers` confined to three files: `config.py` (defines it), `analyze_types.py` (scanner uses it), `cli.py` (connects them in `_build_registry` and `_cmd_scan_allocatable`)

## Design Decisions

**Config-driven over hardcoded.** Any project-specific allocation wrapper should be configurable, not embedded in VIT core. The `ALLOCATE` scanner is the general-purpose mechanism; helpers are project-specific extensions.

**Pre-compute over thread.** Analysis results belong on the data structure they describe, not as parameters passed through every function in the call chain. `DerivedTypeInfo.field_mappings` is the natural home — it's computed once when the registry is built and consumed wherever the type info is used.

**Positional argument parsing.** The helper config uses 1-indexed argument positions rather than trying to match by argument name or type. This is simple, unambiguous, and works regardless of argument naming conventions. For the common case (size immediately follows array), a fast regex is used. For non-adjacent arguments, the parenthesis-aware splitter handles complex expressions.

## Files Modified

| File | Change |
|------|--------|
| `vit/vit/config.py` | Added `allocatable_helpers` to VitConfig, `_parse_allocatable_helpers()`, save support |
| `vit/vit/analyze_types.py` | Replaced hardcoded ParseAry regex with configurable `_scan_helper_call()` |
| `vit/vit/derived_type_parser.py` | Added `field_mappings` to DerivedTypeInfo |
| `vit/vit/cli.py` | Pre-compute mappings in `_build_registry()`, removed threading from all call sites |
| `vit/vit/integrate.py` | Use `type_info.field_mappings`, removed `allocatable_helpers` parameter |
| `vit/vit/kernel_verify.py` | Use `type_info.field_mappings`, removed `allocatable_helpers` parameter |
| `vit/vit/translate.py` | Use `type_info.field_mappings`, removed `allocatable_helpers` parameter |
| `ROSCO/vit.yaml` | Added `allocatable.helpers` section with ParseAry definition |

## Verification

- All 486 tests pass
- Scanner results unchanged: ControlParameters 56/68 mapped, PerformanceData 5/5
- Verification regression: PIController 40/40, saturate 40/40
