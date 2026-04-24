# Phase 0 Retrospective: Where We Are and What's Ahead
**Date:** 2026-04-24
**Status:** completed
**Scope:** Retrospective on Phase 0, assessment of risks for Phase 1+

## What Phase 0 Revealed

Phase 0 was framed as "speculative" in the plan — the first revisit trigger was "is the Registry generator approach viable?" The answer was strongly positive. The OpenFAST Registry tool was better-designed than anticipated: a clean C++ codebase with modular generators, a complete data model that already tracks every field property we need (POINTER/ALLOCATABLE/deferred dims/nested types/C type mappings), and an existing C generator that established the exact pattern we followed. The implementation took ~230 lines of new code. The investigation was the real work — understanding the parser's `usefrom` semantics, the `recurse_level` implications for transitive dependencies, the POINTER vs ALLOCATABLE distinction in generated code.

The two open questions blocking Phase 0 both resolved cleanly:
- **OQ1:** The Registry is highly extensible. Adding a new output language is a one-file addition.
- **OQ6:** 5 pointer fields out of hundreds, all non-owning, all pointing to external types we treat as opaque anyway. A non-issue.

## Current State

We have C++ struct definitions for all 140 Registry-generated types across AeroDyn's 10 sub-modules. These are the data containers that every physics function operates on. Having them defined in C++ is a prerequisite for everything that follows — without them, there's nothing for translated C++ code to reference.

But the structs are just definitions. They don't yet interoperate with the Fortran runtime. The gap between "C++ structs exist" and "C++ code can receive data from Fortran callers" is the populator/view-struct layer — filling the C++ struct from the live Fortran type at the integration boundary. In ROSCO, VIT's `analyze-types` and `integrate` commands handled this. For AeroDyn, we have a choice: generate populators from the Registry schema (which has complete field information) or let VIT handle it per-function. This decision can wait until Phase 1 forces it.

## Risks for Phase 1 and Beyond

### Phase 1 is the real test

The Registry generator was a known-tractable problem (code generators are well-understood). Phase 1 — translating AirfoilInfo and DBEMT — hits every unknown at once:

1. **OQ2 (KGen on AeroDyn):** KGen worked on ROSCO's simple DLL build. AeroDyn is a cmake-built executable with module dependencies, include paths, and a completely different build chain. If KGen can't instrument it, the entire Tier 0 verification strategy needs rethinking.

2. **OQ7 (MeshType interop):** MeshType is a complex NWTC Library type with ~30 fields including allocatable arrays and pointers. Many AeroDyn functions take mesh arguments. We made it `void*` in the generated headers, but the physics code needs to actually read mesh data (positions, orientations, velocities). The accessor pattern (BIND(C) wrappers for mesh field access) hasn't been designed yet.

3. **OQ8 (VIT on AeroDyn):** VIT was built for ROSCO — 16 source files, flat module structure, DLL target. AeroDyn has 39 files, deeper module nesting, cmake build, and types that are 10x more complex. Unknown VIT limitations will surface.

4. **Scale.** ROSCO was 52 functions over ~7 weeks of development. AeroDyn is 507 functions. Even with VIT improvements and lessons learned, the volume is qualitatively different. The bottom-up phasing helps (each phase builds on verified layers below), but the total effort is substantial.

### The NWTC Library boundary

KD5 (NWTC Library stays Fortran) is the most consequential architectural decision. Every call from translated C++ into NWTC Library needs a BIND(C) wrapper. The inventory found ~150 unique external callees that need wrappers. That's not a blocker, but it's a steady tax on every phase. If we later decide to translate NWTC Library too (a separate project, much larger scope), the wrapper investment becomes throwaway. But doing NWTC Library first would delay AeroDyn indefinitely — it's shared across all of OpenFAST. The decision is correct; the cost is real.

## Sources of Confidence

**The ROSCO experience is directly transferable.** The three-tier verification strategy, the VIT tool, the integration workflow, the BIND(C)/view-struct interop pattern — all proven on a complete project. ROSCO's `integrated` branch is a pure C++ shared library with 5.25M values verified bit-identical. AeroDyn is larger but not fundamentally different.

**The structural inventory was worth every hour.** The 247 leaf functions, the dependency graph, the sub-system boundaries, the dead code classification — all of this was front-loaded so the phasing is data-driven, not guesswork. We know exactly what each phase contains before starting it.

**The bottom-up approach compounds.** Each translated leaf function becomes a verified building block. By Phase 3, the entire BEMT path is translated and verified. The later phases (FVW/OLAF, AeroAcoustics, core AeroDyn) can leverage all the types, wrappers, and patterns established in Phases 1-3.

## Recommendation for Phase 1

Start with the smallest possible end-to-end proof: pick one leaf function from AirfoilInfo (e.g., `AFI_ComputeAirfoilCoefs` or similar — something with simple scalar/array arguments, no mesh types), and run the full VIT pipeline: extract → verify → integrate. This will force every unknown (OQ2, OQ8) to surface on the simplest possible case. If it works, the rest of Phase 1 is execution. If it fails, we learn exactly what VIT needs before committing to 37 functions.

## Related

- Dev note `202604240609` — Phase 0 implementation details
- Dev note `202604240041` — planning methodology completion
- `PROJECT_PLAN_AERODYN.md` — Phase 1 section
