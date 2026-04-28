# AirfoilInfo Post-Completion Audit and Cleanup
**Date:** 2026-04-27
**Status:** completed
**Scope:** AirfoilInfo module code quality and documentation accuracy

## Background

With AirfoilInfo complete (20/20 functions translated, integrated, and verified), a rigorous audit was conducted to ensure nothing was left in disagreement with development and design principles before moving to DBEMT. The audit checked: build integrity, baseline regression, constant consolidation, include hygiene, vit.yaml accuracy, CMakeLists completeness, vit_translated.h completeness, and documentation alignment across CLAUDE.md, PROJECT_PLAN_AERODYN.md, and TRANSLATION_TIPS.md.

## Findings and Fixes

### Issue 1: PROJECT_PLAN Section 7 stale status text

Section 7's first sentence said "17 AirfoilInfo functions integrated (3 debug-output Wr* functions deferred)" — written before the Wr* functions were translated. This contradicted the second sentence which correctly said "AirfoilInfo is COMPLETE." Fixed by rewriting to: "AirfoilInfo sub-module COMPLETE (all 20 functions in C++)."

### Issue 2: Duplicate `#include "vit_types.h"` in 16 files

12 `.cpp` files in `modules/aerodyn/src/` had `vit_types.h` included twice: once at line 5 (VIT scaffold boilerplate artifact from `vit integrate`) and again at line ~19-22 (the actual implementation includes). 4 of those files were identical to their `translations/AirfoilInfo/` copies, so the translations also had the duplicate (total: 16 files affected).

Removed the first occurrence (scaffold artifact) from all 16 files. The second occurrence (alongside the real implementation includes) is the one that matters.

**Pattern:** This only affected files that went through `vit integrate`, which generates a wrapper file that prepends a VIT header block including `#include "vit_types.h"` before the `// --- User-provided C++ implementation ---` marker. When the user's implementation also includes `vit_types.h` (as it should, for type definitions), the result is a duplicate. Header guards prevent any functional issue, but it's unnecessary clutter.

### Issue 3: Case count clutter removed from vit.yaml

Some translation entries had `cases_passed`, `cases_total`, `cases_skipped`, and `kernel_dir` fields; others didn't. The inconsistency was flagged in the audit. Rather than adding missing counts to the incomplete entries, we removed all case counts from all 9 entries that had them.

**Rationale:** Once a function is `status: integrated`, the case counts are purely historical metadata. The actual verification evidence lives in the kernel directories (golden fixtures) and dev notes. Keeping counts in vit.yaml adds clutter with no operational value — `vit status` shows the status field, which is what matters. Each entry is now uniform: `status` + `cpp_file` + optional `verification` and `note`.

### Issue 4: Two integration conventions documented

9 early translations (Calculate_Cn through ComputeUASeparationFunction_onCl) went through `vit integrate`, producing the expected divergence: `translations/` holds the raw scaffold (PascalCase function only), `src/` holds the VIT-generated wrapper with a separate `extern "C"` `_c` shim.

10 later translations (AFI_ComputeAirfoilCoefs onward) were hand-integrated, so `translations/` and `src/` are identical, with the `_c` entry point defined directly in the implementation.

**Root cause:** The later functions required Fortran wrapper patterns that `vit integrate` cannot auto-generate:
- **Two-pass architecture** (AFI_Init, ReadAFfile): pass1 computes sizes, Fortran allocates, pass2 fills. `vit integrate` only generates single-call wrappers.
- **Fortran I/O interop** (AFI_WrHeader/WrData/WrTables): stashed filename SAVE variable, parallel Fortran APPEND unit for caller's `CLOSE`.

Hand-integration was necessary, not a shortcut. No code changes made — both patterns work correctly. Added as Item 4 in Section 9 (Deferred Technical Items) with the recommendation to use the later style (direct `_c` entry point, hand-integration) for DBEMT onward.

## Verification

- Build: clean compilation, all 20 C++ files recompiled
- Baselines: 17/17 PASS
- Duplicate includes: zero remaining (verified by grep across all src/ and translations/ .cpp files)
- Case counts: zero remaining in vit.yaml (verified by grep)

## Items confirmed clean (no issues found)

- CMakeLists.txt: all 20 .cpp files present
- vit_translated.h: all 21 extern "C" entry points declared
- Constants: zero duplicate ErrID_*/setErrMsg/UA_* definitions (consolidation from previous session held)
- CLAUDE.md: all claims accurate and current
- TRANSLATION_TIPS.md: accurate and current
- Dev notes: comprehensive coverage of all 20 functions
