# Auto-Detect C++ Implemented Callees — Eliminate vit.yaml Helper Entries
**Date:** 2026-04-26
**Status:** completed
**Scope:** VIT callee bridge generation — source-driven detection replacing manual registration

## Problem

When translating AFI_ComputeAirfoilCoefs (a multi-function translation with internal helpers and NWTC utility calls), VIT's callee bridge generator needed to know which callees have C++ implementations (no bridge needed) vs. which are Fortran-only (bridge required). The initial solution was to add 5 entries to `vit.yaml`'s translations registry:

```yaml
AFI_ComputeAirfoilCoefs1D:    # internal helper — same .cpp file
AFI_ComputeAirfoilCoefs2D:    # internal helper — same .cpp file
AFI_Output_ExtrapInterp1:     # inline in the translation
CubicSplineInterpM:           # NWTC utility in vit_nwtc.cpp
MPi2Pi:                       # NWTC utility in vit_nwtc.h (inline)
```

This was sloppy: the translations registry should track actual translations, not serve as a mechanism for controlling bridge generation. Every future multi-function translation would need the same pattern — manual registration of internal helpers.

## Solution

Replace manual registration with automatic detection: scan the C++ source files for function definitions.

**In `_generate_callee_bridges()` (kernel_verify.py):**

1. Read the C++ translation file (e.g., `afi_computeairfoilcoefs.cpp`)
2. Read all shared header/source files (from `shared_files` config — e.g., `vit_nwtc.h`, `vit_nwtc.cpp`)
3. Extract function names using regex: `(?:static\s+)?(?:inline\s+)?(?:void|int|double|...) (\w+)\s*\(`
4. Build a `cpp_implemented` set (case-insensitive)
5. For each detected callee: if in `cpp_implemented`, skip bridge generation

**Helper callee detection also improved:** When a callee is in `cpp_implemented` (an internal C++ helper), its Fortran body is scanned for ITS callees — those callees might need Fortran bridges. This replaces the previous approach that relied on `vit.yaml` to identify same-file helpers.

## What Changed

**`vit/vit/kernel_verify.py`:** Replaced the `vit.yaml`-driven skip logic (same-file check + shared-file check via translations registry) with source-driven detection. The function `_scan_cpp_for_functions()` extracts function names from C/C++ source. The `cpp_implemented` set is built once and used for both bridge skipping and helper callee scanning.

**`openfast/vit.yaml`:** Removed all 5 helper entries. The translations registry now only contains actual top-level translations with verification results.

## Verification

- **AFI_ComputeAirfoilCoefs kernel (clean source):** 20/20 IDENTICAL — only the FindBoundingTables bridge was generated (correct: it's the one Fortran callee that needs a bridge)
- **FindBoundingTables kernel:** 21/21 IDENTICAL (regression check)
- **Production baselines:** 16/16 PASS (auto-detection only affects kernel verification, not production integration)

## Design Rationale

The C++ source is the single source of truth for what's implemented in C++. A function defined in the translation file or a shared header has a C++ implementation — period. No manual tracking needed. This works automatically for:

- Internal static helpers in the same .cpp file
- Inline functions in shared headers (e.g., `MPi2Pi` in `vit_nwtc.h`)
- Compiled utility functions in shared .cpp files (e.g., `CubicSplineInterpM` in `vit_nwtc.cpp`)

The only functions that get Fortran bridges are those NOT found in any C++ source — i.e., functions that only exist in Fortran and need a C-callable wrapper.

## Limitation

Callee detection requires clean (pre-integration) Fortran source. Integrated source has wrapper bodies with no CALL statements, so `detect_callees` returns empty. This is a pre-existing requirement (documented in CLAUDE.md) — not introduced by this change. When re-verifying after integration, the kernel fixture must already exist from a prior clean-source verification run.

## Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `e484b3f` | Auto-detect C++ implemented callees from source |
| OpenFAST | `9be6581bd` | Remove helper function entries from vit.yaml |
