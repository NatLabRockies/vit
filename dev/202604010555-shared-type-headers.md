# Phase 9: Shared Type Headers — Fix VIT Codegen

**Date:** 2026-04-01
**Status:** completed
**Scope:** Extract embedded struct typedefs into shared headers, fix VIT codegen to prevent recurrence

## Problem

VIT had three codegen paths that handled struct typedefs differently:

| Path | Module | Behavior |
|------|--------|----------|
| Kernel verification | `kernel_verify.py` | Separate `.h` files per type (correct) |
| Integration | `integrate.py` | Inlined full struct definitions into each `.cpp` (wrong) |
| Translation scaffold | `translate.py` | Inlined full struct definitions into each scaffold (wrong) |

This produced 384 duplicate struct definitions across 39 files. Integrated files were ~60% struct boilerplate (e.g., `aerodyntorque.cpp`: 1,369 lines, only ~70 of which were actual translation code). Translation files totaled 16,009 lines, most of it duplicated struct content.

The root cause: `_generate_standalone_cpp()` and `generate_translation_scaffold()` each independently called `generate_c_struct_header()` / `generate_view_struct_header()` and appended the result inline. The kernel path already proved the separate-header pattern worked — the other two paths just never followed it.

## What Changed

### New shared function: `generate_type_headers()`

Added to `derived_type_parser.py`. Takes a derived type registry and kind aliases, returns `list[tuple[str, str]]` — (filename, content) pairs in dependency order. Each header is self-contained (`emit_includes=True`) with `#ifndef` guards. View types get `_view_t.h` suffix, BIND(C) types get `_t.h`.

This function consolidates the "which types, in what order, with what content" logic that was previously duplicated across three modules.

Fixed the compatibility filter to use `is_c_compatible_recursive()` instead of `is_c_compatible()` — the non-recursive version incorrectly rejects types with nested `TYPE(X)` fields even when those nested types are themselves C-compatible.

### `integrate.py` changes

`generate_integration_plan()` now calls `generate_type_headers()` and creates `IntegrationPatch` entries to copy each header to `src/` (same idempotent pattern as `rosco_constants.h`). Headers are skipped if they already exist.

`_generate_standalone_cpp()` accepts a new `struct_headers` parameter — list of filenames to `#include`. The inline struct embedding code was removed entirely.

### `translate.py` changes

`generate_translation_scaffold()` replaced the `derived_types` inline embedding with `struct_headers` parameter. Emits `#include` directives. The `derived_types` parameter is kept for `build_c_params()` (parameter type resolution).

`generate_translation_prompt()` is unchanged — the prompt still shows inline struct content so translators can see field names.

### `cli.py` changes

`cmd_translate()` calls `generate_type_headers()` and writes headers to `translations/types/` before generating the scaffold.

### `kernel_verify.py` changes

Refactored to use `generate_type_headers()` instead of its own manual loop. View populator generation stays in kernel_verify (kernel-specific). The callee bridge section also uses `generate_type_headers()` instead of direct `generate_c_struct_header()` calls.

### ROSCO migration (one-time)

- Created `translations/types/` with 11 shared type headers
- Replaced inline struct blocks in 32 translation files with `#include` directives (7 files had no structs)
- Added `#include` directives to `foreaftdamping.cpp` (it used types but never had inline definitions — relied on wrapper injection)

### What this prevents

After this change, there is no codepath in VIT that inlines struct definitions. Every codegen path uses `#include`:
- `_generate_standalone_cpp()` requires `struct_headers`
- `generate_translation_scaffold()` requires `struct_headers`
- `kernel_verify.py` uses `generate_type_headers()` to write separate files

A 40th function would automatically get `#include` directives. The headers are generated once per type (idempotent), not once per function.

## Results

### Line count reduction

| Location | Before | After | Reduction |
|----------|--------|-------|-----------|
| Translation files (39) | 16,009 lines | 3,399 lines | 79% |
| Integrated files (39) | 29,928 lines | ~6,000 lines | ~80% |
| `aerodyntorque.cpp` (example) | 1,369 lines | 93 lines | 93% |

### Test Results

- Unit tests: 573/573 passed (7 new tests added)
- Verification: 39/39 passed (3 parallel batches)
- Integration: 39/39 passed
- 9-scenario comparison: ALL IDENTICAL — 672,000 values

## Design Decisions

**Why fix the codegen, not just clean up ROSCO?** The user identified that the duplication was a VIT design flaw, not a ROSCO problem. Cleaning up ROSCO without fixing VIT would allow the duplication to recur with any new function or project. The fix is structural: VIT's codegen no longer has an inline struct path.

**Why `is_c_compatible_recursive()` in the filter?** The non-recursive `is_c_compatible()` returns `False` for types with nested `TYPE(X)` fields (e.g., `LocalVariables` containing `TYPE(WE)`). In ROSCO, this doesn't matter because all types are `BIND(C)` (which short-circuits the check). But for correctness with any Fortran codebase, the recursive check is needed. Discovered via a new unit test that uses synthetic non-BIND(C) types.

**Why keep `generate_translation_prompt()` unchanged?** The prompt is documentation for the translator, not compiled code. Showing struct field names inline in the prompt is valuable — the translator needs to see what fields exist without opening separate files.

## Files Modified

| File | Repo | Change |
|------|------|--------|
| `vit/derived_type_parser.py` | VIT | Added `generate_type_headers()`, fixed filter to use `is_c_compatible_recursive()` |
| `vit/integrate.py` | VIT | `_generate_standalone_cpp()`: struct_headers param. `generate_integration_plan()`: type header patches |
| `vit/translate.py` | VIT | `generate_translation_scaffold()`: struct_headers param |
| `vit/cli.py` | VIT | `cmd_translate()`: generate headers in translations/types/ |
| `vit/kernel_verify.py` | VIT | Refactored to use `generate_type_headers()` |
| `tests/test_core.py` | VIT | 3 new tests for `generate_type_headers()` |
| `tests/test_integrate.py` | VIT | 2 new tests for struct_headers in standalone cpp |
| `tests/test_translate.py` | VIT | 2 new tests for struct_headers in scaffold |
| 33 `translations/**/*.cpp` | ROSCO | Replaced inline structs with `#include` |
| `translations/types/*.h` (11 new) | ROSCO | Shared type headers |
| `TRANSLATION_TIPS.md` | ROSCO | Documented translations/types/ |
| `PROJECT_PLAN.md` | VIT | Marked Phase 9 item complete |
