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

### OQ2: KGen on AeroDyn [answered 2026-04-24]

**Answer:** Yes. KGen works on AeroDyn's cmake executable build chain. First successful extraction: Calculate_Cn from AirfoilInfo.f90, 20 cases captured from the `ad_BAR_SineMotion` test case. Required 12 VIT fixes for AeroDyn's multi-target cmake, preprocessor-controlled precision, Docker timestamps, and same-file caller/callee pattern. See dev notes `202604241004` (first extraction), `202604241051` (adaptation issues catalog).

### OQ3: NWTC Library interop surface [answered]

287 unique external callees. ~140 Registry boilerplate (eliminated by C++ generator), ~22 NWTC utilities, ~30 mesh-related, ~95 other. True interop wrapper count: ~150. Manageable — wrappers built incrementally per phase.

### OQ4: r-test coverage [partially answered]

9 BEMT-path cases + 8 OLAF-path cases cover BEMT, FVW, UnsteadyAero, AirfoilInfo, DBEMT. **Not covered:** AeroAcoustics (separate driver), C-binding (called from C), linearization (requires full OpenFAST). Full dynamic coverage analysis deferred to Phase 1.

### OQ5: OpenMP in FVW [answered]

Simple `!$OMP PARALLEL DO` loops in FVW_BiotSavart.f90. Embarrassingly parallel over control points/segments. Direct `#pragma omp parallel for` translation. Should be preserved — performance-critical.

### OQ6: POINTER fields in Registry types [answered 2026-04-24]

**Answer:** AeroDyn has only 5 POINTER field instances, all non-owning references to external module types (FlowFieldType from InflowWind, WaveFieldType from SeaState). In the Registry data model, POINTER fields are marked with `*` prefix (`is_pointer=true`, `is_target=false`). Copy uses shallow association (`=>`), Destroy nullifies without deallocating, Pack/Unpack use `RegPackPointer`/`c_loc` for reference tracking. In the C++ generator, POINTER fields map to `void*` (opaque non-owning pointer). No target pointers exist in AeroDyn's Registry types.

### OQ7: Mesh types at interop boundary [answered 2026-04-24]

**Answer:** MeshType interop uses a view struct populator pattern (~80 lines total), not NWTC Library translation. AeroDyn physics code only reads mesh data fields (Position, TranslationVel, Orientation, etc.) — it never calls mesh lifecycle operations. A C++ `MeshType_View` struct (~50 lines) holds pointers to the data arrays; a Fortran BIND(C) populator (~30 lines) fills it from the real MeshType using `C_LOC()`. Phases 1-3 and 5 have zero mesh dereferences — the view struct is not needed until Phase 4 (11 accesses in FVW). Phase 6 is heaviest (271 accesses). See dev note `202604240640`.

### OQ8: VIT enhancements for AeroDyn [answered 2026-04-25]

**Answer:** VIT works on AeroDyn. 14 VIT fixes were needed (12 for initial AeroDyn adaptation, 2 more for derived type infrastructure). Proven across 4 functions with increasing type complexity: scalar/array (Calculate_Cn), AFI_ParameterType view struct (FindBoundingTables), nested TYPE embedding (Compute_iLoweriUpper), AFI_Table_Type with tanh math (ComputeUA360_CnOffset). All 16/16 regression pass. Known limitation: ALLOCATABLE TYPE() array access (`p%Table(i)%field`) needs marshalling solution for mid-level functions.

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
- [x] Commit generated C++ types
- [x] Follow-up: `-cppcode-double` flag for correct ReKi→double mapping (dev note `202604241459`)

**Dev notes:** `202604240609` (Registry generator), `202604240622` (Phase 0 retrospective), `202604240640` (MeshType interop investigation)

### Phase 1: AirfoilInfo + DBEMT [in progress]

**Goal:** Translate the two leaf sub-modules. Validate that VIT + KGen work on AeroDyn. This is the "prove it works" gate.

