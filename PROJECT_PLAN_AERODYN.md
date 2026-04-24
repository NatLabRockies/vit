# PROJECT PLAN: AeroDyn Translation

## 1. Vision and Done Criteria

**Goal:** Translate OpenFAST's AeroDyn module from Fortran to C++ using VIT, producing a pure C++ AeroDyn module with no Fortran source files in the build and no libgfortran dependency.

**Target codebase:** AeroDyn — the aerodynamics module of OpenFAST (NREL). 42 Fortran source files, ~75,000 lines, 507 hand-written procedures + 740 Registry-generated procedures across 5 sub-systems (BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, core AeroDyn). Approximately 10× ROSCO's scope.

**Done criteria:**
- [ ] All AeroDyn Fortran source files removed from the build
- [ ] No libgfortran dependency (confirmed via `ldd` or equivalent)
- [ ] 16 regression test cases pass bit-identical against committed baselines
- [ ] NREL regression 16/16 pass at rtol=2, atol=1.9
- [ ] AeroDyn Driver executable functional for all 16 test cases
- [ ] C-binding layer functional (Exawind interface preserved)
- [ ] C++ Registry generator produces all `_Types` equivalents

**Explicit out-of-scope:**
- NWTC Library translation (shared framework, separate project)
- InflowWind, SeaState, LinDyn translation (separate modules, read-only data providers)
- OpenFAST glue code changes (AeroDyn is a leaf module from the glue code's perspective)
- Performance optimization beyond parity (optimization is a follow-on, not a gate)

**Target verification rigor:** Three-tier, same as ROSCO:
- Tier 0: KGen float64 kernel verification per function
- Tier 1: 16-case simulation comparison, bit-identical against committed baselines
- Tier 2: NREL regression at 1e-2 tolerance (correctness gate, 16/16)

**Scope decisions:** dev note `202604232342`

## 2. Structural Inventory

Completed 2026-04-23. Data files at `vit/analysis/aerodyn/`.

| Metric | Value |
|--------|-------|
| Total files | 39 |
| Total lines | 75,293 |
| Hand-written lines | 46,803 (62%) |
| Registry-generated lines | 28,490 (38%) |
| Modules | 35 |
| Hand-written top-level procedures | 507 |
| Internal procedures (CONTAINS blocks) | 190 |
| Registry-generated procedures | 740 |
| Leaf functions (no internal callees) | 247 |
| Derived types (AeroDyn) | 150 |
| Derived types (NWTC Library deps) | 26 |
| Call graph edges | 3,451 (1,817 internal, 1,634 external) |
| Genuinely dead procedures | 14 (660 lines, excluded from scope) |
| Confirmed framework interface | 3 (called from OpenFAST glue code) |
| C-binding entry points | 10 |
| Procedures with SAVE state | 1 |

**Sub-system breakdown:**

| Sub-system | Procedures | Lines | Leaves | Phase |
|-----------|-----------|-------|--------|-------|
| AirfoilInfo | 23 | 2,387 | 12 | 1 |
| DBEMT | 14 | 1,028 | 5 | 1 |
| BEMTUncoupled | 29 | 1,653 | 16 | 2 |
| UnsteadyAero | 44 | 5,037 | 17 | 2 |
| BEMT | 37 | 2,881 | 16 | 3 |
| FVW/OLAF | 137 | 10,738 | 59 | 4 |
| AeroAcoustics | 51 | 4,424 | 21 | 5 |
| Core AeroDyn | 85 | 13,549 | 36 | 6 |
| Inflow/C-binding/Drivers | 66 | 7,468 | 18 | 7 |
| Standalone (no module) | 21 | 810 | 2 | 7 |

**Inventory data files:**
- `analysis/aerodyn/function_inventory.json` — 1,437 procedures with full metadata
- `analysis/aerodyn/call_graph.json` — 3,451 caller→callee edges
- `analysis/aerodyn/type_inventory.json` — 176 derived type definitions
- `analysis/aerodyn/module_dependencies.json` — 35 modules, topologically sorted
- `analysis/aerodyn/uncalled_procedures.json` — 67 uncalled, fully classified
- `analysis/aerodyn/framework_boundary.json` — 192 external API procedures
- `analysis/aerodyn/loc_metrics.json` — LOC breakdown
- `analysis/aerodyn/inventory_summary.md` — human-readable digest

**Key observations that shaped this plan:**
1. Nearly half (247/507) of hand-written procedures are leaf functions — translation can start at the edges
2. Registry-generated code is 38% of lines — the C++ Registry generator eliminates this wholesale
3. Only 1 procedure has SAVE state — no DLL persistence problem (unlike ROSCO)
4. External dependency boundary is clean — NWTC Library, InflowWind, SeaState, LinDyn are read-only data providers
5. FVW/OLAF is the largest sub-system (137 procs, 10,738 lines) and uses OpenMP

**Dev notes:** `202604232137` (inventory), `202604232243` (uncalled investigation), `202604232318` (uncalled refinement)

## 3. Key Decisions

### KD1: Type strategy — Registry generator + VIT (committed)

Registry-generated types (`_Types.f90`, 740 procedures) handled by a new C++ code generator that extends the OpenFAST Registry. Hand-written types handled by VIT's existing `analyze-types` / BIND(C) / view struct machinery.

**Rationale:** Clean tool separation. The Registry has a schema (`.txt` input files) — the generator reads the same schema, emits C++. VIT handles smaller hand-written types it already knows.

### KD2: KGen extraction target — Both (committed)

AeroDyn Driver (`aerodyn_driver`) as primary target for most functions. Full OpenFAST executable for coupled-mode-only functions (linearization routines, 3 confirmed framework interface procedures).

**Rationale:** AeroDyn Driver is self-contained, fast to iterate, exercises the same physics. Full OpenFAST is needed only for functions not reachable via the Driver (Jacobian routines, constraint state residuals).

### KD3: Phasing order — Bottom-up by dependency layer (committed)

Translate leaves first, work up the call graph. Registry generator (Phase 0) before any physics. Each layer verified before building the next.

**Rationale:** Mirrors ROSCO's successful approach. Dependency graph is clear from inventory.

### KD4: FMA handling — Same as ROSCO (committed)

`-ffp-contract=off` on both compilers during translation/verification. Re-enable on pure C++ build.

### KD5: NWTC Library — Fortran, C interop boundary (committed)

NWTC Library stays Fortran. AeroDyn C++ calls it via `extern "C"` / `BIND(C)` wrappers. 287 unique external callees; ~150 need wrappers (after excluding Registry boilerplate and Fortran intrinsics).

**Rationale:** NWTC Library is shared across all of OpenFAST. Translating it affects every module. The interop boundary is clean.

### KD6: External modules — Fortran, C interop (committed)

InflowWind, SeaState, LinDyn stay Fortran. AeroDyn receives their data via view structs / C interop.

**Dev note:** `202604240012`

## 4. Open Questions

### OQ1: Registry generator feasibility [answered 2026-04-24]

**Answer:** Highly feasible. The Registry is a clean C++ code generator (~3,200 lines) with a modular architecture — separate generator files per output language (`registry_gen_fortran.cpp`, `registry_gen_c.cpp`). Adding `registry_gen_cpp.cpp` follows the exact same pattern. The data model (`registry.hpp`) already captures POINTER/ALLOCATABLE/deferred dims/nested types/C type mappings. A new `-cppcode` flag was added and generates `ModuleName_Types.hpp` headers. All 10 AeroDyn headers (2,666 lines, 140 structs) compile cleanly with g++ C++17.

### OQ2: KGen on AeroDyn [unknown]

Will KGen's instrumentation work on AeroDyn's cmake build chain?

**Blocks:** Phase 1. **Info needed:** Run `vit extract` on one AeroDyn function. **When:** Before Phase 1.

### OQ3: NWTC Library interop surface [answered]

287 unique external callees. ~140 Registry boilerplate (eliminated by C++ generator), ~22 NWTC utilities, ~30 mesh-related, ~95 other. True interop wrapper count: ~150. Manageable — wrappers built incrementally per phase.

### OQ4: r-test coverage [partially answered]

9 BEMT-path cases + 8 OLAF-path cases cover BEMT, FVW, UnsteadyAero, AirfoilInfo, DBEMT. **Not covered:** AeroAcoustics (separate driver), C-binding (called from C), linearization (requires full OpenFAST). Full dynamic coverage analysis deferred to Phase 1.

### OQ5: OpenMP in FVW [answered]

Simple `!$OMP PARALLEL DO` loops in FVW_BiotSavart.f90. Embarrassingly parallel over control points/segments. Direct `#pragma omp parallel for` translation. Should be preserved — performance-critical.

### OQ6: POINTER fields in Registry types [answered 2026-04-24]

**Answer:** AeroDyn has only 5 POINTER field instances, all non-owning references to external module types (FlowFieldType from InflowWind, WaveFieldType from SeaState). In the Registry data model, POINTER fields are marked with `*` prefix (`is_pointer=true`, `is_target=false`). Copy uses shallow association (`=>`), Destroy nullifies without deallocating, Pack/Unpack use `RegPackPointer`/`c_loc` for reference tracking. In the C++ generator, POINTER fields map to `void*` (opaque non-owning pointer). No target pointers exist in AeroDyn's Registry types.

### OQ7: Mesh types at interop boundary [unknown]

How to pass `MeshType` / `MeshMapType` (complex NWTC Library types) across C interop?

**Blocks:** Phase 1. **Info needed:** Read MeshType definition, design view struct pattern. **When:** Before Phase 1.

### OQ8: VIT enhancements for AeroDyn [unknown]

What breaks when running VIT on AeroDyn's larger types, cmake build, module structure?

**Blocks:** Phase 1. **Info needed:** Attempt full extract/verify/integrate on one function. **When:** Before Phase 1 (overlaps OQ2).

### OQ9: Upstream sync strategy [unknown — decision needed]

How to handle NREL upstream changes during ~12-month effort? Merge frequency, conflict policy?

**Blocks:** Nothing immediately. **Info needed:** Team decision + NREL release cadence check. **When:** Before significant translation (after Phase 1).

### OQ10: Full OpenFAST build [unknown]

What's needed to build full OpenFAST for coupled-mode extraction? How long? What test cases exercise linearization?

**Blocks:** Phase 6 (framework interface procedures). **When:** Before Phase 6.

**Dev note:** `202604240016`

## 5. Phase Roadmap

### Phase 0: Registry C++ Generator [speculative]

**Goal:** Build a C++ code generator that produces C-compatible struct headers from Registry schema files, so translated C++ physics code has type definitions to work with.

**Scope:** 10 Registry input files (2,180 lines) → 10 C++ headers (2,666 lines, 140 structs). The 740 Fortran boilerplate procedures (Copy/Destroy/Pack/Unpack/ExtrapInterp) remain Fortran for now — they're called by the OpenFAST framework which stays Fortran. C++ equivalents are a Phase 8 concern.

**Confidence:** committed — OQ1 and OQ6 answered, implementation complete.

**Dependencies:** None (first phase).

**Work:**
- [x] Investigate OpenFAST Registry tool source code (OQ1) — highly feasible, modular C++ architecture
- [x] Understand POINTER vs ALLOCATABLE in generated code (OQ6) — 5 POINTERs, all non-owning, map to void*
- [x] Design C++ output format — C-compatible structs with pointer+size for allocatable, void* for opaque/external types
- [x] Implement generator — `registry_gen_cpp.cpp` (~230 lines), `-cppcode` flag
- [x] Verify generated types compile — 10/10 headers compile with g++ C++17, 10/10 Fortran files identical (no regression)
- [ ] Commit generated C++ types

**Dev note:** `202604241XXX` (Phase 0 implementation)

### Phase 1: AirfoilInfo + DBEMT [planned]

**Goal:** Translate the two leaf sub-modules. Validate that VIT + KGen work on AeroDyn. This is the "prove it works" gate.

**Scope:** 37 procedures, ~3,415 lines. 17 leaf functions.

**Confidence:** planned — shape is clear, details depend on OQ2/OQ7/OQ8.

**Dependencies:** Phase 0 (C++ types must exist for the translated functions to reference).

**Work:**
- [ ] Validate KGen extraction on AeroDyn build chain (OQ2)
- [ ] Validate VIT extract/verify/integrate cycle (OQ8)
- [ ] Design mesh interop pattern for MeshType (OQ7)
- [ ] Design NWTC Library interop wrappers (first batch)
- [ ] Translate AirfoilInfo leaf functions (12 leaves)
- [ ] Translate AirfoilInfo mid-level functions (11 remaining)
- [ ] Translate DBEMT leaf functions (5 leaves)
- [ ] Translate DBEMT mid-level functions (9 remaining)
- [ ] Verify: KGen kernel pass for all 37 functions
- [ ] Verify: 16-case baseline bit-identical
- [ ] Verify: NREL regression 16/16

**Revisit trigger:** After KGen/VIT validation — if extraction fails or VIT needs major enhancements, reassess timeline and approach before continuing.

### Phase 2: BEMTUncoupled + UnsteadyAero [planned]

**Goal:** Translate the two mid-level sub-modules that depend on Phase 1.

**Scope:** 73 procedures, ~6,690 lines. 33 leaf functions.

**Confidence:** planned — depends on Phase 1 proving the approach.

**Dependencies:** Phase 1.

**Work:**
- [ ] Translate BEMTUncoupled (29 procedures, includes `mod_root1dim`)
- [ ] Translate UnsteadyAero (44 procedures)
- [ ] Verify: KGen kernel pass for all 73 functions
- [ ] Verify: 16-case baseline bit-identical
- [ ] Verify: NREL regression 16/16

### Phase 3: BEMT [planned]

**Goal:** Translate the BEMT solver. After this phase, the entire BEMT path is C++.

**Scope:** 37 procedures, ~2,881 lines. 16 leaf functions.

**Confidence:** planned.

**Dependencies:** Phases 1-2.

**Work:**
- [ ] Translate BEMT procedures (37)
- [ ] Verify: KGen kernel pass
- [ ] Verify: 16-case baseline bit-identical (9 BEMT-path cases are the primary gate)
- [ ] Verify: NREL regression 16/16

**Revisit trigger:** BEMT path complete — re-examine FVW/OLAF scope and sub-phasing before continuing. FVW is the largest sub-system (137 procs) and may need to be broken into sub-phases.

### Phase 4: FVW/OLAF [speculative]

**Goal:** Translate the Free Vortex Wake solver.

**Scope:** 137 procedures, ~10,738 lines. 59 leaf functions. Largest sub-system.

**Confidence:** speculative — this is ~27% of the total hand-written code. Will likely need sub-phases. OpenMP translation required (OQ5 answered: simple parallel DO loops, direct `#pragma omp` translation).

**Dependencies:** Phases 1-2 (AirfoilInfo, UnsteadyAero).

**Work:**
- [ ] Sub-phase: FVW_BiotSavart (8 procedures, OpenMP)
- [ ] Sub-phase: FVW_VortexTools (36 procedures)
- [ ] Sub-phase: FVW_Subs (41 procedures)
- [ ] Sub-phase: FVW_Wings (10 procedures)
- [ ] Sub-phase: FVW_IO (13 procedures)
- [ ] Sub-phase: FVW core (22 procedures)
- [ ] Sub-phase: FVW_Tests (5 test utilities — translate if time permits, genuinely dead are excluded)
- [ ] Verify: KGen kernel pass
- [ ] Verify: 16-case baseline bit-identical (8 OLAF-path cases)
- [ ] Verify: NREL regression 16/16

### Phase 5: AeroAcoustics [speculative]

**Goal:** Translate the aeroacoustics module.

**Scope:** 51 procedures, ~4,424 lines. 21 leaf functions.

**Confidence:** speculative — NOT covered by the 16 r-test cases. Needs its own verification strategy (separate `aeroacoustics_driver` or new test cases).

**Dependencies:** Phase 1 (AirfoilInfo).

**Work:**
- [ ] Design AeroAcoustics verification strategy (no r-test coverage — OQ4)
- [ ] Translate TNO sub-module (4 procedures, includes SLATEC callbacks f_int1/f_int2)
- [ ] Translate AeroAcoustics_IO (19 procedures)
- [ ] Translate AeroAcoustics core (28 procedures)
- [ ] Verify: KGen kernel pass
- [ ] Verify: AeroAcoustics-specific test suite (TBD)

**Revisit trigger:** Before starting — resolve AeroAcoustics verification strategy. May need to build/run the `aeroacoustics_driver` executable or create new test cases.

### Phase 6: Core AeroDyn [speculative]

**Goal:** Translate the main AeroDyn orchestration module.

**Scope:** 85 procedures, ~13,549 lines. 36 leaf functions. Most lines of any sub-system.

**Confidence:** speculative — depends on all prior phases. Includes the 3 confirmed framework interface procedures that require full OpenFAST extraction (OQ10).

**Dependencies:** Phases 1-5.

**Work:**
- [ ] Build full OpenFAST for coupled-mode extraction (OQ10)
- [ ] Translate AeroDyn_IO (17 procedures)
- [ ] Translate AeroDyn_AllBldNdOuts_IO (4 procedures, includes the 1,938-line `Calc_WriteAllBldNdOutput`)
- [ ] Translate AeroDyn core (64 procedures, includes `AD_Init`, `AD_CalcOutput`, `AD_UpdateStates`)
- [ ] Translate framework interface procedures (3 — requires full OpenFAST KGen extraction)
- [ ] Verify: KGen kernel pass
- [ ] Verify: 16-case baseline bit-identical
- [ ] Verify: NREL regression 16/16

### Phase 7: Inflow / C-binding / Drivers [speculative]

**Goal:** Translate the integration layer and test infrastructure.

**Scope:** 66 + 21 standalone = 87 procedures, ~8,278 lines.

**Confidence:** speculative — translated last because drivers are the KGen extraction target (must keep working throughout).

**Dependencies:** Phase 6.

**Work:**
- [ ] Translate AeroDyn_Inflow (20 procedures)
- [ ] Translate C-binding layer (20 procedures, 10 entry points)
- [ ] Translate AeroDyn Driver (22 procedures)
- [ ] Translate UA Driver + AA Driver (remaining standalone procedures)
- [ ] Verify: all 16 test cases run with C++ driver
- [ ] Verify: C-binding entry points callable from C

### Phase 8: Integration + Cleanup [unknown]

**Goal:** Final integration — remove all Fortran, confirm done criteria.

**Scope:** No new translation. Integration testing, cleanup, documentation.

**Confidence:** unknown — scope depends on what Phases 0-7 leave behind.

**Dependencies:** All prior phases.

**Work:**
- [ ] Remove all Fortran source files from the build
- [ ] Confirm no libgfortran dependency
- [ ] Full 16/16 regression pass (bit-identical)
- [ ] NREL regression 16/16
- [ ] Upstream sync decision (OQ9)
- [ ] Clean up translation artifacts (metadata, scaffolding, `_c` indirection)
- [ ] Restructure source into module subdirectories (as done for ROSCO's `integrated` branch)
- [ ] Final dev note + plan update

## 6. Revisit Triggers

| Trigger | When | What to reassess |
|---------|------|-----------------|
| Registry investigation complete | After Phase 0 investigation | Is the Registry generator approach viable? Pivot to standalone generator or Fortran-with-wrappers if not |
| KGen/VIT validation | After Phase 1 first extraction | Does KGen work on AeroDyn? Is VIT adequate? This is the "prove it works" gate |
| BEMT path complete | After Phase 3 | Re-examine FVW/OLAF sub-phasing. 137 procedures may need finer granularity |
| AeroAcoustics verification | Before Phase 5 | How to verify without r-test coverage? Design test strategy before translating |
| Full OpenFAST build | Before Phase 6 | Build full OpenFAST for coupled-mode extraction. May surface new challenges |
| Quarterly | Every 3 months | Upstream sync check. Is NREL's AeroDyn changing significantly? Merge needed? |

## 7. Current State

**Date:** 2026-04-24

**Status:** Planning complete. Steps 1-7 of CREATING_PROJECT_PLANS.md methodology executed. Structural inventory committed. Scope decisions, key decisions, and open questions documented. Phase roadmap drafted.

**Next action:** Begin Phase 0 investigation — read the OpenFAST Registry source code to answer OQ1 and OQ6, then design the C++ generator.

**Completed milestones:**
- [x] Docker container (`vit-dev-openfast`) built and verified
- [x] OpenFAST builds, AeroDyn Driver runs 16 regression test cases
- [x] Platform-specific baselines committed for all 16 test cases
- [x] NREL regression 16/16 PASS with wrapper script
- [x] Three-tier verification infrastructure operational
- [x] fparser2 structural inventory (7 JSON data files)
- [x] All 67 uncalled procedures fully classified (0 unknowns)
- [x] Scope decisions locked (5)
- [x] Key architectural decisions locked (6)
- [x] Open questions enumerated (10, 3 answered)
- [x] PROJECT_PLAN_AERODYN.md drafted

## 8. Change Log

| Date | What changed | Why | Commit |
|------|-------------|-----|--------|
| 2026-04-24 | Initial plan document | Steps 1-7 complete | TBD |
