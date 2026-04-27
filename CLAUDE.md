# CLAUDE.md

## What This Project Is

This workspace contains three tools that work together to enable verified Fortran→C++ translation in scientific codebases.

**VIT (Verified Incremental Translator)** is the tool we are building. It orchestrates the extraction of runtime state from a Fortran function during a real simulation, wires a user-provided C++ translation into a standalone verification kernel, runs bit-identical verification against the captured state, and generates integration patches to replace the Fortran with C++ in the real codebase. VIT does NOT translate Fortran to C++ — it verifies and integrates translations written by humans or LLMs.

**KGen** (NCAR) is an existing third-party tool that instruments Fortran code to capture function inputs and outputs during a simulation run, then generates a standalone "kernel" that can replay those calls. VIT wraps KGen and patches its generated code. KGen requires Python 2.7.

**ROSCO (completed, April 2026)** was the first test case for VIT. It is a wind turbine controller written in Fortran; ROSCO's Python simulation loads a compiled Fortran shared library (`libdiscon.so`) and runs a controller loop. VIT verified and integrated 52 ROSCO functions plus a C++ DISCON entry point — from simple math (`saturate`, `wrap_180`) through filters (`LPFilter`, `NotchFilter`, `PreFilterMeasuredSignals`) to complex controllers (`PitchControl`, `WindSpeedEstimator`), I/O (`ReadAvrSWAP`, `ReadControlParameterFileSub`), and orchestrators (`SetParameters`). Verification covers **27 simulation scenarios × 13 output arrays per scenario ≈ 5.25M values compared** (~2.56M non-trivial) across a three-build chain (**upstream Fortran ≡ modified Fortran ≡ pure C++**, three distinct `libdiscon.so` binaries). On the `integrated` branch, ROSCO is a **pure C++ shared library** — all 16 Fortran source files removed from the build, no libgfortran dependency. C++ runs ~25% faster than Fortran on ARM64. One known tolerated difference: 3 values at Scenario 16 `bld_pitch*` index 4444 differ by `8.47e-22 rad` (single-root-cause gfortran code-generation artifact, not a translation bug). The ROSCO workspace is at `vit_translation/`; `PROJECT_PLAN_ROSCO.md` captures the full phase history.

