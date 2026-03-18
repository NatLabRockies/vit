# Eliminate Passthrough, Reorder Phases for Clean-Slate Approach
**Date:** 2026-03-18
**Status:** completed
**Scope:** PROJECT_PLAN.md revision, CLAUDE.md updates

## Background

During Phase 0 implementation, a question arose about the passthrough mechanism — the pattern where non-C-compatible derived type args (like `TYPE(ErrorVariables)`) are excluded from the C++ interface. The Fortran wrapper accepts them but doesn't forward them to C++. This was used for the original `interp1d` translation and was the planned approach for `sigma`, `unwrap`, and other ErrVar functions.

Review of the full PROJECT_PLAN.md revealed that passthrough is a brittle special case that contradicts the project's trajectory. The plan calls for ALL types to become BIND(C) structs — ErrorVariables included. Continuing to rely on passthrough for new translations would create technical debt that has to be unwound later.

## Decision

1. **Treat ErrorVariables like every other type.** Make it a BIND(C) struct passed by pointer. The fix is small: replace `CHARACTER(:), ALLOCATABLE :: ErrMsg` with `CHARACTER(1024)`. The other 3 fields (`aviFAIL`, `ErrStat`, `size_avcMSG`) are already C-compatible.

2. **Delay ALL functions with ErrVar** until the type is properly handled in Phase 4 (type system). No passthrough, no decomposed args, no special cases.

3. **Start from a clean ROSCO.** Nothing pre-integrated. All functions — including interp1d — proceed through phases in order. interp1d is not special-cased as "already done with passthrough."

## Functions Affected

Five functions moved to later phases:
- `interp1d` — was "Already Done" with passthrough → now Phase 5 (after ErrorVariables BIND(C) fix)
- `sigma` — was Phase 1 → now Phase 5
- `unwrap` — was Phase 2 → now Phase 5
- `interp2d` — was Phase 3 → now Phase 5
- `AeroDynTorque` — was Phase 4.5 → now Phase 5

Nine functions unaffected (no ErrVar):
- `saturate`, `wrap_180`, `wrap_360` — Phase 1
- `CPfunction` — Phase 1
- `matinv3`, `identity`, `ColemanTransform`, `ColemanTransformInverse` — Phase 2
- `ratelimit` — Phase 3 (blocked by rlParams, not ErrVar)

## Phase Renumbering

The old Phase 3 (2D assumed-shape arrays) existed solely for `interp2d`, which has ErrVar. With interp2d delayed, that phase had no translation target. It was deleted and its VIT enhancement work merged into the new Phase 5. All subsequent phases renumbered.

| Old Phase | New Phase | Content |
|-----------|-----------|---------|
| Phase 0 | Phase 0 ✓ | Translation infrastructure (complete) |
| Phase 1 | Phase 1 | Low-hanging fruit (thinner: no sigma) |
| Phase 2 | Phase 2 | Fixed-size arrays + subroutines (no unwrap) |
| ~~Phase 3~~ | *(deleted)* | 2D assumed-shape — no target without ErrVar |
| Phase 4 | Phase 3 | Load-bearing derived types (ratelimit + rlParams) |
| Phase 4.5 | Phase 4 | Complete type system C structs (incl. ErrorVariables) |
| *(new)* | Phase 5 | 2D arrays + all ErrVar functions → 14/14 Functions.f90 |
| Phase 5 | Phase 6 | Filters |
| Phase 6 | Phase 7 | Controllers |
| Phase 7 | Phase 8 | ControllerBlocks |
| Phase 8 | Phase 9 | Type Ownership Transfer |
| Phase 9 | Phase 10 | I/O and Platform |
| Phase 10 | Phase 11 | Entry Point |
| Phase 11 | Phase 12 | Generalization |

## ErrorVariables Investigation

Exploration of Functions.f90 confirmed the exact usage pattern for each ErrVar function:

- **interp1d, interp2d**: Validate inputs, write `ErrVar%aviFAIL = -1` and `ErrVar%ErrMsg` on error, check status to prepend routine name.
- **AeroDynTorque**: Passes ErrVar to interp2d, checks status. Never writes directly.
- **sigma, unwrap**: Don't use ErrVar for any internal logic. Only check `ErrVar%aviFAIL` status for error message context prepending.

The ErrorVariables type itself has 4 fields:
- `size_avcMSG` — `INTEGER(IntKi)` — already C-compatible
- `aviFAIL` — `INTEGER(C_INT)` — already C-compatible
- `ErrStat` — `INTEGER(C_INT)` — already C-compatible
- `ErrMsg` — `CHARACTER(:), ALLOCATABLE` — sole blocker

After replacing the ALLOCATABLE ErrMsg with `CHARACTER(1024)`, ErrorVariables becomes a plain BIND(C) struct. C++ receives `ErrorVariables*` and accesses fields directly, same as FilterParameters, piParams, and every other type.

## Changes Made

### PROJECT_PLAN.md
- **Section 1**: Updated current state — 3 verified (not 4), none integrated, clean slate
- **Section 2**: Moved interp1d from "Already Done" to "Major VIT Work Required." Moved sigma and unwrap to "Major VIT Work Required." All marked as "Blocked by ErrorVariables (Phase 4)."
- **Section 4**: Replaced ErrorVariables "passthrough + decomposed args" strategy with "BIND(C) struct passed by pointer, same as all other types"
- **Section 4a**: Updated `vit translate` description to match actual implementation — implementation-only files (not extern "C" wrappers), translation prompt to stdout, TRANSLATION_TIPS.md inclusion. Updated metadata list to match actual 7-field output.
- **Section 5**: Deleted old Phase 3, renumbered all phases (0–12), added new Phase 5 for ErrVar functions + 2D arrays, corrected all phase targets (4/14 → 8/14 → 9/14 → 14/14)
- **Section 6**: Updated all tech debt phase references to new numbering
- **Section 7**: Updated coverage counts and roadmap progression

### CLAUDE.md
- Updated C++ Translations table — interp1d status changed to "Blocked by ErrorVariables (Phase 5)"
- Added "Integration starts from a clean ROSCO" note
- Rewrote Known Limitations — passthrough is legacy, all types become BIND(C) structs
- Updated Clean Build Test to only include ErrVar-free functions

## Rationale

The passthrough mechanism (`build_c_params()` excluding non-C-compatible derived types) was the right call for the initial proof-of-concept. It let us verify interp1d without solving the full type system. But continuing to rely on it creates two problems:

1. **Every passthrough function needs re-integration later.** When ErrorVariables becomes a BIND(C) struct, the C++ signature changes (it gains an `ErrorVariables*` parameter). All prior translations using passthrough must be updated and re-verified.

2. **The passthrough path is undertested.** It works for "function doesn't touch ErrVar at all" but the plan shows functions like interp1d that *write* to ErrVar fields. Passthrough can't handle that — the C++ code has no access to the struct. This means passthrough was always a temporary bridge, not a real solution.

By delaying ErrVar functions until the type is properly handled, we avoid building on a bridge we know we'll tear down. The early phases (1–3) proceed with ErrVar-free functions, building confidence in the pipeline. Phase 4 solves the type system. Phase 5 translates all five ErrVar functions with the real, permanent infrastructure.
