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
- **Current stage:** **Phase 1 proof-of-concept complete.** Full VIT pipeline (extract → verify → integrate) proven on AeroDyn with Calculate_Cn: 20/20 IDENTICAL kernel verification, 16/16 regression tests pass with integrated C++. Phase 0 (Registry C++ generator) complete. 12 VIT fixes and 4 infrastructure changes committed for AeroDyn support. See "AeroDyn Workflow" section below for extraction/verification/integration commands.
- **Scope decisions (committed 2026-04-23, dev note `202604232342`):**
  1. **Breadth:** All of AeroDyn — all 5 sub-systems (BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, core AeroDyn), all 507 hand-written procedures
  2. **Registry types:** Build a C++ code generator for the OpenFAST Registry (replaces 740 auto-generated Fortran procedures)
  3. **Drivers/C-binding:** Translate everything — full C++, no Fortran remaining
  4. **Verification:** Three-tier, same as ROSCO — KGen float64 kernel verification + 16-case simulation bit-identical + NREL regression 16/16
  5. **Done criteria:** Pure C++ AeroDyn module — no Fortran source in the build, no libgfortran, 16/16 regression pass, AeroDyn Driver functional, C-binding preserved
- **Key architectural decisions (committed 2026-04-24, dev note `202604240012`):**
  1. **Type strategy:** Registry generator for Registry types, VIT `analyze-types` for hand-written types (clean tool separation)
  2. **KGen extraction:** Both AeroDyn Driver (primary, 16 r-test cases) AND full OpenFAST (for coupled-mode-only functions like linearization)
  3. **Phasing:** Bottom-up by dependency layer — Registry generator (Phase 0) → leaf modules → mid-level → orchestrators → drivers/C-binding (Phase 7)
  4. **FMA:** `-ffp-contract=off` during translation, re-enable on pure C++ build (same as ROSCO)
  5. **NWTC Library:** Stays Fortran, C interop boundary (shared framework, separate project to translate)
  6. **External modules:** InflowWind, SeaState, LinDyn stay Fortran, accessed via C interop (read-only data providers)
