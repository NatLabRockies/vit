# PROJECT PLAN: AeroDyn Translation

## 1. Vision and Done Criteria

**Goal:** Translate OpenFAST's AeroDyn module from Fortran to C++ using VIT, producing a pure C++ AeroDyn module with no Fortran source files in the build and no libgfortran dependency.

**Target codebase:** AeroDyn — the aerodynamics module of OpenFAST (NREL). 39 Fortran source files, ~75,000 lines, 507 hand-written procedures + 740 Registry-generated procedures across 5 sub-systems (BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, core AeroDyn). Approximately 10× ROSCO's scope.

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
- Tier 2: NREL regression at OpenFAST's default tolerances, `rtol=2` and `atol=1.9` (relative 10⁻², absolute about 10⁻¹·⁹) — the correctness gate, 17/17

**Scope decisions:** dev note `202604232342`

## 2. Structural Inventory

Completed 2026-04-23. Data files in `data/`, beside this plan.

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
- `data/function_inventory.json` — 1,437 procedures with full metadata
- `data/call_graph.json` — 3,451 caller→callee edges
- `data/type_inventory.json` — 176 derived type definitions
- `data/module_dependencies.json` — 35 modules, topologically sorted
- `data/uncalled_procedures.json` — 67 uncalled, fully classified
- `data/framework_boundary.json` — 192 external API procedures
- `data/loc_metrics.json` — LOC breakdown
- `data/inventory_summary.md` — human-readable digest

**Key observations that shaped this plan:**
1. Nearly half (247/507) of hand-written procedures are leaf functions — translation can start at the edges
2. Registry-generated code is 38% of lines — the C++ Registry generator eliminates this wholesale
3. Only 1 procedure has SAVE state — no DLL persistence problem (unlike ROSCO)
4. External dependency boundary is clean — NWTC Library, InflowWind, SeaState, LinDyn are read-only data providers
5. FVW/OLAF is the largest sub-system (137 procs, 10,738 lines) and uses OpenMP

**Dev notes:** `202604232137` (inventory), `202604232243` (uncalled investigation), `202604232318` (uncalled refinement). The first inventory reported 210 procedures as dead code; classifying why each was uncalled (C-binding entry points, drivers, callbacks, framework interfaces) left 14.

**Regenerating the inventory.** The inventory tool is now VIT's generic `vit inventory` (it was `scripts/fortran_inventory.py`), so AeroDyn's specifics are passed as options: `--generated-marker STARTOFREGISTRYGENERATEDFILE`, and the 35 framework interface names as entry points (`AD_`, `BEMT_`, `UA_`, `FVW_`, `DBEMT_` and `AA_` with `Init`, `End`, `UpdateStates` and `CalcOutput`, plus `BEMT_ReInit`, `AFI_Init`, `AFI_End`, and `AD_CalcConstrStateResidual`, `AD_CalcContStateDeriv`, `AD_UpdateDiscState`, `AD_GetOP` and the four `AD_JacobianP*` routines). Given those, it reproduces the committed procedure inventory, call graph, types, module graph and line counts exactly (checked 2026-09-30 on clean source, `edab46311`). Two differences from the committed files: the key `is_registry_generated` is now `is_generated`, and C entry points are recognised by `BIND(C)`, so the 23 non-BIND(C) helpers in the C-binding module are no longer counted as external interface.

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

### KD7: Vectorization flag for sensitive feedback loops (committed 2026-04-28)

`-fno-tree-loop-vectorize` on DBEMT.f90. Replacing a PRIVATE function body with a C++ wrapper changes gfortran's vectorization decision for the caller's `exp()` loop (scalar libm vs SIMD libmvec, 1 ULP difference). The DBEMT exponential filter feedback loop amplifies this through 600 UA-coupled timesteps into catastrophic divergence (max_abs=5.73e+06). The flag ensures consistent scalar `exp()` regardless of wrapper insertion. Same root cause as the kernel verification libmvec issue (KD4/dev note `202604260357`). Other modules with similar sensitive feedback loops may need the same treatment.

**Dev note:** `202604281854`

### KD8: Auto-type infrastructure in `vit translate` (committed 2026-04-28)

`vit translate` auto-detects derived types with ALLOCATABLE fields, sets `strategy: view` in vit.yaml, writes/updates vit_types.h, and creates view populator .f90 files. No manual type prerequisites needed before translation. `vit integrate` auto-adds populator .f90 to CMakeLists.txt.

