# AeroDyn Translation: Agent Manual

The operating manual for translating OpenFAST's AeroDyn module from Fortran to C++ with VIT. It is
written for a coding agent working in the AeroDyn workspace described below, and is equally usable
by a person.

## What This Project Is

**VIT (Verified Incremental Translator)** orchestrates extraction of runtime state from a Fortran function, wires a C++ translation into a verification kernel, compares its outputs with the Fortran's field by field (the target is bit-identical), and generates integration patches. VIT does NOT translate — it verifies and integrates translations written by humans or LLMs.

**KGen** instruments Fortran code to capture function inputs/outputs during simulation, then generates a standalone kernel for replay. VIT wraps KGen, and depends on the fork at `https://github.com/kevinmenear/KGen`, which carries fixes the original NCAR release lacks. KGen requires Python 2.7.

**ROSCO (complete, April 2026)** — first test case. 52 functions verified and integrated into a pure C++ `libdiscon.so`. See `docs/case-studies/rosco/PROJECT_PLAN.md`. Translation fork: `https://github.com/kevinmenear/ROSCO`.

**OpenFAST / AeroDyn (active, April 2026–)** — current target. AeroDyn is the aerodynamics module of OpenFAST: 39 Fortran source files, about 75,000 lines (46,803 hand-written), 507 hand-written procedures, roughly 10× ROSCO's scope. Translation fork: `https://github.com/kevinmenear/openfast`. See the plan beside this manual, `docs/case-studies/aerodyn/PROJECT_PLAN.md`.

## Workspace and Container

The workflow assumes one directory holding three checkouts, mounted at `/workspace` in a container named `vit-dev-openfast`:

| Checkout | Repository | Remote |
|----------|------------|--------|
| `vit/` | VIT (this repository) | `git@github.com:NatLabRockies/vit.git` |
| `KGen/` | The KGen fork | `git@github.com:kevinmenear/KGen.git` |
| `openfast/` | The translation fork of OpenFAST | `origin` = `git@github.com:kevinmenear/openfast.git`; `upstream` = `https://github.com/OpenFAST/openfast.git` |

The OpenFAST fork holds the translation state: `vit.yaml`, `translations/`, `kernel/`, the integrated sources, the baselines in `baselines/aerodyn/`, and the `scripts/` used below. Its `vit.yaml` sets `kgen.path` to `/workspace/KGen/bin/kgen`.

Build the image from `docker/Dockerfile` with OpenFAST's build dependencies (see `docker/README.md`). Then, from the workspace directory, start the container with the VIT checkout mounted over the image's copy, so `vit` runs your checkout:

```bash
docker build -f vit/docker/Dockerfile -t vit-openfast \
    --build-arg EXTRA_APT_PACKAGES="liblapack-dev libopenblas-dev libyaml-cpp-dev" \
    --build-arg EXTRA_PIP_PACKAGES="vtk bokeh nptdms pandas" vit
docker run -d --name vit-dev-openfast \
    -v "$PWD":/workspace -v "$PWD/vit":/opt/vit vit-openfast sleep infinity
```

The container name matters: `integrate_all.sh` and the commands below call `docker exec vit-dev-openfast`. Steps marked "on the host" run `git` on the host and reach the container through `docker exec`.

This manual was VIT's root `CLAUDE.md` until 2026-09-30; VIT's root `CLAUDE.md` now covers developing VIT itself. To have an agent load this manual, point the workspace's own instructions file at it: for Claude Code, a `CLAUDE.md` in the workspace directory containing `@vit/docs/case-studies/aerodyn/AGENT_MANUAL.md`.

**Not yet verified:** the 17 baselines are specific to ARM64 with OpenBLAS, and were produced in the original development container. They have not been reproduced in an image built from `docker/Dockerfile`. Do that before trusting a baseline comparison made in a new image.

Commit when asked. Do not push unless explicitly asked. Confirm before running any destructive git command.

## Project Status — AeroDyn

Status as of 2026-09-30. The fork's `vit.yaml` lists 32 functions, all integrated, with 17/17 baselines passing at the last integration (2026-07-24).

