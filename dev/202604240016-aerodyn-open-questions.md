# AeroDyn Translation Open Questions
**Date:** 2026-04-24
**Status:** in-progress
**Scope:** Step 4 of CREATING_PROJECT_PLANS.md — open questions for PROJECT_PLAN_AERODYN.md

## Summary

Ten open questions identified from the structural inventory and architectural decisions. Each represents something we don't know that affects the translation plan. These become first-class entries in PROJECT_PLAN_AERODYN.md Section 4, tracked until answered.

Per the methodology, each question has: what we don't know, why it matters (what it blocks), what information would answer it, and when we need the answer.

## Open Questions

### OQ1: Registry generator feasibility

**Question:** How complex is the OpenFAST Registry tool? Can we extend it to emit C++ or do we need a standalone generator?

**Why it matters:** Phase 0 depends on this. If the Registry is a clean code generator with a schema, extending it is straightforward. If it's deeply intertwined with Fortran-specific assumptions, we may need a standalone tool that reads the same `.txt` input files.

**What would answer it:** Read the Registry source code, understand its input format and code generation architecture.

**When needed:** Before Phase 0 starts.

**Status:** unknown

### OQ2: KGen on AeroDyn — has it been tested?

**Question:** Will KGen's instrumentation work on AeroDyn's build chain? KGen worked on ROSCO (single shared library, ctypes harness). AeroDyn is a standalone executable with a cmake build, multiple source files per module, and NWTC Library dependencies.

**Why it matters:** KGen is the foundation of Tier 0 verification. If it can't instrument AeroDyn, we need an alternative extraction approach.

**What would answer it:** Run `vit extract` on one AeroDyn function (e.g., a small leaf in BEMTUncoupled) and see if it succeeds.

**When needed:** Before Phase 1 starts.

**Status:** unknown

### OQ3: NWTC Library interop surface size

**Question:** How many distinct NWTC Library routines does AeroDyn call? The call graph shows 1,634 external edges, but many are to the same routine from different callers. How many unique BIND(C) wrappers do we need?

**Why it matters:** Each external call needs a C interop wrapper. If it's 20 routines, it's a day of work. If it's 200, it's a phase unto itself.

**What would answer it:** Query `call_graph.json` for unique external callees.

**When needed:** Before Phase 1 starts. Can be answered right now from existing data.

**Status:** answered (2026-04-24). 287 unique external callees. Breakdown: ~140 Registry Pack/Unpack/Copy/Destroy boilerplate (eliminated by the C++ Registry generator), ~22 NWTC utility routines (SetErrStat, AllocAry, WrScr, etc.), ~30 mesh-related routines, ~95 other (I/O parsing, VTK, InflowWind/SeaState calls, math utilities, Fortran intrinsics like MOVE_ALLOC and C_F_POINTER). Excluding Registry boilerplate and Fortran intrinsics, the true interop wrapper count is ~150 unique routines. This is significant but manageable — wrappers can be generated incrementally as each phase encounters new external callees.

### OQ4: r-test coverage of the 507 procedures

**Question:** Do the 16 regression test cases exercise all 507 hand-written procedures? Which functions have zero coverage — never called during any test case?

**Why it matters:** Functions with zero test coverage can't be verified at the simulation level (Tier 1). We'd rely solely on KGen kernel verification (Tier 0). We may need additional test cases or scenarios.

**What would answer it:** Instrument the build with call tracing (or use KGen invocation counts) across all 16 test cases. Or static analysis: trace the call graph from the 16 test entry points and see which procedures are reachable.

**When needed:** Before phasing is finalized. Can be partially answered from the call graph now.

**Status:** partially answered (2026-04-24). Static analysis of the 16 r-test cases: 9 exercise the BEMT path (BEMT, UnsteadyAero, AirfoilInfo, DBEMT), 8 exercise the OLAF path (FVW, UnsteadyAero, AirfoilInfo). **AeroAcoustics is NOT exercised** by any r-test case (it has a separate driver executable). **C-binding is NOT exercised** (called from C, not from aerodyn_driver). **Linearization (Jacobian routines) is NOT exercised** (requires full OpenFAST). Full dynamic coverage analysis (which specific procedures are called) requires runtime instrumentation — deferred to Phase 1.

### OQ5: OpenMP in FVW

**Question:** FVW_BiotSavart.f90 uses `OMP_LIB`. How is OpenMP used — simple parallel loops, reductions, or complex thread management? Does the C++ translation need to preserve parallelism?

**Why it matters:** Affects Phase 4 (FVW/OLAF). If OpenMP is used for performance-critical loops, the C++ translation should preserve it (OpenMP works in C++ too). If it's minimal, we can translate without it and add parallelism later.

**What would answer it:** Read FVW_BiotSavart.f90 and check OpenMP pragma usage.

**When needed:** Before Phase 4 starts.