**OpenFAST / AeroDyn (active, April 2026–)** is the current translation target. AeroDyn is the aerodynamics module of OpenFAST (NREL's wind turbine simulation framework): 42 Fortran source files totaling ~75,000 lines (~507 hand-written procedures + 740 Registry-generated), roughly 10× ROSCO's scope. Work happens in the sibling workspace `vit_translation_openfast/`, which has its own independent clones of VIT, KGen, and OpenFAST — separate from the ROSCO workspace so experimental VIT changes for AeroDyn cannot contaminate the completed ROSCO work. The structural inventory is complete (`vit/analysis/aerodyn/`), scope decisions are locked, and `PROJECT_PLAN_AERODYN.md` is being drafted following the methodology in `CREATING_PROJECT_PLANS.md`. See dev note `202604140013-openfast-aerodyn-phase-start.md` for the kickoff record and `202604232342-aerodyn-scope-decisions.md` for scope decisions.

## Project Status

### ROSCO — complete (April 2026)

- **Phase 11 complete** on the `integrated` branch: pure C++ `libdiscon.so`, all 16 Fortran source files removed from `CMakeLists.txt`, confirmed by `ldd libdiscon.so` (no `libgfortran.so` — only `libstdc++`, `libm`, `libgcc_s`, `libc`).
- **All 52 translatable functions verified and integrated.** 42 via KGen kernel verification at float64. 10 Stage B/C/D/E functions (file I/O, config parsing, platform modules, orchestrators) verified via 27-scenario simulation comparison at float32 (the avrSWAP deployment precision).
- The ROSCO workspace's `main` branch intentionally keeps Fortran source files on disk so extract/verify/integrate cycles remain reproducible (`reset_to_clean.sh` checks them out of git for each cycle). See **Git → ROSCO branches (historical reference)** below for how `main` and `integrated` relate.
- `PROJECT_PLAN_ROSCO.md` in the VIT repo has phase-by-phase detail with completed checkboxes.

### OpenFAST / AeroDyn — active (April 2026–)

- **Workspace:** `vit_translation_openfast/` — sibling to the ROSCO workspace, with independent git clones of VIT, KGen, and OpenFAST (no symlinks). Both workspace VIT clones share the same `main` branch on the `NatLabRockies/vit` remote.
- **Target:** translate all of AeroDyn to C++ — 507 hand-written procedures across 46,803 lines, plus a C++ code generator for the OpenFAST Registry (740 generated procedures / 28,490 lines), plus drivers and C-binding layer. End state: pure C++ AeroDyn module, no Fortran source, no libgfortran dependency. 42 Fortran source files, ~75,000 total lines.
- **Current stage:** **Phase 1 in progress — 17 AirfoilInfo functions integrated** (including AFI_Init orchestrator with two-pass multi-table validation + spline initialization, CalculateUACoeffs orchestrator, AFI_ValidateInitInput via test-validate, CheckValuesAreUniqueMonotonicIncreasing via test-validate, AFI_ComputeAirfoilCoefs with ALLOCATABLE Table array marshalling, AFI_ComputeUACoefs with angle-aware 2D interpolation, and ReadAFfile with two-pass idiomatic file parser). Full VIT pipeline proven. Phase 0 (Registry C++ generator) complete. Shared NWTC utility library (`vit_nwtc.h/.cpp`) with EqualRealNos, fZeros, InterpExtrapStp, InterpStp, kernelSmoothing, MPi2Pi, LocateBin, CubicSplineInterpM, CubicSplineInitM, CubicLinSplineInitM, AddOrSub2Pi, Angles_ExtrapInterp1. All infrastructure mature: view reverse-copy, callee bridges with INOUT re-sync + pre-call flush + C++ auto-detection, kernel repair, LOGICAL type support, array-returning function bridges, ALLOCATABLE TYPE() array marshalling, LBOUND fix for 0-based dimensions, test-validate for validation/FUNCTION return types, two-pass file I/O with keyword-checked ParseVar semantics. See "AeroDyn Workflow" section below.
- **Scope decisions (committed 2026-04-23, dev note `202604232342`):**
  1. **Breadth:** All of AeroDyn — all 5 sub-systems (BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, core AeroDyn), all 507 hand-written procedures
  2. **Registry types:** Build a C++ code generator for the OpenFAST Registry (replaces 740 auto-generated Fortran procedures)
  3. **Drivers/C-binding:** Translate everything — full C++, no Fortran remaining
  4. **Verification:** KGen float64 kernel verification (physics functions) + `vit test-validate` branch-coverage dual-call tests (validation functions) + 17-case simulation bit-identical + NREL regression 17/17
  5. **Done criteria:** Pure C++ AeroDyn module — no Fortran source in the build, no libgfortran, 17/17 regression pass, AeroDyn Driver functional, C-binding preserved
- **Key architectural decisions (committed 2026-04-24, dev note `202604240012`):**
  1. **Type strategy:** Registry generator for Registry types, VIT `analyze-types` for hand-written types (clean tool separation)
  2. **KGen extraction:** Both AeroDyn Driver (primary, 16 r-test cases) AND full OpenFAST (for coupled-mode-only functions like linearization)
  3. **Phasing:** Bottom-up by dependency layer — Registry generator (Phase 0) → leaf modules → mid-level → orchestrators → drivers/C-binding (Phase 7)
  4. **FMA:** `-ffp-contract=off` during translation, re-enable on pure C++ build (same as ROSCO)
  5. **NWTC Library:** Stays Fortran at the module level (shared framework, separate project to translate). Individual utility functions used by AeroDyn (EqualRealNos, fZeros, InterpExtrapStp) are translated to C++ in `vit_nwtc.h` — a shared header included by all AeroDyn translations. This prepares for pure-C++ AeroDyn while providing transitive verification via KGen kernel comparison.
  6. **External modules:** InflowWind, SeaState, LinDyn stay Fortran, accessed via C interop (read-only data providers)
- **Translation boundary:** Everything inside AeroDyn → C++. Everything outside (NWTC Library, InflowWind, SeaState, LinDyn, OpenFAST glue code) → stays Fortran, crossed via BIND(C) / view structs. Exception: individual NWTC utility functions used by AeroDyn are translated to C++ in `vit_nwtc.h` (see KD5 and "Shared NWTC utility header" below).
- **Open questions (10, dev note `202604240016`):** **Answered:** OQ1 (Registry feasible, dev note `202604240609`), OQ2 (KGen works on AeroDyn cmake, dev note `202604241004`), OQ3 (287 external callees, ~150 wrappers), OQ5 (OpenMP simple parallel DO), OQ6 (5 POINTERs, all non-owning, dev note `202604240609`), OQ7 (MeshType view struct ~80 lines, not needed until Phase 4, dev note `202604240640`). **Answered:** OQ4 (r-test coverage — gcov line coverage infrastructure provides definitive per-line data across all 17 test cases; `query_coverage.py --call-sites` is the standard extraction workflow). **Answered:** OQ8 (VIT on AeroDyn — 20+ fixes applied, derived types proven across 14 functions including ALLOCATABLE TYPE() array marshalling). **Open:** OQ9 (upstream sync), OQ10 (full OpenFAST build).
- **Structural inventory findings (dev note `202604232137`):** 507 hand-written top-level procedures, 247 leaf functions (first translation targets), 5 semi-independent sub-systems, 176 derived types (150 AeroDyn + 26 NWTC deps), 14 genuinely dead procedures (660 lines, excluded from scope), 3 confirmed framework interface procedures, 10 C-binding entry points, only 1 procedure with SAVE state. External dependencies: NWTC Library (framework), InflowWind, SeaState, LinDyn.
- **Verification infrastructure:** 17 AeroDyn regression test cases with committed baselines (`openfast/baselines/aerodyn/`), regenerated 2026-04-24 with `-ffp-contract=off` (16 original + `ad_MHK_RM1_Fixed_UA` added 2026-04-26 for 2D UA interpolation coverage). Three verification scripts: `verify_aerodyn_baselines.sh` (bit-identical gate), `run_nrel_regression.sh` (NREL 1e-2 correctness gate), `check_aerodyn_nrel_drift.sh` (informational drift reporter). **After integrating any function, run `verify_aerodyn_baselines.sh all` to confirm 17/17 pass.** New baselines must be generated from clean Fortran (not from the integrated C++ build — see dev note `202604262336`).
- **Next step:** Remaining AirfoilInfo (AFI_WrTables/WrHeader/WrData — ~303 lines, debug output functions) or DBEMT module (14 procedures, mostly leaf math). Seventeen functions integrated and verified: Calculate_Cn, FindBoundingTables, Compute_iLoweriUpper, ComputeUA360_CnOffset, Calculate_C_alpha, ComputeUASeparationFunction_zero, ComputeUA360_updateCnSeparated, ComputeUA360_updateSeparationF, ComputeUASeparationFunction_onCl, ComputeUA360_AttachedFlow, CalculateUACoeffs (all 20/20 kernel + 17/17 baselines), AFI_ValidateInitInput (7/7 test-validate + 17/17 baselines), CheckValuesAreUniqueMonotonicIncreasing (11/11 test-validate + 17/17 baselines), AFI_ComputeAirfoilCoefs (20/20 kernel + 17/17 baselines — first Table array marshalling), AFI_ComputeUACoefs (20/20 kernel + 17/17 baselines — 2D UA interpolation), ReadAFfile (17/17 baselines — two-pass file parser, keyword-checked ParseVar semantics), AFI_Init (17/17 baselines — two-pass orchestrator with CubicSplineInitM/CubicLinSplineInitM in vit_nwtc). See `PROJECT_PLAN_AERODYN.md` Phase 1 for full scope.
- **Methodology reference:** `CREATING_PROJECT_PLANS.md` — read this before drafting new plan documents.

### AeroDyn Workflow

VIT configuration is at `openfast/vit.yaml`. Key differences from ROSCO:
- `source_dir: modules` — broad, so KGen can find NWTC Library sources for USE resolution
- `fortran.flags: -cpp -fdefault-real-8 -fdefault-double-8 -ffp-contract=off` — matches the OpenFAST project build
- `fortran.preprocessor_defines: [OPENFAST_DOUBLE_PRECISION, HAS_FORTRAN2008_FEATURES]` — passed as `-D` flags to KGen so it resolves `#ifdef` correctly
- `build_system.cmake_file: modules/aerodyn/CMakeLists.txt` — VIT auto-detects which `add_library` target contains the source file
- `shared_files:` — lists shared headers/sources (e.g., `vit_nwtc.h`, `vit_nwtc.cpp`, `vit_aerodyn_constants.h`) copied to all kernel directories during verification. VIT searches for these by filename starting from the translation directory and the project source tree.

**Translation workflow (per function):**

```bash
# 1. Extract (inside vit-dev-openfast container, from /workspace/openfast)
#    Requires clean source — run reset_to_clean.sh first if source has integration wrappers
vit extract FuncName -f modules/aerodyn/src/File.f90 -l LINE

# 2. Generate scaffold (on clean or integrated source — detects callees from original Fortran)
vit translate FuncName -f modules/aerodyn/src/File.f90 --module ModuleName
#    The prompt shows already-translated callee _c signatures automatically.
#    Do NOT add manual extern "C" declarations — VIT handles these.

# 3. Write the C++ implementation in the generated scaffold

# 4. Verify (20/20 IDENTICAL expected)
vit verify FuncName translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90 --kernel-dir kernel/FuncName

# 5. Integrate (auto-patches source .f90 AND CMakeLists.txt)
#    Use --reverse-copy when the function modifies scalar fields in view-type INOUT args
vit integrate FuncName translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90 --apply [--reverse-copy]

# 6. Rebuild
cd build && cmake .. && cmake --build . --target aerodyn_driver -j$(nproc)

# 7. Regression test (17/17 must pass)
cd /workspace/openfast && ./scripts/verify_aerodyn_baselines.sh all
```

**Callee declarations:** When a translation calls other already-translated functions (e.g., `computeua360_cnoffset_c`), the declarations are provided automatically. During kernel verification, VIT generates `vit_kernel_callees.h`. During integration, VIT auto-includes `vit_translated.h` in the production `.cpp` wrapper. Translators write pure algorithm code — no `extern "C"` boilerplate needed.

**`--reverse-copy` flag:** Use when the function modifies scalar fields in view-type INOUT arguments (e.g., `p%UA_BL.alphaBreakUpper`). VIT generates the reverse-copy subroutine in both kernel verification and production wrappers. Not needed when the function only modifies ALLOCATABLE arrays (those are visible through `C_LOC` pointers). VIT also auto-enables reverse-copy during kernel verification for all INOUT view-type arguments.

### Test-Validate Workflow (Validation Functions)

Validation functions (e.g., `AFI_ValidateInitInput`) check input parameters and report errors via `SetErrStat`. KGen kernel verification only exercises the happy path (valid inputs → ErrStat=0), leaving all error branches untested. `vit test-validate` is the alternative verification path for these functions.

**When to use:** Functions whose interesting behavior is error-branch coverage, not floating-point arithmetic. Typically functions named `Validate*`, `Check*`, or functions that only call `SetErrStat` with no math.

**How it works:** Prompt-based dual-call testing. VIT extracts raw materials (Fortran source, C++ translation, struct definitions), generates a test harness skeleton and a Fortran BIND(C) bridge, and assembles a prompt. The LLM writes branch-coverage tests using the prompt. Each test calls both the C++ translation and the original Fortran (via the bridge) and asserts they produce the same `ErrStat` — the same C++ ≡ Fortran principle as KGen kernel verification.

**What VIT generates:**
- **Test skeleton** (`<name>_test.cpp`) — boilerplate: includes, TestResult struct, main runner, extern "C" bridge declaration. LLM fills in `make_valid_input()` and per-branch test functions.
- **Fortran bridge** (`<name>_bridge.f90`) — BIND(C) subroutine that receives derived types as `TYPE(C_PTR)` (converted via `C_F_POINTER`), handles CHARACTER args locally (only ErrStat returned to C++). Module USE statements derived from source file scan (no hardcoded conventions).
- **Patched source** — if the function is PRIVATE, a local copy with `PUBLIC :: FuncName` added. Compiled locally to produce a `.mod` that overrides the production `.mod` for bridge compilation only.
- **Makefile** — compiles patched source + bridge + test, links against production `.a` libraries (derived from CMake target's `link.txt`). No project rebuild needed.

**Workflow:**
```bash
# 1. Translate (generates scaffold + prompt)
vit translate FuncName -f modules/aerodyn/src/Source.f90 --module ModuleName

# 2. Write the C++ implementation

# 3. Generate test harness + prompt (does NOT overwrite existing test file)
vit test-validate FuncName translations/Module/funcname.cpp \
  -f modules/aerodyn/src/Source.f90 --module ModuleName

# 4. Write test implementations using the prompt

# 5. Build and run tests (inside container)
vit test-validate FuncName translations/Module/funcname.cpp \
  -f modules/aerodyn/src/Source.f90 --module ModuleName --run

# 6. Integrate + rebuild + baselines (same as KGen path)
vit integrate FuncName translations/Module/funcname.cpp \
  -f modules/aerodyn/src/Source.f90 --apply
cd build && cmake --build . --target aerodyn_driver -j$(nproc)
cd /workspace/openfast && ./scripts/verify_aerodyn_baselines.sh all
```

**File overwrite protection:** Re-running `vit test-validate` skips the test file if it contains real implementations (no `// --- TODO:` markers). Use `--force` to overwrite. Bridge, Makefile, and patched source are always regenerated (they're deterministic).

**Test storage:** `translations/<Module>/<name>_test.cpp`, alongside the translation, bridge, and Makefile. Same directory convention as KGen kernel fixtures.

**Shared NWTC utilities (`vit_nwtc.h` + `vit_nwtc.cpp`):** C++ translations of NWTC_Library functions used by AeroDyn translations. Located at `modules/aerodyn/src/`. Declarations in `vit_nwtc.h`, implementations compiled once in `vit_nwtc.cpp`. All translations `#include "vit_nwtc.h"` instead of inlining NWTC functions or constants. VIT automatically copies both files to kernel directories during verification and adds `vit_nwtc.o` to the kernel Makefile.

Contents:
- **Constants** (in header, `static constexpr`): `Pi`, `TwoPi`, `PiBy2`, `D2R`, `R2D`
- **`EqualRealNos(a, b)`** — epsilon-based relative tolerance comparison (from `EqualRealNos8` in NWTC_Num.f90)
- **`fZeros(x, f, n, roots, roots_size, nZeros, has_period, period)`** — zero-crossing finder with optional periodic wraparound (from `fZero_R8`). Arrays are 0-based. `has_period`/`period` replace the Fortran OPTIONAL parameter.
- **`InterpExtrapStp(XVal, XAry, YAry, Ind, AryLen)`** — linear interpolation/extrapolation with stepping index search (from NWTC_Num.f90). `Ind` is **1-based** (Fortran convention) — caller initializes to 1. Arrays are 0-based.
- **`InterpStp(XVal, XAry, YAry, Ind, AryLen)`** — linear interpolation with stepping (no extrapolation, clamps to endpoint values). Same calling convention as InterpExtrapStp.
- **`kernelSmoothing(x, f, n, bw, kernelType, xs, fs, ns)`** — kernel density smoothing with configurable kernel type (Epanechnikov, Quartic, Biweight, Triweight, Tricube, Gaussian). Constants: `kernelType_EPANECHINIKOV` through `kernelType_GAUSSIAN`.
- **`MPi2Pi(angle)`** — normalize angle to [-Pi, Pi]. Inline (header-only). Uses `fmod` + conditional adjustment to match Fortran `MODULO` semantics.
- **`LocateBin(XVal, XAry, AryLen)`** — binary search returning 1-based lower-bound index. Returns 0 if below range, AryLen if above. Arrays are 0-based in C++.
- **`CubicSplineInterpM(X, XAry, YAry, Coef, Res, NumPts, nCols, nCoefRows)`** — multi-column cubic spline interpolation. `nCoefRows` is the first dimension of the Coef array — typically `NumPts-1` (one fewer than knot points, because splines have N-1 segments for N knots). Column-major layout for both YAry and Coef. **Critical:** always pass the actual Coef dimension, not NumPts — mismatching causes shifted interpolation results.
- **`CubicSplineInitM(XAry, YAry, Coef, NumPts, NumCrvs, errStat*, errMsg*)`** — compute natural cubic spline coefficients for irregularly-spaced data with multiple curves. Custom Gaussian elimination on tridiagonal system (no LAPACK). Output Coef has same 3D column-major layout as CubicSplineInterpM: `[nCoefRows x NumCrvs x 4]` where nCoefRows = NumPts-1. From `CubicSplineInitM` in NWTC_Num.f90.
- **`CubicLinSplineInitM(XAry, YAry, Coef, NumPts, NumCrvs, errStat*, errMsg*)`** — compute linear spline coefficients in cubic format (c2=c3=0). Same array layouts as CubicSplineInitM. From `CubicLinSplineInitM` in NWTC_Num.f90.
- **`AddOrSub2Pi(OldAngle, NewAngle*)`** — adjusts NewAngle to within Pi of OldAngle by adding/subtracting multiples of 2*Pi. Coarse jump via `int(delta/2Pi)`, then iterative ±2Pi fine adjustment (max 10 steps). From `AddOrSub2Pi_R8` in NWTC_Num.f90.
- **`Angles_ExtrapInterp1(Angle1, Angle2, tin[2], Angle_out*, tin_out)`** — angle-aware linear interpolation between two angles. Calls AddOrSub2Pi to adjust Angle2 within Pi of Angle1 before interpolating. Short-circuits if angles are equal. From `Angles_ExtrapInterp1_R8` in NWTC_Num.f90. Used by AFI_UA_BL_Type_ExtrapInterp1 for 8 angle fields in the UA_BL struct.

Design rationale: NWTC_Library stays Fortran as a module, but individual utility functions needed by AeroDyn are translated to C++ for the pure-C++ end state. Compiled as a separate unit (not header-only inline) so implementations exist once regardless of how many translations include the header. KGen kernel verification transitively validates these implementations against the Fortran originals. `reset_to_clean.sh` preserves `vit_nwtc.cpp` (it's a shared utility, not a per-function wrapper stub).

**Build flags:** The OpenFAST fork has `FP_CONTRACT_OFF=ON` (default) in `CMakeLists.txt`, which sets `-ffp-contract=off` on both Fortran and C++ compilers. This is required for bit-identical verification during the translation phase. For production pure-C++ builds after translation: `cmake .. -DFP_CONTRACT_OFF=OFF`.

**Kernel vectorization flags:** VIT adds `-fvect-cost-model=unlimited` to kernel Fortran compilation flags and `-lmvec -lm` to the linker. KGen-reduced modules have fewer functions than the full production build, which changes gfortran's vectorization cost heuristics — loops that are vectorized in production (using libmvec's SIMD cos/sin) may not be vectorized in the smaller kernel context (falling back to scalar libm cos/sin). Vectorized and scalar trig implementations differ by 1 ULP for certain inputs, and these differences cascade through complex computations. The flags force consistent vectorization matching the production CMake build. See dev note `202604260357` for the full investigation.

**Registry C++ headers:** Generate with `-cppcode-double` flag for correct ReKi→double mapping: `openfast_registry input.txt -cppcode-double -O output_dir`

**Line coverage data:** Per-line execution counts for all AeroDyn source files across all 17 test cases are at `vit/analysis/aerodyn/line_coverage.json` (generated from clean source via gcov). Regenerate with: `docker exec vit-dev-openfast bash -c "cd /workspace/openfast && bash scripts/generate_coverage.sh"`. Must be regenerated from clean source (after `reset_to_clean.sh`), not integrated source.

### AeroDyn Test Case Coverage Map

18 test cases used for gcov line coverage data. 17 have committed baselines in `openfast/baselines/aerodyn/`; `ad_Sphere_OLAF` is in the coverage map but has no committed baseline (used for coverage only). Use this to pick the right test case for KGen extraction based on which code path the target function is on.

| Test Case | UA | AFTab | Wake | DBEMT | Cavit | Notes |
|-----------|-----|-------|------|-------|-------|-------|
| ad_BAR_SineMotion | 0 | 1 | 1 (BEMT) | 1 | - | **Default.** No UA. Most BEMT-path functions. |
| ad_BAR_SineMotion_UA4_DBEMT3 | 4 | 1 | 1 (BEMT) | 3 | - | **UA init path.** CalculateUACoeffs, UA separation functions. |
| ad_BAR_CombinedCases | 0 | 1 | 1 (BEMT) | 1 | - | Combined analysis type (AnalysisType=3). |
| ad_BAR_RNAMotion | 0 | 1 | 1 (BEMT) | 0 | - | RNA motion, no DBEMT. |
| ad_BAR_OLAF | 0 | 1 | 3 (OLAF) | - | - | OLAF wake model, BAR turbine. |
| ad_MHK_RM1_Fixed | 0 | 2 | 1 (BEMT) | 2 | Yes | **Multi-table Re interp.** FindBoundingTables, AFI_ComputeAirfoilCoefs2D. |
| ad_MHK_RM1_Fixed_UA | 4 | 2 | 1 (BEMT) | 2 | No | **2D UA interp.** AFI_ComputeUACoefs2D + Angles_ExtrapInterp1. Copy of MHK_Fixed with UA enabled, CavitCheck off. |
| ad_MHK_RM1_Fixed_IfW | 0 | 2 | 1 (BEMT) | 2 | Yes | Same as MHK_Fixed + InflowWind. |
| ad_MHK_RM1_Floating | 0 | 2 | 1 (BEMT) | 2 | Yes | Floating MHK, cavitation. |
| ad_Kite_OLAF | 3 | 1 | 3 (OLAF) | - | - | **UA_Mod=3** (Minnema/Pierce) + OLAF. |
| ad_B1n2_OLAF | 0 | 3 | 3 (OLAF) | - | - | **AFTabMod=3** (UserProp interp) + OLAF. |
| ad_Sphere_OLAF | 0 | 3 | 3 (OLAF) | - | - | AFTabMod=3, spherical geometry. |
| ad_VerticalAxis_OLAF | 0 | 2 | 3 (OLAF) | - | - | **VAWT.** AFTabMod=2 + OLAF. |
| ad_EllipticalWingInf_OLAF | 0 | 1 | 3 (OLAF) | - | - | Infinite elliptical wing, OLAF. |
| ad_HelicalWakeInf_OLAF | 0 | 1 | 3 (OLAF) | - | - | Infinite helical wake, OLAF. |
| ad_MultipleHAWT | 0 | 1 | 1 (BEMT) | 0 | - | 2-turbine HAWT. |
| ad_QuadRotor_OLAF | 0 | 1 | 3 (OLAF) | - | - | 4-rotor quad, OLAF. |
| ad_timeseries_shutdown | 0 | 1 | 1 (BEMT) | 0 | - | Time-dependent input (AnalysisType=2), shutdown event. |

**Test case selection guide:**
- **General BEMT functions:** `ad_BAR_SineMotion` (default)
- **UA-dependent functions** (CalculateUACoeffs, Compute_iLoweriUpper, ComputeUA360_*): `ad_BAR_SineMotion_UA4_DBEMT3`
- **Multi-table interpolation** (FindBoundingTables, AFI_ComputeAirfoilCoefs2D): `ad_MHK_RM1_Fixed`
- **2D UA interpolation** (AFI_ComputeUACoefs2D, Angles_ExtrapInterp1): `ad_MHK_RM1_Fixed_UA`
- **OLAF/FVW functions:** `ad_BAR_OLAF` or `ad_Kite_OLAF` (latter adds UA)
- **DBEMT functions:** `ad_BAR_SineMotion` (DBEMT_Mod=1) or `ad_BAR_SineMotion_UA4_DBEMT3` (DBEMT_Mod=3)

### Extraction Workflow (AeroDyn)

Extraction requires clean (pre-integration) source. Line numbers shift after each integration, so always find them fresh.

**Step 1 — Query coverage data for the call site and test case:**
```bash
docker exec vit-dev-openfast bash -c "python3 /workspace/vit/scripts/query_coverage.py --call-sites FunctionName"
```
This returns every call site (file:line) with which test cases exercise it and how many times. Pick the call site and test case with the most hits. **Do not manually trace code paths to guess test cases** — the coverage data is definitive and manual analysis has proven unreliable (see dev note `202604250803`).

If coverage data doesn't exist yet, generate it (once, ~10 min):
```bash
# Reset to clean source first (run on Mac)
bash scripts/reset_to_clean.sh
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && bash scripts/generate_coverage.sh"
```

**Step 2 — Reset source to clean Fortran:**
```bash
# Run on Mac, not in container
bash scripts/reset_to_clean.sh
```

**Step 3 — Update `vit.yaml`** with the test case from Step 1.

**Step 4 — Rebuild with clean source** (inside container):
```bash
cd /workspace/openfast/build && cmake --build . --target aerodyn_driver -j$(nproc)
```

**Step 5 — Extract** (inside container):
```bash
cd /workspace/openfast && vit extract FunctionName -f modules/aerodyn/src/SourceFile.f90 -l LINE
```

**Step 6 — Restore integrated source:**
```bash
# Run on Mac
git checkout -- modules/aerodyn/src/AirfoilInfo.f90
```
Then rebuild (or use the committed integrated version if no new integrations were added).

**Step 7 — Restore `vit.yaml` `run_cmd`** to the default (`ad_BAR_SineMotion`) if it was changed.

**If extraction reports "No state data captured":**

Follow the debugging checklist in `vit/docs/EXTRACTION_INTERNALS.md` Section 7. The most common causes are:
1. **Wrong test case** — query coverage data to verify the line is hit by the configured test case.
2. **Cross-module instrumentation** — KGen instruments files outside the build target's cmake directory, causing a silent build failure. Run the build wrapper manually (`bash .vit_build_wrapper.sh`) and look for compile errors. See EXTRACTION_INTERNALS.md Section 6 (F2) for details.
3. **Source not clean** — integration wrappers referencing VIT modules cause build failures.

Do not assume infrastructure is broken. Verify the call site, test case, and build output first. Only consider deeper infrastructure issues as a last resort, and communicate with the user before investigating. For the complete pipeline reference, see `vit/docs/EXTRACTION_INTERNALS.md`.

**Other pitfalls:**
- KGen modifies `AirfoilInfo_Types.f90` and `AeroDyn_Driver.f90` (strips blank comment lines). Always `git checkout` these after extraction if they show as modified.
- If a kernel fixture already exists for a function, its `kernel_driver.f90` contains the actual call site that was used — this is ground truth, not hand-written documentation.

## Development Environment

### Docker Containers (two, one per project)

All building, extraction, simulation, and testing happens inside a Docker container. **Never run VIT commands, ROSCO/OpenFAST builds, or KGen directly on the Mac** — KGen's Fortran parser does not work on macOS. The project now has two containers, one per target codebase, with separate mount points:

| Container | Mac path mounted | Container path | Project | How it was built |
|-----------|-----------------|----------------|---------|------------------|
| **`vit-dev`** | `vit_translation/` | `/workspace/` | ROSCO (completed) | Interactively, on `ubuntu:24.04`, with Python 2.7 compiled from source inside the container. Committed and exported as `vit-dev-image.tar.gz`. See dev note `202603231054`. |
| **`vit-dev-openfast`** | `vit_translation_openfast/` | `/workspace/` | OpenFAST / AeroDyn (active) | From `vit/docker/Dockerfile.openfast`, which FROMs `vit-dev-image` and adds OpenFAST-specific dependencies (LAPACK/BLAS, yaml-cpp, Python 2.7 rebuilt, fparser, vtk/Bokeh/nptdms). Built and verified 2026-04-14. See dev note `202604140204`. |

Files edited on the Mac are immediately visible inside the corresponding container. **Use the right container for the right project** — `vit-dev` cannot see the OpenFAST workspace, and `vit-dev-openfast` cannot see the ROSCO workspace. If you're doing AeroDyn work, you want `vit-dev-openfast`; if you're back on ROSCO for something, you want `vit-dev`.

**Running commands in the OpenFAST container:**

```bash
# Start if stopped:
docker start vit-dev-openfast

# Or recreate from the built image:
docker run -d --name vit-dev-openfast \
    -v ~/vit_translation_openfast:/workspace \
    vit-dev-openfast:latest

# After mounting a fresh workspace, re-install VIT as an editable package
# (needed because the inherited install from vit-dev-image points at the ROSCO workspace):
docker exec vit-dev-openfast pip3 install -e /workspace/vit --break-system-packages

# Run VIT commands:
docker exec -it vit-dev-openfast bash -c "cd /workspace/openfast && vit --help"
```

**Rebuilding `vit-dev-openfast` from scratch** (if the container is destroyed or corrupted):

```bash
# Load the base image if not already loaded:
docker load < vit-dev-image.tar.gz

# Build:
cd vit_translation_openfast/vit/docker
docker build -f Dockerfile.openfast -t vit-dev-openfast ..
```

The Dockerfile is self-documenting and records all the setup steps. Building from scratch takes ~10-15 minutes (most of which is the Python 2.7 source compile).

**The "Never chain build and simulation in one `docker exec` command" warning applies to both containers** — the build step changes the working directory to `rosco/controller/build/` (or equivalent) and the next command's `cd` will fail. Use separate `docker exec` calls.

**Run commands inside the container like this:**

```bash
docker exec -it vit-dev bash -c "cd /workspace/ROSCO && <command>"
```

**For multi-command sequences:**

```bash
docker exec -it vit-dev bash -c "
  cd /workspace/ROSCO &&
  <command1> &&
  <command2>
"
```

**WARNING: Never chain build and simulation in one `docker exec` command.** The build step `cd rosco/controller/build` changes the working directory. Subsequent commands like `cd Examples` will fail with `No such file or directory` because the CWD is still inside the build directory. Use separate `docker exec` calls:

```bash
# Build (changes CWD to build/)
docker exec vit-dev bash -c "cd /workspace/ROSCO/rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so"

# Simulate (separate call, starts at /workspace/ROSCO)
docker exec vit-dev bash -c "cd /workspace/ROSCO/Examples && python3 vit_sim.py"
```

**Start the container if it is stopped:**

```bash
docker start vit-dev
```

### What Is Installed in the Container

- Ubuntu 24.04 (ARM, via Colima on Apple Silicon)
- gfortran, g++, cmake
- Python 3.12 (system), Python 2.7 (built from source at `/opt/python27`)
- VIT: `pip install -e /workspace/vit`
- ROSCO: `pip install -e /workspace/ROSCO`

### If the Container Is Destroyed

If the container needs to be recreated, Python 2.7 must be rebuilt from source. The tarball is at `/workspace/Python-2.7.18.tar.xz`:

```bash
cd /workspace/Python-2.7.18
./configure --prefix=/opt/python27
make -j$(nproc)
make install
ln -s /opt/python27/bin/python2.7 /usr/local/bin/python2.7
```

Then reinstall VIT and ROSCO:

```bash
pip3 install -e /workspace/vit --break-system-packages
pip3 install -e /workspace/ROSCO --break-system-packages
```

### Container Quirks

- **Clock skew warnings** during `make` are harmless (filesystem timestamp differences between Mac and container).
- **No network access** from inside the container due to corporate TLS inspection. All packages were pre-installed.

### FMA Contraction and Bit-Identical Verification

ROSCO's `CMakeLists.txt` sets `-ffp-contract=off` on both Fortran and C++ compilers. This disables FMA (fused multiply-add) contraction during the translation/verification phase, ensuring gfortran and g++ produce bit-identical results for equivalent floating-point expressions. Without this, the compilers schedule FMA differently (each independently choosing which multiply-adds to fuse), causing 1-ULP differences that accumulate in closed-loop feedback.

**This flag is for verification, not permanent.** Once all of ROSCO is translated to C++ and there's no mixed Fortran/C++ binary, FMA can be re-enabled freely — the C++ code will stand on its own with full compiler optimizations. During the translation phase, `-ffp-contract=off` is required so that each function's correctness can be proven bit-exactly against the Fortran original.

**If a translation shows `IN_TOL` (instead of `IDENTICAL`) in KGen field data with `-ffp-contract=off`**, it likely indicates a real translation bug — investigate the expression structure.

**Debugging persistent kernel failures (same cases fail regardless of code changes):** If the same N cases fail with the same diffs whether using C++ or pure Fortran (replace the VIT wrapper with the `.kgen` original), the issue is NOT in the translation — it's in the kernel compilation environment. Run the pure-Fortran test first to determine if the diff is a translation bug or an infrastructure issue. The most common infrastructure cause: **vectorized vs scalar math library** — the production build links `libmvec` (glibc SIMD cos/sin/exp/log) while the kernel may not. Check with `nm production_binary | grep _ZGV` vs `nm kernel.exe | grep _ZGV`. VIT's `-fvect-cost-model=unlimited` + `-lmvec -lm` flags address this, but new platforms or compiler versions may need similar investigation. See dev note `202604260357` for the full diagnostic chain that uncovered this.

VIT's kernel verification Makefile uses `-ffp-contract=off` in CXXFLAGS to match. **`vit.yaml`'s `fortran.flags` must also include `-ffp-contract=off`** so the kernel Makefile's Fortran compiler flags match the project's CMakeLists.txt. Without this, the kernel Fortran uses FMA while the kernel C++ does not, causing sub-ULP differences that show as IN_TOL instead of IDENTICAL.

## VIT Assumption Confirmation Protocol

When VIT encounters a value it has inferred from source evidence but cannot be certain is correct, it signals the caller via **exit code 10** and a `[VIT:CONFIRM]` block on stdout.

### When to use which approach

- **Has evidence, inferred a value** → exit code 10 (confirmation needed). VIT found source evidence and derived a value, but the inference could be wrong. Human must validate.
- **No evidence, arbitrary fallback** → hard error (exit 1). VIT doesn't have enough information to proceed. Guessing would silently produce wrong code.
- **Technical/performance default** → safe to hardcode. Wrong value = slower or noisier, not semantically incorrect. Examples: `max_iterations=5`, `tolerance=1e-14`.

**The test:** if a wrong default would produce code that compiles but behaves incorrectly, it needs either EXIT_CONFIRM (if inference exists) or a hard error (if no evidence).

### Claude's responsibilities when exit code is 10

1. Parse the `[VIT:CONFIRM]` block(s) from VIT's output
2. Present the assumption to the user clearly — what VIT inferred, from where, what value it will use
3. Wait for the user to confirm or provide an alternative value
4. Re-run VIT with the confirmed value as an explicit CLI argument (the `cli_override` field tells which flag to use)

**Claude must NEVER auto-confirm an assumption.** The entire point of exit code 10 is that VIT has identified a decision that requires human judgment. Even if the inferred value looks correct, present it to the user and wait.

### Example interaction

```
Claude runs: vit analyze-types --fix character --type ErrorVariables --field ErrMsg --apply
VIT outputs: [VIT:CONFIRM] block with assumed_value: 1024, inferred from ALLOCATE

Claude: VIT analyzed ErrorVariables.ErrMsg and inferred a buffer size of 1024
        from the ALLOCATE call in ReadSetParameters.f90. This will replace
        CHARACTER(:), ALLOCATABLE with CHARACTER(1024).
        Does 1024 look correct, or should it be a different size?

User: 1024 is correct.

Claude runs: vit analyze-types --fix character --type ErrorVariables --field ErrMsg --length 1024 --apply
VIT proceeds with the confirmed value.
```

## VIT Manual Review Protocol

When VIT can automatically rewrite most references to a field but encounters code it cannot safely transform (e.g., complex arithmetic with `EXP`, `CMPLX`), it applies nothing, signals the caller via **exit code 11** and a `[VIT:MANUAL]` block on stdout.

### How it works

1. VIT scans all references to the field and classifies each as auto-rewritable or manual
2. If any manual lines exist without a `--rewrite` covering them, VIT exits 11 — **nothing is written to disk**
3. When all manual lines have `--rewrite` entries (or there are none), VIT applies everything atomically

### Claude's responsibilities when exit code is 11

1. Parse the `[VIT:MANUAL]` block(s) from VIT's output
2. Read the flagged source line(s) and understand the context — what the code does, why VIT can't auto-rewrite it
3. **Propose a rewrite** to the user — Claude should use domain knowledge to suggest what the replacement code should be, just as it would when writing a translation
4. Present the proposal clearly: the original line, the proposed replacement, and why
5. Wait for the user to approve or adjust
6. Re-run VIT with the approved rewrite as a `--rewrite` argument (the `cli_override` field shows the exact flag format)

**Claude must NEVER auto-approve a manual rewrite.** The user must see and approve the proposed code before VIT applies it.

### Example interaction

```
Claude runs: vit analyze-types --fix complex --type LocalVariables --field AWC_complexangle --apply
VIT outputs: [VIT:MANUAL] block with:
  edit_1_line: 759
  edit_1_reason: complex intrinsic: EXP
  edit_1_source: LocalVar%AWC_complexangle(K) = LocalVar%AWC_complexangle(K) + amp * EXP(complexI * (AWC_angle(K)))
  edit_1_cli_override: --rewrite 'Controllers.f90:759=<replacement>'

Claude: VIT can automatically rewrite 10 of 11 references to AWC_complexangle,
        but found one line with complex arithmetic it can't transform:

        Controllers.f90:759:
          LocalVar%AWC_complexangle(K) = ... + amp * EXP(complexI * (AWC_angle(K)))

        This is Euler's formula: amp * e^(iθ) = amp*(cos θ + i·sin θ).
        I propose decomposing it as:
          _re(K) = _re(K) + amp * COS(AWC_angle(K))
          _im(K) = _im(K) + amp * SIN(AWC_angle(K))

        Does this look correct?

User: Yes.

Claude runs: vit analyze-types --fix complex --type LocalVariables --field AWC_complexangle --apply \
  --rewrite 'Controllers.f90:759=<COS/SIN lines>'
VIT applies all changes (type definition + 10 auto-rewrites + 1 manual rewrite), exits 0.
```

### Exit code summary

| Code | Meaning | Nothing written | Claude action |
|------|---------|-----------------|---------------|
| 0 | Success | (changes applied) | Done |
| 1 | Error | Yes | Fix the error |
| 10 | Confirmation needed | Yes | Present inferred value, wait for confirmation, re-run with override |
| 11 | Manual rewrite needed | Yes | Propose rewrite, wait for approval, re-run with `--rewrite` |

## ROSCO Workflow (Completed — Historical Reference)

The ROSCO translation is complete (52 functions, pure C++ `libdiscon.so`). Operational details (build/extract/verify/integrate commands, batch scripts, Stage B/C/D/E verification, clean build test, performance benchmarks) are in `PROJECT_PLAN_ROSCO.md` and the ROSCO workspace's git history. The ROSCO-specific `vit.yaml` example is in the ROSCO repo at `/workspace/ROSCO/vit.yaml`.

## VIT Configuration

Each project has a `vit.yaml` at its root: `openfast/vit.yaml` (AeroDyn, active) and `ROSCO/vit.yaml` (ROSCO, historical). The config file specifies KGen paths, build/run commands, kind aliases, Fortran compiler flags, type strategies, and the translations registry. See the AeroDyn `vit.yaml` for the current configuration; key AeroDyn-specific fields are documented in the "AeroDyn Workflow" section above.

Key fields (common to both projects):
- `kgen.path`, `build_cmd`, `run_cmd`, `clean_cmd`: KGen extraction pipeline commands
- `kgen.invocation`: which invocations to capture (`0:0:1-20` = process 0, thread 0, invocations 1-20)
- `fortran.kind_aliases`: maps project kinds to standard types (e.g., `ReKi: REAL(8)`)
- `fortran.flags`: compiler flags propagated to kernel Makefile (must include `-ffp-contract=off`)
- `types`: derived type handling strategy (`view` for ALLOCATABLE types, default `bind_c`)
- `translations`: tracks verified/integrated functions with case counts and kernel paths
- `verification.tolerance`: max acceptable floating-point difference (`1.0e-14`)

## VIT CLI Commands Reference

VIT ships 13 subcommands. The seven primary commands (`extract`, `verify`, `integrate`, `translate`, `test-validate`, `sim-compare`, `analyze-types`) are covered in detail above under "Full Workflow" and the Stage sections. This section lists the full surface so nothing is hidden.

**Global flags** (accepted by every subcommand, must appear before the subcommand name):
- `--config PATH` / `-c PATH` — use a non-default `vit.yaml` location.
- `--verbose` / `-v` — verbose logging (stacktraces on error, extra progress detail).

**Primary commands** (already documented above):
| Command | Purpose | Notable flags |
|---------|---------|---------------|
| `vit extract` | Instrument Fortran + capture KGen state | `-f FILE`, `-l LINE`, `--run-args`, `--dry-run`, `--assumed-size NAME=SIZE` (repeatable — CLI equivalent of `kgen.assumed_size_arrays` config; use to override or supplement) |
| `vit verify` | Run kernel verification of C++ against captured state | `-f FILE`, `--kernel-dir DIR`, `--no-field-log` (suppress per-field float comparison output — useful for CI/batch runs) |
| `vit integrate` | Generate/apply integration patches | `-f FILE`, `--apply`, `--reverse-copy`, `--auto-allocate`, `--dry-run` |
| `vit translate` | Generate C++ scaffold + translation prompt | `-f FILE`, `--module NAME` |
| `vit sim-compare` | Compare two directories of `.npz` simulation arrays | `--tolerance FLOAT` |
| `vit analyze-types` | Scan/fix derived types for C interop | `--fix {character,logical,complex,bind_c}`, `--scan-allocatable`, `--apply`, `--type`, `--field`, `--length`, `--rewrite` |
| `vit test-validate` | Generate test harness + prompt for validation functions | `-f FILE`, `--module NAME`, `--run`, `--force` |

**Utility and advanced commands** (less commonly used; full details via `vit <cmd> --help`):
| Command | Purpose |
|---------|---------|
| `vit init` | Initialize a `vit.yaml` template in the current directory |
| `vit find` | Locate call sites for a function across the source tree |
| `vit parse` | Parse a Fortran function signature and print extracted types/args (debug/inspect tool) |
| `vit interface` | Generate Fortran↔C interface files for a function (intermediate step used internally by `integrate`) |
| `vit run` | Full pipeline wrapper: interface → verify → integrate (`--file`, `--kernel-dir`, `--skip-verify`, `--no-field-log`, `--apply`) |
| `vit status` | Show translation status from `vit.yaml` (which functions are stub/translated/verified/integrated) |

## Testing and Development Process

### Test Structure

Tests live in `vit/tests/` and use pytest. Run them inside the container:

```bash
docker exec -it vit-dev bash -c "cd /workspace/vit && python3 -m pytest tests/ -v"
```

### Golden Test Fixtures

Golden kernel directories exist in each project's `kernel/` directory from successful extractions. ROSCO has 42 golden fixtures in `/workspace/ROSCO/kernel/`; AeroDyn has 8 (and growing) in `/workspace/openfast/kernel/`. Use these for fast verification regression during development — there is no need to re-extract unless the extraction logic itself has changed, or the golden fixtures are missing or corrupted.

For re-extraction, check the golden fixture's `kernel_driver.f90` for the actual call site. For AeroDyn, use `query_coverage.py --call-sites FunctionName` to find the right test case. For ROSCO, check `scripts/extract_all.sh`. Do not maintain static extraction parameter tables — line numbers shift after each integration.

**When to re-extract:**
- Changes to `vit/vit/extract.py`
- Changes to KGen source code that affect extraction or state capture
- Golden fixtures are missing (`kernel/<function>/` directories don't exist)
- Kernel build fails due to stale or incompatible generated code

**When re-extraction is NOT needed:**
- Changes to `vit/vit/kernel_verify.py` (verification patches)
- Changes to `vit/vit/cli.py` (CLI flags, output formatting)
- Changes to `vit/vit/integrate.py` (integration plan generation)
- Adding new features like field data output or rich table display

### Regression Testing

**AeroDyn:** After integrating any function, run `bash scripts/verify_aerodyn_baselines.sh all` (17/17 must pass). For VIT code changes, re-verify golden kernel fixtures with `vit verify`.

**When to re-extract (either project):**
### Development Workflow

1. **Understand the current code** before making changes. Read the relevant source files.
2. **Write or update tests** for the feature being added or changed.
3. **Implement the change.**
4. **Run unit tests** to confirm nothing is broken.
5. **Run verification regression** (fast, against golden fixtures) to confirm the pipeline still works.
6. **Run full regression** only when the change warrants it (see "When to re-extract" above).
7. **If changing NaN handling or comparison logic**, run the determinism stress test.

### Writing Tests

- Test files go in `vit/tests/`.
- Match existing conventions in `vit/tests/test_core.py`.
- For functions that patch Fortran source (like `_patch_nan_comparisons`), test with small synthetic Fortran snippets — not the full ROSCO file.
- For integration-level tests, use the golden fixtures in `/workspace/ROSCO/kernel/`.

### Code Style

Match existing VIT conventions. Look at the existing code for patterns on docstrings, type hints, naming, and structure.

## C++ Translations

**ROSCO (completed):** 52 verified C++ translations in `ROSCO/translations/`, organized by Fortran module (`Functions/`, `Filters/`, `Controllers/`, `ControllerBlocks/`, `ReadSetParameters/`, `IO/`). See `PROJECT_PLAN_ROSCO.md` for the complete function list.

**AeroDyn (in progress):** Translations in `openfast/translations/AirfoilInfo/`. Current status tracked in `openfast/vit.yaml` under `translations:`.

Each translation file has a metadata header (source file, function name, MD5 hash, VIT version, verification status) generated by `vit translate`.

### Translation Workflow

**Always use `vit translate` to seed translations.** Do not write C++ from scratch. The workflow:

1. **Generate scaffold:** `vit translate FunctionName -f source.f90 --module ModuleName`
   - Creates the `.cpp` file with all struct definitions, the function signature, and a TODO stub
   - Outputs a **translation prompt** that includes the Fortran source, struct layouts, callee `_c` signatures (with correct OPTIONAL param handling), and TRANSLATION_TIPS.md content
2. **Write the implementation** using the translation prompt as context — it has the correct callee signatures (e.g., `lpfilter_c` with 9 args including `has_InitialValue`/`InitialValue` for OPTIONAL params)
3. **Verify:** `vit verify FunctionName translation.cpp -f source.f90 --kernel-dir kernel/FunctionName`

Skipping `vit translate` leads to signature mismatches (e.g., calling `lpfilter_c` with 7 args instead of 9) because the callee bridge signatures include OPTIONAL parameter handling that isn't obvious from the Fortran source alone.

Integration starts from a clean ROSCO. All functions are integrated in phase order — nothing is pre-integrated.

Note: `interp1d.cpp` uses VIT's per-array size parameter convention (`n_xData`, `n_yData`) rather than a single shared `n` parameter. See "Known Limitations" below.

### TRANSLATION_TIPS.md

`ROSCO/TRANSLATION_TIPS.md` is a living document that captures lessons learned from translation failures. When a translation fails verification and the fix reveals a generalizable lesson, add it to this file. The `vit translate` command includes its contents in the translation prompt automatically, so the prompt gets smarter over time.

TRANSLATION_TIPS.md is also the place to document project-specific resources available to translators — shared headers, constants, utility functions. For example, ROSCO's `rosco_constants.h` is documented there so translators know to use `PI` from the header instead of defining it inline. Any shared resource that a translator should know about belongs in this file.

### Translation Conventions

**Source casing preservation:** VIT extracts the function name casing from the Fortran source declaration (`SUBROUTINE LPFilter(...)` → `LPFilter`, not `lpfilter`). The C++ translation must define the function with the same casing. The `_c` wrapper name is always lowercase (`lpfilter_c`) for BIND(C) compatibility.

**POSIX collision guard:** If a function's exact name (case-sensitive) exists as a libc symbol, VIT appends `_impl` to avoid PLT interposition in shared libraries. This only triggers for all-lowercase names that match libc symbols (e.g., a hypothetical Fortran `FUNCTION shutdown(...)` → C++ `shutdown_impl()`). PascalCase names like `Shutdown` don't collide because libc's `shutdown` is lowercase.

**Kernel file naming:** During kernel verification, VIT generates two files: `{name}.hpp` (user's translation, included via `#include`) and `{name}.cpp` (wrapper with `extern "C"` entry point). Both use lowercase names.

## Known Limitations

### Derived types

VIT handles derived types with two strategies, configured per type in `vit.yaml`:

- **BIND(C) structs** (default for compatible types): Types with only scalar/fixed-size fields are passed directly as C struct pointers. ErrorVariables, LocalVariables, FilterParameters, piParams, etc. all use this path. `vit analyze-types` scans types and offers automated fixes (`--fix character`, `--fix logical`, `--fix complex`, `--fix bind_c`) to make types C-compatible. All four fix modes are implemented (Phases 4A-4C).

- **View structs** (for ALLOCATABLE types): Types with ALLOCATABLE arrays (ControlParameters, PerformanceData, ExtControlType, AFI_ParameterType, AFI_Table_Type) use the pointer+size view pattern. A generated view struct exposes ALLOCATABLE fields as `type* field; int n_field;` pairs. A generated Fortran populator fills the view from the real type at runtime using `C_LOC(LBOUND(...))` + `SIZE()`. Configured in `vit.yaml` under `types: { TypeName: { strategy: view } }`. ALLOCATABLE field sizes are scanned from source via configurable allocation helpers (`allocatable.helpers` in `vit.yaml`) and universal `ALLOCATE` pattern matching. All ALLOCATABLE fields are included in the view struct — the scanner is informational only.

- **ALLOCATABLE TYPE() array marshalling:** When an ALLOCATABLE field contains an array of derived types (e.g., `p%Table(:)` where each element is `AFI_Table_Type`), and the inner type also has `strategy: view`, the populator creates a module-level SAVE array of child view structs, populates each in a loop by calling the child populator, and stores a typed pointer (`afi_table_type_view_t* Table` instead of `void* Table`). C++ can index arbitrary elements: `p->Table[i].field`. Non-view inner types remain `void*` / `C_NULL_PTR`. First used by AFI_ComputeAirfoilCoefs.

- **LBOUND-based C_LOC:** The populator uses `C_LOC(src%field(LBOUND(src%field,1), ...))` instead of `C_LOC(src%field(1,1,1))` for all array ranks. This correctly handles arrays with non-default lower bounds (e.g., `SplineCoefs(:,:,0:3)` where the third dimension starts at 0). Without this, C_LOC at `(1,1,1)` would skip the first k=0 slice.

- **Derived type registry is auto-discovered and recursive**: `build_derived_type_registry()` searches the source directory for TYPE definitions used in function args, then recursively discovers nested TYPE(X) fields until all transitive dependencies are found. Types with `strategy: view` in config are marked for view struct treatment. Types not in the registry at all default to passthrough.

- **USE statement matching is string-based**: `_get_use_statements_for_derived_types()` checks if the derived type name appears in the USE line text. This works for `USE Module, ONLY: TypeName` but would miss bare `USE Module` (can't tell what it exports) or false-match on substring collisions.

### Assumed-shape arrays

- **Per-array size params**: VIT generates a separate `n_<name>` size parameter for every assumed-shape array arg, even when arrays are semantically the same length (e.g., `xData` and `yData`). The user's C++ must accept per-array sizes. A future improvement could coalesce size params when arrays share the same rank, but that requires inference VIT currently avoids.

### C++ → C++ call path

During the translation phase (mixed Fortran/C++ binary), every translated function has a Fortran wrapper. Fortran callers use the wrapper. But when a translated C++ function needs to call another translated C++ function (e.g., PitchControl calling PIController), it calls the `_c` entry point directly via a shared C header — no round-trip through Fortran.

- **Fortran callers** → Fortran wrapper → `_c` function → C++ implementation (unchanged)
- **C++ callers** → `_c` function directly via `#include` shared header (no wrapper)
- **Shared header**: `vit integrate --apply` emits/updates a header with `extern "C"` declarations for each integrated function's `_c` entry point
- **`vit translate`**: Scaffolds include `_c` declarations for already-translated callees (detected from `vit.yaml` translations registry)

On the `integrated` branch (Phase 11 complete), all Fortran wrappers are removed and C++ calls C++ directly throughout. On `main`, the Fortran wrappers still exist on disk but are not in the build (CMakeLists compiles only `.cpp` files).

### Kernel callee bridges

During kernel verification, only the target function is replaced with C++. If it calls another already-translated function (e.g., ForeAftDamping calls PIController), VIT auto-generates a BIND(C) bridge in the kernel that wraps C→Fortran:

```
C++ translation calls picontroller_c()
    → BIND(C) bridge (auto-generated in kernel)
        → original Fortran PIController() (in kernel, untouched)
```

VIT detects translated callees by scanning the target function's original source and cross-referencing against `vit.yaml` translations. For each callee, it generates: a Fortran bridge function, a C declarations header (`vit_kernel_callees.h`), and Makefile rules. This is automatic during `vit verify` — no manual configuration needed.

**View-type stash for callee bridges:** When a callee bridge needs a view-type argument (like `ControlParameters`), it cannot use `C_F_POINTER` on the view pointer — the view struct has a completely different memory layout than the original Fortran type. Instead, the kernel wrapper "stashes" a pointer to the original Fortran type in a module-level SAVE variable (`vit_original_controlparameters`) before calling C++. Callee bridges USE this variable to get the original type directly. The stash is set in the view populator module and valid for the entire wrapper call duration.

**Callee PUBLIC visibility:** KGen's reduced kernel modules often have `PRIVATE` default access, hiding callee functions that bridges need to `USE`. VIT auto-detects this and injects `PUBLIC` statements for each callee function during `setup_kernel_cpp`. No manual intervention needed.

**C++ callee auto-detection:** VIT scans the C++ translation file and shared headers for function definitions to determine which callees have C++ implementations. Callees found in C++ source skip Fortran bridge generation automatically — no `vit.yaml` registration needed. Three skip rules: (1) **same-file:** function defined in the same `.cpp` as the target (internal helper), (2) **shared-file:** function declared in a `shared_files` header/source (NWTC utility), (3) **helper callees:** after identifying same-file helpers, VIT also scans their Fortran bodies for THEIR callees (e.g., `AFI_ComputeAirfoilCoefs2D` calls `FindBoundingTables` — the bridge for FindBoundingTables is generated even though 2D itself is skipped). This enables multi-function translations in a single `.cpp` file without manual callee registration.

### KGen extraction

- **Call site must be reachable at runtime**: KGen produces zero state files if the call site is never executed. Each extraction uses `--run-args '--scenario N'` to run the specific scenario that exercises the target function. Call sites gated by mode flags (e.g., `IF (CntrPar%CC_Mode > 0)`) need the corresponding mode enabled in the scenario config.

- **Invocation ordering**: KGen counts invocations per enclosing subroutine. When running multiple scenarios, earlier scenarios consume the capture range. VIT addresses this by running the target scenario first in `vit_sim.py` and by using `--run-args '--scenario N'` for per-scenario extraction.

- **Project Fortran flags**: KGen-generated kernel Makefiles don't inherit project-specific compiler flags. VIT's `fortran.flags` config field in `vit.yaml` propagates flags like `-fdefault-real-8` and `-ffp-contract=off` to the kernel Makefile.

- **Assumed-size arrays**: KGen generates state capture functions with `DIMENSION(:)` parameters. Fortran forbids passing assumed-size `(*)` arrays to assumed-shape parameters. VIT auto-detects assumed-size arrays, scans source for index evidence, and proposes sizes via the confirm protocol (exit code 10). Pre-configure `kgen.assumed_size_arrays` in `vit.yaml` to skip confirmation. **The size must cover ALL indices the function writes to** — undersizing causes silent heap corruption.

- **DLL SAVE variable persistence**: Fortran SAVE variables may persist across scenarios within a single `vit_sim.py` run (ctypes `dlclose()` may not truly unload the library). VIT's `kgen.dll_persistence.resets` config patches the instrumented source to reset project-specific guard variables (e.g., ROSCO's `AlreadyInitialized`) on each call. Note: with per-scenario extraction (`--run-args '--scenario N'`), only one scenario runs per process, so this reset may be unnecessary in practice — but the config provides a safety net for multi-scenario extraction.

- **PROCEDURE POINTER declarations**: KGen's original parser couldn't handle `PROCEDURE(...), POINTER` (Fortran 2003+). Fixed in our KGen fork (commit `31d653c`).

- **Cross-module instrumentation**: KGen may instrument files outside the cmake build target's directory (e.g., `NWTC_Base_reduced.f90` in `icefloe` when extracting from `aerodyn`). VIT auto-detects this by comparing state/Makefile paths against the cmake target directory, then transplants `kw_*` subroutines into the correct in-target module file. See EXTRACTION_INTERNALS.md Section 6 (F2).

- **Compile-error-driven kernel repair**: KGen bundles reduced copies of framework modules that may be missing constants, subroutines, and library links. VIT's `_repair_kernel_build()` loop (max 5 iterations) attempts to build the kernel, parses gfortran/linker errors, and auto-fixes from project source. Handles `missing_constant` (inlines CHARACTER length params), `undefined_symbol` (adds declarations), and `link_undefined` (detects LAPACK/BLAS by naming convention, or injects Fortran subroutines). No manual kernel patching needed.

- **Vectorized math library (libmvec)**: KGen-reduced modules have fewer functions than the full production build, changing gfortran's vectorization cost heuristics at `-O2`. Loops that use transcendental math functions (cos, sin, exp, log) may be vectorized in production (using glibc's libmvec SIMD implementations) but not in the kernel (using scalar libm). Vectorized and scalar trig implementations can differ by 1 ULP, and these differences cascade through complex computations (e.g., CalculateUACoeffs alphabreakupper). VIT compensates by adding `-fvect-cost-model=unlimited` (force vectorization) and `-lmvec -lm` (provide the vectorized math library) to kernel builds. See dev note `202604260357`.

## Development Notes Convention

Development notes live in `vit/dev/` (the VIT repo) and are named with a datetime tag: `YYYYMMDDHHMM-descriptor.md` (e.g., `202603101739-project-inception.md`). Dev notes are committed to the VIT repo, not to ROSCO or OpenFAST.

**Workspace note:** the ROSCO workspace (`vit_translation/vit/dev/`) and the OpenFAST workspace (`vit_translation_openfast/vit/dev/`) have their own independent VIT clones, but both share the same `main` branch on the `NatLabRockies/vit` remote. A dev note written in one workspace propagates to the other via `git push` (in the writing workspace) followed by `git pull` (in the other). **Active work is now in the OpenFAST workspace — new dev notes should be written there.** The ROSCO workspace is kept in sync via `git pull` only when convenient (it is currently several commits behind and does not need active maintenance).

When the user says "add this to development notes", "make a dev note", or similar:
1. Get the current time by running `date '+%Y%m%d%H%M'` — do NOT guess or invent timestamps
2. Create a new file in `dev/` with that datetime tag and a descriptive slug
3. The note should provide **comprehensive detail** — not a summary, but a thorough record that gives full context to anyone reading it later
4. Include relevant background, rationale, technical details, decisions made, alternatives considered, and next steps
5. Reference related dev notes, code, or external resources where applicable
6. Use the standard header format:

```markdown
# Title
**Date:** YYYY-MM-DD
**Status:** completed | in-progress | abandoned
**Scope:** [area of change]
```

## Project Plans

Each VIT translation effort has its own plan document named `PROJECT_PLAN_<TARGET>.md` at the VIT repo root. Current plans:

- **`PROJECT_PLAN_ROSCO.md`** — the completed ROSCO translation effort. Contains the function-by-function assessment, full module map, derived type strategy, 11-phase enhancement roadmap, Phase 11.5 (post-completion verification push), and the translation workflow (Section 4a: `vit translate` command, module-organized storage in `translations/`, and translation metadata). Historical record of a completed project.
- **`PROJECT_PLAN_AERODYN.md`** — the in-progress OpenFAST AeroDyn translation (approximately 9× ROSCO's scope). To be drafted using the methodology in `CREATING_PROJECT_PLANS.md`. Lives in the OpenFAST workspace (`vit_translation_openfast/`).

**Before starting a new translation effort, read `CREATING_PROJECT_PLANS.md`.** It is a meta-document derived from a rigorous retrospective of ROSCO's plan drift, and it describes how to produce a better plan: structural inventory (via AST tooling like `fparser2`) before any plan text, explicit confidence labels on phases, first-class open questions, named revisit triggers, and a standard document structure. The ROSCO plan drifted in enumerable ways (granularity imposition, implicit strategy decisions, post-hoc Phase 11.5, no confidence markers, end-of-project auditing) that the methodology is designed to prevent in future efforts. Applying it takes 2-3 days up front but saves weeks of mid-project thrashing.

### Execution Process

We execute each plan one phase at a time. For each phase:

1. **Review**: Read the phase in `PROJECT_PLAN_<TARGET>.md` and understand the scope
2. **Plan**: Create a detailed implementation plan for the phase
3. **Execute**: Implement the VIT enhancements and/or write C++ translations
4. **Test**: Run unit tests, verification regression, and any phase-specific validation
5. **Update**: Mark the phase and its sub-items as complete in `PROJECT_PLAN_<TARGET>.md`. Update the phase description to reflect what was actually done — adjustments, additional work discovered, or better approaches found during implementation
6. **Dev note**: Write a dev note capturing the phase completion

Plan documents use checkboxes (`- [ ]` / `- [x]`) for phases and sub-items so progress is visible at a glance. They are living records: they should always reflect reality, not just the original plan. See `CREATING_PROJECT_PLANS.md` for the full maintenance protocol (revisit triggers, change log discipline, confidence label updates, done-criteria revisions).

## Git

The project now has **two sibling workspaces** on disk, each with its own independent git clones. **Active work is in the OpenFAST workspace;** the ROSCO workspace is historical and only needs to be synced when the user returns to ROSCO-specific work.

### ROSCO workspace (completed) — `vit_translation/`

| Repo | Path | Remote | What goes here |
|------|------|--------|---------------|
| **VIT** | `vit_translation/vit/` | `git@github.com:NatLabRockies/vit.git` (private) | VIT tool code, tests, dev notes, plan docs (`PROJECT_PLAN_*.md`), `CREATING_PROJECT_PLANS.md`, `CLAUDE.md` |
| **KGen** | `vit_translation/KGen/` | `git@github.com:kevinmenear/KGen.git` | Forked from NCAR/KGen with our parser fixes (PROCEDURE POINTER, BIND(C), backslash escape) |
| **ROSCO** | `vit_translation/ROSCO/` | `git@github.com:kevinmenear/ROSCO.git` | Forked from NREL/ROSCO. Translations, `vit.yaml`, `TRANSLATION_TIPS.md`, golden kernel fixtures, `baseline_arrays/`, `upstream_arrays/` |

### OpenFAST workspace (active) — `vit_translation_openfast/`

| Repo | Path | Remote(s) | What goes here |
|------|------|-----------|----------------|
| **VIT** | `vit_translation_openfast/vit/` | `origin` = `git@github.com:NatLabRockies/vit.git` (**same remote** as ROSCO workspace) | Independent clone, same `main` branch — commits converge via push/pull |
| **KGen** | `vit_translation_openfast/KGen/` | `origin` = `git@github.com:kevinmenear/KGen.git` (same remote) | Independent clone, same `master` branch |
| **OpenFAST** | `vit_translation_openfast/openfast/` | `origin` = `git@github.com:kevinmenear/openfast.git` (**our fork**); `upstream` = `https://github.com/OpenFAST/openfast.git` (NREL) | Standard fork pattern. Push to `origin` for our translation work. Pull from `upstream` to sync future NREL OpenFAST releases. `reg_tests/r-test` is a submodule at `https://github.com/OpenFAST/r-test.git` — not forked (we only read the reference test data there). |

Both workspace VIT clones share the same `main` branch on the same remote. A commit made in one workspace is synced to the other via `git push` (in the committing workspace) followed by `git pull` (in the other). Dev notes, plan documents, `CREATING_PROJECT_PLANS.md`, `CLAUDE.md`, and all tool changes for the AeroDyn effort happen in the OpenFAST workspace. The ROSCO workspace only needs syncing when the user goes back to ROSCO work.

ROSCO, KGen, and OpenFAST are all forks with the same pattern: `origin` → our fork, `upstream` (when applicable) → the canonical upstream. VIT-specific ROSCO files (translations, config, kernel fixtures) were committed to the ROSCO fork during the ROSCO project; that work is complete. OpenFAST-side changes during the AeroDyn translation effort (integration wrappers, `_c` entry point declarations, CMakeLists edits, any new test scaffolding) will land on the OpenFAST fork's `main` branch. Changes to VIT tool code, tests, dev notes, and planning docs go in the VIT repo (either workspace; they converge on the remote).

### ROSCO branches (historical reference)

Applies to the ROSCO fork only. OpenFAST's branch story has not been written yet — for now, `main` is the only branch of the OpenFAST clone.

The ROSCO fork has two meaningful branches:

| Branch | Role | `rosco/controller/src/` layout | Fortran files on disk? | `CMakeLists.txt` |
|--------|------|-------------------------------|------------------------|------------------|
| **`main`** | Development workspace where extract/verify/integrate cycles run | Flat (`.cpp` files directly under `src/`) | Yes — `.f90` files coexist so `reset_to_clean.sh` can restore them for extraction cycles | Pure C/C++ (`LANGUAGES C CXX`); the 16 `.f90` files on disk are **not** in SOURCES and are uncompiled artifacts |
| **`integrated`** | Final production state — pure C++ end product | Module subdirectories: `Functions/`, `Filters/`, `Controllers/`, `ControllerBlocks/`, `ReadSetParameters/`, `IO/`, plus `include/` for shared headers (`vit_types.h`, `vit_translated.h`, `rosco_constants.h`, `restart_fields.h`) | No — all Fortran source files removed | Pure C/C++, references module subdirectories |

The `integrated` branch is the canonical final state. Key commits on `integrated` not on `main`:
- `423b5c3` — **Remove all Fortran from integrated branch — pure C++ controller** (removes all 16 `.f90` files)
- `b8473d6` — **Remove translation artifacts: metadata, extern C wrappers, `_c` indirection** (strips 1,733 lines of scaffolding, renames `_c()` calls to PascalCase via shared `vit_translated.h`)
- `7ccc73f` — **Restructure src/ into module subdirectories** (moves flat `.cpp` files into `Functions/`, `Filters/`, etc.)

Day-to-day development happens on `main` because the extract/verify cycles need real Fortran source present. The `integrated` branch is refreshed by merging `main` + re-running the three cleanup commits when substantial changes land on `main`. Dev notes live on `main` only. `baseline_arrays/`, `upstream_arrays/`, `kernel/`, `translations/`, and all `scripts/` live on `main`.

Run `git add` and `git commit` directly when the user asks to commit. Do not run `git push` unless explicitly asked. Do not run destructive git commands (`reset --hard`, `push --force`, etc.) without confirmation. The only exception for autonomous git usage is `git checkout .` on ROSCO as part of a clean build test (see "Clean Build Test" above).
