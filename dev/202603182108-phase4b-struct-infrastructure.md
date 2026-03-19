# Phase 4B: C Struct Infrastructure for Simple Types
**Date:** 2026-03-18
**Status:** completed
**Scope:** derived_type_parser.py, analyze_types.py, kernel_verify.py, integrate.py, translate.py, test_derived_types.py

## Summary

Phase 4B builds the derived type infrastructure needed for Phase 4C (LocalVariables) and Phase 6 (Filters). All 7 target types (WE, FilterParameters, ObjectInstances, ControlParameters, PerformanceData, PitManVar, DebugVariables) were confirmed structurally C-compatible by VIT's auto-discovery — no ROSCO source changes were needed.

## What Was Done

### 1. Fixed 2D Array C Struct Syntax

**Problem:** `generate_c_struct_header()` produced `double P[3,3];` for WE's `DIMENSION(3,3)` fields — invalid C syntax.

**Fix:** Added `_fortran_dims_to_c()` helper that converts comma-separated Fortran dimensions to bracket syntax:
- `"1024"` → `[1024]`
- `"3,3"` → `[3][3]`
- `"3,1"` → `[3][1]`

When multi-dimensional arrays are present, a column-major note is emitted in the header comment explaining that Fortran `P(i,j)` maps to C `P[j-1][i-1]`.

### 2. Recursive C Compatibility Checking

**Problem:** `is_c_compatible` rejected ALL nested TYPE fields, even when the nested type was itself C-compatible. This caused false positives for types like LocalVariables that contain WE, FilterParameters, etc.

**Fix:** Added `is_c_compatible_recursive(registry)` method on `DerivedTypeInfo`. For TYPE(X) fields, looks up X in the registry and recursively checks compatibility. Unknown types are conservatively treated as incompatible. The existing `is_c_compatible` property is unchanged for backward compatibility.

### 3. Registry-Aware classify_field_blockers()

**Problem:** `classify_field_blockers()` flagged every nested TYPE as a blocker, even compatible ones.

**Fix:** Added optional `registry` parameter. When provided, nested TYPE(X) fields are only reported as blockers if X is NOT C-compatible in the registry. Without registry, behavior is unchanged (backward compat).

Updated `analyze_all_types()` to build a registry from `scan_all_derived_types()` and pass it through, making `vit analyze-types` smarter about nested types.

### 4. Nested Struct in C Headers

Extended `generate_c_struct_header()` with:
- `registry` parameter for looking up nested type names
- `emit_includes` parameter: `True` emits `#include "nested_t.h"` (for kernel_verify separate-file mode), `False` skips includes (for integrate/translate inline mode)
- TYPE(X) fields rendered as `x_t field_name;`

### 5. Header Dependency Ordering

Added `order_types_by_dependency()` — topological sort using Kahn's algorithm. Leaf types (no nested TYPE fields) come first, types with dependencies come last. Detects cycles and raises ValueError.

### 6. Registry Threaded Through All Callers

- `kernel_verify.py`: `generate_c_struct_header()` now receives `registry=derived_types`
- `integrate.py`: Uses `order_types_by_dependency()` + `emit_includes=False` for inline mode
- `translate.py`: Same ordering + `emit_includes=False` in both scaffold and prompt generation

## Design Decisions

- **BIND(C) deferred to Phase 4C.** All 7 types are homogeneous (same base type per struct), so padding matches without BIND(C). Proven by ErrorVariables working in Phase 5A. `--fix bind_c` will be added when LocalVariables (mixed types) actually needs it.
- **DIMENSION(3,1) kept as `[3][1]`** — exact layout match, documents the 3×1 column vector semantics. No flattening.
- **Memory layout test deferred** — not needed for homogeneous types.
- **No ROSCO source changes** — this phase is purely VIT tooling infrastructure.

## Test Results

- **399 tests pass** (364 existing + 35 new Phase 4B tests)
- **6 golden fixture verifications: 40/40 each** (saturate, wrap_180, wrap_360, ratelimit, sigma, interp1d)

### New Test Classes

- `TestMultiDimArrays` (8 tests): 2D → `[3][3]`, `[3][1]`, 1D regression, column-major comment
- `TestRecursiveCCompatible` (7 tests): simple/compatible/incompatible/unknown nested types, registry-aware blockers
- `TestNestedStructHeader` (4 tests): nested field rendering, include/no-include modes
- `TestDependencyOrdering` (5 tests): parent-child ordering, stable for independent types, cycle detection
- `TestAllSevenTypes` (11 tests): parse + verify struct headers for all 7 target types

## Files Changed

| File | Changes |
|------|---------|
| `vit/derived_type_parser.py` | `_fortran_dims_to_c()`, `is_c_compatible_recursive()`, `generate_c_struct_header()` extended, `classify_field_blockers()` registry param, `order_types_by_dependency()` |
| `vit/analyze_types.py` | Build registry, pass to `classify_field_blockers()` |
| `vit/kernel_verify.py` | Thread `registry=derived_types` to header generation |
| `vit/integrate.py` | Dependency ordering + `emit_includes=False` |
| `vit/translate.py` | Dependency ordering + `emit_includes=False` (2 locations) |
| `vit/tests/test_derived_types.py` | 35 new tests across 5 test classes |
| `vit/PROJECT_PLAN.md` | Phase 4B items checked off |

## What's Next

Phase 4C (LocalVariables) can now focus purely on fixing incompatible fields (LOGICAL, COMPLEX, CHARACTER array) rather than infrastructure. The recursive compatibility checking, nested struct headers, and dependency ordering are all ready.