- **Phase 0 (Registry C++ generator): COMPLETE.**
- **Phase 1 (AirfoilInfo + DBEMT): every function planned for translation is integrated.** Next in the plan: Phase 2, BEMTUncoupled and UnsteadyAero.
- **AirfoilInfo: COMPLETE (20/20).** `AFI_Init` is a hand-written two-pass special case, re-integrated 2026-07-24 (dev note `202607240452`). Five verification approaches proven (KGen, test-validate, file-output, simulation baselines, custom Fortran test harness). Shared NWTC utility library (`vit_nwtc.h/.cpp`).
- **DBEMT: 12 of 14 procedures in C++.**
  - **Phase A (leaf math): COMPLETE (5/5).** ComputeTau2, ComputeTau1, DBEMT_InitStates, DBEMT_CalcOutput, DBEMT_CalcContStateDeriv.
  - **Phase B (time integrators): COMPLETE (3/3).** RK4, AB4, ABM4. RK4 and AB4 also passed 20/20 KGen. **ABM4 was never kernel-verified** — its only call site is inside `DBEMT_UpdateStates`, whose kernel segfaults in `kr_dbemt_types_dbemt_elementinputtype` deserializing the `u_elem(2)` local array. It was gated transitively through its kernel-verified callees plus 16,920 baseline-exercised calls in `ad_BAR_SineMotion_UA4_DBEMT3` (dev note `202604300017`).
  - **Phase C (orchestrators): COMPLETE (2/2).** UpdateStates (20/20 KGen + 17/17 baselines), InitStates_AllNodes (4/4 test-validate + 17/17 baselines).
  - **Phase D (init/validation/end): ValidateInitInp and ReInit integrated.** `DBEMT_Init` stays in Fortran: init functions are not translated during incremental translation, because the Fortran wrapper needed for INTENT(OUT) types with ALLOCATABLE fields outweighs the translation (dev note `202605011207`). `DBEMT_End` is out of scope: it only calls Registry-generated Destroy routines.
- **Scope:** All of AeroDyn — 507 hand-written procedures, Registry C++ generator (740 generated procedures), drivers/C-binding. End state: pure C++ AeroDyn module, no Fortran, no libgfortran.
- **Verification:** KGen float64 kernel + `vit test-validate` branch-coverage + custom Fortran test harness (zero-coverage functions) + 17-case simulation bit-identical + NREL regression 17/17.
- **Translation boundary:** Everything inside AeroDyn → C++. Everything outside (NWTC Library, InflowWind, SeaState, LinDyn) → stays Fortran, crossed via BIND(C) / view structs. Individual NWTC utility functions used by AeroDyn translated in `vit_nwtc.h`.
- **Key architectural decisions** (dev note `202604240012`): Registry generator for Registry types, VIT `analyze-types` for hand-written types. Bottom-up phasing by dependency layer. `-ffp-contract=off` during translation. See the plan for full scope decisions and open questions.
- **Structural inventory** (dev note `202604232137`): 507 hand-written procedures, 247 leaf functions, 5 sub-systems, 176 derived types (150 AeroDyn, 26 from the NWTC Library), 1 SAVE-state procedure. The plan adds 14 genuinely dead procedures (excluded) and 10 C-binding entry points.
- **Line coverage data:** `docs/case-studies/aerodyn/data/line_coverage.json` in VIT's repository (gcov, generated 2026-04-25 from clean source: the fork at `c4cfa46e3`, with `AirfoilInfo.f90` restored from `edab46311` by `reset_to_clean.sh`). Regenerate from clean source: `docker exec vit-dev-openfast bash -c "cd /workspace/openfast && bash scripts/generate_coverage.sh"`.

## AeroDyn Workflow

VIT configuration: `openfast/vit.yaml`. Its `project.source_dir` is the broad `modules/`, so KGen finds the NWTC Library sources AeroDyn USEs; `fortran.flags` (`-cpp -fdefault-real-8 -fdefault-double-8 -ffp-contract=off`) match OpenFAST's build; and `fortran.preprocessor_defines` sets `OPENFAST_DOUBLE_PRECISION` and `HAS_FORTRAN2008_FEATURES`. Build flags: `FP_CONTRACT_OFF=ON` in `CMakeLists.txt` sets `-ffp-contract=off` on both compilers. Registry headers: `openfast_registry input.txt -cppcode-double -O output_dir`.

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