**Scope:** 37 procedures, ~3,415 lines. 17 leaf functions.

**Confidence:** committed — proof-of-concept complete (Calculate_Cn), OQ2 answered, infrastructure hardened.

**Dependencies:** Phase 0 (C++ types must exist for the translated functions to reference).

**Work:**
- [x] Validate KGen extraction on AeroDyn build chain (OQ2) — 20 cases captured, 12 VIT fixes
- [x] Validate VIT extract/verify/integrate cycle (OQ8, partial) — Calculate_Cn 20/20 IDENTICAL
- [x] First function: Calculate_Cn verified and integrated, 16/16 regression tests pass
- [x] Infrastructure: FP_CONTRACT_OFF cmake option, baselines regenerated, ReKi precision, cmake auto-patching
- [x] View struct infrastructure: nested TYPE() embedding, ALLOCATABLE TYPE() opaque, rank-3, LOGICAL(C_BOOL), TYPE() space normalization (8 VIT fixes, dev note `202604241905`)
- [x] FindBoundingTables: 21/21 IDENTICAL, AFI_ParameterType view struct
- [x] Compute_iLoweriUpper: 20/20 IDENTICAL, AFI_Table_Type view struct with nested AFI_UA_BL_Type
- [x] ComputeUA360_CnOffset: 21/21 IDENTICAL, AFI_Table_Type view struct, tanh math
- [x] gcov line coverage infrastructure: definitive test case selection for all functions
- [x] OQ8 answered: VIT works on AeroDyn derived types
- [ ] Design mesh interop pattern for MeshType (OQ7 answered — ~80-line view struct, not needed until Phase 4)
- [ ] Design NWTC Library interop wrappers (first batch)
- [ ] Translate AirfoilInfo leaf functions (8 remaining — ALLOCATABLE TYPE() array access pattern needed for functions that read `p%Table(i)%field`)
- [ ] Translate AirfoilInfo mid-level functions (11 remaining)
- [ ] Translate DBEMT leaf functions (5 leaves)
- [ ] Translate DBEMT mid-level functions (9 remaining)
- [ ] Verify: KGen kernel pass for all 37 functions
- [ ] Verify: 16-case baseline bit-identical
- [ ] Verify: NREL regression 16/16

**Known VIT limitations (to be resolved as needed during Phase 1):**
- **ALLOCATABLE TYPE() array access:** Current view struct uses `void*` (opaque) for ALLOCATABLE TYPE() fields like `p%Table(:)`. Functions that access elements (e.g., `p%Table(i)%Alpha`) will need a marshalling solution — probably a flattened array-of-structs view where the populator serializes each element's fields into contiguous C memory. First function needing this: `AFI_ComputeAirfoilCoefs1D` (reads `p%Table(iTable)%Coefs`).
- **Nested TYPE() embedding is single-level:** Inline struct generation skips TYPE() fields inside nested types. If a nested type contains another C-compatible nested type, it won't be embedded. Not triggered by current AirfoilInfo types (AFI_UA_BL_Type is all scalars). Will need recursion if deeper nesting appears (likely Phase 2 or Phase 4).

**Revisit trigger:** After KGen/VIT validation — if extraction fails or VIT needs major enhancements, reassess timeline and approach before continuing. **Status: POC passed, no showstoppers found. Derived type handling proven.**

**Dev notes:** `202604241004` (first extraction), `202604241026` (precision mismatch), `202604241051` (VIT adaptation issues), `202604241122` (first verification), `202604241337` (FMA confirmed), `202604241400` (first full verification), `202604241415` (full pipeline proven), `202604241459` (four infrastructure fixes), `202604241905` (derived type functions proven)

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

**Date:** 2026-04-25

**Status:** Phase 0 complete. Phase 1 in progress — 4 AirfoilInfo functions integrated, gcov coverage infrastructure operational. Ready for batch translation of remaining leaf functions.

