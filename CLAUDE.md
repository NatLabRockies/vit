# CLAUDE.md

## What This Project Is

This workspace contains three tools that work together to enable verified Fortran→C++ translation in scientific codebases.

**VIT (Verified Incremental Translator)** is the tool we are building. It orchestrates the extraction of runtime state from a Fortran function during a real simulation, wires a user-provided C++ translation into a standalone verification kernel, runs bit-identical verification against the captured state, and generates integration patches to replace the Fortran with C++ in the real codebase. VIT does NOT translate Fortran to C++ — it verifies and integrates translations written by humans or LLMs.

**KGen** (NCAR) is an existing third-party tool that instruments Fortran code to capture function inputs and outputs during a simulation run, then generates a standalone "kernel" that can replay those calls. VIT wraps KGen and patches its generated code. KGen requires Python 2.7.

**ROSCO** is a wind turbine controller written in Fortran. It is the test case for VIT. ROSCO's Python simulation loads a compiled Fortran shared library (`libdiscon.so`) and runs a controller loop. The target function for our proof-of-concept is `saturate` — a simple min/max clamp in `rosco/controller/src/Functions.f90`, called from `rosco/controller/src/Controllers.f90` at line 102.

## Development Environment

### Docker Container

All building, extraction, simulation, and testing happens inside a Docker container. **Never run VIT commands, ROSCO builds, or KGen directly on the Mac** — KGen's Fortran parser does not work on macOS.

The container is named `vit-dev` and mounts the workspace:

| Mac path | Container path |
|----------|----------------|
| `vit_translation/` | `/workspace/` |

Files edited on the Mac are immediately visible inside the container and vice versa.

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

On a fresh clone, the pickled turbine model does not exist yet. `04_simple_sim.py` (used by both extraction and baseline capture) loads `Examples/examples_out/01_NREL5MW_saved.p`, which is created by `01_turbine_model.py`. Run this once before any simulation:

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
vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102
```

Expected: `✓ Extraction successful` with `Cases: 20`.

The `--file` flag takes the file with the **call site** (Controllers.f90), not the definition.

### 3. Verify (C++ Against Captured State)

```bash
vit verify saturate saturate.cpp \
  -f rosco/controller/src/Functions.f90 \
  --kernel-dir kernel/
```

Expected: `✓ VERIFICATION PASSED: 40/40 passed`

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
python3 04_simple_sim.py > /workspace/ROSCO/integrated_output.txt 2>&1
cd /workspace/ROSCO
diff baseline_output.txt integrated_output.txt
```

Empty diff = the C++ translation produces byte-identical simulation output.

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
  run_cmd: cd /workspace/ROSCO/Examples && python3 04_simple_sim.py
  clean_cmd: cd /workspace/ROSCO/rosco/controller/build && make clean
  install_cmd: cp /workspace/ROSCO/rosco/controller/build/libdiscon.so /workspace/ROSCO/rosco/lib/libdiscon.so
  invocation: 0:0:1-20
build_system:
  type: cmake
  cmake_file: rosco/controller/CMakeLists.txt
  target_name: discon
fortran:
  kind_aliases:
    ReKi: REAL(8)
    DbKi: REAL(8)
    SiKi: REAL(4)
    IntKi: INTEGER(4)
  compiler: gfortran
verification:
  tolerance: 1.0e-14
translations:
  saturate:
    status: integrated
    cpp_file: saturate.cpp