- **Translation boundary:** Everything inside AeroDyn → C++. Everything outside (NWTC Library, InflowWind, SeaState, LinDyn, OpenFAST glue code) → stays Fortran, crossed via BIND(C) / view structs.
- **Open questions (10, dev note `202604240016`):** **Answered:** OQ1 (Registry feasible, dev note `202604240609`), OQ2 (KGen works on AeroDyn cmake, dev note `202604241004`), OQ3 (287 external callees, ~150 wrappers), OQ5 (OpenMP simple parallel DO), OQ6 (5 POINTERs, all non-owning, dev note `202604240609`), OQ7 (MeshType view struct ~80 lines, not needed until Phase 4, dev note `202604240640`). **Partially answered:** OQ4 (r-test coverage), OQ8 (VIT on AeroDyn — 12 fixes applied, simple functions work, derived types not yet tested). **Open:** OQ9 (upstream sync), OQ10 (full OpenFAST build).
- **Structural inventory findings (dev note `202604232137`):** 507 hand-written top-level procedures, 247 leaf functions (first translation targets), 5 semi-independent sub-systems, 176 derived types (150 AeroDyn + 26 NWTC deps), 14 genuinely dead procedures (660 lines, excluded from scope), 3 confirmed framework interface procedures, 10 C-binding entry points, only 1 procedure with SAVE state. External dependencies: NWTC Library (framework), InflowWind, SeaState, LinDyn.
- **Verification infrastructure:** 16 AeroDyn regression test cases with committed baselines (`openfast/baselines/aerodyn/`), regenerated 2026-04-24 with `-ffp-contract=off`. Three verification scripts: `verify_aerodyn_baselines.sh` (bit-identical gate), `run_nrel_regression.sh` (NREL 1e-2 correctness gate, 16/16 PASS), `check_aerodyn_nrel_drift.sh` (informational drift reporter). **After integrating any function, run `verify_aerodyn_baselines.sh all` to confirm 16/16 pass.**
- **Next step:** Scale Phase 1 — translate remaining AirfoilInfo functions (next target: a function with derived type arguments to test VIT's type handling), then DBEMT. Infrastructure proven on Calculate_Cn. See `PROJECT_PLAN_AERODYN.md` Phase 1 for full scope.
- **Methodology reference:** `CREATING_PROJECT_PLANS.md` — read this before drafting new plan documents.

### AeroDyn Workflow

VIT configuration is at `openfast/vit.yaml`. Key differences from ROSCO:
- `source_dir: modules` — broad, so KGen can find NWTC Library sources for USE resolution
- `fortran.flags: -cpp -fdefault-real-8 -fdefault-double-8 -ffp-contract=off` — matches the OpenFAST project build
- `fortran.preprocessor_defines: [OPENFAST_DOUBLE_PRECISION, HAS_FORTRAN2008_FEATURES]` — passed as `-D` flags to KGen so it resolves `#ifdef` correctly
- `build_system.cmake_file: modules/aerodyn/CMakeLists.txt` — VIT auto-detects which `add_library` target contains the source file

**Translation workflow (per function):**

```bash
# 1. Extract (inside vit-dev-openfast container, from /workspace/openfast)
vit extract FuncName -f modules/aerodyn/src/File.f90 -l LINE

# 2. Generate scaffold
vit translate FuncName -f modules/aerodyn/src/File.f90 --module ModuleName

# 3. Write the C++ implementation in the generated scaffold

# 4. Verify (20/20 IDENTICAL expected)
vit verify FuncName translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90 --kernel-dir kernel/FuncName

# 5. Integrate (auto-patches AirfoilInfo.f90 AND CMakeLists.txt)
vit integrate FuncName translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90 --apply

# 6. Rebuild
cd build && cmake .. && cmake --build . --target aerodyn_driver -j$(nproc)

# 7. Regression test (16/16 must pass)
cd /workspace/openfast && ./scripts/verify_aerodyn_baselines.sh all
```

**Build flags:** The OpenFAST fork has `FP_CONTRACT_OFF=ON` (default) in `CMakeLists.txt`, which sets `-ffp-contract=off` on both Fortran and C++ compilers. This is required for bit-identical verification during the translation phase. For production pure-C++ builds after translation: `cmake .. -DFP_CONTRACT_OFF=OFF`.

**Registry C++ headers:** Generate with `-cppcode-double` flag for correct ReKi→double mapping: `openfast_registry input.txt -cppcode-double -O output_dir`

**Extraction parameters:** Per-function extraction details (callers, test cases, invocation ranges) are tracked in `EXTRACTION_PARAMS.md` in the OpenFAST repo. See also the call graph at `vit/analysis/aerodyn/call_graph.json` for tracing complex call paths.

### AeroDyn Test Case Coverage Map

17 regression test cases with different mode combinations. Use this to pick the right test case for KGen extraction based on which code path the target function is on.

| Test Case | UA | AFTab | Wake | DBEMT | Cavit | Notes |
|-----------|-----|-------|------|-------|-------|-------|
| ad_BAR_SineMotion | 0 | 1 | 1 (BEMT) | 1 | - | **Default.** No UA. Most BEMT-path functions. |
| ad_BAR_SineMotion_UA4_DBEMT3 | 4 | 1 | 1 (BEMT) | 3 | - | **UA init path.** CalculateUACoeffs, UA separation functions. |
| ad_BAR_CombinedCases | 0 | 1 | 1 (BEMT) | 1 | - | Combined analysis type (AnalysisType=3). |
| ad_BAR_RNAMotion | 0 | 1 | 1 (BEMT) | 0 | - | RNA motion, no DBEMT. |
| ad_BAR_OLAF | 0 | 1 | 3 (OLAF) | - | - | OLAF wake model, BAR turbine. |
| ad_MHK_RM1_Fixed | 0 | 2 | 1 (BEMT) | 2 | Yes | **Multi-table Re interp.** FindBoundingTables, AFI_ComputeAirfoilCoefs2D. |
| ad_MHK_RM1_Fixed_IfW | 0 | 2 | 1 (BEMT) | 2 | Yes | Same as above + InflowWind. |
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
- **OLAF/FVW functions:** `ad_BAR_OLAF` or `ad_Kite_OLAF` (latter adds UA)
- **DBEMT functions:** `ad_BAR_SineMotion` (DBEMT_Mod=1) or `ad_BAR_SineMotion_UA4_DBEMT3` (DBEMT_Mod=3)

### Extraction Workflow (AeroDyn)

Extraction requires clean (pre-integration) source. Line numbers shift after each integration, so always find them fresh.

**Step-by-step:**

1. **Reset source to clean Fortran:**
   ```bash
   # Run on Mac, not in container
   bash scripts/reset_to_clean.sh
   ```

2. **Find the call site line number.** Two methods:
   - **Simple grep** (leaf functions with 1-2 callers):
     ```bash
     grep -n "call FunctionName" modules/aerodyn/src/SourceFile.f90
     ```
   - **Call graph lookup** (complex call paths): check `vit/analysis/aerodyn/call_graph.json` to find callers and trace which mode flag gates the call.
   
   Pick the call site on a branch that the chosen test case exercises. See `EXTRACTION_PARAMS.md` for per-function details.

3. **Choose the test case** from the coverage map above. Update `vit.yaml` `run_cmd` if different from the current setting:
   ```yaml
   run_cmd: cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/TEST_CASE
       && /workspace/openfast/build/modules/aerodyn/aerodyn_driver ad_driver.dvr
   ```

4. **Rebuild with clean source** (inside container):
   ```bash
   cd /workspace/openfast/build && cmake --build . --target aerodyn_driver -j$(nproc)
   ```

5. **Extract** (inside container):
   ```bash
   cd /workspace/openfast && vit extract FunctionName -f modules/aerodyn/src/SourceFile.f90 -l LINE
   ```

6. **Restore integrated source:**
   ```bash
   # Run on Mac
   git checkout -- modules/aerodyn/src/AirfoilInfo.f90
   ```
   Then re-integrate all functions and rebuild (or use the committed integrated version if no new integrations were added).

7. **Restore `vit.yaml` `run_cmd`** to the default (`ad_BAR_SineMotion`) if it was changed.

**Common pitfalls:**
- KGen modifies `AirfoilInfo_Types.f90` and `AeroDyn_Driver.f90` (strips blank comment lines). Always `git checkout` these after extraction if they show as modified.
- If extraction reports "No state data captured", check: (a) correct line number, (b) test case exercises the code path, (c) source is clean (no integration wrappers referencing VIT modules).
- The `invocation` setting in vit.yaml controls how many calls KGen captures. Default `0:0:1-5` works for runtime functions; use `0:0:1-20` for init-time functions called once per airfoil (30 airfoils x 1 call = 30 total, but KGen counts per call site).

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

**If a translation shows `IN_TOL` (instead of `IDENTICAL`) in KGen field data with `-ffp-contract=off`**, it indicates a real translation bug — investigate the expression structure.

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

## Full Workflow: Start to Finish

All commands run inside the container from `/workspace/ROSCO`.

### 1. Build ROSCO

```bash
cd rosco/controller/build
rm -rf *
cmake .. -DCMAKE_INSTALL_PREFIX=install
cmake --build .
cp libdiscon.so ../../lib/libdiscon.so
cd /workspace/ROSCO
```

### 1b. Initialize Turbine Model (first run only)

On a fresh clone, the pickled turbine model does not exist yet. `vit_sim.py` (used by both extraction and baseline capture) loads `Examples/examples_out/01_NREL5MW_saved.p`, which is created by `01_turbine_model.py`. Run this once before any simulation:

```bash
cd Examples
python3 01_turbine_model.py
cd /workspace/ROSCO
```

Expected: Prints turbine info and creates `Examples/examples_out/01_NREL5MW_saved.p`.

This only needs to run once per fresh clone. Skip if the pickle already exists.

### 2. Extract (KGen State Capture)

This instruments the Fortran code, rebuilds the library, runs the simulation, and captures runtime state. Takes a couple of minutes.

```bash
rm -rf kernel/ state/ model.ini include.ini kgen.log
vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102 --run-args '--scenario 3'
```

Expected: `✓ Extraction successful` with `Cases: 20` (may be 20-23 due to KGen's ElapsedTime phase selecting additional invocations beyond the CLI range).

The `--file` flag takes the file with the **call site** (Controllers.f90), not the definition.

### 3. Verify (C++ Against Captured State)

```bash
vit verify saturate saturate.cpp \
  -f rosco/controller/src/Functions.f90 \
  --kernel-dir kernel/
```

Expected: `✓ VERIFICATION PASSED: 20/20 passed` (case count matches the extraction)

The `--file` flag takes the file with the function **definition** (Functions.f90).

### 4. Integrate (Apply Patches to Codebase)

```bash
# Review plan first:
vit integrate saturate saturate.cpp -f rosco/controller/src/Functions.f90

# Apply:
vit integrate saturate saturate.cpp -f rosco/controller/src/Functions.f90 --apply
```

All patches (Fortran modifications and CMake changes) are applied automatically by `--apply`. No manual edits needed. Multiple functions can be integrated sequentially — each `--apply` reads the current file state and generates correct patches (skipping `enable_language(CXX)`, `stdc++` link, and `USE ISO_C_BINDING` if already present).

### 5. Rebuild and Verify Simulation Output

```bash
cd rosco/controller/build
rm -rf *
cmake .. -DCMAKE_INSTALL_PREFIX=install
cmake --build .
cp libdiscon.so ../../lib/libdiscon.so
cd /workspace/ROSCO

cd Examples
python3 vit_sim.py > /workspace/ROSCO/integrated_output.txt 2>&1
cd /workspace/ROSCO
diff baseline_output.txt integrated_output.txt
```

Empty diff = the C++ translation produces byte-identical simulation output.

### Batch Workflow (All Functions)

For production integration of all 52 functions, use the batch scripts instead of repeating the single-function workflow:

```bash
# 1. Reset to clean Fortran (run on Mac, not in container)
bash scripts/reset_to_clean.sh

# 2. Build clean baseline + capture arrays (in container)
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
mkdir -p baseline_arrays && cd Examples && python3 vit_sim.py --output-dir /workspace/ROSCO/baseline_arrays && cd /workspace/ROSCO

# 3. Verify all translations against golden kernels
bash scripts/verify_all.sh    # 42/42 passed

# 4. Integrate all functions
bash scripts/integrate_all.sh  # 52/52 passed

# 5. Rebuild and compare all 27 scenarios
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
# Compare arrays (see Clean Build Test → Step 6 for the comparison script)

# 6. Prepare for commit (run on Mac, not in container)
bash scripts/prepare_commit.sh
# Restores .f90 to clean, src/*.cpp to committed state (via git ls-files), removes artifacts.
# Preserves baseline_arrays/ (canonical reference for Stage B simulation-level verification).
# Only genuinely new/changed files remain in git status.
```

Baseline simulation arrays (`baseline_arrays/`, 27 `.npz` files) are committed to ROSCO as canonical references for Stage B/C/D/E simulation-level verification. These Stage functions do not use KGen extraction — they are verified by comparing 27-scenario × 13-array simulation output against these baseline arrays. An `upstream_arrays/` directory (26 `.npz` files, skipping Scenario 4 where upstream ROSCO segfaults on the Flp_Mode=2 uninitialized-K bug) is also committed and used by `reset_to_upstream.sh` to prove that the type modifications in the fork (BIND(C), `INTEGER restart`, REAL AWC_complexangle pair) are behaviorally neutral vs upstream. `prepare_commit.sh` preserves `baseline_arrays/`.

### Non-KGen Verification (Stage B Functions)

Functions that cannot use KGen (file I/O, config parsing, functions with complex runtime dependencies) use simulation-level verification instead of kernel-level verification:

1. **Translate:** `vit translate FunctionName -f source.f90 --module ModuleName`
   - The translation prompt now includes ALLOCATABLE field hints for view types, listing which arrays may need pre-allocation in the wrapper. This helps translators understand which ALLOCATABLE fields in view-type arguments (e.g., ControlParameters) the function reads from, so the C++ translation can expect populated pointer+size pairs.

2. **Integrate:** `vit integrate FunctionName cpp_file -f source.f90 --apply --reverse-copy --auto-allocate`
   - `--reverse-copy`: Adds `vit_copy_scalars_to_<type>(view, original)` calls after the C++ function returns, for each view-type INOUT argument. This is needed when the C++ translation modifies scalar fields in the view struct that Fortran code downstream needs to see — the reverse-copy writes the updated scalar values back from the view into the original Fortran derived type. Without this flag, scalar modifications made by C++ are invisible to subsequent Fortran callers.
   - `--auto-allocate`: Scans the original Fortran source for `ALLOCATE` statements and emits corresponding allocation calls in the generated Fortran wrapper, before the view populator runs (so that `C_LOC()` sees allocated arrays). Safe allocations (size depends on other arguments or constants) are emitted directly. Unsafe allocations (circular same-argument dependency, sizes from local variables) are emitted as commented-out code with warnings for manual review.

3. **Verify:** `vit sim-compare baseline_arrays/ integrated_arrays/ [--tolerance 0.0]`
   - Compares `.npz` simulation output arrays between a baseline (pure Fortran) build and an integrated (C++) build across all scenarios.
   - Reports per-scenario, per-array results (IDENTICAL or MISMATCH with max difference). Exit code 0 if all arrays match, exit code 1 if any differ.
   - `--tolerance 0.0` (the default) requires exact bit-identical match. A non-zero tolerance allows bounded floating-point differences (useful for FMA sanity checks).
   - This replaces the inline Python comparison script for Stage B verification, providing a proper CLI command with structured output.

4. **View populator reverse-copy subroutine:** `vit analyze-types --scan-allocatable` now auto-generates a `vit_copy_scalars_to_<type>` subroutine alongside the forward `vit_populate_<type>_view` populator in each view populator module. The reverse-copy subroutine copies all non-ALLOCATABLE (scalar) fields from the C view struct back into the original Fortran derived type. This is the mechanism that `--reverse-copy` relies on — the integration wrapper calls this subroutine after the C++ function returns.

### Non-KGen Verification (Stage C Functions)

Stage C functions (WriteRestartFile, ReadRestartFile, Debug) in ROSCO_IO.f90 use manual integration in `integrate_all.sh` because their `CHARACTER(size_avcOUTNAME)` argument type is not supported by `vit translate` or `vit integrate`. They follow the same simulation-level verification as Stage B.

- **WriteRestartFile + ReadRestartFile**: Only triggered by `iStatus==-8/-9` (not exercised in standard 27-scenario sim-compare). Verified indirectly: sim-compare proves no regression from wrapper plumbing. Split into separate files (`translations/IO/writerestartfile.cpp`, `translations/IO/readrestartfile.cpp`) with a shared header (`translations/IO/restart_fields.h`) containing a `checkpoint_fields` template to guarantee identical field ordering.
- **ReadRestartFile wrapper**: The Fortran wrapper handles callee dispatch — after `readrestartfile_c` reads the checkpoint, the wrapper calls `ReadControlParameterFileSub` and `ReadCpFile` (already-translated Stage B functions) from the Fortran side. This avoids the C++ needing to handle the two-pass ALLOCATE protocol.
- **Debug**: Does not affect simulation output arrays. Verified via `scripts/debug_test.sh` which compares `.RO.dbg` data rows between baseline and integrated builds. Header formatting differs (Fortran `CHARACTER(15)` truncates column names; C++ does not) — only data rows are compared.

**Debug verification:**

```bash
# After building clean baseline:
bash scripts/debug_test.sh baseline

# After integrate_all.sh + rebuild:
bash scripts/debug_test.sh integrated

# Compare data rows:
bash scripts/debug_test.sh compare
# OK vit_sim3.RO.dbg: 15999 data rows IDENTICAL
```

Or run all steps: `bash scripts/debug_test.sh all`

### Non-KGen Verification (Stage D Functions)

Stage D functions (UpdateZeroMQ, ExtController) use manual integration due to platform-specific constructs (`#ifdef` preprocessor directives, guarded `ALLOCATE`). Verified via 27-scenario sim-compare.

- **UpdateZeroMQ**: ZeroMQInterface.f90 — ZeroMQ message interface. The original file has `#ifdef ZMQ_CLIENT` preprocessor directives that break VIT's Fortran parser. Manual integration replaces the entire module with a C++ wrapper. Since ZMQ is not active in the test scenarios, sim-compare verifies wrapper plumbing (no regression).
- **ExtController**: ExtControl.f90 — External controller DLL loader. Uses standard `vit integrate` plus a guarded `ALLOCATE` for `ExtDLL%avrSWAP` (conditional, not auto-allocatable). Sim-compare verified.

### Non-KGen Verification (Stage E Functions)

Stage E functions (PreFilterMeasuredSignals, SetParameters) are the final two non-DISCON orchestrators. Both are called every timestep, so 27-scenario sim-compare provides full coverage.

- **PreFilterMeasuredSignals**: Filters.f90 — filter cascade calling 6 already-translated filter `_c` entry points (LPFilter, SecLPFilter, HPFilter, NotchFilter, NotchFilterSlopes, wrap_180). Standard `vit integrate` with CntrPar view type. Double-indexed ALLOCATABLE array access (1-based → 0-based conversion).
- **SetParameters**: ReadSetParameters.f90 — initialization + per-timestep orchestrator. Manual integration with callee dispatch: Fortran wrapper handles per-timestep init (ErrVar clear, objInst counters, avrSWAP defaults), banner print, accINFILE save, ReadControlParameterFileSub call (two-pass ALLOCATE protocol), and ReadCpFile call. C++ handles LocalVar initialization (~25 fields), CheckInputs call, and OL_Index computation.

## VIT Configuration

The file `/workspace/ROSCO/vit.yaml` configures the pipeline. All paths must be absolute container paths (starting with `/workspace/`).

```yaml
project:
  name: ROSCO
  source_dir: rosco/controller/src
  build_dir: rosco/controller/build
kgen:
  path: /workspace/KGen/bin/kgen
  build_cmd: cd /workspace/ROSCO/rosco/controller/build && cmake --build .
  run_cmd: cd /workspace/ROSCO/Examples && python3 vit_sim.py
  clean_cmd: cd /workspace/ROSCO/rosco/controller/build && make clean
  install_cmd: cp /workspace/ROSCO/rosco/controller/build/libdiscon.so /workspace/ROSCO/rosco/lib/libdiscon.so
  invocation: 0:0:1-20
  assumed_size_arrays:
    avrSWAP: 3000
  dll_persistence:
    resets:
      - anchor: "CALL ReadAvrSWAP"
        code: "LocalVar%AlreadyInitialized = 0"
build_system:
  type: cmake
  cmake_file: rosco/controller/CMakeLists.txt
  target_name: discon
fortran:
  kind_aliases:
    ReKi: REAL(4)
    DbKi: REAL(8)
    SiKi: REAL(4)
    IntKi: INTEGER(4)
  compiler: gfortran
  flags: -fdefault-real-8 -fdefault-double-8 -ffp-contract=off
verification:
  tolerance: 1.0e-14
translations:
  saturate:
    status: integrated
    cpp_file: saturate.cpp
types:
  ControlParameters:
    strategy: view
  PerformanceData:
    strategy: view
  ExtControlType:
    strategy: view
allocatable:
  helpers:
    - name: ParseAry
      array_arg: 3
      size_arg: 4
```

Key fields:
- `kgen.path`: path to KGen entry point (requires Python 2.7)
- `kgen.build_cmd`, `run_cmd`, `clean_cmd`, `install_cmd`: commands KGen runs to build/run/clean the Fortran project
- `kgen.invocation`: selects which KGen invocations to capture state for (`0:0:1-20` means process 0, thread 0, invocations 1 through 20)
- `fortran.kind_aliases`: maps ROSCO's custom kinds to standard types (`DbKi → REAL(8)`, etc.)
- `fortran.flags`: extra Fortran compiler flags propagated to kernel Makefile (e.g., `-fdefault-real-8`). Without these, Fortran literals in the kernel may lose precision.
- `types`: maps derived type names to their handling strategy (`view` for ALLOCATABLE types, default is `bind_c`)
- `allocatable.helpers`: configurable allocation helper subroutines for ALLOCATABLE size scanning (name, array_arg position, size_arg position)
- `verification.tolerance`: maximum acceptable floating-point difference (`1.0e-14`)
- `translations`: tracks which functions have been verified/integrated
- `kgen.assumed_size_arrays`: (optional) maps assumed-size array parameter names to explicit sizes. VIT auto-detects assumed-size `(*)` arrays from the function signature and infers sizes from source evidence. If configured, these values are used as known-good overrides that skip confirmation. If not configured, VIT proposes sizes via `EXIT_CONFIRM`. KGen generates `DIMENSION(:)` write functions that can't accept `(*)` arrays — VIT patches the call sites to use explicit sections (e.g., `avrswap(1:3000)` instead of `avrswap`).
- `kgen.dll_persistence`: workaround for Fortran SAVE variables persisting across scenario boundaries within a single simulation run. `resets` is a list of project-specific SAVE variable resets (each with a sed `anchor` pattern and `code` to insert before it). VIT patches the instrumented source to reset these on every call, ensuring each scenario re-initializes. Note: KGen's own SAVE arrays (kgen_isstop, kgen_invoke) do NOT need resetting — each extraction phase is a separate subprocess, and within a single run the counters naturally exceed the capture range after the first scenario.

## VIT CLI Commands Reference

VIT ships 12 subcommands. The six primary commands (`extract`, `verify`, `integrate`, `translate`, `sim-compare`, `analyze-types`) are covered in detail above under "Full Workflow" and the Stage sections. This section lists the full surface so nothing is hidden.

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

Golden kernel directories exist in `/workspace/ROSCO/kernel/` from successful extractions, one per function (42 total: `kernel/saturate/`, `kernel/wrap_180/`, `kernel/wrap_360/`, `kernel/ratelimit/`, `kernel/ColemanTransform/`, `kernel/ColemanTransformInverse/`, `kernel/identity/`, `kernel/interp1d/`, `kernel/interp2d/`, `kernel/sigma/`, `kernel/LPFilter/`, `kernel/HPFilter/`, `kernel/SecLPFilter/`, `kernel/NotchFilter/`, `kernel/NotchFilterSlopes/`, `kernel/SecLPFilter_Vel/`, `kernel/PIController/`, `kernel/PIIController/`, `kernel/ResController/`, `kernel/ForeAftDamping/`, `kernel/IPC/`, `kernel/FloatingFeedback/`, `kernel/ActiveWakeControl/`, `kernel/VariableSpeedControl/`, `kernel/PitchControl/`, `kernel/YawRateControl/`, `kernel/FlapControl/`, `kernel/CableControl/`, `kernel/StructuralControl/`, `kernel/PitchSaturation/`, `kernel/AeroDynTorque/`, `kernel/WindSpeedEstimator/`, `kernel/PowerControlSetpoints/`, `kernel/ComputeVariablesSetpoints/`, `kernel/StateMachine/`, `kernel/SetpointSmoother/`, `kernel/RefSpeedExclusion/`, `kernel/Shutdown/`, `kernel/Startup/`, `kernel/ReadAvrSWAP/`, `kernel/PIDController/`, `kernel/unwrap/`). Use these for fast verification regression during development — there is no need to re-extract unless the extraction logic itself has changed, or the golden fixtures are missing or corrupted.

Note: Stage B functions (CheckInputs, ReadCpFile, ReadControlParameterFileSub), Stage C functions (WriteRestartFile, ReadRestartFile, Debug), Stage D functions (UpdateZeroMQ, ExtController), and Stage E functions (PreFilterMeasuredSignals, SetParameters) do not have KGen golden fixtures — they are verified via 27-scenario simulation comparison (`baseline_arrays/`). Debug output is additionally verified via `scripts/debug_test.sh`.

**Extraction parameters (for regenerating golden fixtures):**

| Function | Call site file | Line | Command |
|----------|---------------|------|---------|
| `saturate` | `Controllers.f90` | 102 | `vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102 --run-args '--scenario 3'` |
| `wrap_180` | `Filters.f90` | 432 | `vit extract wrap_180 -f rosco/controller/src/Filters.f90 -l 432 --run-args '--scenario 3'` |
| `wrap_360` | `Controllers.f90` | 517 | `vit extract wrap_360 -f rosco/controller/src/Controllers.f90 -l 517 --run-args '--scenario 2'` |
| `ColemanTransform` | `Controllers.f90` | 510 | `vit extract ColemanTransform -f rosco/controller/src/Controllers.f90 -l 510 --run-args '--scenario 8'` |
| `ColemanTransformInverse` | `Controllers.f90` | 561 | `vit extract ColemanTransformInverse -f rosco/controller/src/Controllers.f90 -l 561 --run-args '--scenario 8'` |
| `identity` | `ControllerBlocks.f90` | 454 | `vit extract identity -f rosco/controller/src/ControllerBlocks.f90 -l 454 --run-args '--scenario 3'` |
| `ratelimit` | `Controllers.f90` | 103 | `vit extract ratelimit -f rosco/controller/src/Controllers.f90 -l 103 --run-args '--scenario 3'` |
| `interp1d` | `Controllers.f90` | 62 | `vit extract interp1d -f rosco/controller/src/Controllers.f90 -l 62 --run-args '--scenario 3'` |
| `sigma` | `Controllers.f90` | 528 | `vit extract sigma -f rosco/controller/src/Controllers.f90 -l 528 --run-args '--scenario 8'` |
| `LPFilter` | `Filters.f90` | 347 | `vit extract LPFilter -f rosco/controller/src/Filters.f90 -l 347 --run-args '--scenario 3'` |
| `HPFilter` | `Filters.f90` | 375 | `vit extract HPFilter -f rosco/controller/src/Filters.f90 -l 375 --run-args '--scenario 3'` |
| `SecLPFilter` | `Filters.f90` | 371 | `vit extract SecLPFilter -f rosco/controller/src/Filters.f90 -l 371 --run-args '--scenario 3'` |
| `NotchFilter` | `Filters.f90` | 356 | `vit extract NotchFilter -f rosco/controller/src/Filters.f90 -l 356 --run-args '--scenario 3'` |
| `NotchFilterSlopes` | `Filters.f90` | 405 | `vit extract NotchFilterSlopes -f rosco/controller/src/Filters.f90 -l 405 --run-args '--scenario 6'` |
| `SecLPFilter_Vel` | `Controllers.f90` | 941 | `vit extract SecLPFilter_Vel -f rosco/controller/src/Controllers.f90 -l 941 --run-args '--scenario 3'` |
| `PIController` | `Controllers.f90` | 68 | `vit extract PIController -f rosco/controller/src/Controllers.f90 -l 68 --run-args '--scenario 3'` |
| `PIIController` | `Controllers.f90` | 682 | `vit extract PIIController -f rosco/controller/src/Controllers.f90 -l 682 --run-args '--scenario 4'` |
| `ResController` | `Controllers.f90` | 813 | `vit extract ResController -f rosco/controller/src/Controllers.f90 -l 813 --run-args '--scenario 8'` |
| `ForeAftDamping` | `Controllers.f90` | 80 | `vit extract ForeAftDamping -f rosco/controller/src/Controllers.f90 -l 80 --run-args '--scenario 7'` |
| `IPC` | `Controllers.f90` | 73 | `vit extract IPC -f rosco/controller/src/Controllers.f90 -l 73 --run-args '--scenario 8'` |
| `FloatingFeedback` | `Controllers.f90` | 96 | `vit extract FloatingFeedback -f rosco/controller/src/Controllers.f90 -l 96 --run-args '--scenario 7'` |
| `ActiveWakeControl` | `Controllers.f90` | 144 | `vit extract ActiveWakeControl -f rosco/controller/src/Controllers.f90 -l 144 --run-args '--scenario 8'` |
| `VariableSpeedControl` | `DISCON.F90` | 117 | `vit extract VariableSpeedControl -f rosco/controller/src/DISCON.F90 -l 117 --run-args '--scenario 3'` |
| `PitchControl` | `DISCON.F90` | 119 | `vit extract PitchControl -f rosco/controller/src/DISCON.F90 -l 119 --run-args '--scenario 3'` |
| `YawRateControl` | `DISCON.F90` | 123 | `vit extract YawRateControl -f rosco/controller/src/DISCON.F90 -l 123 --run-args '--scenario 7'` |
| `FlapControl` | `DISCON.F90` | 127 | `vit extract FlapControl -f rosco/controller/src/DISCON.F90 -l 127 --run-args '--scenario 7'` |
| `CableControl` | `DISCON.F90` | 132 | `vit extract CableControl -f rosco/controller/src/DISCON.F90 -l 132 --run-args '--scenario 7'` |
| `StructuralControl` | `DISCON.F90` | 137 | `vit extract StructuralControl -f rosco/controller/src/DISCON.F90 -l 137 --run-args '--scenario 7'` |
| `PitchSaturation` | `Controllers.f90` | 87 | `vit extract PitchSaturation -f rosco/controller/src/Controllers.f90 -l 87 --run-args '--scenario 3'` |
| `interp2d` | `ControllerBlocks.f90` | 425 | `vit extract interp2d -f rosco/controller/src/ControllerBlocks.f90 -l 425 --run-args '--scenario 3'` |
| `AeroDynTorque` | `ControllerBlocks.f90` | 389 | `vit extract AeroDynTorque -f rosco/controller/src/ControllerBlocks.f90 -l 389 --run-args '--scenario 3'` |
| `WindSpeedEstimator` | `DISCON.F90` | 109 | `vit extract WindSpeedEstimator -f rosco/controller/src/DISCON.F90 -l 109 --run-args '--scenario 3'` |
| `PowerControlSetpoints` | `DISCON.F90` | 110 | `vit extract PowerControlSetpoints -f rosco/controller/src/DISCON.F90 -l 110 --run-args '--scenario 3'` |
| `ComputeVariablesSetpoints` | `DISCON.F90` | 114 | `vit extract ComputeVariablesSetpoints -f rosco/controller/src/DISCON.F90 -l 114 --run-args '--scenario 3'` |
| `StateMachine` | `DISCON.F90` | 115 | `vit extract StateMachine -f rosco/controller/src/DISCON.F90 -l 115 --run-args '--scenario 3'` |
| `SetpointSmoother` | `DISCON.F90` | 116 | `vit extract SetpointSmoother -f rosco/controller/src/DISCON.F90 -l 116 --run-args '--scenario 3'` |
| `RefSpeedExclusion` | `ControllerBlocks.f90` | 157 | `vit extract RefSpeedExclusion -f rosco/controller/src/ControllerBlocks.f90 -l 157 --run-args '--scenario 3'` |
| `Shutdown` | `DISCON.F90` | 107 | `vit extract Shutdown -f rosco/controller/src/DISCON.F90 -l 107 --run-args '--scenario 9'` |
| `Startup` | `DISCON.F90` | 112 | `vit extract Startup -f rosco/controller/src/DISCON.F90 -l 112 --run-args '--scenario 9'` |
| `ReadAvrSWAP` | `DISCON.F90` | 81 | `vit extract ReadAvrSWAP -f rosco/controller/src/DISCON.F90 -l 81 --run-args '--scenario 3'` |
| `PIDController` | `Controllers.f90` | 346 | `vit extract PIDController -f rosco/controller/src/Controllers.f90 -l 346 --run-args '--scenario 10'` |

Each extraction deposits into `kernel/<function_name>/` and restores the source file automatically.

**Note:** Every extraction uses an explicit `--run-args '--scenario N'` to run only the scenario needed for that function. This avoids wastefully running all 27 scenarios. Scenario 3 is the default for most functions (all mode flags enabled, all code paths reachable). Functions that receive zero inputs in Scenario 3 use Scenario 7 (synthetic non-zero inputs for YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl), Scenario 8 (non-zero blade root moments and IPC gains for IPC, ActiveWakeControl), Scenario 9 (SD_Mode=1, SU_Mode=1 for Shutdown, Startup), or Scenario 10 (OL_Mode=2 azimuth tracking for PIDController). Without the correct scenario, KGen verification only proves plumbing (zero in → zero out), not arithmetic correctness.

**Note:** SecLPFilter_Vel requires `CC_Mode=1` in Scenario 3 of vit_sim.py (so CableControl is actually called). This was fixed — the `CALL CableControl` in DISCON.F90 is gated by `CC_Mode > 0`.

**IMPORTANT:** Extraction requires clean (pre-integration) source files. If ROSCO source has already-integrated C++ wrappers, the kernel will contain unresolved `_c` function calls.

Before extracting, prepare the source tree:

1. **Reset integrated source files** — use commits that have all bug fixes but NO integration wrappers.
2. **Keep Phase 4C ROSCO_Types.f90** — KGen's BIND(C) parser bug is fixed, so Phase 4C types (with `BIND(C)`, `INTEGER(C_INT) restart`) are parseable.
3. **Create C++ stubs** for the `*.cpp` files referenced in CMakeLists.txt.
4. **Use explicit `--run-args '--scenario N'`** for each function — see the extraction parameters table above for the correct scenario.

```bash
cd vit_translation/ROSCO

# Functions.f90 + Filters.f90: use dba7738 (has restart fix + notch filter bug fix, no wrappers)
git checkout dba7738 -- rosco/controller/src/Functions.f90 rosco/controller/src/Filters.f90

# Controllers.f90 + ControllerBlocks.f90: use 46e8d4f (Phase 4C + K init fix, no wrappers)
git checkout 46e8d4f -- rosco/controller/src/Controllers.f90 rosco/controller/src/ControllerBlocks.f90

# DISCON.F90: use upstream (no wrappers)
git checkout e8010f0 -- rosco/controller/src/DISCON.F90

# Create C++ stubs
for f in rosco/controller/src/*.cpp; do echo "// stub" > "$f"; done
```

No manual `sed` fixes needed — the correct commits already have all bug fixes applied.

Do NOT reset `ROSCO_Types.f90` (Phase 4C types are needed), `ExtControl.f90`, `ReadSetParameters.f90`, `ROSCO_IO.f90` (Phase 4A/4C fixes, no wrappers), or `CMakeLists.txt` (`-ffp-contract=off` flags + C++ source list).

**Alternative (simpler):** Reset ALL source files to full upstream (`e8010f0`), including ROSCO_Types.f90. This avoids the restart type mismatch entirely. KGen handles the original types fine. The kernel uses upstream types but VIT's verification patches and C++ wrapper generation handle the mapping. This is simpler but loses Phase 4C type compatibility during extraction.

```bash
cd vit_translation/ROSCO
git checkout e8010f0 -- rosco/controller/src/Functions.f90 rosco/controller/src/ControllerBlocks.f90 rosco/controller/src/Filters.f90 rosco/controller/src/Controllers.f90 rosco/controller/src/ROSCO_Types.f90 rosco/controller/src/ExtControl.f90 rosco/controller/src/ReadSetParameters.f90 rosco/controller/src/ROSCO_IO.f90
for f in rosco/controller/src/*.cpp; do echo "// stub" > "$f"; done
```

Then rebuild clean Fortran before running `vit extract`.

**Note on PIIController extraction:** PIIController (line 682, Flp_Mode=2) requires two ROSCO bug fixes committed to our fork: uninitialized K in FlapControl init (Controllers.f90:669) and uninitialized n in the Flp_Mode==2 notch filter branch (Filters.f90:412). These are defects in ROSCO's untested Flp_Mode=2 research code (BAR turbine), introduced in v2.9.0. Without the fixes, the 1-DOF sim segfaults before FlapControl is entered. With the fixes, PIIController extracts and verifies successfully. See dev note `202603201150`. Both fixes are included in the commits used by `scripts/reset_to_clean.sh` — no manual patching needed.

**Source reset shortcut:** Instead of the manual git checkout commands above, use `bash scripts/reset_to_clean.sh` from the ROSCO repo root. It performs all resets, applies bug fixes, and creates C++ stubs in one step.

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

After every feature implementation, run:

1. **Unit tests:**
   ```bash
   docker exec -it vit-dev bash -c "cd /workspace/vit && python3 -m pytest tests/ -v"
   ```

2. **Verification regression (fast, uses golden fixtures):**
   **Requires clean source** — run `bash scripts/reset_to_clean.sh` from the Mac first if source has integration wrappers. `vit verify` scans source files to detect translated callees for bridge generation; integrated source has `_c()` wrappers that break callee detection.
   ```bash
   docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/verify_all.sh"
   ```
   Must show: `42/42 passed` (KGen-verified functions only). Each function should report `N/N passed` where N is the number of cases in its golden fixture (typically 20-23). Stage B/C/D/E functions are not included in `verify_all.sh` — they are verified via 27-scenario simulation comparison against `baseline_arrays/`. For parallel execution, launch 3 batches via separate docker exec calls:
   ```bash
   docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/verify_all.sh 1" &
   docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/verify_all.sh 2" &
   docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/verify_all.sh 3" &
   wait
   ```

3. **Full re-extraction (when extraction logic changes):**
   ```bash
   # Reset source, rebuild, then:
   docker exec vit-dev bash -c "cd /workspace/ROSCO && rm -rf kernel/ && bash scripts/extract_all.sh"
   ```
   Must show: `42/42 passed` with `Cases: 20` or more for each function.

4. **Determinism stress test (when touching NaN handling or comparison logic):**
   ```bash
   docker exec vit-dev bash -c "
     cd /workspace/ROSCO &&
     for run in \$(seq 1 5); do
       rm -rf kernel/ state/ model.ini include.ini kgen.log
       vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102 --run-args '--scenario 3' 2>&1 > /dev/null
       result=\$(vit verify saturate translations/Functions/saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/saturate 2>&1 | grep -oP '\d+/\d+ passed')
       echo \"Run \$run: \$result\"
     done
   "
   ```
   Must show: `20/20 passed` every run with zero variation.

### Clean Build Test (Full End-to-End)

This is the definitive test of the entire VIT pipeline: start from a completely clean ROSCO, build a pure-Fortran baseline, extract kernels for all functions, verify the C++ translations, integrate them into the codebase, rebuild, and confirm byte-identical simulation output.

The clean build test covers all 52 integrated functions across 27 scenarios × 13 output arrays (~5.25M values total, ~2.56M non-trivial).

**Step 1 — Reset ROSCO to clean state (runs on Mac, not in container):**

```bash
cd vit_translation/ROSCO
bash scripts/reset_to_clean.sh
rm -rf kernel/ state/ model.ini include.ini kgen.log baseline_output.txt integrated_output.txt
```

The script resets all source files to clean Fortran with bug fixes applied and creates C++ stubs. No manual patching needed. See `scripts/reset_to_clean.sh` for commit details.

This gives clean Fortran (no integration wrappers) compatible with Phase 4C ROSCO_Types.f90. Files NOT reset: `ROSCO_Types.f90`, `ExtControl.f90`, `ROSCO_IO.f90` (Phase 4A/4C type fixes, no wrappers), `CMakeLists.txt` (`-ffp-contract=off` flags). `ReadSetParameters.f90` IS reset (restored to HEAD, which has no wrappers). The `translations/` directory and `vit.yaml` are preserved.

**FITPACK stack scrubber prerequisite:** Scenario 3 determinism requires `libscrub.so`. If not already built:
```bash
gcc -shared -fPIC -o /workspace/ROSCO/rosco/lib/libscrub.so /workspace/ROSCO/rosco/controller/src/scrub_stack.c
```

**Step 2 — Build clean Fortran baseline and capture baseline output + arrays (in container):**

```bash
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
mkdir -p baseline_arrays
cd Examples && python3 vit_sim.py --output-dir /workspace/ROSCO/baseline_arrays > /workspace/ROSCO/baseline_output.txt 2>&1 && cd /workspace/ROSCO
```

If the turbine model pickle doesn't exist yet, run `python3 01_turbine_model.py` in `Examples/` first (see "Initialize Turbine Model" above).

**Step 3 — Extract kernels (in container):**

Each extraction instruments the source, rebuilds, runs the simulation, captures runtime state, and deposits the kernel into `kernel/<function_name>/`. The source file is restored automatically between runs.

```bash
bash scripts/extract_all.sh
```

Must show `42/42 passed` with `Cases: 20` or more for each function. See `scripts/extract_all.sh` for the per-function scenario assignments.

**Step 4 — Verify C++ translations against extracted kernels (in container):**

```bash
bash scripts/verify_all.sh
```

Must show `42/42 passed`.

**Note:** PitchControl depends on PitchSaturation (callee dependency). PitchSaturation must integrate before PitchControl in `integrate_all.sh`.

**Step 5 — Integrate all functions (in container):**

```bash
bash scripts/integrate_all.sh
```

Must show `52/52 passed`. Each `--apply` reads the current file state and generates correct patches, skipping items already present (e.g., `enable_language(CXX)`, `stdc++` link, `USE ISO_C_BINDING`). See `scripts/integrate_all.sh` for the full function list.

**Step 6 — Rebuild with C++ and compare simulation arrays against baseline (in container):**

```bash
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
mkdir -p integrated_arrays
cd Examples && python3 vit_sim.py --output-dir /workspace/ROSCO/integrated_arrays > /workspace/ROSCO/integrated_output.txt 2>&1 && cd /workspace/ROSCO
```

Then compare all simulation arrays byte-for-byte. Prefer the `vit sim-compare` CLI (same comparison, structured output, proper exit code):

```bash
vit sim-compare baseline_arrays/ integrated_arrays/
```

Or use the inline Python loop for full-detail scenario-by-scenario output:

```bash
python3 -c "
import numpy as np, sys
ok = True
total = 0
for s in range(1, 28):
    b = np.load(f'baseline_arrays/scenario_{s}.npz')
    i = np.load(f'integrated_arrays/scenario_{s}.npz')
    for key in sorted(b.files):
        n = len(b[key])
        total += n
        if not np.array_equal(b[key], i[key]):
            diff = np.abs(b[key] - i[key])
            print(f'MISMATCH scenario {s} {key}: max_diff={diff.max():.2e} at index {diff.argmax()}')
            ok = False
        else:
            print(f'  scenario_{s} {key}: IDENTICAL ({n} values)')
if ok:
    print(f'ALL SCENARIOS IDENTICAL — {total} total values compared')
else:
    print('DIFFERENCES FOUND'); sys.exit(1)
"
```

With `-ffp-contract=off` (the default in CMakeLists.txt), every value must be IDENTICAL **except** the one known tolerated difference: 3 values at Scenario 16 `bld_pitch`/`bld_pitch_2`/`bld_pitch_3` index 4444 differ by `8.47e-22 rad` (single-root-cause gfortran code-gen artifact — see Known Limitations → Tolerated compiler artifact). The comparison covers 13 output arrays per scenario — `bld_pitch`, `bld_pitch_2`, `bld_pitch_3`, `gen_torque`, `gen_speed`, `gen_power`, `nac_yaw`, `flp_angle_1`/`2`/`3`, `cc_actuated_l`, `cc_actuated_dl`, `stc_input` — at every timestep across all 27 scenarios (**~5.25M values total, ~2.56M non-trivial**). Any unexpected difference — even 1 ULP — indicates a translation or integration bug.

**Upstream comparison (optional, stronger chain).** `bash scripts/reset_to_upstream.sh` resets ROSCO to unmodified NREL source (commit `e8010f0`). Rebuild, run `vit_sim.py --output-dir upstream_check/`, then `vit sim-compare upstream_arrays/ upstream_check/` to prove the type modifications in the fork are behaviorally neutral. Upstream segfaults on Scenario 4 due to a Flp_Mode=2 uninitialized-K bug present in ROSCO v2.9.0 — `upstream_arrays/` therefore has 26 `.npz` files, not 27.

Cleanup after passing: `rm -rf integrated_arrays integrated_output.txt`

After confirming the diff is empty, run the **FMA sanity check** — rebuild both baseline and integrated with FMA enabled (the production default) and verify the diff is small and bounded:

```bash
# FMA sanity check: rebuild baseline with FMA enabled
cd /workspace/ROSCO
git checkout e8010f0 -- rosco/controller/src/Functions.f90 rosco/controller/src/ControllerBlocks.f90 rosco/controller/src/Filters.f90
rm -f rosco/controller/src/*.cpp
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install -DCMAKE_Fortran_FLAGS_OVERRIDE='-ffp-contract=fast' -DCMAKE_CXX_FLAGS_OVERRIDE='-ffp-contract=fast' && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
cd Examples && python3 vit_sim.py > /workspace/ROSCO/baseline_fma.txt 2>&1 && cd /workspace/ROSCO

# Re-integrate and rebuild with FMA enabled
# (run all vit integrate --apply commands, then:)
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install -DCMAKE_Fortran_FLAGS_OVERRIDE='-ffp-contract=fast' -DCMAKE_CXX_FLAGS_OVERRIDE='-ffp-contract=fast' && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
cd Examples && python3 vit_sim.py > /workspace/ROSCO/integrated_fma.txt 2>&1 && cd /workspace/ROSCO
diff baseline_fma.txt integrated_fma.txt
rm -f baseline_fma.txt integrated_fma.txt
```

The FMA diff should be small: at most a few lines in Scenario 3 with sub-0.01% Power differences (1-ULP FMA scheduling drift in closed-loop feedback). Scenarios 1+2 should remain byte-identical. Large or widespread differences indicate a translation bug, not FMA rounding.

After both checks pass, delete the test output file:
```bash
rm -f integrated_output.txt
```
The `baseline_output.txt` is kept as a reference. Only delete transient output files (e.g., `integrated_output.txt`, `errvar_fix_output.txt`) — these are not committed and cause confusion if left around.

### Performance

C++ runs **~25% faster than Fortran** across 27 scenarios (17.48s vs 23.17s with `-ffp-contract=off` on ARM64 Linux, gfortran/g++ 13.3.0). The speedup is a language-level property — g++ generates tighter ARM64 code than gfortran for the same translated algorithms. No algorithmic changes.

| Build | `-ffp-contract=off` | `-ffp-contract=fast` | FMA benefit |
|-------|---------------------|----------------------|-------------|
| Upstream Fortran | 23.20s | 21.77s | 6.2% |
| Modified Fortran | 23.17s | 22.34s | 3.6% |
| C++ | 17.48s | 17.17s | 1.8% |

Upstream vs modified Fortran differ by 0.15% (noise) — the fork's type modifications have **zero measurable performance impact**. Fortran benefits more from FMA than C++ because gfortran leaves more unfused multiply-add opportunities for `-ffp-contract=fast` to exploit.

**Production recommendation: C++ with `-ffp-contract=fast`** (17.17s, 26% faster than upstream Fortran with fast FMA — the fastest benchmarked configuration). The `-ffp-contract=off` flag is only needed during the verification phase for bit-identical cross-language comparison; the pure-C++ build on `integrated` can re-enable FMA freely.

Benchmark infrastructure: `vit_sim.py --benchmark N --build LABEL` runs each scenario N times with OS-level fd 1 redirection (suppresses both Python and controller stdout) and writes timing CSV to stderr. See dev notes `202604031707-performance-benchmark.md` and `202604040850-fma-benchmark-and-colleague-test.md` for full per-scenario results.

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

Verified C++ translations live in `ROSCO/translations/Functions/` and `ROSCO/translations/Filters/`, organized by Fortran source module.

| File | Fortran function | Status |
|------|-----------------|--------|
| `translations/Functions/saturate.cpp` | `saturate` in Functions.f90 | Verified (Phase 1) |
| `translations/Functions/wrap_180.cpp` | `wrap_180` in Functions.f90 | Verified (Phase 1) |
| `translations/Functions/wrap_360.cpp` | `wrap_360` in Functions.f90 | Verified (Phase 1) |
| `translations/Functions/ratelimit.cpp` | `ratelimit` in Functions.f90 | Verified (Phase 3) |
| `translations/Functions/unwrap.cpp` | `unwrap` in Functions.f90 | Verified (Phase 10A) — angle unwrapping utility |
| `translations/Functions/colemantransform.cpp` | `ColemanTransform` in Functions.f90 | Verified (Phase 2) |
| `translations/Functions/colemantransforminverse.cpp` | `ColemanTransformInverse` in Functions.f90 | Verified (Phase 2) |
| `translations/Functions/identity.cpp` | `identity` in ControllerBlocks.f90 | Verified (Phase 2) |
| `translations/Functions/sigma.cpp` | `sigma` in Functions.f90 | Verified (Phase 5A) — first ErrorVariables function |
| `translations/Functions/interp1d.cpp` | `interp1d` in Functions.f90 | Verified (Phase 5A) — arrays + ErrorVariables |
| `translations/Filters/lpfilter.cpp` | `LPFilter` in Filters.f90 | Verified (Phase 6) — first Filters module, FilterParameters struct pointer |
| `translations/Filters/hpfilter.cpp` | `HPFilter` in Filters.f90 | Verified (Phase 6) |
| `translations/Filters/seclpfilter.cpp` | `SecLPFilter` in Filters.f90 | Verified (Phase 6A) — second-order, 10 state fields |
| `translations/Filters/seclpfilter_vel.cpp` | `SecLPFilter_Vel` in Filters.f90 | Verified (Phase 7) — was pattern-translated, now KGen-verified with CC_Mode=1 fix |
| `translations/Filters/notchfilter.cpp` | `NotchFilter` in Filters.f90 | Verified (Phase 6B) — discrete notch, omega/betaNum/betaDen args |
| `translations/Filters/notchfilterslopes.cpp` | `NotchFilterSlopes` in Filters.f90 | Verified (Phase 6B) — first function with two OPTIONAL args |
| `translations/Filters/prefiltermeasuredsignals.cpp` | `PreFilterMeasuredSignals` in Filters.f90 | Verified (Stage E) — filter cascade orchestrator, 6 callee types |
| `translations/Controllers/picontroller.cpp` | `PIController` in Controllers.f90 | Verified (Phase 7A) |
| `translations/Controllers/piicontroller.cpp` | `PIIController` in Controllers.f90 | Verified (Phase 7A) — required ROSCO Flp_Mode=2 bug fixes |
| `translations/Controllers/rescontroller.cpp` | `ResController` in Controllers.f90 | Verified (Phase 7A) |
| `translations/Controllers/foreaftdamping.cpp` | `ForeAftDamping` in Controllers.f90 | Verified (Phase 4D) — first ControlParameters view type function, callee bridge to PIController |
| `translations/Controllers/floatingfeedback.cpp` | `FloatingFeedback` in Controllers.f90 | Verified (Phase 7B) — FUNCTION (returns double), gain-scheduled nacelle velocity feedback |
| `translations/Controllers/structuralcontrol.cpp` | `StructuralControl` in Controllers.f90 | Verified (Phase 7B) — structural control inputs, 2 modes |
| `translations/Controllers/cablecontrol.cpp` | `CableControl` in Controllers.f90 | Verified (Phase 7B) — cable length control, SecLPFilter_Vel + PIController |
| `translations/Controllers/flapcontrol.cpp` | `FlapControl` in Controllers.f90 | Verified (Phase 7B) — 3 Flp_Mode branches, Coleman transforms |
| `translations/Controllers/yawratecontrol.cpp` | `YawRateControl` in Controllers.f90 | Verified (Phase 7B) — yaw state machine, SAVE variables, trig filtering |
| `translations/Controllers/variablespeedcontrol.cpp` | `VariableSpeedControl` in Controllers.f90 | Verified (Phase 7B) — generator torque state machine, K*Omega^2 |
| `translations/Controllers/ipc.cpp` | `IPC` in Controllers.f90 | Verified (Phase 7B) — 1P/2P Individual Pitch Control, Coleman transforms |
| `translations/Controllers/activewakecontrol.cpp` | `ActiveWakeControl` in Controllers.f90 | Verified (Phase 7B) — 5 AWC modes, complex decomposition |
| `translations/Controllers/pitchcontrol.cpp` | `PitchControl` in Controllers.f90 | Verified (Phase 7B) — master pitch orchestrator, 185 lines, 11+ callees |
| `translations/ControllerBlocks/pitchsaturation.cpp` | `PitchSaturation` in ControllerBlocks.f90 | Verified (Phase 8A) — peak shaving minimum pitch, interp1d lookup |
| `translations/Functions/interp2d.cpp` | `interp2d` in Functions.f90 | Verified (Phase 8C) — bilinear interpolation, column-major Z macro |
| `translations/Functions/aerodyntorque.cpp` | `AeroDynTorque` in Functions.f90 | Verified (Phase 8C) — aerodynamic torque via Cp lookup |
| `translations/ControllerBlocks/statemachine.cpp` | `StateMachine` in ControllerBlocks.f90 | Verified (Phase 8B) — controller state transitions |
| `translations/ControllerBlocks/setpointsmoother.cpp` | `SetpointSmoother` in ControllerBlocks.f90 | Verified (Phase 8B) — setpoint rate limiting |
| `translations/ControllerBlocks/powercontrolsetpoints.cpp` | `PowerControlSetpoints` in ControllerBlocks.f90 | Verified (Phase 8B) — power/speed reference computation |
| `translations/ControllerBlocks/refspeedexclusion.cpp` | `RefSpeedExclusion` in ControllerBlocks.f90 | Verified (Phase 8B) — resonance avoidance speed exclusion |
| `translations/ControllerBlocks/computevariablessetpoints.cpp` | `ComputeVariablesSetpoints` in ControllerBlocks.f90 | Verified (Phase 8B) — variable setpoint computation |
| `translations/ControllerBlocks/shutdown.cpp` | `Shutdown` in ControllerBlocks.f90 | Verified (Phase 8B) — shutdown detection and staging |
| `translations/ControllerBlocks/startup.cpp` | `Startup` in ControllerBlocks.f90 | Verified (Phase 8B) — startup load staging |
| `translations/ControllerBlocks/windspeedestimator.cpp` | `WindSpeedEstimator` in ControllerBlocks.f90 | Verified (Phase 8C) — EKF wind estimator, std::pow() for x**3.0 |
| `translations/ReadSetParameters/readavrswap.cpp` | `ReadAvrSWAP` in ReadSetParameters.f90 | Verified (Phase 10A) — avrSWAP array unpacking into LocalVar |
| `translations/Controllers/pidcontroller.cpp` | `PIDController` in Controllers.f90 | Verified (Phase 10A) — PID with derivative filtering, OL_Mode=2 azimuth tracking |
| `translations/ReadSetParameters/checkinputs.cpp` | `CheckInputs` in ReadSetParameters.f90 | Verified (Phase 10B) — parameter validation, simulation-verified |
| `translations/ReadSetParameters/readcpfile.cpp` | `ReadCpFile` in ReadSetParameters.f90 | Verified (Phase 10B) — Cp/Ct/Cq table reader, pre-allocation pattern |
| `translations/ReadSetParameters/readcontrolparameterfilesub.cpp` | `ReadControlParameterFileSub` in ReadSetParameters.f90 | Verified (Phase 10B) — DISCON.IN parser, two-pass protocol |
| `translations/ReadSetParameters/setparameters.cpp` | `SetParameters` in ReadSetParameters.f90 | Verified (Stage E) — orchestrator, manual integration with callee dispatch |
| `translations/IO/writerestartfile.cpp` | `WriteRestartFile` in ROSCO_IO.f90 | Verified (Stage C) — binary checkpoint output |
| `translations/IO/readrestartfile.cpp` | `ReadRestartFile` in ROSCO_IO.f90 | Verified (Stage C) — binary checkpoint input, callee dispatch |
| `translations/IO/debug_logger.cpp` | `Debug` in ROSCO_IO.f90 | Verified (Stage C) — debug file logging, static std::ofstream handles |
| `translations/IO/zmq_interface.cpp` | `UpdateZeroMQ` in ZeroMQInterface.f90 | Verified (Stage D) — ZeroMQ platform interface |
| `translations/IO/ext_control.cpp` | `ExtController` in ExtControl.f90 | Verified (Stage D) — external controller DLL loader |

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

- **View structs** (for ALLOCATABLE types): Types with ALLOCATABLE arrays (ControlParameters, PerformanceData, ExtControlType) use the pointer+size view pattern. A generated view struct exposes ALLOCATABLE fields as `type* field; int n_field;` pairs. A generated Fortran populator fills the view from the real type at runtime using `C_LOC()` + `SIZE()`. Configured in `vit.yaml` under `types: { TypeName: { strategy: view } }`. ALLOCATABLE field sizes are scanned from source via configurable allocation helpers (`allocatable.helpers` in `vit.yaml`) and universal `ALLOCATE` pattern matching. All ALLOCATABLE fields are included in the view struct — the scanner is informational only.

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

### KGen extraction

- **Call site must be reachable at runtime**: KGen produces zero state files if the call site is never executed. Each extraction uses `--run-args '--scenario N'` to run the specific scenario that exercises the target function. Call sites gated by mode flags (e.g., `IF (CntrPar%CC_Mode > 0)`) need the corresponding mode enabled in the scenario config.

- **Invocation ordering**: KGen counts invocations per enclosing subroutine. When running multiple scenarios, earlier scenarios consume the capture range. VIT addresses this by running the target scenario first in `vit_sim.py` and by using `--run-args '--scenario N'` for per-scenario extraction.

- **Project Fortran flags**: KGen-generated kernel Makefiles don't inherit project-specific compiler flags. VIT's `fortran.flags` config field in `vit.yaml` propagates flags like `-fdefault-real-8` and `-ffp-contract=off` to the kernel Makefile.

- **Assumed-size arrays**: KGen generates state capture functions with `DIMENSION(:)` parameters. Fortran forbids passing assumed-size `(*)` arrays to assumed-shape parameters. VIT auto-detects assumed-size arrays, scans source for index evidence, and proposes sizes via the confirm protocol (exit code 10). Pre-configure `kgen.assumed_size_arrays` in `vit.yaml` to skip confirmation. **The size must cover ALL indices the function writes to** — undersizing causes silent heap corruption.

- **DLL SAVE variable persistence**: Fortran SAVE variables may persist across scenarios within a single `vit_sim.py` run (ctypes `dlclose()` may not truly unload the library). VIT's `kgen.dll_persistence.resets` config patches the instrumented source to reset project-specific guard variables (e.g., ROSCO's `AlreadyInitialized`) on each call. Note: with per-scenario extraction (`--run-args '--scenario N'`), only one scenario runs per process, so this reset may be unnecessary in practice — but the config provides a safety net for multi-scenario extraction.

- **PROCEDURE POINTER declarations**: KGen's original parser couldn't handle `PROCEDURE(...), POINTER` (Fortran 2003+). Fixed in our KGen fork (commit `31d653c`).

### Tolerated compiler artifact (Scenario 16)

3 values in the 27-scenario simulation comparison differ between Fortran and C++: `bld_pitch[4444]`, `bld_pitch_2[4444]`, `bld_pitch_3[4444]` in Scenario 16, all by `8.47e-22 rad` (`2^{-70}` vs exact zero).

**Root cause:** gfortran and g++ evaluate the rate limiter expression `x + ((-x/a) * a)` differently for one specific input at one specific timestep. g++ produces exact zero; gfortran produces `2^{-70}`. This is a compiler code-generation difference at the boundary of IEEE 754 representability, not a translation bug — the C++ source faithfully reproduces the Fortran arithmetic. Scenario 16 disables IPC, so all three blades see the same collective pitch and the same artifact appears in all three arrays (one root cause, three manifestations). Fully explained in dev note `202604031325-scenario16-sub-ulp-root-cause.md`.

This is the **only** tolerated difference across the 27-scenario verification chain. Any other mismatch indicates a real bug.

### Honest verification caveats (from adversarial audit)

Two real caveats should be stated alongside the headline verification numbers so they aren't misleading:

1. **51% of compared values are trivially zero** (185 of 351 arrays are all-zero — modes produce zero output when disabled or when inputs are zero). The honest non-trivial comparison is ~**2.56M** values, not 5.25M. Zero-vs-zero still confirms that disabled modes don't accidentally produce output, which is valuable but less powerful than comparing non-trivial outputs.

2. **Simulation-level comparison operates at float32 precision** (the avrSWAP interface and ROSCO's deployment precision). A translation bug producing a sub-float32 error in a code path that does not feed back into the closed loop would be invisible at the simulation level.

**Mitigations:** (a) 42 functions are KGen-verified at float64 — first line of defense catches sub-float32 differences in arithmetic; (b) closed-loop feedback amplifies sub-ULP errors over thousands of timesteps, so a systematic translation bug cannot hide at float32 precision through 24K closed-loop timesteps; (c) float32 is the production avrSWAP interface (OpenFAST uses the same) — equivalent at float32 means equivalent in real deployment. Reference: dev note `202604031638-adversarial-verification-audit.md`.

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