**Next action:** Scale Phase 1 — translate remaining AirfoilInfo leaf functions using coverage-guided extraction, then DBEMT.

**Completed milestones:**
- [x] Docker container (`vit-dev-openfast`) built and verified
- [x] OpenFAST builds, AeroDyn Driver runs 16 regression test cases
- [x] Platform-specific baselines committed for all 16 test cases
- [x] Baselines regenerated with `-ffp-contract=off` (2026-04-24)
- [x] NREL regression 16/16 PASS with wrapper script
- [x] Three-tier verification infrastructure operational
- [x] fparser2 structural inventory (7 JSON data files)
- [x] All 67 uncalled procedures fully classified (0 unknowns)
- [x] Scope decisions locked (5)
- [x] Key architectural decisions locked (6)
- [x] Open questions enumerated (10, 7 answered: OQ1/OQ2/OQ3/OQ5/OQ6/OQ7/OQ8)
- [x] PROJECT_PLAN_AERODYN.md drafted
- [x] Phase 0: Registry C++ generator (`-cppcode`, `-cppcode-double`)
- [x] Phase 0: OQ1, OQ6, OQ7 answered
- [x] Phase 1 POC: Calculate_Cn extracted (20 cases), verified (20/20 IDENTICAL), integrated (16/16 regression pass)
- [x] Phase 1 POC: 12 VIT fixes for AeroDyn adaptation
- [x] Infrastructure: FP_CONTRACT_OFF cmake option, ReKi precision, cmake auto-patching
- [x] Phase 1: FindBoundingTables (21/21), Compute_iLoweriUpper (20/20), ComputeUA360_CnOffset (21/21) — all integrated, 16/16 regression
- [x] Phase 1: View struct infrastructure proven (nested TYPE embedding, ALLOCATABLE TYPE opaque, rank-3, LOGICAL(C_BOOL))
- [x] gcov line coverage infrastructure: per-line execution counts for all AeroDyn source across 17 test cases (`generate_coverage.sh`, `parse_gcov.py`, `query_coverage.py`)
- [x] KGen ETime profiling disabled (saves one build cycle per extraction)
- [x] OQ8 answered: VIT works on AeroDyn — derived types, view structs, nested types all proven

## 8. Change Log

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-24 | Initial plan document | Steps 1-7 complete |
| 2026-04-24 | Phase 0 complete | Registry C++ generator, OQ1/OQ6 answered |
| 2026-04-24 | OQ7 answered | MeshType interop is ~80-line view struct, not NWTC translation |
| 2026-04-24 | Phase 1 POC complete | Calculate_Cn end-to-end, 12 VIT fixes, 4 infrastructure fixes |
| 2026-04-24 | Baselines regenerated | `-ffp-contract=off` in cmake, 16/16 verified |
| 2026-04-24 | View struct infrastructure | 8 VIT fixes for AeroDyn Registry-generated types (nested TYPE embedding, ALLOCATABLE TYPE opaque, rank-3, LOGICAL(C_BOOL), TYPE() normalization) |
| 2026-04-24 | FindBoundingTables + Compute_iLoweriUpper | First derived type functions verified (21/21 + 20/20 IDENTICAL) and integrated (16/16 regression) |
| 2026-04-25 | ComputeUA360_CnOffset | 21/21 IDENTICAL, integrated (16/16 regression). Fourth AirfoilInfo function. |
| 2026-04-25 | gcov coverage infrastructure | Per-line execution counts for all source across 17 test cases. Eliminates test case guesswork for extraction. |
| 2026-04-25 | EXTRACTION_PARAMS.md removed | Replaced by coverage data. Hand-maintained records caused a multi-hour debugging session (wrong line number). |
| 2026-04-25 | KGen ETime disabled | Default disabled in KGen fork. VIT specifies invocation range explicitly. |
| 2026-04-25 | OQ8 answered | VIT works on AeroDyn: derived types, view structs, nested types all proven across 4 functions. |
