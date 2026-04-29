# PROJECT PLAN: AeroDyn Translation

## 1. Vision and Done Criteria

**Goal:** Translate OpenFAST's AeroDyn module from Fortran to C++ using VIT, producing a pure C++ AeroDyn module with no Fortran source files in the build and no libgfortran dependency.

**Target codebase:** AeroDyn — the aerodynamics module of OpenFAST (NREL). 42 Fortran source files, ~75,000 lines, 507 hand-written procedures + 740 Registry-generated procedures across 5 sub-systems (BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, core AeroDyn). Approximately 10× ROSCO's scope.

**Done criteria:**
- [ ] All AeroDyn Fortran source files removed from the build
- [ ] No libgfortran dependency (confirmed via `ldd` or equivalent)
- [ ] 17 regression test cases pass bit-identical against committed baselines
- [ ] NREL regression 17/17 pass at rtol=2, atol=1.9
- [ ] AeroDyn Driver executable functional for all 17 test cases
- [ ] C-binding layer functional (Exawind interface preserved)
- [ ] C++ Registry generator produces all `_Types` equivalents

**Explicit out-of-scope:**
- NWTC Library translation (shared framework, separate project)
- InflowWind, SeaState, LinDyn translation (separate modules, read-only data providers)
- OpenFAST glue code changes (AeroDyn is a leaf module from the glue code's perspective)
- Performance optimization beyond parity (optimization is a follow-on, not a gate)

**Target verification rigor:** Three-tier, same as ROSCO:
- Tier 0: KGen float64 kernel verification per function
- Tier 1: 17-case simulation comparison, bit-identical against committed baselines
- Tier 2: NREL regression at 1e-2 tolerance (correctness gate, 17/17)

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

### OQ8: VIT enhancements for AeroDyn [answered 2026-04-25, updated 2026-04-28]

**Answer:** VIT works on AeroDyn. 25 VIT fixes total (12 for initial AeroDyn adaptation, 2 for derived type infrastructure, 11 during DBEMT Phase A). Proven across 25 functions spanning two modules. DBEMT added: rank-2 TYPE() ALLOCATABLE view structs, error handling (ErrStat/ErrMsg), CHARACTER(*) kernel repair, Fortran intrinsic callee filtering, auto-type infrastructure in `vit translate`, typed pointers for C-compatible ALLOCATABLE TYPE() fields, callee detection excluding field access, fixed-size arrays of view types (KD9). Known limitation resolved: ALLOCATABLE TYPE() array access proven via typed pointer marshalling (AFI_ParameterType.Table).

### OQ9: Upstream sync strategy [unknown — decision needed]

How to handle NREL upstream changes during ~12-month effort? Merge frequency, conflict policy?

**Blocks:** Nothing immediately. **Info needed:** Team decision + NREL release cadence check. **When:** Before significant translation (after Phase 1).

### OQ10: Full OpenFAST build [unknown]

What's needed to build full OpenFAST for coupled-mode extraction? How long? What test cases exercise linearization?

**Blocks:** Phase 6 (framework interface procedures). **When:** Before Phase 6.

**Dev note:** `202604240016`

## 5. Phase Roadmap

### KD7: Vectorization flag for sensitive feedback loops (committed 2026-04-28)

`-fno-tree-loop-vectorize` on DBEMT.f90. Replacing a PRIVATE function body with a C++ wrapper changes gfortran's vectorization decision for the caller's `exp()` loop (scalar libm vs SIMD libmvec, 1 ULP difference). The DBEMT exponential filter feedback loop amplifies this through 600 UA-coupled timesteps into catastrophic divergence (max_abs=5.73e+06). The flag ensures consistent scalar `exp()` regardless of wrapper insertion. Same root cause as the kernel verification libmvec issue (KD4/dev note `202604260357`). Other modules with similar sensitive feedback loops may need the same treatment.

**Dev note:** `202604281854`

### KD8: Auto-type infrastructure in `vit translate` (committed 2026-04-28)

`vit translate` auto-detects derived types with ALLOCATABLE fields, sets `strategy: view` in vit.yaml, writes/updates vit_types.h, and creates view populator .f90 files. No manual type prerequisites needed before translation. `vit integrate` auto-adds populator .f90 to CMakeLists.txt.

**Dev note:** `202604282134`

### KD9: Fixed-size arrays of view types (committed 2026-04-29)

VIT now handles `TYPE(Foo), DIMENSION(1:N) :: bar` where Foo has ALLOCATABLE fields and `strategy: view`. Emits `foo_view_t bar[N]` in C structs, `TYPE(foo_view_t) :: bar(N)` in BIND(C) mirrors, and populate/reverse-copy loops. Pattern appears 7 times across 4 AeroDyn modules (xdot history arrays in AB4/ABM4, u_DBEMT/u_SkewWake input arrays in BEMT). Unblocks DBEMT Phase B time integrators.

**Dev note:** `202604291527`

### Phase 0: Registry C++ Generator [complete]

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
- [x] Calculate_C_alpha: 20/20 IDENTICAL, LAPACK dgels dependency, CHARACTER(*) support, compile-error-driven kernel repair (dev note `202604251331`)
- [x] ComputeUASeparationFunction_zero: 20/20 IDENTICAL, first view reverse-copy in kernel verification (dev note `202604251425`)
- [x] ComputeUA360_updateCnSeparated: 20/20 IDENTICAL, callee bridge to CnOffset, callee PUBLIC visibility fix
- [x] ComputeUA360_updateSeparationF: 20/20 IDENTICAL, CSHIFT intrinsic, callee bridge, most complex leaf
- [x] Infrastructure: view reverse-copy in kernel verification, callee PUBLIC visibility, kernel repair, auto-callee declarations (dev notes `202604251425`, `202604251451`, `202604251503`)
- [x] Design mesh interop pattern for MeshType (OQ7 answered — ~80-line view struct, not needed until Phase 4)
- [x] ComputeUASeparationFunction_onCl: 20/20 IDENTICAL, first callee-dependent function (calls ComputeUASeparationFunction_zero via bridge), callee bridge INOUT view re-sync fix (dev note `202604251821`)
- [x] Shared NWTC utility header (`vit_nwtc.h`): C++ translations of EqualRealNos, fZeros, InterpExtrapStp + constants (Pi, TwoPi, PiBy2, D2R, R2D). Replaces inlined duplicates in 6 translations. VIT enhanced to copy shared headers to kernel. All 9 functions re-verified. (dev note `202604251943`)
- [x] ComputeUA360_AttachedFlow: 20/20 IDENTICAL, first function using NWTC utility callees from `vit_nwtc.h` (fZeros, InterpExtrapStp, EqualRealNos) + callee bridge to Compute_iLoweriUpper, `--reverse-copy` for 9 UA_BL scalars (dev note `202604251943`)
- [x] CalculateUACoeffs: 20/20 kernel IDENTICAL (libmvec vectorization fix + clean re-extraction), 16/16 baselines PASS. 326-line orchestrator, 7 callee bridges, 2 NWTC utilities (InterpStp, kernelSmoothing), LOGICAL type support, array-returning function bridges, callee flush-reload, Calculate_C_alpha buffer overflow fix. 8 VIT infrastructure fixes. Previous 15/20 was caused by KGen-reduced module not vectorizing cos/sin loop → scalar libm vs production's vectorized libmvec (1-ULP trig diff cascading to 1.24e-14 in cnBreakUpper). Fixed by adding `-fvect-cost-model=unlimited` + `-lmvec -lm` to VIT kernel flags. (dev notes `202604252257`, `202604252345`, `202604260357`)
- [x] AFI_ValidateInitInput: 7/7 test-validate PASS (C++ ≡ Fortran on all 6 error branches + happy path), 16/16 baselines PASS. First function using `vit test-validate` — prompt-based branch-coverage testing with BIND(C) Fortran bridge, PRIVATE function patching, production-build linking. No KGen extraction needed. (dev notes `202604261044`, `202604261131`, `202604261142`, `202604261328`)
- [x] CheckValuesAreUniqueMonotonicIncreasing: 11/11 test-validate PASS, 17/17 baselines PASS. Trivial validator using `vit test-validate` with FUNCTION return type support (LOGICAL→C_BOOL via vit_result output parameter) and assumed-shape array bridging. (dev note `202604270158`)
- [x] ReadAFfile: 17/17 baselines PASS. Idiomatic C++ file parser with two-pass architecture (Pass 1: parse + return sizes, Pass 2: fill Fortran-allocated arrays from cache). Keyword-based `AirfoilFileParser` class matching Fortran `ParseVar` semantics (non-consuming on keyword mismatch). Three bugs fixed: (1) heap corruption from uninitialized `FileInfo` cleanup, (2) `ParseVar` keyword-check-before-consume semantics for files with missing UA parameters, (3) conditional Cpmin column mapping when `InCol_Cm=0`. Verified across 9 airfoil file formats (BAR, MHK, OLAF, Kite). (dev notes `202604270158`, `202604270219`, `202604270333`)
- [x] AFI_Init: 17/17 baselines PASS + 11/11 test-validate PASS (C++ ≡ Fortran on all error branches). Two-pass orchestrator: pass 1 handles multi-table validation and secondVals computation (AFITable_2Re log(Re), AFITable_2User UserProp), pass 2 computes cubic spline coefficients via new CubicSplineInitM/CubicLinSplineInitM in vit_nwtc. Fortran wrapper allocates secondVals and SplineCoefs between passes. ColUAf overwrite bug caught and fixed during initial verification (pass 1 was resetting ColUAf=0, overwriting ReadAFfile's value). (dev notes `202604271433`, `202604271455`)
- [x] AFI_WrHeader + AFI_WrData + AFI_WrTables: file-output PASS (C++ data ≡ Fortran data). Debug-output functions with native C++ file I/O (fopen/fprintf). WrTables self-contained; WrHeader+WrData use stashed filename for Fortran→C++ file path passing. NWTC_Init required for R2D constant (runtime-initialized, not PARAMETER). CHARACTER(17) left-justify padding. File-output verification: two test harnesses comparing data lines (skipping timestamps/headers/blanks, trimming trailing whitespace). (dev note `202604271730`)
  - **Note:** AirfoilInfo module is now COMPLETE — all 20 hand-written functions in C++. AFI_ComputeAirfoilCoefs1D/2D and AFI_ComputeUACoefs2D are static helpers inside their parent files. Registry-generated routines (Pack/Unpack/Copy/Destroy/ExtrapInterp in AirfoilInfo_Types.f90) are handled by the Phase 0 Registry C++ generator.
**DBEMT Module** (14 procedures, 926 lines in DBEMT.f90). Dynamic inflow corrections for blade element momentum theory. Pure computation — no file I/O, no SAVE variables, no OpenMP. Three modes: finite difference filtering (Modes 1/2, analytical exponential solution) and continuous ODE integration via ABM4 (Mode 3). Called exclusively by BEMT. 7 PUBLIC / 7 PRIVATE procedures.

**DBEMT Prerequisites:**
- [x] Add DBEMT_Mod constants to `vit_aerodyn_constants.h` — all 5 constants defined
- [x] Add `num2lstr` equivalent to `vit_nwtc.h` — inline function, `snprintf` + left-trim
- [x] Add `ErrID_Severe` to `vit_nwtc.h` — value 3, was missing between Warn(2) and Fatal(4)
- [x] Create `translations/DBEMT/` directory
- [x] DBEMT_ParameterType view struct + populator — done during ComputeTau2
- [x] DBEMT_InputType view struct + populator — done during ComputeTau1
- [x] DBEMT_MiscVarType C struct (int32_t FirstWarn_tau1) — done during ComputeTau1
- [x] `-fno-tree-loop-vectorize` on DBEMT.f90 — prevents vectorization divergence after integration (KD7)
- [x] DBEMT_ContinuousStateType + DBEMT_OtherStateType view structs — auto-generated during DBEMT_InitStates translation (KD8). OtherStateType includes xdot[4] embedded view array (KD9).
- [ ] DBEMT_InitInputType view — auto-generated by `vit translate` when needed (KD8)

**DBEMT Type System:**

| Type | Strategy | Key Fields | Notes |
|------|----------|------------|-------|
| DBEMT_ParameterType | view | DT, NumBlades, NumNodes, k_0ye, tau1_const, DBEMT_Mod, **spanRatio(:,:)** | ALLOCATABLE spanRatio |
| DBEMT_InputType | view | AxInd_disk, Un_disk, R_disk, **element(:,:)** | ALLOCATABLE array of ElementInputType |
| DBEMT_ContinuousStateType | view | **element(:,:)** | ALLOCATABLE array of ElementContinuousStateType |
| DBEMT_OtherStateType | view | tau1, tau2, **areStatesInitialized(:,:)**, **n(:,:)**, **xdot(1:4)** | Most complex: ALLOCATABLE arrays + fixed-size array of ContinuousStateType. View exposes 4 embedded ContinuousState sub-views for xdot. |
| DBEMT_InitInputType | view | NumBlades, NumNodes, tau1_const, DBEMT_Mod, **rLocal(:,:)** | Init-only |
| DBEMT_ElementInputType | BIND(C) | vind_s(1:2), spanRatio | Fixed-size, C-compatible |
| DBEMT_ElementContinuousStateType | BIND(C) | vind(1:2), vind_1(1:2) | Fixed-size, C-compatible |
| DBEMT_MiscVarType | BIND(C) | FirstWarn_tau1 (LOGICAL) | Single field |

**DBEMT Phase A — Leaf Math (5 functions, ~197 lines, KGen verified):**
- [x] ComputeTau2 (26 lines, PRIVATE) — 20/20 IDENTICAL, 17/17 baselines PASS. Translation bug found: `0.26 * spanRatio * spanRatio` ≠ Fortran `0.26*spanRatio**2` (operator precedence — C++ left-to-right vs Fortran `**` binding tighter). Fix: parentheses `0.26 * (spanRatio * spanRatio)`. Also required `-fno-tree-loop-vectorize` on DBEMT.f90 (KD7). KGen's 20/20 IDENTICAL missed the bug — input-dependent, only triggers on specific spanRatio bit patterns. Baselines caught it. (dev notes `202604281854`, `202604281859`, `202604281913`)
- [x] ComputeTau1 (66 lines, PRIVATE) — 20/20 IDENTICAL, 17/17 baselines PASS. First DBEMT function with error handling (ErrStat/ErrMsg, SetErrStat lambda). Surfaced 3 VIT bugs: (1) catastrophic regex backtracking in `scan_allocatable_sizes`, (2) TYPE() ALLOCATABLE rank mismatch in view populator, (3) KGen CHARACTER(*) code generation. All fixed. (dev note `202604282039`)
- [x] DBEMT_InitStates (26 lines, PRIVATE) — 20/20 IDENTICAL, 17/17 baselines PASS. First function with INOUT state types (ContinuousStateType, OtherStateType). Surfaced 3 VIT bugs: (1) void* for C-compatible ALLOCATABLE TYPE() fields, (2) C_NULL_PTR for same in populator, (3) callee detection matching field access `%element(i,j)`. Also fixed integrate_all.sh missing InputType view restore. (dev note `202604291005`)
- [x] DBEMT_CalcOutput (35 lines, PUBLIC) — 20/20 IDENTICAL, 17/17 baselines PASS. Cross-module extraction from BEMT.f90:1215. ErrMsg blank-padding lesson: Fortran `ErrMsg=""` fills with spaces, C++ must `memset(errMsg,' ',ErrMsgLen)`. Added to TRANSLATION_TIPS.md. (dev note `202604291756`)
- [x] DBEMT_CalcContStateDeriv (44 lines, PUBLIC) — 20/20 IDENTICAL, 17/17 baselines PASS. First function with translated callee (ComputeTau2 via bridge). Surfaced 3 VIT bugs in callee bridge generation: (1) missing USE for inherited types, (2) no C++ callee detection on integrated source, (3) CONTAINS whitespace in PUBLIC patching. All fixed. (dev notes `202604291824`, `202604291859`)
- [x] **Phase A COMPLETE** — all 5 leaf functions integrated, 17/17 baselines PASS.

**DBEMT Phase B — Time Integrators (3 functions, ~241 lines, KGen verified):**

All three are ONLY exercised by `ad_BAR_SineMotion_UA4_DBEMT3` (Mode 3). Extraction must use this test case (DBEMT_Mod=3). `vit translate` will auto-generate DBEMT_OtherStateType and DBEMT_ContinuousStateType view structs when first encountered. The OtherStateType is the most complex DBEMT type — ALLOCATABLE arrays + fixed-size `xdot(1:4)` array of ContinuousStateType. The xdot field needs 4 embedded ContinuousState sub-views for the ABM4 history buffer.

Key decision: **`DBEMT_ElementInputType_ExtrapInterp` inlined in C++** — the Registry-generated interpolation routine is only 6 lines of Lagrange interpolation on 3 scalars (vind_s[0], vind_s[1], spanRatio). Inlining avoids a callee bridge to Registry-generated Fortran and is verified transitively through kernel tests.

- [ ] DBEMT_RK4 (100 lines, PRIVATE) — 4th-order Runge-Kutta. 4 CalcContStateDeriv evals + 3 ExtrapInterp calls. Self-starting. Test case: `ad_BAR_SineMotion_UA4_DBEMT3` (270 hits — startup only, n≤2).
- [ ] DBEMT_AB4 (77 lines, PRIVATE) — Adams-Bashforth predictor. Uses RK4 for startup, then 4-level xdot history from `OtherState%xdot(1..4)%element(i,j)`. Shifts xdot history on each call. Test case: `ad_BAR_SineMotion_UA4_DBEMT3` (16,920 hits).
- [ ] DBEMT_ABM4 (64 lines, PRIVATE) — Adams-Bashforth-Moulton predictor-corrector. Calls AB4 for prediction, then applies correction formula. Test case: `ad_BAR_SineMotion_UA4_DBEMT3` (16,920 hits).
- [ ] Rebuild + verify 17/17 baselines

**DBEMT Phase C — Orchestrators (2 functions, ~85 lines, simulation verified):**
- [ ] DBEMT_UpdateStates (67 lines, PUBLIC, hot path) — main entry called by BEMT per blade/node. Calls ComputeTau1/Tau2/InitStates (all C++ direct), then branches: Mode 3 → ABM4 (C++ direct), Modes 1/2 → inline finite difference math (~20 lines, analytical exponential filter). Needs two InputType views (inputs at t and t+dt). Hand-integrated.
- [ ] DBEMT_InitStates_AllNodes (18 lines, PUBLIC) — trivial double loop calling DBEMT_InitStates per node. Hand-integrated.
- [ ] Rebuild + verify 17/17 baselines

**DBEMT Phase D — Init/Validation/End (3 translated + 1 skipped, ~221 lines translated, mixed verification):**
- [ ] DBEMT_ValidateInitInp (49 lines, PRIVATE) — **test-validate** (8 error branches: interval, DBEMT_Mod, numBlades, numNodes, tau1_const, rLocal allocated, rLocal values, max rLocal). Needs DBEMT_InitInputType view.
- [ ] DBEMT_ReInit (45 lines, PUBLIC) — simulation verified. Zeros all state arrays, sets areStatesInitialized=false, sets tau1 from mode. Replace Registry `CopyElementContinuousStateType` with inline copy (4 doubles per element).
- [ ] DBEMT_Init (127 lines, PUBLIC) — simulation verified. Contains ALLOCATABLE operations (allocate u%element, p%spanRatio, x%element, OtherState%n, areStatesInitialized). Two-pass architecture if needed (same pattern as AFI_Init). Calls ValidateInitInp + ReInit. **Translate last; could remain Fortran if time-constrained** — allocated arrays are visible through view structs regardless, and the key algorithmic content (spanRatio computation) is 10 lines.
- [ ] ~~DBEMT_End (47 lines, PUBLIC)~~ — **DO NOT TRANSLATE.** Calls only Registry-generated Destroy routines (DBEMT_DestroyInput, DestroyParam, DestroyContState, DestroyMisc, DestroyOtherState). Framework cleanup, not algorithmic. Fortran continues to own deallocation of Fortran-allocated memory.
- [ ] Rebuild + verify 17/17 baselines. **DBEMT module COMPLETE (13/14 translated, 1 skipped).**

**DBEMT Key Decisions:**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| ExtrapInterp | Inline 6-line Lagrange in C++ | Avoid callee bridge to Registry-generated code; verified transitively |
| DBEMT_End | Do not translate | Only calls Registry Destroy; C++ should not own Fortran deallocation |
| DBEMT_Init | Translate last, two-pass if needed | Allocation-heavy; runs once; arrays visible through views regardless |
| OtherState%xdot(1:4) | 4 embedded ContinuousState views | VIT now auto-generates this (KD9). Pattern appears 7× across 4 modules. |
| OPTIONAL in ComputeTau2 | has_X/X pair in bridge | Established VIT pattern from AirfoilInfo |
| num2lstr | std::to_string() | Only in warning messages; exact Fortran format match not critical |
| PRIVATE functions | Integrate each individually via `vit integrate` | PRIVATE function bodies ARE replaced with wrappers (proven by ComputeTau2/ComputeTau1). Previous concern about optimization sensitivity was a misdiagnosis — actual root cause was a translation bug + vectorization flag (KD7). The workflow works exactly as it did for ROSCO. |

**DBEMT Coverage Data (hits per test case):**

| Function | ad_MHK_RM1_Fixed | ad_BAR_SineMotion_UA4_DBEMT3 | ad_BAR_SineMotion |
|----------|-----------------|-------------------------------|-------------------|
| DBEMT_UpdateStates | 38,400 | 16,920 | 6,300 |
| ComputeTau1 | 38,400 | 16,920 | 6,300 |
| ComputeTau2 | 38,400 | 51,570 | 6,300 |
| DBEMT_InitStates | 38,400 | 16,920 | 6,300 |
| DBEMT_CalcOutput | 72,060 | 47,460 | 11,844 |
| DBEMT_CalcContStateDeriv | 0 | 34,650 | 0 |
| DBEMT_ABM4 | 0 | 16,920 | 0 |
| DBEMT_AB4 | 0 | 16,920 | 0 |
| DBEMT_RK4 | 0 | 270 | 0 |
| DBEMT_ValidateInitInp | 1 | 1 | 1 |

Finite difference path (Modes 1/2): `ad_MHK_RM1_Fixed` has highest coverage. Continuous ODE path (Mode 3): **ONLY** `ad_BAR_SineMotion_UA4_DBEMT3`.

**Revisit trigger:** OtherState%xdot(1:4) infrastructure resolved (KD9). Phase B ready to proceed.

**Phase A status (2026-04-29): COMPLETE.** All 5 leaf functions integrated (ComputeTau2, ComputeTau1, DBEMT_InitStates, DBEMT_CalcOutput, DBEMT_CalcContStateDeriv). 11 VIT bugs fixed during DBEMT Phase A. Infrastructure proven: all DBEMT view structs, error handling, auto-type, fixed-size view arrays (KD9), callee bridges for view-type parameters, C++ callee detection on integrated source.

**Dev notes:** `202604241004` (first extraction), `202604241026` (precision mismatch), `202604241051` (VIT adaptation issues), `202604241122` (first verification), `202604241337` (FMA confirmed), `202604241400` (first full verification), `202604241415` (full pipeline proven), `202604241459` (four infrastructure fixes), `202604241905` (derived type functions proven), `202604251125` (cross-module instrumentation), `202604251317` (kernel repair), `202604251331` (Calculate_C_alpha complete), `202604251425` (view reverse-copy fix), `202604251451` (three functions + two fixes), `202604251503` (auto-callee declarations), `202604251821` (onCl + callee bridge re-sync + integrate_all.sh), `202604251943` (vit_nwtc.h + AttachedFlow), `202604270158` (ReadAFfile in-progress), `202604270219` (ReadAFfile approach pivot), `202604270333` (ReadAFfile integration — three root causes), `202604270354` (ReadAFfile reflection + path forward), `202604271433` (AFI_Init integration — two-pass orchestrator + CubicSplineInitM), `202604271455` (AFI_Init test-validate — 11/11 multi-table validation)

### Phase 2: BEMTUncoupled + UnsteadyAero [planned]

**Goal:** Translate the two mid-level sub-modules that depend on Phase 1.

**Scope:** 73 procedures, ~6,690 lines. 33 leaf functions.

**Confidence:** planned — depends on Phase 1 proving the approach.

**Dependencies:** Phase 1.

**Work:**
- [ ] Translate BEMTUncoupled (29 procedures, includes `mod_root1dim`)
- [ ] Translate UnsteadyAero (44 procedures)
- [ ] Verify: KGen kernel pass for all 73 functions
- [ ] Verify: 17-case baseline bit-identical
- [ ] Verify: NREL regression 17/17

### Phase 3: BEMT [planned]

**Goal:** Translate the BEMT solver. After this phase, the entire BEMT path is C++.

**Scope:** 37 procedures, ~2,881 lines. 16 leaf functions.

**Confidence:** planned.

**Dependencies:** Phases 1-2.

**Work:**
- [ ] Translate BEMT procedures (37)
- [ ] Verify: KGen kernel pass
- [ ] Verify: 17-case baseline bit-identical (9 BEMT-path cases are the primary gate)
- [ ] Verify: NREL regression 17/17

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
- [ ] Verify: 17-case baseline bit-identical (8 OLAF-path cases)
- [ ] Verify: NREL regression 17/17

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
- [ ] Verify: 17-case baseline bit-identical
- [ ] Verify: NREL regression 17/17

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
- [ ] Verify: all 17 test cases run with C++ driver
- [ ] Verify: C-binding entry points callable from C

### Phase 8: Integration + Cleanup [unknown]

**Goal:** Final integration — remove all Fortran, confirm done criteria.

**Scope:** No new translation. Integration testing, cleanup, documentation.

**Confidence:** unknown — scope depends on what Phases 0-7 leave behind.

**Dependencies:** All prior phases.

**Work:**
- [ ] Remove all Fortran source files from the build
- [ ] Confirm no libgfortran dependency
- [ ] Full 17/17 regression pass (bit-identical)
- [ ] NREL regression 17/17
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

**Date:** 2026-04-29

**Status:** Phase 0 complete. Phase 1 in progress — **AirfoilInfo COMPLETE (20/20), DBEMT Phase A COMPLETE (5/5).** All leaf math functions integrated, 17/17 baselines PASS. VIT infrastructure hardened with 11 bug fixes + 3 enhancements (auto-type KD8, fixed-size view arrays KD9, callee bridge fixes). Phase B xdot infrastructure and callee bridge infrastructure ready.

**Next action:** DBEMT Phase B — time integrators (DBEMT_RK4, DBEMT_AB4, DBEMT_ABM4).

**Completed milestones:**
- [x] Docker container (`vit-dev-openfast`) built and verified
- [x] OpenFAST builds, AeroDyn Driver runs 16 regression test cases
- [x] Platform-specific baselines committed for all 16 test cases
- [x] Baselines regenerated with `-ffp-contract=off` (2026-04-24)
- [x] NREL regression 17/17 PASS with wrapper script
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
- [x] Phase 1: Calculate_C_alpha (20/20), ComputeUASeparationFunction_zero (20/20), ComputeUA360_updateCnSeparated (20/20), ComputeUA360_updateSeparationF (20/20) — all integrated, 16/16 regression
- [x] Phase 1: Infrastructure — view reverse-copy in kernel verification, callee PUBLIC visibility, compile-error-driven kernel repair, cross-module instrumentation, auto-callee declarations
- [x] OQ4 answered: gcov coverage definitive across all 17 test cases
- [x] Phase 1: ComputeUASeparationFunction_onCl (20/20) — first callee-dependent function, callee bridge INOUT view re-sync fix
- [x] Phase 1: Shared NWTC utility header (`vit_nwtc.h`) — C++ translations of EqualRealNos, fZeros, InterpExtrapStp + NWTC constants. 6 translations refactored, 9/9 re-verified. VIT enhanced to copy shared headers to kernel.
- [x] Phase 1: ComputeUA360_AttachedFlow (20/20) — first function using NWTC utility callees from shared header, `--reverse-copy`, 16/16 regression. integrate_all.sh: 10/10.
- [x] Phase 1: CalculateUACoeffs (15/20 kernel, 16/16 baselines) — orchestrator, 8 VIT infrastructure fixes, Calculate_C_alpha buffer overflow fix, InterpStp + kernelSmoothing in vit_nwtc
- [x] Phase 1: CalculateUACoeffs 20/20 (libmvec fix, clean re-extraction, shared_files config) — root cause: KGen-reduced module changed gfortran vectorization heuristics → scalar vs vectorized cos/sin (1-ULP trig diff). Fix: `-fvect-cost-model=unlimited` + `-lmvec -lm` in VIT kernel flags. Dev note `202604260357`.
- [x] Phase 1: AFI_ValidateInitInput — 7/7 test-validate, 16/16 baselines. First `vit test-validate` function. Test-validate audit: all 8 issues fixed. (dev notes `202604261044`, `202604261142`)
- [x] Phase 1: AFI_ComputeAirfoilCoefs — 20/20 kernel, 16/16 baselines. 4 infrastructure additions (ALLOCATABLE TYPE() array marshalling, LBOUND fix, C++ callee auto-detection, 3 NWTC utilities). SplineCoefs dimension fix. (dev note `202604262013`)
- [x] Phase 1: AFI_ComputeUACoefs — 20/20 kernel, 17/17 baselines. 2 NWTC utilities (AddOrSub2Pi, Angles_ExtrapInterp1). Static AFI_CopyUA_BL_Type to prevent Registry bridge. (dev note `202604262231`)
- [x] Phase 1: ad_MHK_RM1_Fixed_UA regression test — 17th baseline. Exercises 2D UA interpolation (AFTabMod=2Re + UA_Mod=4). UAStartRad bug found and fixed. Baseline generated from clean Fortran. (dev note `202604262336`)
- [x] Phase 1: CheckValuesAreUniqueMonotonicIncreasing — 11/11 test-validate, 17/17 baselines. VIT test-validate enhanced for FUNCTION return types (LOGICAL→C_BOOL) and assumed-shape arrays. 15th AirfoilInfo function. (dev note `202604270158`)
- [x] Phase 1: ReadAFfile — 17/17 baselines. Two-pass idiomatic C++ file parser with keyword-based `AirfoilFileParser`. Three bugs found and fixed: uninitialized FileInfo cleanup, ParseVar keyword-check-before-consume, conditional Cpmin column mapping. 16th AirfoilInfo function. (dev notes `202604270158`, `202604270219`, `202604270333`)
- [x] CLAUDE.md refactored — 819 → 194 lines. Reference material extracted to docs/. Context window freed for active workflow.
- [x] Phase 1: ComputeTau2 integrated — 20/20 KGen, 17/17 baselines. Translation bug (operator precedence `a*x**2`) + vectorization fix (`-fno-tree-loop-vectorize`). All 17 baselines regenerated. (dev notes `202604281854`, `202604281859`, `202604281913`)
- [x] Phase 1: ComputeTau1 integrated — 20/20 KGen, 17/17 baselines. First DBEMT function with error handling. 3 VIT bugs fixed (regex, rank mismatch, CHARACTER(*)). (dev note `202604282039`)
- [x] VIT: auto-type infrastructure — `vit translate` auto-detects view types, writes vit_types.h + populator .f90. No manual prerequisites. (dev note `202604282134`)
- [x] VIT: 5 bug fixes during DBEMT — regex backtracking, TYPE() rank mismatch, CHARACTER(*) kernel repair, C++ scan false positives, Fortran intrinsic callee detection
- [x] Workflow discipline documented — two-layer verification, dual-computation debug technique, operator precedence warning in CLAUDE.md + TRANSLATION_TIPS.md + memory
- [x] Phase 1: DBEMT_InitStates integrated — 20/20 KGen, 17/17 baselines. First INOUT state types. 3 VIT bugs fixed (void* for C-compatible TYPE(), C_NULL_PTR populator, field access callee detection). integrate_all.sh fixed (missing InputType view restore). (dev note `202604291005`)
- [x] VIT: typed pointers for C-compatible ALLOCATABLE TYPE() fields — void* → typed* in C structs, C_NULL_PTR → C_LOC in populators
- [x] VIT: callee detection excludes field access — (?<!%) lookbehind prevents %element(i,j) matching
- [x] VIT: fixed-size arrays of view types (KD9) — xdot[4] pattern, 7 instances across 4 modules. Embeds child_view_t arrays + populate/reverse-copy loops. Unblocks Phase B. (dev note `202604291527`)
- [x] Phase 1: DBEMT_CalcOutput integrated — 20/20 KGen, 17/17 baselines. Cross-module extraction from BEMT.f90. ErrMsg blank-padding lesson added to TRANSLATION_TIPS.md. (dev note `202604291756`)
- [x] Phase 1: DBEMT_CalcContStateDeriv integrated — 20/20 KGen, 17/17 baselines. First translated callee (ComputeTau2 via bridge). 3 callee bridge VIT fixes. (dev notes `202604291824`, `202604291859`)
- [x] **DBEMT Phase A COMPLETE** — all 5 leaf functions integrated. 11 VIT bug fixes + 3 enhancements during Phase A.
- [x] VIT: callee bridge fixes — USE fallback for inherited types, C++ callee detection on integrated source, CONTAINS whitespace in PUBLIC patching. (dev note `202604291859`)
- [x] VIT: coverage query reads clean source via git show — line numbers match coverage data regardless of integration state. (dev note `202604291005`)

## 8. History

Detailed change history is in dev notes (`vit/dev/YYYYMMDDHHMM-descriptor.md`). Key dev notes by phase:

**Phase 0:** `202604240609` (Registry generator), `202604240622` (retrospective)

**Phase 1 — AirfoilInfo:** `202604241004` (first extraction), `202604241905` (derived types), `202604251943` (vit_nwtc.h), `202604260357` (libmvec vectorization), `202604261142` (test-validate audit), `202604270333` (ReadAFfile root causes), `202604271433` (AFI_Init two-pass)

**Phase 1 — DBEMT:** `202604281854` (ComputeTau2 integration — operator precedence + vectorization fix), `202604281859` (KGen coverage gap), `202604281913` (the case for KGen), `202604282039` (ComputeTau1 + 3 VIT bugs), `202604282134` (auto-type infrastructure), `202604282138` (session reflection), `202604291005` (DBEMT_InitStates + 3 VIT fixes), `202604291527` (fixed-size view type arrays enhancement), `202604291756` (CalcOutput + ErrMsg blank-padding), `202604291824` (Phase A complete), `202604291831` (session reflection), `202604291859` (callee bridge fixes — 3 root causes)

## 9. Deferred Technical Items

Items that are safe in the current state but need attention before or during a specific future phase. Each entry names the trigger (when it must be resolved) and the consequence of not resolving it.

| Item | Location | Current State | Trigger | Consequence if Ignored |
|------|----------|--------------|---------|----------------------|
| **vit_wr_stashed_filename SAVE variable** | AirfoilInfo.f90 line 48 | Module-level SAVE variable passes filename from AFI_WrHeader to AFI_WrData. Works because callers (UnsteadyAero `UA_WriteAFIParamsToFile`) are single-threaded and follow a strict WrHeader→WrData×N→close sequence. | Phase 2 (UnsteadyAero translation) or any multi-threaded caller | Second concurrent WrHeader call overwrites the stashed filename, causing WrData to write to the wrong file. **Fix:** Pass filename through WrData's interface directly, or eliminate the stash when callers are translated to C++. |
| **CheckValuesAreUniqueMonotonicIncreasing dual existence** | afi_init.cpp (static inline) + AirfoilInfo.f90 (Fortran original) | C++ version inlined in afi_init.cpp; Fortran version remains for any external Fortran callers. Both produce identical results (verified 11/11 test-validate). | Phase 6 (core AeroDyn) if any Fortran caller is translated | Divergence risk if either copy is modified independently. **Fix:** When all callers are in C++, compile the standalone translation and remove the Fortran version. |
| **NWTC_Init required for R2D/D2R in test bridges** | All file-output and test-validate Fortran bridges | `R2D`, `D2R`, `Pi` etc. are runtime-initialized module variables in NWTC_Library, not compile-time PARAMETERs. Test bridges must call `NWTC_Init()` before using Fortran functions that depend on these constants. | Any new test-validate or file-output bridge | Fortran output uses R2D=0.0, producing silently wrong results. **Fix:** Always include `CALL NWTC_Init()` in bridge setup. Already documented in CLAUDE.md. |
| **Two integration conventions in translations/** | `translations/AirfoilInfo/` vs `modules/aerodyn/src/` | 9 early functions (Calculate_Cn through ComputeUASeparationFunction_onCl) went through `vit integrate`, so `translations/` holds the raw scaffold and `src/` has the VIT-generated wrapper + `extern "C"` shim. 10 later functions (AFI_ComputeAirfoilCoefs onward) were hand-integrated because they needed custom Fortran wrappers (two-pass architecture for AFI_Init/ReadAFfile, I/O interop for Wr*), so `translations/` = `src/` with the `_c` entry point defined directly. Both patterns work correctly. | DBEMT and future modules | No consequence — informational only. **Recommendation:** Use the later style (direct `_c` entry point, hand-integration) for DBEMT onward, since `vit integrate` cannot generate two-pass or custom I/O wrappers. |
