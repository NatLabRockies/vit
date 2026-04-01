# Phase 9: From Inline Struct Bloat to Consolidated `vit_types.h`

**Date:** 2026-04-01
**Status:** completed
**Scope:** Identify and fix struct typedef duplication across VIT codegen, evolve through three iterations to the correct design

## The Problem

After completing Phase 8 (all 39 ROSCO functions verified and integrated), the user asked: "What are those giant structs in the beginning of, for instance, aerodyntorque.cpp and cablecontrol.cpp?"

The answer revealed a design flaw. VIT had three codegen paths that handled struct typedefs differently:

| Path | Module | Behavior |
|------|--------|----------|
| Kernel verification | `kernel_verify.py` | Separate `.h` files per type (correct) |
| Integration | `integrate.py` | Inlined full struct definitions into each `.cpp` |
| Translation scaffold | `translate.py` | Inlined full struct definitions into each scaffold |

This produced **384 duplicate struct definitions** across 39 files. Integrated files were ~60% struct boilerplate — `aerodyntorque.cpp` was 1,369 lines, but only ~70 lines were actual translation code. The 39 translation files totaled 16,009 lines, most of it duplicated struct content. There were 11 unique struct types (`localvariables_t` with 168+ fields, `controlparameters_view_t` with ~160 fields, `filterparameters_t` with 57 arrays, plus 8 smaller types), each duplicated across every file that used it.

The root cause: `_generate_standalone_cpp()` and `generate_translation_scaffold()` each independently called `generate_c_struct_header()` / `generate_view_struct_header()` and appended the result inline. This worked when we had 3 simple functions in Phase 1. By Phase 8, with 39 functions and 11 types, it had become untenable — but it happened gradually enough that no single commit made it obvious.

## Iteration 1: Extract to Separate Files

The user's first question was whether VIT should have done this differently from the start. The answer was yes — the kernel path already proved the separate-header pattern worked. The fix needed to be in VIT's codegen, not just a ROSCO cleanup, so the problem couldn't recur.

### What we built

Added `generate_type_headers()` to `derived_type_parser.py` — a shared function returning `list[tuple[str, str]]` (filename, content) pairs in dependency order. All three codegen paths were refactored to use it:

- `integrate.py`: `_generate_standalone_cpp()` got a `struct_headers` parameter, emitting `#include` directives instead of inline structs. `generate_integration_plan()` created `IntegrationPatch` entries to copy each header to `src/`.
- `translate.py`: `generate_translation_scaffold()` replaced inline embedding with `struct_headers`.
- `kernel_verify.py`: Refactored to use the shared function instead of its own manual loop.
- `cli.py`: `cmd_translate()` wrote headers to `translations/types/` before scaffold generation.

This produced 11 separate `.h` files in `translations/types/` (`piparams_t.h`, `localvariables_t.h`, etc.), each with its own `#ifndef` guard and `emit_includes=True` for nested type dependencies.

### Results

Translation files: 16,009 → 3,399 lines (79% reduction). `aerodyntorque.cpp`: 1,369 → 93 lines (93%). All 39/39 verified, 672,000 values identical.

### What was wrong

The `*local*` pattern in ROSCO's `.gitignore` blocked `localvariables_t.h` from being committed — required `git add -f`. More fundamentally, 11 separate files was wrong granularity: 8 of them were under 20 lines, and no translation ever needed one without needing several. The user asked: "Does it make sense to have all these .h files — or should it just be consolidated?"

## Iteration 2: Consolidate into One File

The user pushed with three design questions that exposed the remaining issues:

1. **How will a new user know when this file should be created?** They shouldn't need to. VIT should create and manage it automatically.

2. **Does `rosco_types.h` conflict with `ROSCO_Types.f90`?** Yes — the name implies a 1:1 translation that doesn't exist. These are VIT-generated C interop structs, not a translation of the Fortran type module. In Phase 11 (pure C++), the real C++ types would use classes, `std::vector`, constructors — a completely different design.

3. **Should it be incrementally built?** Yes — created on first `vit translate` that encounters derived types, updated as new types are discovered. This is exactly the pattern `vit_translated.h` already uses for `extern "C"` declarations.

### What we built

Added `generate_consolidated_types_header()` to `derived_type_parser.py`. Takes a registry, kind aliases, and optional existing file content. Returns complete file content.

- **Create from scratch:** Full file with `#ifndef VIT_TYPES_H` guard, `#include <stdint.h>`, all types in dependency order, closing `#endif`
- **Append to existing:** Detects present types via inner `#ifndef TYPENAME_T_H` guards, generates only new types, inserts before closing `#endif // VIT_TYPES_H`
- **Idempotent:** Same types twice → same content
- Uses `emit_includes=False` for BIND(C) types (all types are in the same file; dependency order handles forward references)