**Dev note:** `202604282134`

### KD9: Fixed-size arrays of view types (committed 2026-04-29)

VIT now handles `TYPE(Foo), DIMENSION(1:N) :: bar` where Foo has ALLOCATABLE fields and `strategy: view`. Emits `foo_view_t bar[N]` in C structs, `TYPE(foo_view_t) :: bar(N)` in BIND(C) mirrors, and populate/reverse-copy loops. Pattern appears 7 times across 4 AeroDyn modules (xdot history arrays in AB4/ABM4, u_DBEMT/u_SkewWake input arrays in BEMT). Unblocks DBEMT Phase B time integrators.

**Dev note:** `202604291527`

### KD10: Initialization functions (committed 2026-05-01; under revision)

Functions that ALLOCATE the components of an `INTENT(OUT)` derived type stay in Fortran during incremental translation. Translating `DBEMT_Init` needed a 60-line hand-written allocation wrapper for 25 lines of computation. VIT's `--auto-allocate`, built to generate that wrapper, did so by matching one codebase's coding style, and was rejected. `DBEMT_Init` and `AFI_Init` were reverted to Fortran.

**Exception:** `AFI_Init` was re-integrated on 2026-07-24 as an explicit hand-written special case (a two-pass C++ call with a Fortran allocation wrapper, as ROSCO's `ReadControlParameterFileSub`), which completed AirfoilInfo. `DBEMT_Init` remains Fortran.

**Under revision:** VIT's adopt path (`dc7e503`, 2026-09-24) generates these wrappers: the C++ allocates, and the generated Fortran wrapper adopts the allocation into the Fortran component. On the fork's `adopt-eval` branch, generated wrappers replaced the hand-written ones for `ReadAFfile` and `AFI_Init` with 17/17 baselines. That removes the cost this decision rested on. Next action (section 7): merge it, then decide whether `DBEMT_Init` is translated (OQ11).

**Dev notes:** `202605010708`, `202605010945`, `202605011207`, `202607240452`, `202609242010`

## 4. Open Questions

### OQ1: Registry generator feasibility [answered 2026-04-24]

**Answer:** Highly feasible. The Registry is a clean C++ code generator (~3,200 lines) with a modular architecture — separate generator files per output language (`registry_gen_fortran.cpp`, `registry_gen_c.cpp`). Adding `registry_gen_cpp.cpp` follows the exact same pattern. The data model (`registry.hpp`) already captures POINTER/ALLOCATABLE/deferred dims/nested types/C type mappings. A new `-cppcode` flag was added and generates `ModuleName_Types.hpp` headers. All 10 AeroDyn headers (2,666 lines, 140 structs) compile cleanly with g++ C++17.

### OQ2: KGen on AeroDyn [answered 2026-04-24]

**Answer:** Yes. KGen works on AeroDyn's cmake executable build chain. First successful extraction: Calculate_Cn from AirfoilInfo.f90, 20 cases captured from the `ad_BAR_SineMotion` test case. Required 12 VIT fixes for AeroDyn's multi-target cmake, preprocessor-controlled precision, Docker timestamps, and same-file caller/callee pattern. See dev notes `202604241004` (first extraction), `202604241051` (adaptation issues catalog).

### OQ3: NWTC Library interop surface [answered]

287 unique external callees. ~140 Registry boilerplate (eliminated by C++ generator), ~22 NWTC utilities, ~30 mesh-related, ~95 other. True interop wrapper count: ~150. Manageable — wrappers built incrementally per phase.

### OQ4: r-test coverage [answered 2026-04-25]

9 BEMT-path cases + 8 OLAF-path cases cover BEMT, FVW, UnsteadyAero, AirfoilInfo, DBEMT. **Not covered:** AeroAcoustics (separate driver), C-binding (called from C), linearization (requires full OpenFAST). gcov line coverage per test case is in `data/line_coverage.json` (generated 2026-04-25 from clean source: the fork at `c4cfa46e3`, with `AirfoilInfo.f90` restored from `edab46311`; until 2026-10-01 the file recorded `edab46311` for every file, so lookups in files that differ between the two commits were wrong) and is how extraction test cases are chosen (`vit find FunctionName --coverage data/line_coverage.json`; dev note `202604250803`). It covers 17 of the 18 r-test cases: `ad_MHK_RM1_Fixed_UA`, added later as the 17th baseline, is not in it.

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

### OQ11: Adopt path for initialization functions [investigating]

Merge the fork's `adopt-eval` branch (generated wrappers for `ReadAFfile` and `AFI_Init`) into `main`, and translate `DBEMT_Init` the same way?

**Why it matters:** decides whether KD10 stands, and whether DBEMT finishes at 12/14 or 13/14. **Information so far:** 17/17 baselines with the generated wrappers; red tests fail as they should; an AddressSanitizer build ran three cases clean. One known blind spot: a wrong lower bound on `SplineCoefs` passes the baselines, because no Fortran code indexes it; VIT's round-trip test guards it instead. **When:** the next action. **Owner:** the author. **Dev note:** `202609242010`

## 5. Phase Roadmap

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

### Phase 1: AirfoilInfo + DBEMT [complete, except the KD10 revisit]

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
- [x] AFI_Init (reverted to Fortran 2026-05-01 under KD10; re-integrated 2026-07-24 as an explicit special case, with CheckValuesAreUniqueMonotonicIncreasing as its own standalone unit; dev note `202607240452`): 17/17 baselines PASS + 11/11 test-validate PASS (C++ ≡ Fortran on all error branches). Two-pass orchestrator: pass 1 handles multi-table validation and secondVals computation (AFITable_2Re log(Re), AFITable_2User UserProp), pass 2 computes cubic spline coefficients via new CubicSplineInitM/CubicLinSplineInitM in vit_nwtc. Fortran wrapper allocates secondVals and SplineCoefs between passes. ColUAf overwrite bug caught and fixed during initial verification (pass 1 was resetting ColUAf=0, overwriting ReadAFfile's value). (dev notes `202604271433`, `202604271455`)
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
- [x] DBEMT_CalcOutput (35 lines, PUBLIC) — 20/20 IDENTICAL, 17/17 baselines PASS. Cross-module extraction from BEMT.f90:1215. ErrMsg blank-padding lesson: Fortran `ErrMsg=""` fills with spaces, C++ must `memset(errMsg,' ',ErrMsgLen)`. Recorded then in TRANSLATION_TIPS.md; now VIT's `character-padding` check. (dev note `202604291756`)
- [x] DBEMT_CalcContStateDeriv (44 lines, PUBLIC) — 20/20 IDENTICAL, 17/17 baselines PASS. First function with translated callee (ComputeTau2 via bridge). Surfaced 3 VIT bugs in callee bridge generation: (1) missing USE for inherited types, (2) no C++ callee detection on integrated source, (3) CONTAINS whitespace in PUBLIC patching. All fixed. (dev notes `202604291824`, `202604291859`)
- [x] **Phase A COMPLETE** — all 5 leaf functions integrated, 17/17 baselines PASS.

**DBEMT Phase B — Time Integrators (3 functions, ~241 lines, KGen verified):**

All three are ONLY exercised by `ad_BAR_SineMotion_UA4_DBEMT3` (Mode 3). Extraction must use this test case (DBEMT_Mod=3). `vit translate` will auto-generate DBEMT_OtherStateType and DBEMT_ContinuousStateType view structs when first encountered. The OtherStateType is the most complex DBEMT type — ALLOCATABLE arrays + fixed-size `xdot(1:4)` array of ContinuousStateType. The xdot field needs 4 embedded ContinuousState sub-views for the ABM4 history buffer.

Key decision: **`DBEMT_ElementInputType_ExtrapInterp` via production bridge** — a permanent BIND(C) wrapper (`vit_bridge_extrapinterp.f90`) calls the Fortran routine from C++. This avoids translating Registry-generated code and is verified transitively through kernel tests. The bridge handles assumed-shape array parameters (`u(:)` of derived type) via the derived-type-array infrastructure (dev note `202604292127`).

- [x] DBEMT_RK4 (100 lines, PRIVATE) — 4th-order Runge-Kutta. 4 CalcContStateDeriv evals + 3 ExtrapInterp calls. Self-starting. Test case: `ad_BAR_SineMotion_UA4_DBEMT3` (270 hits — startup only, n≤2). 20/20 KGen, 17/17 baselines. 7 VIT infrastructure fixes (derived-type-array support, case-insensitive view propagation, bridge ErrMsg copy-back, stale KGen cleanup). (dev notes `202604292127`, `202604292152`, `202604292220`)
- [x] DBEMT_AB4 (77 lines, PRIVATE) — Adams-Bashforth predictor. Uses RK4 for startup, then 4-level xdot history from `OtherState%xdot(1..4)%element(i,j)`. First C++-calls-C++ callee pattern. 20/20 KGen, 17/17 baselines. (dev note `202604292325`)
- [x] DBEMT_ABM4 (64 lines, PRIVATE) — Adams-Bashforth-Moulton predictor-corrector. Calls AB4 for prediction, then applies correction formula. 20/20 KGen, 17/17 baselines. KGen extraction required CHARACTER(*) length resolution fix in VIT. (dev notes `202604300017`, `202604300138`, `202604300149`)
- [x] Rebuild + verify 17/17 baselines

**DBEMT Phase C — Orchestrators (2 functions, ~85 lines) [COMPLETE]:**
- [x] DBEMT_UpdateStates (67 lines, PUBLIC, hot path) — 20/20 KGen, 17/17 baselines. Main entry called by BEMT per blade/node. Calls all 4 translated callees + inline finite difference math. VIT fixed for fixed-size view-type array parameters (`u(2)` pattern). Cross-module extraction from BEMT.f90:939 with ad_MHK_RM1_Fixed (DBEMT_Mod=2, 38,400 hits). `--reverse-copy` for OtherState scalars (tau1, tau2). (dev note `202604301041`)
- [x] DBEMT_InitStates_AllNodes (18 lines, PUBLIC) — 4/4 test-validate, 17/17 baselines. Double loop calling DBEMT_InitStates per node. Zero gcov coverage (Jacobian path, DBEMT guard never true). Verified via custom Fortran test harness: dual-call comparison (C++ vs Fortran) on 4 scenarios (all-uninitialized 3×5, partial-init, continuous mode DBEMT_Mod=3, 1×1 edge case).
- [x] Rebuild + verify 17/17 baselines

**DBEMT Phase D — Init/Validation/End (2 translated, 1 kept in Fortran, 1 skipped):**
- [x] DBEMT_ValidateInitInp (49 lines, PRIVATE) — 9/9 test-validate (8 error branches: interval, DBEMT_Mod, numBlades, numNodes, tau1_const, rLocal allocated, rLocal values, max rLocal), 17/17 baselines. ALLOCATABLE-aware test-validate bridge. (dev note `202605010447`)
- [x] DBEMT_ReInit (45 lines, PUBLIC) — 4/4 custom Fortran test harness, then 4/4 KGen once extracted at its call site in BEMT, not its definition; 17/17 baselines. (dev notes `202605010526`, `202605010623`)
- [ ] DBEMT_Init (127 lines, PUBLIC) — **integrated and reverted on 2026-05-01; stays Fortran under KD10.** Revisit with the adopt path (OQ11). Original plan: Contains ALLOCATABLE operations (allocate u%element, p%spanRatio, x%element, OtherState%n, areStatesInitialized). Two-pass architecture if needed (same pattern as AFI_Init). Calls ValidateInitInp + ReInit. **Translate last; could remain Fortran if time-constrained** — allocated arrays are visible through view structs regardless, and the key algorithmic content (spanRatio computation) is 10 lines.
- [ ] ~~DBEMT_End (47 lines, PUBLIC)~~ — **DO NOT TRANSLATE.** Calls only Registry-generated Destroy routines (DBEMT_DestroyInput, DestroyParam, DestroyContState, DestroyMisc, DestroyOtherState). Framework cleanup, not algorithmic. Fortran continues to own deallocation of Fortran-allocated memory.
- [x] Rebuild + verify 17/17 baselines. **DBEMT: 12/14 in C++** — DBEMT_Init kept in Fortran (KD10), DBEMT_End skipped. 13/14 if OQ11 translates DBEMT_Init.

**DBEMT Key Decisions:**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| ExtrapInterp | Production bridge (`vit_bridge_extrapinterp.f90`) | BIND(C) wrapper calls Fortran; avoids translating Registry-generated code; verified transitively |
| DBEMT_End | Do not translate | Only calls Registry Destroy; C++ should not own Fortran deallocation |
| DBEMT_Init | Kept in Fortran (KD10), after a translation was integrated and reverted on 2026-05-01 | Allocation-heavy; runs once; arrays visible through views regardless. Under revision (OQ11) |
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

**Revisit trigger:** Phase D complete except DBEMT_Init (KD10). Next: the adopt path (OQ11), then Phase 2.

**Phase A status (2026-04-29): COMPLETE.** All 5 leaf functions integrated (ComputeTau2, ComputeTau1, DBEMT_InitStates, DBEMT_CalcOutput, DBEMT_CalcContStateDeriv). 11 VIT bugs fixed during DBEMT Phase A. Infrastructure proven: all DBEMT view structs, error handling, auto-type, fixed-size view arrays (KD9), callee bridges for view-type parameters, C++ callee detection on integrated source.

**Dev notes:** `202604241004` (first extraction), `202604241026` (precision mismatch), `202604241051` (VIT adaptation issues), `202604241122` (first verification), `202604241337` (FMA confirmed), `202604241400` (first full verification), `202604241415` (full pipeline proven), `202604241459` (four infrastructure fixes), `202604241905` (derived type functions proven), `202604251125` (cross-module instrumentation), `202604251317` (kernel repair), `202604251331` (Calculate_C_alpha complete), `202604251425` (view reverse-copy fix), `202604251451` (three functions + two fixes), `202604251503` (auto-callee declarations), `202604251821` (onCl + callee bridge re-sync + integrate_all.sh), `202604251943` (vit_nwtc.h + AttachedFlow), `202604270158` (ReadAFfile in-progress), `202604270219` (ReadAFfile approach pivot), `202604270333` (ReadAFfile integration — three root causes), `202604270354` (ReadAFfile reflection + path forward), `202604271433` (AFI_Init integration — two-pass orchestrator + CubicSplineInitM), `202604271455` (AFI_Init test-validate — 11/11 multi-table validation), `202605010447` (ALLOCATABLE-aware test-validate bridges), `202605010526` (DBEMT_ReInit), `202605010623` (KGen pragma at the call site), `202605010708` / `202605010945` / `202605011207` (DBEMT_Init, `--auto-allocate`, and the revert), `202607240452` (AFI_Init special case), `202609242010` (adopt path)

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

**Confidence:** speculative — NOT covered by the r-test cases. Needs its own verification strategy (separate `aeroacoustics_driver` or new test cases).

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
| Registry investigation complete (held 2026-04-24: viable) | After Phase 0 investigation | Is the Registry generator approach viable? Pivot to standalone generator or Fortran-with-wrappers if not |
| KGen/VIT validation (held 2026-04-24: works) | After Phase 1 first extraction | Does KGen work on AeroDyn? Is VIT adequate? This is the "prove it works" gate |
| BEMT path complete | After Phase 3 | Re-examine FVW/OLAF sub-phasing. 137 procedures may need finer granularity |
| AeroAcoustics verification | Before Phase 5 | How to verify without r-test coverage? Design test strategy before translating |
| Full OpenFAST build | Before Phase 6 | Build full OpenFAST for coupled-mode extraction. May surface new challenges |
| Quarterly | Every 3 months | Upstream sync check. Is NREL's AeroDyn changing significantly? Merge needed? Not yet held (OQ9 open) |
| Adopt path merged | After OQ11 | Does KD10 stand? Translate `DBEMT_Init`? Do later initializers (`UA_Init` in Phase 2, `BEMT_Init` in Phase 3) follow the same path? |

## 7. Current State

**Date:** 2026-09-30

**Status:** Phase 0 complete. Phase 1 complete except the initialization-function revisit (KD10, OQ11). The fork's `main` (`db0796299`) has 32 functions integrated, with 17/17 baselines passing at the last integration (2026-07-24):
- **AirfoilInfo: 20/20.** `AFI_Init` is an explicit hand-written special case; `CheckValuesAreUniqueMonotonicIncreasing` is its own unit.
- **DBEMT: 12/14.** Phases A–C, and Phase D's `DBEMT_ValidateInitInp` and `DBEMT_ReInit`. `DBEMT_Init` stays Fortran (KD10); `DBEMT_End` is skipped.

**Since 2026-04-30:**
- 2026-05-01: Phase D. `DBEMT_Init` was integrated, first with a hand-written wrapper and then with `--auto-allocate`; both were reverted with `AFI_Init` the same day, and KD10 was decided.
- 2026-07-24: `AFI_Init` re-integrated as a special case.
- 2026-08-10: the fork's `TRANSLATION_TIPS.md` deleted. The translation prompt's guidance is now generated from VIT's check registry (`vit check --list`), and the callable shared functions are read from the shared headers.
- 2026-09-24: VIT's adopt path; evaluated on the fork's `adopt-eval` branch (`f961dab51`), 17/17 baselines with generated wrappers for `ReadAFfile` and `AFI_Init`. Not merged.

No AeroDyn function has been integrated since 2026-07-24.

**Next action:**
1. Merge `adopt-eval` into the fork's `main`, replacing the hand-written `ReadAFfile` and `AFI_Init` wrappers with generated ones; rerun the 17 baselines.
2. Decide OQ11: translate `DBEMT_Init` through the adopt path, finishing DBEMT at 13/14, or keep KD10 as it stands.
3. Start Phase 2: BEMTUncoupled and UnsteadyAero.

## 8. History

Detailed change history is in dev notes (this repository's `dev/YYYYMMDDHHMM-descriptor.md`). Key dev notes by phase:

**Phase 0:** `202604240609` (Registry generator), `202604240622` (retrospective)

**Phase 1 — AirfoilInfo:** `202604241004` (first extraction), `202604241905` (derived types), `202604251943` (vit_nwtc.h), `202604260357` (libmvec vectorization), `202604261142` (test-validate audit), `202604270333` (ReadAFfile root causes), `202604271433` (AFI_Init two-pass)

**Phase 1 — DBEMT Phase A/B:** `202604281854` (ComputeTau2 integration — operator precedence + vectorization fix), `202604281859` (KGen coverage gap), `202604281913` (the case for KGen), `202604282039` (ComputeTau1 + 3 VIT bugs), `202604282134` (auto-type infrastructure), `202604282138` (session reflection), `202604291005` (DBEMT_InitStates + 3 VIT fixes), `202604291527` (fixed-size view type arrays enhancement), `202604291756` (CalcOutput + ErrMsg blank-padding), `202604291824` (Phase A complete), `202604291831` (session reflection), `202604291859` (callee bridge fixes — 3 root causes)

**Phase 1 — DBEMT Phase C:** `202604301041` (UpdateStates integration — fixed-size view-type array params), `202604301103` (repair loop fix — off-by-one + idempotency), `202604301834` (InitStates_AllNodes — zero-coverage test harness, Phase C complete), `202604301844` (Phase C reflection), `202604301926` (Phase C audit — bridge truncation fix)

**Phase 1 — DBEMT Phase D and initialization functions:** `202605010447` (ALLOCATABLE-aware test-validate bridges), `202605010526` (DBEMT_ReInit + KGen failure), `202605010623` (pragma at the call site), `202605010708` (DBEMT_Init hand-written wrapper), `202605010945` (`--auto-allocate`), `202605011207` (revert; KD10), `202607240452` (AFI_Init special case), `202609242010` (adopt path)

**This plan:** Current State was not updated between 2026-04-30 and 2026-09-30, while Phase D, the KD10 revert and the `AFI_Init` re-integration happened; the 2026-09-30 update brought it current. The Change Log the planning method prescribes was replaced on 2026-04-28 by this section's dev-note references, because a log of every session duplicated the dev notes and was never complete.

### Milestones to 2026-04-30
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
- [x] Phase B: DBEMT_RK4 integrated — 20/20 KGen, 17/17 baselines. First Phase B time integrator. 7 VIT infrastructure fixes: derived-type-array support (5 fixes in interface_gen.py), case-insensitive view strategy propagation, bridge ErrMsg copy-back. Production bridge pattern for ExtrapInterp. Stale KGen artifacts cleaned from NWTC_Base.f90. AbortErrLev constant added to vit_nwtc.h. (dev notes `202604292127`, `202604292152`, `202604292220`)
- [x] Shared utilities refactor — SetErrStat extracted from per-file lambdas to shared function in vit_nwtc.h. vit_fortran_intrinsics.h created with fortran_minloc/maxloc/cshift templates, replacing 14 inline loops across 5 files. (dev note `202604292256`)
- [x] Phase B: DBEMT_AB4 integrated — 20/20 KGen, 17/17 baselines. First C++-calls-C++ callee (RK4 from AB4). First xdot[4] history access in translation. (dev note `202604292325`)
- [x] Phase B: DBEMT_ABM4 integrated — 20/20 KGen, 17/17 baselines. KGen kernel crashed due to CHARACTER(*) length mismatch (4096 vs 8196). Root cause: VIT hardcoded CHARACTER buffer length; state data used ErrMsgLen. Fix: per-variable length resolution tracing kernel local → enclosing function parameter → caller's actual argument → resolved constant from source tree. (dev notes `202604300017`, `202604300138`, `202604300149`)
- [x] **DBEMT Phase B COMPLETE** — all 3 time integrators translated and integrated. 20 VIT bug fixes total.
- [x] Phase C: DBEMT_UpdateStates integrated — 20/20 KGen, 17/17 baselines. Hot-path orchestrator calling all 4 translated callees + inline finite difference math. VIT fixed for fixed-size view-type array parameters (`u(2)` pattern, 6 changes in interface_gen.py). Repair loop off-by-one fixed (non-idempotent `_fix_undefined_symbol` + unbounded loop). ExtrapInterp bridge CHARACTER buffer truncation fixed (1024 → 8196). (dev notes `202604301041`, `202604301103`, `202604301926`)
- [x] Phase C: DBEMT_InitStates_AllNodes integrated — 4/4 test-validate, 17/17 baselines. Zero gcov coverage (Jacobian path only). Verified via custom Fortran test harness with dual-call comparison. New verification approach for zero-coverage functions with ALLOCATABLE types. (dev note `202604301834`)
- [x] **DBEMT Phase C COMPLETE** — both orchestrators translated and integrated. 22 VIT bug fixes + 4 infrastructure enhancements total across DBEMT.


## 9. Deferred Technical Items

Items that are safe in the current state but need attention before or during a specific future phase. Each entry names the trigger (when it must be resolved) and the consequence of not resolving it.

| Item | Location | Current State | Trigger | Consequence if Ignored |
|------|----------|--------------|---------|----------------------|
| **vit_wr_stashed_filename SAVE variable** | AirfoilInfo.f90 line 48 | Module-level SAVE variable passes filename from AFI_WrHeader to AFI_WrData. Works because callers (UnsteadyAero `UA_WriteAFIParamsToFile`) are single-threaded and follow a strict WrHeader→WrData×N→close sequence. | Phase 2 (UnsteadyAero translation) or any multi-threaded caller | Second concurrent WrHeader call overwrites the stashed filename, causing WrData to write to the wrong file. **Fix:** Pass filename through WrData's interface directly, or eliminate the stash when callers are translated to C++. |
| ~~**CheckValuesAreUniqueMonotonicIncreasing dual existence**~~ | — | **Resolved 2026-07-24:** the C++ is a standalone unit, `checkvaluesareuniquemonotonicincreasing.cpp`, and `afi_init.cpp` calls it instead of an inlined copy (dev note `202607240452`). | — | — |
| **NWTC_Init required for R2D/D2R in test bridges** | All file-output and test-validate Fortran bridges | `R2D`, `D2R`, `Pi` etc. are runtime-initialized module variables in NWTC_Library, not compile-time PARAMETERs. Test bridges must call `NWTC_Init()` before using Fortran functions that depend on these constants. | Any new test-validate or file-output bridge | Fortran output uses R2D=0.0, producing silently wrong results. **Fix:** Always include `CALL NWTC_Init()` in bridge setup. Documented in the AeroDyn operating manual (`CLAUDE.md`) since 2026-09-30. |
| **Two integration conventions in translations/** | `translations/AirfoilInfo/` vs `modules/aerodyn/src/` | 9 early functions (Calculate_Cn through ComputeUASeparationFunction_onCl) went through `vit integrate`, so `translations/` holds the raw scaffold and `src/` has the VIT-generated wrapper + `extern "C"` shim. 10 later functions (AFI_ComputeAirfoilCoefs onward) were hand-integrated because they needed custom Fortran wrappers (two-pass architecture for AFI_Init/ReadAFfile, I/O interop for Wr*), so `translations/` = `src/` with the `_c` entry point defined directly. Both patterns work correctly. | DBEMT and future modules | No consequence — informational only. **What happened:** `integrate_all.sh` runs `vit integrate` for 10 of DBEMT's 12 functions; ComputeTau1 and ComputeTau2 are restored from committed files instead. The adopt path (OQ11) generates the allocation wrappers that forced hand-integration, so `vit integrate` can be the default for new modules. |
