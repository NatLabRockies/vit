# Phase 9: Constants Header + Self-Contained Translations

**Date:** 2026-03-31
**Status:** completed
**Scope:** Create shared constants header, make translations self-contained, remove wrapper include injection

## What Changed

### Shared constants header

Created `ROSCO/translations/rosco_constants.h` consolidating all constants from Fortran's `Constants.f90`:
- Math: `PI`, `D2R`, `R2D`, `RPS2RPM` (constexpr double)
- Control mode enumerations: `VS_Mode_*`, `VS_State_*`, `VS_FBP_*`, `PC_State_*`, `PRC_Comm_*`, `NP_*` (constexpr int)

14 translation files updated to use the header instead of inline `static const` definitions. 3 additional translations (ColemanTransform, ColemanTransformInverse, ResController) also updated — they had local `const double PI` inside function bodies that were removed in favor of the header.

### Self-contained translations

Removed unconditional `#include <algorithm>`, `#include <cmath>`, and `#include "rosco_constants.h"` from VIT's codegen:
- `integrate.py` `_generate_standalone_cpp()`: no longer injects standard library includes
- `interface_gen.py` `generate_cpp_wrapper()`: no longer injects standard library includes
- `translate.py` `generate_translation_scaffold()`: reverted — scaffolds are minimal

Each translation file now includes exactly what it uses. The wrapper adds nothing beyond the `extern "C"` entry point and struct typedefs.

### Discovery mechanism

Rather than making the scaffold "smart" (auto-detecting which includes are needed) or adding configurable resource paths to `vit.yaml`, the constants header is documented in `TRANSLATION_TIPS.md`. The `vit translate` command already includes TRANSLATION_TIPS.md content in the translation prompt, so every translator (human or LLM) learns about `rosco_constants.h` automatically.

Added guidance to `CLAUDE.md` that project-specific resources available to translators should be documented in TRANSLATION_TIPS.md — this keeps the mechanism general without building VIT infrastructure for one header.

## Design Decisions

**Why not inject includes from the wrapper?** It creates hidden dependencies. A translation file that compiles only because the wrapper provides `<cmath>` looks self-contained but isn't. When read in isolation, it's unclear where `std::cos` comes from. Translations should compile on their own — include what you use.

**Why not make the scaffold add includes automatically?** An LLM translator knows it needs `<cmath>` for `std::cos`. That's basic C++ knowledge. Suggesting standard library includes is hand-holding that adds VIT complexity for zero value. Project-specific resources (like `rosco_constants.h`) are non-obvious, so those are documented in TRANSLATION_TIPS.md where the translator will see them.

**Why TRANSLATION_TIPS.md instead of vit.yaml config?** There's currently one project-specific header. Adding a config schema, parser, and scaffold integration for one item is premature. TRANSLATION_TIPS.md is already included in the translation prompt automatically. When VIT targets a second codebase (Phase 12), a config-driven approach becomes worthwhile.

## The sed incident

During the audit, `sed -i '' '/#include <cmath>/d'` removed `<cmath>` from files that use bare `cos`/`sin` (without `std::` prefix). The audit searched for `std::cos` but missed bare `cos`. Then `sed -i '' '/.../i\...'` on macOS concatenated the inserted line with the next line (`#include <cmath>#include "rosco_constants.h"`). Both issues were caught by the build and fixed.

Lesson: when auditing includes, search for both `std::cos` and bare `cos`. And test sed commands on macOS — the `-i` flag behavior differs from GNU sed.

## Files modified

| File | Change |
|------|--------|
| `ROSCO/translations/rosco_constants.h` | New — shared constants header |
| 17 translation `.cpp` files | Added/updated includes, removed inline constants |
| `vit/vit/integrate.py` | Removed unconditional `<algorithm>`, `<cmath>`, `rosco_constants.h`; added constants header copy to src/ |
| `vit/vit/interface_gen.py` | Removed unconditional `<algorithm>`, `<cmath>` |
| `vit/vit/translate.py` | Reverted scaffold includes — minimal scaffolds |
| `vit/vit/kernel_verify.py` | Added constants header copy to kernel dir |
| `ROSCO/TRANSLATION_TIPS.md` | Documented rosco_constants.h |
| `vit/CLAUDE.md` | Added guidance on documenting project resources in TRANSLATION_TIPS.md |

## Test Results

- Unit tests: 566/566 passed
- Verification: 39/39 passed
- Integration: 39/39 passed
- 9-scenario comparison: ALL IDENTICAL — 672,000 values