**Callee declarations:** VIT handles these automatically. During kernel verification: `vit_kernel_callees.h`. During integration: `vit_translated.h`. No `extern "C"` boilerplate needed.

**Always use `vit translate` to seed translations.** Do not write C++ from scratch. The scaffold includes callee `_c` signatures with OPTIONAL parameter handling that isn't obvious from the Fortran source. Skipping it leads to signature mismatches. The prompt's guidance section is **generated from VIT's check registry** (`vit/checks.py`), and the list of callable shared functions is **read from the shared headers** (`vit/header_api.py`) — neither is hand-maintained, so neither can drift. When you learn something from a translation failure, add a *check* (`vit check --list` shows the registry) rather than prose; if it has no precise detector, record it in `PUSHED_TO_MUTATION` or `TOOL_GAPS` with the reason. Re-running `vit translate` refreshes the scaffold only while it is still an untouched placeholder (it contains `// TODO:`). Once you have written an implementation, VIT keeps it and says so.

**`vit translate` auto-generates type infrastructure.** When it encounters a derived type with ALLOCATABLE fields that has no strategy configured in vit.yaml, it automatically: (1) sets `strategy: view` in vit.yaml, (2) writes/updates `vit_types.h` with the C view struct, (3) creates the Fortran view populator module (.f90). No manual type setup needed before translating. `vit integrate` then auto-adds the populator .f90 to CMakeLists.txt.

**`--reverse-copy` flag:** Use when the function modifies scalar fields in view-type INOUT arguments. Not needed when only ALLOCATABLE arrays are modified (those work through `C_LOC` pointers).

**Operator precedence: Fortran `a*x**2` ≠ C++ `a * x * x`.** Fortran's `**` binds tighter than `*`, so `a*x**2` means `a * (x*x)`. C++ evaluates `a * x * x` left-to-right as `(a*x) * x`. The different intermediate rounding produces 1-3 ULP differences for certain values. Always parenthesize: `a * (x * x)`. This is enforced by the `exponent-grouping` check, which excludes the two benign shapes (`x*x*x`, and a leading power-of-two literal). It needs the Fortran source: run `vit check translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90`.

**Vectorization fix for DBEMT:** `modules/aerodyn/CMakeLists.txt` sets `-fno-tree-loop-vectorize` on `DBEMT.f90`. Replacing a module-internal function body with a wrapper changes gfortran's vectorization decision for the caller's `exp()` loop (scalar libm vs SIMD libmvec, 1 ULP difference). The DBEMT exponential filter feedback loop amplifies this through UA-coupled timesteps. The flag ensures consistent scalar `exp()` regardless of wrapper insertion. Same root cause as the kernel verification libmvec issue (dev note `202604260357`). Other modules with similar sensitive feedback loops may need the same treatment.

**Verification methods:** See `docs/VERIFICATION_METHODS.md` for test-validate, file-output comparison, dual-call harnesses, program-level comparison, and FMA debugging. AeroDyn's program-level checks, run from `/workspace/openfast` (each takes a case name or `all`): `scripts/verify_aerodyn_baselines.sh` (bit-identical against the 17 committed baselines), `scripts/run_nrel_regression.sh` (NREL's regression at OpenFAST's default tolerances), and `scripts/check_aerodyn_nrel_drift.sh` (an informational drift report). Generate a new baseline only from the clean Fortran build, with `scripts/generate_aerodyn_baseline.sh`.

**Cycle telemetry.** Every `vit` command logs a start/end event to `.vit/cycle_log.jsonl` in the directory it runs from (override with `VIT_TELEMETRY_LOG`, disable with `VIT_TELEMETRY=0`). Joined against Claude Code session transcripts, this yields per-function tokens, wall clock, and attempts to first verification pass. From the VIT checkout:

```bash
python3 -m vit.telemetry join --cycle-log ../openfast/.vit/cycle_log.jsonl \
    --transcripts ~/.claude/projects/<project-key>/ \
    --out per_function.csv
```

The two cycle steps that are not `vit` commands must log themselves, or rebuild and regress time vanishes from the totals:

```bash
python3 -m vit.telemetry start --function FuncName --command regress
python3 -m vit.telemetry end   --function FuncName --command regress --exit-code $rc
```

Attribution assumes one function at a time. Interleaved work produces overlapping spans, which the joiner flags. Read `vit/telemetry.py`'s module docstring before trusting any number it produces.