```

Key fields:
- `kgen.path`: path to KGen entry point (requires Python 2.7)
- `kgen.build_cmd`, `run_cmd`, `clean_cmd`, `install_cmd`: commands KGen runs to build/run/clean the Fortran project
- `kgen.invocation`: selects which KGen invocations to capture state for (`0:0:1-20` means process 0, thread 0, invocations 1 through 20)
- `fortran.kind_aliases`: maps ROSCO's custom kinds to standard types (`DbKi → REAL(8)`, etc.)
- `verification.tolerance`: maximum acceptable floating-point difference (`1.0e-14`)
- `translations`: tracks which functions have been verified/integrated

## Testing and Development Process

### Test Structure

Tests live in `vit/tests/` and use pytest. Run them inside the container:

```bash
docker exec -it vit-dev bash -c "cd /workspace/vit && python3 -m pytest tests/ -v"
```

### Golden Test Fixtures

Golden kernel directories exist in `/workspace/ROSCO/kernel/` from successful extractions, one per function (`kernel/saturate/`, `kernel/wrap_180/`, `kernel/interp1d/`). Use these for fast verification regression during development — there is no need to re-extract unless the extraction logic itself has changed, or the golden fixtures are missing or corrupted.

**Extraction parameters (for regenerating golden fixtures):**

| Function | Call site file | Line | Command |
|----------|---------------|------|---------|
| `saturate` | `Controllers.f90` | 102 | `vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102` |
| `wrap_180` | `Filters.f90` | 430 | `vit extract wrap_180 -f rosco/controller/src/Filters.f90 -l 430` |
| `interp1d` | `Controllers.f90` | 62 | `vit extract interp1d -f rosco/controller/src/Controllers.f90 -l 62` |

Each extraction deposits into `kernel/<function_name>/` and restores the source file automatically.

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
   ```bash
   docker exec vit-dev bash -c "cd /workspace/ROSCO && vit verify saturate saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/saturate 2>&1 | tail -3"
   docker exec vit-dev bash -c "cd /workspace/ROSCO && vit verify wrap_180 wrap_180.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/wrap_180 2>&1 | tail -3"
   docker exec vit-dev bash -c "cd /workspace/ROSCO && vit verify interp1d interp1d.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/interp1d 2>&1 | tail -3"
   ```
   Must show: `40/40 passed` for each function.

3. **Determinism stress test (when touching NaN handling or comparison logic):**
   ```bash
   docker exec vit-dev bash -c "
     cd /workspace/ROSCO &&
     for run in \$(seq 1 5); do
       rm -rf kernel/ state/ model.ini include.ini kgen.log
       vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102 2>&1 > /dev/null
       result=\$(vit verify saturate saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/ 2>&1 | grep -oP '\d+/\d+ passed')
       echo \"Run \$run: \$result\"
     done
   "
   ```
   Must show: `40/40 passed` every run with zero variation.

### Clean Build Test (Full End-to-End)

This is the definitive test of the entire VIT pipeline: start from a completely clean ROSCO, build a pure-Fortran baseline, extract kernels for all functions, verify the C++ translations, integrate them into the codebase, rebuild, and confirm byte-identical simulation output.

The functions included in the clean build test expand as phases complete. Initially only ErrVar-free functions are tested.

**Step 1 — Reset ROSCO to clean state (runs on Mac, not in container):**

```bash
cd vit_translation/ROSCO
git checkout .
rm -f rosco/controller/src/*_cpp.cpp
rm -rf kernel/ state/ model.ini include.ini kgen.log baseline_output.txt integrated_output.txt
```

This resets all tracked source files to their upstream state and removes all VIT work products. Untracked config files (`vit.yaml`) and the `translations/` directory are preserved.

**Step 2 — Copy translations into ROSCO root for verification (runs on Mac):**

```bash
cp translations/Functions/saturate.cpp .
cp translations/Functions/wrap_180.cpp .
```

Translations live in `ROSCO/translations/Functions/`. The copies in the ROSCO root are only needed for verification commands that reference them by relative path. Add more functions as phases complete.

**Step 3 — Build clean Fortran baseline and capture baseline output (in container):**

```bash
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
cd Examples && python3 04_simple_sim.py > /workspace/ROSCO/baseline_output.txt 2>&1 && cd /workspace/ROSCO
```

If the turbine model pickle doesn't exist yet, run `python3 01_turbine_model.py` in `Examples/` first (see "Initialize Turbine Model" above).

**Step 4 — Extract kernels (in container):**

Each extraction instruments the source, rebuilds, runs the simulation, captures runtime state, and deposits the kernel into `kernel/<function_name>/`. The source file is restored automatically between runs.

```bash
vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102
vit extract wrap_180 -f rosco/controller/src/Filters.f90 -l 430
```

Each must show `Extraction successful` with `Cases: 20`.

**Step 5 — Verify C++ translations against extracted kernels (in container):**

```bash
vit verify saturate saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/saturate
vit verify wrap_180 wrap_180.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/wrap_180
```

Each must show `40/40 passed`.

**Step 6 — Integrate all functions (in container):**

```bash
vit integrate saturate saturate.cpp -f rosco/controller/src/Functions.f90 --apply
vit integrate wrap_180 wrap_180.cpp -f rosco/controller/src/Functions.f90 --apply
```

Each `--apply` reads the current file state and generates correct patches, skipping items already present (e.g., `enable_language(CXX)`, `stdc++` link, `USE ISO_C_BINDING`).

**Step 7 — Rebuild with C++ and diff against baseline (in container):**

```bash
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
cd Examples && python3 04_simple_sim.py > /workspace/ROSCO/integrated_output.txt 2>&1 && cd /workspace/ROSCO
diff baseline_output.txt integrated_output.txt
```

Empty diff = success. All three C++ translations produce byte-identical simulation output.

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

Verified C++ translations live in `ROSCO/translations/Functions/`, organized by Fortran source module. Canonical copies also exist in `vit_translation/translations/` (flat layout, for backwards compatibility).

| File | Fortran function | Status |
|------|-----------------|--------|
| `translations/Functions/saturate.cpp` | `saturate` in Functions.f90 | Verified (Phase 1) |
| `translations/Functions/wrap_180.cpp` | `wrap_180` in Functions.f90 | Verified (Phase 1) |
| `translations/Functions/wrap_360.cpp` | `wrap_360` in Functions.f90 | Verified (Phase 1) |
| `translations/Functions/interp1d.cpp` | `interp1d` in Functions.f90 | Blocked by ErrorVariables (Phase 5) |

Each translation file has a metadata header (source file, function name, MD5 hash, VIT version, verification status) generated by `vit translate`.

Integration starts from a clean ROSCO. All functions are integrated in phase order — nothing is pre-integrated.

Note: `interp1d.cpp` uses VIT's per-array size parameter convention (`n_xData`, `n_yData`) rather than a single shared `n` parameter. See "Known Limitations" below.

### TRANSLATION_TIPS.md

`ROSCO/TRANSLATION_TIPS.md` is a living document that captures lessons learned from translation failures. When a translation fails verification and the fix reveals a generalizable lesson, add it to this file. The `vit translate` command includes its contents in the translation prompt automatically, so the prompt gets smarter over time.

## Known Limitations and Future Work

### Derived types

All derived types (including ErrorVariables) will become BIND(C) structs passed by pointer — no passthrough, no decomposed args. Functions with ErrorVariables are delayed until Phase 4 (type system) and translated in Phase 5. The passthrough mechanism in the current code is legacy and not used for new translations.

- **Derived type registry not wired into pipeline**: `get_type_mapping()` accepts an optional `derived_types` registry, but nothing in the CLI or pipeline populates it. All `TYPE(...)` args currently default to non-C-compatible. This will be wired in Phase 3 (ratelimit/rlParams) and completed in Phase 4 (full type system).

- **USE statement matching is string-based**: `_get_use_statements_for_derived_types()` checks if the derived type name appears in the USE line text. This works for `USE Module, ONLY: TypeName` but would miss bare `USE Module` (can't tell what it exports) or false-match on substring collisions.

### Assumed-shape arrays

- **Per-array size params**: VIT generates a separate `n_<name>` size parameter for every assumed-shape array arg, even when arrays are semantically the same length (e.g., `xData` and `yData`). The user's C++ must accept per-array sizes. A future improvement could coalesce size params when arrays share the same rank, but that requires inference VIT currently avoids.

- **No multi-dimensional assumed-shape handling**: `DIMENSION(:,:)` arrays would get a single `n_` size param via the same `:` detection, which is wrong for 2-D. There is no guard or warning — it would silently generate broken code. Add an error or proper handling when a multi-dimensional assumed-shape is encountered. Fixed in Phase 5.

## Development Notes Convention

Development notes live in `vit/dev/` (the VIT repo) and are named with a datetime tag: `YYYYMMDDHHMM-descriptor.md` (e.g., `202603101739-project-inception.md`). Dev notes are committed to the VIT repo, not ROSCO.

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

## Project Plan

`PROJECT_PLAN.md` is the master roadmap for translating all of ROSCO from Fortran to C++. It contains the function-by-function assessment, full module map, derived type strategy, an 11-phase enhancement roadmap, and the translation workflow (Section 4a: `vit translate` command, module-organized storage in `translations/`, and translation metadata).

### Execution Process

We execute the plan one phase at a time. For each phase:

1. **Review**: Read the phase in `PROJECT_PLAN.md` and understand the scope
2. **Plan**: Create a detailed implementation plan for the phase
3. **Execute**: Implement the VIT enhancements and/or write C++ translations
4. **Test**: Run unit tests, verification regression, and any phase-specific validation
5. **Update**: Mark the phase and its sub-items as complete in `PROJECT_PLAN.md`. Update the phase description to reflect what was actually done — adjustments, additional work discovered, or better approaches found during implementation
6. **Dev note**: Write a dev note capturing the phase completion

`PROJECT_PLAN.md` uses checkboxes (`- [ ]` / `- [x]`) for phases and sub-items so progress is visible at a glance. The document is a living record: it should always reflect reality, not just the original plan.

## Demo

There is a scripted demo in `demo/demo_script.md`. When the user says "Start VIT", read that file and follow it exactly — every text block, tool call, and command, in order, verbatim. Do not improvise, add extra commentary, or skip steps. The script has two phases: an Opening (output function list, then wait for user input) and Acts 1–6 (triggered when the user says "interp1d").

## Git

There are two repos in this workspace:

| Repo | Path | Remote | What goes here |
|------|------|--------|---------------|
| **VIT** | `vit_translation/vit/` | `origin` (private) | VIT tool code, tests, dev notes, PROJECT_PLAN.md, CLAUDE.md |
| **ROSCO** | `vit_translation/ROSCO/` | `git@github.com:kevinmenear/ROSCO.git` | Forked from NREL/ROSCO. Translations, vit.yaml, TRANSLATION_TIPS.md, golden kernel fixtures |

ROSCO is a fork of the upstream NREL/ROSCO repo. VIT-specific files (translations, config, kernel fixtures) are committed and pushed to the fork. Changes to translations, vit.yaml, and kernel fixtures go in the ROSCO repo. Changes to VIT tool code, tests, dev notes, and planning docs go in the VIT repo.

Run `git add` and `git commit` directly when the user asks to commit. Do not run `git push` unless explicitly asked. Do not run destructive git commands (`reset --hard`, `push --force`, etc.) without confirmation. The only exception for autonomous git usage is `git checkout .` on ROSCO as part of a clean build test (see "Clean Build Test" above).
