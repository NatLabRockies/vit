# Full ROSCO Assessment and Project Plan

**Date:** 2026-03-17
**Status:** completed
**Scope:** project-wide assessment, roadmap

## Background

A colleague expressed interest in fully converting ROSCO to C++ and requested a meeting to see VIT in action. This triggered a comprehensive assessment of the entire ROSCO codebase against VIT's current capabilities, culminating in `PROJECT_PLAN.md` — a phased roadmap from today's 4 verified functions through complete ROSCO translation and beyond.

## Assessment Process

Three parallel exploration efforts were conducted:

1. **Functions.f90 analysis**: All 14 functions read and classified by signature complexity — argument types, array patterns, derived type usage, and algorithmic characteristics.
2. **Full ROSCO codebase map**: All 11 modules (~8,600 lines of Fortran, ~65 functions/subroutines), derived type definitions in ROSCO_Types.f90 (12 types, 544 lines), the dependency graph between modules, and the CMake build structure.
3. **VIT capability audit**: All 10 VIT modules read to identify exactly what Fortran patterns are supported, what would silently generate wrong code, and what would fail outright.

## Key Findings

### Functions.f90 Readiness

- **4 done**: saturate, wrap_180, wrap_360, interp1d
- **2 ready now** (no VIT changes): sigma, CPfunction
- **5 minor work** (fixed-size arrays, subroutines, array returns): matinv3, identity, ColemanTransform, ColemanTransformInverse, unwrap
- **3 major work** (2D arrays, load-bearing derived types): ratelimit, interp2d, AeroDynTorque

### ROSCO Beyond Functions.f90

- **Filters.f90** (7 functions): All stateful, using FilterParameters with instance-based indexing (1024-slot arrays). BIND(C)-compatible state types — tractable once VIT handles struct mapping.
- **Controllers.f90** (14 functions): Heavy derived type usage (LocalVariables, ControlParameters). The most complex translation targets in ROSCO.
- **ControllerBlocks.f90** (8 functions): Includes WindSpeedEstimator (Kalman filter with 3×3 matrix ops) and multi-stage state machines.
- **I/O modules** (~4,700 lines total): ReadSetParameters, ROSCO_Helpers, ROSCO_IO. Better translated directly than through VIT — I/O logic doesn't benefit from runtime state verification.
- **Platform/entry**: SysSubs, ExtControl, ZeroMQInterface, DISCON. Small, translate last.

### Derived Types Are the Keystone

Every module depends on ROSCO_Types. The 12 types split into:
- **Tier 1 (BIND(C) compatible)**: FilterParameters, rlParams, piParams, resParams, WE, ObjectInstances, DebugVariables — all fixed-size arrays, directly mappable to C structs.
- **Tier 2 (ALLOCATABLE)**: ControlParameters (~115 fields), PerformanceData — need std::vector or pointer+size.
- **Tier 3 (nested/strings)**: ErrorVariables, LocalVariables (~170 fields with nested types), ExtDLL_Type.

The two-phase strategy: (A) passthrough and field extraction during incremental VIT translation, (B) full C++ struct rewrite when translating modules wholesale.

### VIT Technical Debt

Silent breakage risks identified:
- 2D assumed-shape arrays generate wrong interface (single n_ param for DIMENSION(:,:))
- ALLOCATABLE args parsed as regular arrays
- CHARACTER results assume numeric kind conversion
- INTENT(OUT) arrays may get wrong intent in subroutine interface
- Derived type registry not wired into CLI
- Subroutine codepath untested end-to-end

## Deliverable

`PROJECT_PLAN.md` created in vit repo root with 7 sections:
1. Vision and Current State
2. Functions.f90 Function-by-Function Assessment
3. Full ROSCO Module Map (every module, every function)
4. Derived Type Translation Strategy
5. VIT Enhancement Roadmap (11 phases)
6. VIT Technical Debt
7. Meeting Preparation

## What This Enables

The roadmap provides a clear narrative for the meeting: VIT works today on simple functions, has a concrete path to all of Functions.f90, and a phased plan through the entire ROSCO codebase to a pure C++ shared library. Each phase is scoped, each function is classified, and the blocking issues are identified.
