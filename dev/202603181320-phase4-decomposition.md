# Phase 4 Decomposition: From Monolith to Incremental Sub-Phases
**Date:** 2026-03-18
**Status:** completed
**Scope:** PROJECT_PLAN.md restructuring, workflow design

## Background

Phase 4 as originally written was a monolithic phase that tried to solve the entire ROSCO type system in one shot: 13 derived types, 5 different fix patterns, pointer+size view structs, multi-dimensional array transposition, memory layout verification, ownership tracking, and a new `vit analyze-types` command. This was too large and conflated work with different downstream dependencies — ErrorVariables (needed for Phase 5) was bundled with ControlParameters pointer views (not needed until AeroDynTorque).

The decomposition was prompted by completing Phase 3 and reflecting on the path ahead. Two key insights emerged during the reflection:

### Insight 1: Extraction Requires Clean Source

During Phase 3, we discovered that KGen captures the current state of source files. If Functions.f90 already has integrated C++ wrappers (e.g., `saturate_c` calls from Phase 1), the kernel includes those unresolved references and fails to build. This means:

- **Extraction and integration are separate phases.** Extraction needs clean (pre-integration) source. Integration is idempotent and cumulative.
- **Golden kernel fixtures persist.** Once extracted from clean source, they're reused for fast regression indefinitely.
- **The committed ROSCO state is "fully integrated."** We reset to clean only for extraction, then re-integrate everything and commit.

This is now documented in CLAUDE.md with the specific `git checkout e8010f0` command to restore upstream source files.

### Insight 2: Fortran Source Modifications Must Be Automated

The type fixes require changes to both type definitions AND all downstream references. For example, changing `LOGICAL :: restart` to `INTEGER(C_INT) :: restart` requires updating 65 references across 5 files (`IF (restart)` → `IF (restart /= 0)`, `.TRUE.` → `1`, etc.). These changes are:

- **General patterns** — LOGICAL→int, COMPLEX→split, CHARACTER ALLOCATABLE→fixed recur across scientific Fortran codebases
- **Mechanical** — the transformations are deterministic
- **Pervasive** — too many sites for reliable manual editing
- **Error-prone** — missing one `.NOT. restart` → `(restart == 0)` would silently break logic

VIT must automate these via `vit analyze-types --fix <pattern>`. Each fix mode rewrites the type definition and scans all source files for `%fieldname` references, applying the corresponding transformation.

## The Decomposition

### Phase 4A: ErrorVariables + `analyze-types` Foundation
**Delivers:** ErrorVariables as C struct, `vit analyze-types` command skeleton, `--fix character` mode.

ErrorVariables is the simplest fix — one field change (`CHARACTER(:), ALLOCATABLE :: ErrMsg` → `CHARACTER(1024)`). The ALLOCATE call is already commented out in ReadSetParameters.f90. This immediately unblocks Phase 5's first 4 functions (interp1d, sigma, unwrap, interp2d).

### Phase 4B: Already-Compatible Structs + Nested Type Recursion
**Delivers:** C structs for 7 types, recursive `is_c_compatible`, `--fix bind_c` mode.

The key VIT enhancement is recursive compatibility checking. Currently `is_c_compatible` rejects any type with `TYPE(X)` fields, even if X is itself compatible. LocalVariables has 5 nested types (WE, FilterParameters, piParams, resParams, rlParams) — all compatible. Without recursive checking, LocalVariables can never become compatible.

Also handles multi-dimensional fixed arrays (`DIMENSION(3,3)` → `double P[3][3]`). The column-major vs row-major question for WE%P(3,3) needs a convention decision in this phase.

Unblocks Phase 6 Filters.

### Phase 4C: LocalVariables Automated Fixes
**Delivers:** LocalVariables as C struct, `--fix logical`, `--fix complex`, `--fix character_array` modes.

The hardest sub-phase. LOGICAL→INTEGER is fully mechanical (65+ reference updates). COMPLEX→split is harder — AWC_complexangle uses real complex arithmetic (`EXP(complexI * angle)`) and is read/written in binary restart files. The `--fix complex` tool may need to flag complex arithmetic for review rather than blindly rewriting.

Unblocks Phase 7 Controllers and Phase 8 ControllerBlocks (partially).

### Phase 4D: Pointer+Size View Pattern for ALLOCATABLE Types
**Delivers:** ControlParameters and PerformanceData accessible from C++ via view structs.

New pattern: "view struct" — a C struct that mirrors a Fortran type by copying scalars and taking `C_LOC` of ALLOCATABLE arrays. No Fortran type definition changes; the view is a parallel data structure populated by a BIND(C) subroutine.

Unblocks AeroDynTorque (Phase 5) and full ControlParameters access in Phases 7-8.

## Dependency Graph

```
4A (ErrorVariables) ──→ Phase 5 first batch
4B (Simple structs)  ──→ Phase 6 Filters
4C (LocalVariables)  ──→ Phase 7-8 partial
4D (View structs)    ──→ Phase 5 AeroDynTorque, Phase 7-8 full
```

4A and 4B are independent of each other. 4C depends on 4B (nested type recursion). 4D is independent of 4C but naturally comes last.

## Interleaving with Translation Phases

The recommended execution order interleaves type work with translation:

4A → 5(interp1d, sigma, unwrap, interp2d) → 4B → 6(Filters) → 4C → 4D → 5(AeroDynTorque) → 7 → 8

This delivers translated functions sooner rather than solving the entire type system before translating anything.

## `analyze-types` Tool Design

The tool builds incrementally across sub-phases:
- **4A:** Basic per-type compatibility report + `--fix character` (rewrite CHARACTER ALLOCATABLE → fixed)
- **4B:** Recursive nested-type compatibility + multi-dim arrays + `--fix bind_c` (add BIND(C) attribute)
- **4C:** `--fix logical` (LOGICAL → INTEGER + 65+ reference rewrites), `--fix complex` (COMPLEX → split re/im)
- **4D:** View struct generation for ALLOCATABLE types, "bind_c" vs "view" classification

Each `--fix` mode handles both the type definition change AND all downstream `%fieldname` reference updates. The ROSCO analysis from the 2026-03-17 investigation serves as the validation target: the tool's output must match the manual findings.

## Alternatives Considered

**All-at-once (original plan):** Rejected — too large, no incremental value delivery, downstream phases blocked until everything is done.

**Manual edits without tooling:** Rejected — brittle, error-prone for pervasive changes (65 `restart` references), not reproducible for other codebases (Phase 12).

**ErrorVariables-only, defer everything else:** Considered but insufficient — Phase 6 Filters need FilterParameters, which requires the nested type recursion from 4B. Breaking after 4A would leave too many functions blocked.

**Build complete `analyze-types` upfront before any fixes:** Rejected — over-engineering. Better to build incrementally, adding capabilities as needed and immediately using them.