**Status:** answered (2026-04-24). OpenMP usage is simple `!$OMP PARALLEL DO` loops with explicit private variable lists and `schedule(runtime)`. All instances are in `FVW_BiotSavart.f90` — embarrassingly parallel loops over control points and segments for Biot-Savart velocity computation. No critical sections, no reductions, no complex thread management. Direct translation to C++ `#pragma omp parallel for` with the same private/schedule clauses. OpenMP should be preserved in translation — it's performance-critical for the FVW solver.

### OQ6: POINTER fields in Registry types

**Question:** ROSCO only had ALLOCATABLE arrays. AeroDyn's Registry types have POINTER fields. How does the Registry handle POINTERs in Pack/Unpack/Copy? Does the C++ generator need different semantics for pointer vs allocatable?

**Why it matters:** Affects Phase 0 (Registry generator design). Pointer semantics in Fortran (can alias, can be null, can point to subarrays) are more complex than allocatable.

**What would answer it:** Read the Registry-generated `_Types.f90` files and the Registry source to understand how POINTERs are handled.

**When needed:** Before Phase 0 starts.

**Status:** unknown

### OQ7: Mesh types at the interop boundary

**Question:** AeroDyn uses OpenFAST's mesh infrastructure (`MeshType`, `MeshMapType` from NWTC Library). These are complex types with allocated arrays and point-to-point mapping. How do we pass them across the C interop boundary?

**Why it matters:** Meshes are arguments to many AeroDyn functions. If we can't efficiently pass them to C++, every function that takes a mesh argument needs special handling.

**What would answer it:** Read the MeshType definition in NWTC Library and design the view struct / C interop pattern for it.

**When needed:** Before Phase 1 starts (mesh data flows through even leaf-level functions).

**Status:** unknown

### OQ8: VIT enhancements needed for AeroDyn

**Question:** VIT was built for ROSCO (16 source files, flat module structure, DLL build target). AeroDyn has 39 files, a cmake build, module-level organization, and larger types. What VIT enhancements are needed?

**Why it matters:** Undiscovered VIT limitations will block translation phases. Better to identify them up front.

**What would answer it:** Attempt a full extract/verify/integrate cycle on one AeroDyn function. The failures will reveal what VIT needs.

**When needed:** Before Phase 1. Partially overlaps with OQ2 (KGen test).

**Status:** unknown

### OQ9: Upstream sync strategy

**Question:** NREL actively develops OpenFAST. How do we handle upstream changes during a ~12-month translation effort? Merge frequency, conflict resolution policy, rebaselining procedure?

**Why it matters:** If NREL merges a major AeroDyn change mid-project, we need a plan for incorporating it without losing translation work.

**What would answer it:** Decision from the team on merge policy. Check NREL's release cadence and AeroDyn change frequency.

**When needed:** Before significant translation work begins. Can be deferred to a revisit trigger after Phase 1.

**Status:** unknown — decision needed, not investigation

### OQ10: Full OpenFAST build for coupled-mode extraction

**Question:** We decided to use full OpenFAST for coupled-mode-only functions (KD2). But we haven't built full OpenFAST — only `aerodyn_driver`. What additional modules need to build? How long does a full build take? What test cases exercise the linearization path?

**Why it matters:** Blocks extraction of the 3 confirmed framework interface procedures (`AD_JacobianPInput`, `AD_JacobianPContState`, `AD_VarsPackExtInput`) and any other coupled-mode functions discovered during translation.

**What would answer it:** Build full OpenFAST in the container, run a linearization test case.

**When needed:** Before translating framework interface procedures (likely Phase 6 or later).

**Status:** unknown

## Answering priority

Questions that can be answered immediately from existing data or source:
- **OQ3** — query call_graph.json (minutes)
- **OQ5** — read FVW_BiotSavart.f90 (minutes)
- **OQ4** — partial answer from static call graph analysis (hours)

Questions that require investigation before Phase 0:
- **OQ1** — read Registry source (hours to days)
- **OQ6** — read Registry-generated types (hours)

Questions that require investigation before Phase 1:
- **OQ2** — run KGen on one AeroDyn function (hours)
- **OQ7** — read MeshType, design interop pattern (days)
- **OQ8** — attempt full VIT cycle on one function (days, overlaps OQ2)

Questions that can be deferred:
- **OQ9** — upstream sync policy (before significant translation, after Phase 1)
- **OQ10** — full OpenFAST build (before Phase 6)

## Related

- Dev note `202604240012-aerodyn-key-decisions.md` — the 6 key decisions these questions arise from
- Dev note `202604232342-aerodyn-scope-decisions.md` — the 5 scope decisions
- Dev note `202604232137-aerodyn-structural-inventory.md` — inventory data
- `vit/analysis/aerodyn/call_graph.json` — source for answering OQ3, OQ4
- `CREATING_PROJECT_PLANS.md` — methodology (Step 4: Open Questions)