The lifecycle:
```
vit translate LPFilter → creates translations/vit_types.h with filterparameters_t
vit translate PitchControl → appends localvariables_t, controlparameters_view_t, etc.
vit integrate PitchControl --apply → copies vit_types.h to src/
```

The user never creates or edits the file. Every scaffold gets `#include "vit_types.h"`.

## Iteration 3: Clean Up the Kernel Hack

The initial consolidation generated BOTH per-type files AND `vit_types.h` in the kernel directory — the per-type files for the kernel's own wrapper, the consolidated file for the user's translation. The user correctly identified this as a hack.

The fix: kernel_verify.py generates only `vit_types.h`. The kernel wrapper uses it too (passed via `struct_headers=["vit_types.h"]`). The callee bridge section uses `generate_consolidated_types_header()` to incrementally add callee types to the same consolidated file.

Removed `generate_type_headers()` entirely — it was now dead code (only referenced by its own tests). Clean deletion: the function, its 3 tests, and the import in `kernel_verify.py`.

## Final State

### Architecture

One function generates type headers: `generate_consolidated_types_header()`. Three callers:

| Caller | When | Where it writes |
|--------|------|-----------------|
| `cmd_translate()` | User scaffolds a translation | `translations/vit_types.h` (source of truth) |
| `generate_integration_plan()` | User integrates a function | Copies to `src/vit_types.h` via IntegrationPatch |
| `kernel_verify.py` | User verifies a translation | `kernel/{func}/vit_types.h` (ephemeral) |

`generate_translation_prompt()` still shows struct content inline — the prompt is documentation for the translator, not compiled code.

### What this prevents

There is no codepath in VIT that inlines struct definitions or generates per-type files (except for documentation in the translation prompt). A 40th function automatically gets `#include "vit_types.h"`. A second project using VIT gets the same behavior — the file is named generically (`vit_types.h`, not `rosco_types.h`) and managed entirely by VIT.

### Line counts

| Location | Phase 8 (inline) | Iteration 1 (11 files) | Final (vit_types.h) |
|----------|-------------------|------------------------|---------------------|
| Translation files (39) | 16,009 lines | 3,399 lines | 3,399 lines |
| Includes per translation | 0 (inline) | 1–11 per file | 1 per file |
| Type header files | 0 | 11 files (700 lines) | 1 file (714 lines) |
| `aerodyntorque.cpp` (integrated) | 1,369 lines | 93 lines | 93 lines |

### Test results

- Unit tests: 573/573 passed
- Verification: 39/39 passed (3 parallel batches, all clean)
- Integration: 39/39 passed
- 9-scenario comparison: ALL IDENTICAL — 672,000 values

## What Drove the Iterations

This was a case where the first fix solved the stated problem (remove inline bloat) but the user kept asking deeper questions:

1. "Should we have done it this way from the start?" → Yes, fix the codegen so it can't recur
2. "Does it make sense to have all these .h files?" → No, consolidate
3. "How will a new user know when to create this?" → They won't — VIT creates it automatically
4. "This kernel change seems like a hack" → It was — fix it properly

Each question revealed that the solution was still carrying assumptions from the inline era. The final design — one auto-generated, incrementally-built file managed entirely by VIT — is the simplest correct answer. It took three iterations to get there because each iteration exposed the next assumption.

## Files Modified (Final State)

| File | Repo | Change |
|------|------|--------|
| `vit/derived_type_parser.py` | VIT | Added `generate_consolidated_types_header()`, removed `generate_type_headers()` |
| `vit/integrate.py` | VIT | `_generate_standalone_cpp()`: struct_headers param. `generate_integration_plan()`: copy vit_types.h |
| `vit/translate.py` | VIT | `generate_translation_scaffold()`: struct_headers param |
| `vit/cli.py` | VIT | `cmd_translate()`: write/update vit_types.h |
| `vit/kernel_verify.py` | VIT | Generate only vit_types.h (no per-type files), callee types appended incrementally |
| `tests/test_core.py` | VIT | 3 tests for consolidated header (create, append, idempotent) |
| `tests/test_integrate.py` | VIT | Updated struct_headers test |
| `tests/test_translate.py` | VIT | Updated struct_headers test |
| `PROJECT_PLAN.md` | VIT | Updated Phase 9 description |
| 33 `translations/**/*.cpp` | ROSCO | All use `#include "vit_types.h"` |
| `translations/vit_types.h` (new) | ROSCO | Consolidated types header (714 lines, 11 types) |
| `translations/types/` (removed) | ROSCO | 11 separate files deleted |
| `TRANSLATION_TIPS.md` | ROSCO | Updated documentation |

## Related Dev Notes

This note supersedes:
- `202604010555-shared-type-headers.md` — Iteration 1 (separate files)
- `202604010958-consolidated-vit-types-header.md` — Iteration 2 (consolidation)