**VIT reference:** See `docs/VIT_REFERENCE.md` for CLI commands, config fields, exit codes, and known limitations.

## Workflow Discipline

**Follow the established workflow steps exactly.** The 7-step translation workflow (extract → translate → write → verify → integrate → rebuild → baselines) exists because each step was validated through hard-won debugging. Every step matters — skipping integration, deferring baselines, or changing the verification approach leads to hidden problems that compound later.

**If something seems like it shouldn't work (e.g., integration causes divergence), the workflow is probably right and the assumption is wrong.** Before proposing any deviation from the workflow:
1. Investigate thoroughly — check for translation bugs, compiler flag issues, stale state, wrong test case
2. Verify your hypothesis with evidence (assembly diffs, debug prints, isolated tests)
3. Only after exhausting the investigation, bring the question to the user with your findings
4. Never conclude "the workflow doesn't work for this case" without rigorous proof — the ROSCO project integrated 52 functions using this exact workflow

**Two verification layers, both essential:**
- **KGen kernel (step 4, 20 cases):** Fast smoke test. Catches structural bugs — wrong types, wrong indexing, wrong control flow. Per-field diagnostics tell you exactly which output diverged. But 20/20 IDENTICAL does not prove correctness — it only covers 20 inputs out of tens of thousands of simulation calls. Input-dependent bugs (e.g., operator precedence rounding at specific values) can hide in the sample gap.
- **Baselines (step 7, 17 test cases):** Ground truth. Catches everything that reaches simulation output, including feedback-amplified sub-ULP differences. When KGen passes but baselines fail, **investigate the translation first** — not the compiler, not the infrastructure. The ComputeTau2 `a*x**2` bug (dev note `202604281854`) was exactly this: 20/20 IDENTICAL in KGen, catastrophic divergence in baselines, root cause was a parenthesis in the C++ translation.

**When baselines fail after integration:** Add a dual-computation debug print to the Fortran wrapper — compute the result using both original Fortran expressions AND the C++ call, compare per-invocation. This finds value-level mismatches in seconds.

**Never inline a function to work around an infrastructure problem.** If a callee bridge fails, a type doesn't map, or a Fortran pattern doesn't translate cleanly — fix the infrastructure. Inlining a callee's logic into the caller violates DRY, hides the real bug, and means the next function that calls the same callee hits the same wall. Every instance of "I'll just inline this" in this project has been wrong: ComputeTau2 inlining masked three callee bridge bugs; MINLOC/MAXLOC inline loops were copy-pasted 14 times before extraction. If a function exists in Fortran, call it through a bridge or translate it — don't copy its body.

**When KGen extraction fails, it is almost never a KGen bug.** Investigate the actual cause — wrong pragma placement, stale artifacts, wrong file, wrong test case — before attributing to KGen. Trace through KGen's Python source (`kgen/parser/kgparse.py`, `kgen/parser/kganalyze.py`, `kgen/extractor/plugins/gencore/gen_kernel_callsite_file.py`) and compare the failing case against a successful extraction. Every "KGen bug" in this project has turned out to be a usage error (e.g., placing the pragma at a function definition instead of a call site — see dev note `202605010623`). If investigation reveals a genuine KGen bug, fix it in the KGen fork — do not work around it with custom test harnesses or alternative verification. KGen is in our control; workarounds leave the bug for the next function.

## Integration State Management (reset + integrate_all)

Extraction requires clean (pre-integration) Fortran source. `reset_to_clean.sh` restores AirfoilInfo.f90 and DBEMT.f90 to the last upstream commit before integration (`PRE_INTEGRATION_COMMIT` in the script), stubs every per-function .cpp file and view populator module, and removes KGen artifacts. After extraction, `integrate_all.sh` restores the fully-integrated state.

**The pair must be used together.** Without `integrate_all.sh`, early leaf function wrappers are lost.

**After every successful integration (17/17 baselines pass), update both scripts** to include the new function's source files. Add the integrated .f90 to `reset_to_clean.sh`'s restoration loop and the .f90/.cpp to `integrate_all.sh`'s restore steps. Commit the script updates alongside the integration. This ensures the reset/restore cycle works for the next extraction.

**Commit before testing the reset+integrate cycle.** `integrate_all.sh` uses `git checkout HEAD --` to restore files. If new files (view populators, .cpp) haven't been committed yet, the restore fails. Always: integrate → baselines → commit → then verify reset+integrate works.

```bash
# On the host, from openfast/ (git commands + docker exec for vit):
bash scripts/reset_to_clean.sh                    # clean source for extraction
# ... run extraction inside container ...
bash scripts/integrate_all.sh                      # restore every integrated function
# Rebuild inside container:
docker exec vit-dev-openfast bash -c "cd /workspace/openfast/build && cmake .. && cmake --build . --target aerodyn_driver -j\$(nproc)"
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && bash scripts/verify_aerodyn_baselines.sh all"
```

**What `integrate_all.sh` does:**
1. Restores committed source files from git (AirfoilInfo.f90 and DBEMT.f90 with wrappers, view populator modules, production bridges, the DBEMT .cpp files)
2. Runs `vit integrate` for 14 AirfoilInfo functions and 10 DBEMT functions. The other 6 AirfoilInfo functions (ReadAFfile, AFI_WrHeader, AFI_WrData, AFI_WrTables, AFI_Init, and the standalone CheckValuesAreUniqueMonotonicIncreasing unit) and the other 2 DBEMT functions (ComputeTau1, ComputeTau2) are hand-integrated in the committed files
3. Restores committed .cpp files (VIT's generated versions have duplicate `extern "C"` wrappers; committed versions are correct)

The script runs on the host (needs git + docker exec). To run inside the container, replace `docker exec ... vit integrate` with direct `vit integrate`.

## AeroDyn Test Case Coverage Map

The r-test suite has 18 AeroDyn cases. 17 have committed baselines in `openfast/baselines/aerodyn/`; `ad_Sphere_OLAF` has none. The line coverage data also covers 17 cases, but a different 17: it includes `ad_Sphere_OLAF` and lacks `ad_MHK_RM1_Fixed_UA`.

| Test Case | UA | AFTab | Wake | DBEMT | Cavit | Notes |
|-----------|-----|-------|------|-------|-------|-------|
| ad_BAR_SineMotion | 0 | 1 | 1 (BEMT) | 1 | - | **Default.** No UA. Most BEMT-path functions. |
| ad_BAR_SineMotion_UA4_DBEMT3 | 4 | 1 | 1 (BEMT) | 3 | - | **UA init path.** CalculateUACoeffs, UA separation. |
| ad_BAR_CombinedCases | 0 | 1 | 1 (BEMT) | 1 | - | Combined analysis (AnalysisType=3). |
| ad_BAR_RNAMotion | 0 | 1 | 1 (BEMT) | 0 | - | RNA motion, no DBEMT. |
| ad_BAR_OLAF | 0 | 1 | 3 (OLAF) | - | - | OLAF wake model. |
| ad_MHK_RM1_Fixed | 0 | 2 | 1 (BEMT) | 2 | Yes | **Multi-table Re interp.** |
| ad_MHK_RM1_Fixed_UA | 4 | 2 | 1 (BEMT) | 2 | No | **2D UA interp.** Not in the coverage data. |
| ad_MHK_RM1_Fixed_IfW | 0 | 2 | 1 (BEMT) | 2 | Yes | MHK_Fixed + InflowWind. |
| ad_MHK_RM1_Floating | 0 | 2 | 1 (BEMT) | 2 | Yes | Floating MHK, cavitation. |
| ad_Kite_OLAF | 3 | 1 | 3 (OLAF) | - | - | **UA_Mod=3** + OLAF. |
| ad_B1n2_OLAF | 0 | 3 | 3 (OLAF) | - | - | **AFTabMod=3** + OLAF. |
| ad_Sphere_OLAF | 0 | 3 | 3 (OLAF) | - | - | AFTabMod=3, spherical. No baseline (coverage only). |
| ad_VerticalAxis_OLAF | 0 | 2 | 3 (OLAF) | - | - | **VAWT.** |
| ad_EllipticalWingInf_OLAF | 0 | 1 | 3 (OLAF) | - | - | Infinite elliptical wing. |
| ad_HelicalWakeInf_OLAF | 0 | 1 | 3 (OLAF) | - | - | Infinite helical wake. |
| ad_MultipleHAWT | 0 | 1 | 1 (BEMT) | 0 | - | 2-turbine HAWT. |
| ad_QuadRotor_OLAF | 0 | 1 | 3 (OLAF) | - | - | 4-rotor quad. |
| ad_timeseries_shutdown | 0 | 1 | 1 (BEMT) | 0 | - | Time-dependent (AnalysisType=2). |

**Test case selection guide:**
- **General BEMT functions:** `ad_BAR_SineMotion` (default)
- **UA-dependent functions:** `ad_BAR_SineMotion_UA4_DBEMT3`
- **Multi-table interpolation:** `ad_MHK_RM1_Fixed`
- **2D UA interpolation:** `ad_MHK_RM1_Fixed_UA`
- **OLAF/FVW functions:** `ad_BAR_OLAF` or `ad_Kite_OLAF`
- **DBEMT functions (Modes 1/2):** `ad_MHK_RM1_Fixed` (DBEMT_Mod=2; 38,400 calls to `DBEMT_UpdateStates`, more than any other case)
- **DBEMT functions (Mode 3 only):** `ad_BAR_SineMotion_UA4_DBEMT3` (DBEMT_Mod=3, ONLY test with Mode 3)

This guide is a starting point. For a specific call site, the coverage query in the next section is definitive.

## Extraction Workflow (AeroDyn)

Extraction requires clean (pre-integration) source. Line numbers shift after each integration, so always find them fresh.

**Step 1 — Query coverage data for the call site and test case:**
```bash
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && vit find FunctionName --coverage /workspace/vit/docs/case-studies/aerodyn/data/line_coverage.json"
```
This lists every call site (file:line, and the procedure it is in) with the test cases that run it and how often, and ends with the call site and test case with the most executions. Pick that pair unless there is a reason not to. Its line numbers are those of the source the coverage was measured on, and it marks any it could not read that way; if the clean source of Step 2 differs, `vit find FunctionName` there gives the same call's current line. **Do not manually trace code paths to guess test cases** — the coverage data is definitive (see dev note `202604250803`).

If coverage data doesn't exist yet, generate it (once, ~10 min):
```bash
bash scripts/reset_to_clean.sh    # On the host, from openfast/
docker exec vit-dev-openfast bash -c "cd /workspace/openfast && bash scripts/generate_coverage.sh"
```

**Step 2 — Reset source to clean Fortran:**
```bash
bash scripts/reset_to_clean.sh    # On the host, from openfast/, not in the container
```

**Step 3 — Update `vit.yaml`** with the test case from Step 1.

**Step 4 — Rebuild with clean source** (inside container):
```bash
cd /workspace/openfast/build && cmake --build . --target aerodyn_driver -j$(nproc)
```

**Step 5 — Extract at a CALL SITE, not the function definition** (inside container):
```bash
cd /workspace/openfast && vit extract FunctionName -f modules/aerodyn/src/CallerFile.f90 -l CALL_LINE
```
**CRITICAL: The `-f` file and `-l` line must point to a CALL site (where the function is called from), NOT the function definition.** KGen requires the pragma to be inside a subroutine scope. When the pragma wraps a module-level subroutine definition, KGen's parentblock becomes the MODULE (not a subroutine), and it places `INTENT(IN/OUT)` declarations at module scope — invalid Fortran, and nothing is captured.

Use the output of Step 1 to pick the call site. For example, to extract `DBEMT_ReInit`, Step 1 shows (abridged):
```
2 call site(s) for 'DBEMT_ReInit'. Line numbers are those of the source the coverage was measured on, except where marked:
  modules/aerodyn/src/BEMT.f90:778 in BEMT_ReInit
    call DBEMT_ReInit(p%DBEMT, x%DBEMT, OtherState%DBEMT, misc%DBEMT)
    ad_BAR_CombinedCases: 4
  modules/aerodyn/src/DBEMT.f90:231 in DBEMT_Init
    call DBEMT_ReInit(p, x,OtherState,m)
    ad_BAR_CombinedCases: 3
Most executions: ad_BAR_CombinedCases runs modules/aerodyn/src/BEMT.f90:778 4 times.
```
Use `-f modules/aerodyn/src/BEMT.f90 -l 778`, not `-f modules/aerodyn/src/DBEMT.f90 -l 236` (the definition). See dev note `202605010526` for the full diagnosis.

**Step 6 — Restore integrated source:**
```bash
bash scripts/integrate_all.sh    # On the host, from openfast/ — restores every integrated function
```
Then rebuild.

**Step 7 — Restore `vit.yaml` `run_cmd`** to the default (`ad_BAR_SineMotion`) if it was changed.

**If extraction reports `✗ Extraction captured no state data`:** Most common causes: (1) pragma at a function DEFINITION instead of a CALL site (see Step 5), (2) wrong test case, (3) source not clean, (4) `kgen_utils.f90` never got compiled into the instrumented library. Cause 4 was the hardest to spot because *everything reported success*: KGen instruments calls to `kgen_init_vars`/`kgen_check_save`, VIT's build wrapper `sed`s `kgen_utils.f90` into `CMakeLists.txt`, and if that step misses, a Linux shared library still links with the symbols unresolved. The build passes, the simulation runs, and only the capture comes back empty. The wrapper inserts `src/kgen_utils.f90` after the first line of the build file that holds a single Fortran source path, and copies the file into a `src/` directory beside the build file; when no line matches it now stops with an error naming `kgen.add_source_cmd`, the override. To check, run the extraction with `VIT_WRAPPER_LOG=/tmp/vit.log` and confirm the wrapper reported patching `CMakeLists.txt`, or just `grep kgen_utils.f90` the target's `CMakeLists.txt` mid-build. See dev note `202608071141`. Also see `docs/EXTRACTION_INTERNALS.md` Section 7.

**Other pitfalls:**
- KGen can leave `AirfoilInfo_Types.f90` and `AeroDyn_Driver.f90` modified. Check `git status` after extraction and `git checkout` them.
- **NWTC constants are set at run time.** `Pi`, `D2R`, `R2D` and their double-precision versions are module variables assigned by `SetConstants`, which `NWTC_Init` calls (`NWTC_Num.f90`, `NWTC_Library.f90`); they are not PARAMETERs. A test-validate or file-output bridge that runs Fortran using them must `CALL NWTC_Init()` first, or the Fortran side computes with zeros.
- If a kernel fixture already exists, its `kernel_driver.f90` contains the actual call site — this is ground truth.

## Shared C++ Headers

Translations share these files, which live in `openfast/modules/aerodyn/src/`. All are listed in `vit.yaml` `shared_files`, so VIT copies them into kernel directories:

- **`vit_nwtc.h` / `vit_nwtc.cpp`** — C++ translations of NWTC Library utility functions (SetErrStat, EqualRealNos, InterpStp, CubicSplineInitM, etc.) and constants (ErrID_None, AbortErrLev, Pi, ErrMsgLen). Small functions are `inline` in the `.h`; larger functions (cubic spline, fZeros, kernelSmoothing) are in the `.cpp` and compiled once. See `docs/case-studies/aerodyn/NWTC_UTILITIES.md` for the full reference.
- **`vit_fortran_intrinsics.h`** — Header-only templates for Fortran array intrinsics with no C++ standard library equivalent: `fortran_minloc`, `fortran_maxloc` (templated on value/mask lambdas), `fortran_cshift`. Use these instead of writing inline loops.
- **`vit_aerodyn_constants.h`** — AeroDyn module-level constants: the AFTabMod, UA_Mod and DBEMT_Mod values.
- **`vit_types.h`** — the C view structs for derived types, written by `vit translate` (see above).

`vit translate` reads the callable inventory straight out of the shared headers, so adding a function to one makes it visible to the next translation with no other edit.

When adding a new shared function: if it's small enough to inline (under ~15 lines, no complex loops), put it `inline` in the `.h`. If it's larger, declare in the `.h` and implement in the `.cpp`.

## Development Notes Convention

Notes live in VIT's `dev/` directory and are named `YYYYMMDDHHMM-descriptor.md`. Dev notes are committed to the VIT repo, not to ROSCO or OpenFAST.

When creating a dev note:
1. Run `date '+%Y%m%d%H%M'` — do NOT guess timestamps
2. Use the standard header: `# Title` / `**Date:** YYYY-MM-DD` / `**Status:** completed | in-progress | abandoned` / `**Scope:** [area]`
3. Provide comprehensive detail — background, rationale, decisions, alternatives, next steps
