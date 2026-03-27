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

## Testing and Development Process

### Test Structure

Tests live in `vit/tests/` and use pytest. Run them inside the container:

```bash
docker exec -it vit-dev bash -c "cd /workspace/vit && python3 -m pytest tests/ -v"
```

### Golden Test Fixtures

Golden kernel directories exist in `/workspace/ROSCO/kernel/` from successful extractions, one per function (`kernel/saturate/`, `kernel/wrap_180/`, `kernel/wrap_360/`, `kernel/ratelimit/`, `kernel/ColemanTransform/`, `kernel/ColemanTransformInverse/`, `kernel/identity/`, `kernel/interp1d/`, `kernel/sigma/`, `kernel/LPFilter/`, `kernel/HPFilter/`, `kernel/SecLPFilter/`, `kernel/NotchFilter/`, `kernel/NotchFilterSlopes/`, `kernel/SecLPFilter_Vel/`, `kernel/PIController/`, `kernel/PIIController/`, `kernel/ResController/`, `kernel/ForeAftDamping/`, `kernel/IPC/`, `kernel/FloatingFeedback/`, `kernel/ActiveWakeControl/`, `kernel/VariableSpeedControl/`, `kernel/PitchControl/`, `kernel/YawRateControl/`, `kernel/FlapControl/`, `kernel/CableControl/`, `kernel/StructuralControl/`). Use these for fast verification regression during development — there is no need to re-extract unless the extraction logic itself has changed, or the golden fixtures are missing or corrupted.

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

Each extraction deposits into `kernel/<function_name>/` and restores the source file automatically.

**Note:** Every extraction uses an explicit `--run-args '--scenario N'` to run only the scenario needed for that function. This avoids wastefully running all 8 scenarios. Scenario 3 is the default for most functions (all mode flags enabled, all code paths reachable). Functions that receive zero inputs in Scenario 3 use Scenario 7 (synthetic non-zero inputs for YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl) or Scenario 8 (non-zero blade root moments and IPC gains for IPC, ActiveWakeControl). Without the correct scenario, KGen verification only proves plumbing (zero in → zero out), not arithmetic correctness.

**Note:** SecLPFilter_Vel requires `CC_Mode=1` in Scenario 3 of vit_sim.py (so CableControl is actually called). This was fixed — the `CALL CableControl` in DISCON.F90 is gated by `CC_Mode > 0`.

**IMPORTANT:** Extraction requires clean (pre-integration) source files. If ROSCO source has already-integrated C++ wrappers, the kernel will contain unresolved `_c` function calls.

Before extracting, prepare the source tree:

1. **Reset integrated source files** — use commits that have all bug fixes but NO integration wrappers.
2. **Keep Phase 4C ROSCO_Types.f90** — KGen's BIND(C) parser bug is fixed, so Phase 4C types (with `BIND(C)`, `INTEGER(C_INT) restart`) are parseable.
3. **Create C++ stubs** for the `*_cpp.cpp` files referenced in CMakeLists.txt.
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
for f in rosco/controller/src/*_cpp.cpp; do echo "// stub" > "$f"; done
```

No manual `sed` fixes needed — the correct commits already have all bug fixes applied.

Do NOT reset `ROSCO_Types.f90` (Phase 4C types are needed), `ExtControl.f90`, `ReadSetParameters.f90`, `ROSCO_IO.f90` (Phase 4A/4C fixes, no wrappers), or `CMakeLists.txt` (`-ffp-contract=off` flags + C++ source list).

**Alternative (simpler):** Reset ALL source files to full upstream (`e8010f0`), including ROSCO_Types.f90. This avoids the restart type mismatch entirely. KGen handles the original types fine. The kernel uses upstream types but VIT's verification patches and C++ wrapper generation handle the mapping. This is simpler but loses Phase 4C type compatibility during extraction.

```bash
cd vit_translation/ROSCO
git checkout e8010f0 -- rosco/controller/src/Functions.f90 rosco/controller/src/ControllerBlocks.f90 rosco/controller/src/Filters.f90 rosco/controller/src/Controllers.f90 rosco/controller/src/ROSCO_Types.f90 rosco/controller/src/ExtControl.f90 rosco/controller/src/ReadSetParameters.f90 rosco/controller/src/ROSCO_IO.f90
for f in rosco/controller/src/*_cpp.cpp; do echo "// stub" > "$f"; done
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
   ```bash
   docker exec vit-dev bash -c "cd /workspace/ROSCO && bash scripts/verify_all.sh"
   ```
   Must show: `28/28 passed`. Each function should report `N/N passed` where N is the number of cases in its golden fixture (typically 20-23).

3. **Full re-extraction (when extraction logic changes):**
   ```bash
   # Reset source, rebuild, then:
   docker exec vit-dev bash -c "cd /workspace/ROSCO && rm -rf kernel/ && bash scripts/extract_all.sh"
   ```
   Must show: `28/28 passed` with `Cases: 20` or more for each function.

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

The functions included in the clean build test expand as phases complete. Initially only ErrVar-free functions are tested.

**Step 1 — Reset ROSCO to clean state (runs on Mac, not in container):**

```bash
cd vit_translation/ROSCO
bash scripts/reset_to_clean.sh
rm -rf kernel/ state/ model.ini include.ini kgen.log baseline_output.txt integrated_output.txt
```

The script resets all source files to clean Fortran with bug fixes applied and creates C++ stubs. No manual patching needed. See `scripts/reset_to_clean.sh` for commit details.

This gives clean Fortran (no integration wrappers) compatible with Phase 4C ROSCO_Types.f90. Files NOT reset: `ROSCO_Types.f90`, `ExtControl.f90`, `ReadSetParameters.f90`, `ROSCO_IO.f90` (Phase 4A/4C type fixes, no wrappers), `CMakeLists.txt` (`-ffp-contract=off` flags). The `translations/` directory and `vit.yaml` are preserved.

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

Must show `28/28 passed` with `Cases: 20` or more for each function. See `scripts/extract_all.sh` for the per-function scenario assignments.

**Step 4 — Verify C++ translations against extracted kernels (in container):**

```bash
bash scripts/verify_all.sh
```

Must show `28/28 passed`.

**Note:** PitchControl is excluded from the clean build test. It depends on PitchSaturation (ControllerBlocks.f90, Phase 8), which is not yet translated. PitchControl's C++ calls `pitchsaturation_c()` — an undefined symbol without the translation. PitchControl joins the clean build test once PitchSaturation is translated in Phase 8.

**Step 5 — Integrate all functions (in container):**

```bash
bash scripts/integrate_all.sh
```

Must show `27/27 passed`. Each `--apply` reads the current file state and generates correct patches, skipping items already present (e.g., `enable_language(CXX)`, `stdc++` link, `USE ISO_C_BINDING`). See `scripts/integrate_all.sh` for the full function list.

**Step 6 — Rebuild with C++ and compare simulation arrays against baseline (in container):**

```bash
cd rosco/controller/build && rm -rf * && cmake .. -DCMAKE_INSTALL_PREFIX=install && cmake --build . && cp libdiscon.so ../../lib/libdiscon.so && cd /workspace/ROSCO
mkdir -p integrated_arrays
cd Examples && python3 vit_sim.py --output-dir /workspace/ROSCO/integrated_arrays > /workspace/ROSCO/integrated_output.txt 2>&1 && cd /workspace/ROSCO
```

Then compare all simulation arrays byte-for-byte:

```bash
python3 -c "
import numpy as np, sys
ok = True
total = 0
for s in [1, 2, 3, 4, 5, 6, 7, 8]:
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

With `-ffp-contract=off` (the default in CMakeLists.txt), every value must be IDENTICAL. The comparison covers gen_torque, bld_pitch, gen_speed, gen_power, and nac_yaw at every timestep across all 8 scenarios (~612,000 values total). Any difference — even 1 ULP — indicates a translation or integration bug.

Cleanup after passing: `rm -rf integrated_arrays integrated_output.txt`

After confirming the diff is empty, run the **FMA sanity check** — rebuild both baseline and integrated with FMA enabled (the production default) and verify the diff is small and bounded:

```bash
# FMA sanity check: rebuild baseline with FMA enabled
cd /workspace/ROSCO
git checkout e8010f0 -- rosco/controller/src/Functions.f90 rosco/controller/src/ControllerBlocks.f90 rosco/controller/src/Filters.f90
rm -f rosco/controller/src/*_cpp.cpp
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

Each translation file has a metadata header (source file, function name, MD5 hash, VIT version, verification status) generated by `vit translate`.

Integration starts from a clean ROSCO. All functions are integrated in phase order — nothing is pre-integrated.

Note: `interp1d.cpp` uses VIT's per-array size parameter convention (`n_xData`, `n_yData`) rather than a single shared `n` parameter. See "Known Limitations" below.

### TRANSLATION_TIPS.md

`ROSCO/TRANSLATION_TIPS.md` is a living document that captures lessons learned from translation failures. When a translation fails verification and the fix reveals a generalizable lesson, add it to this file. The `vit translate` command includes its contents in the translation prompt automatically, so the prompt gets smarter over time.

## Known Limitations and Future Work

### Derived types

VIT handles derived types with two strategies, configured per type in `vit.yaml`:

- **BIND(C) structs** (default for compatible types): Types with only scalar/fixed-size fields are passed directly as C struct pointers. ErrorVariables, LocalVariables, FilterParameters, piParams, etc. all use this path. `vit analyze-types` scans types and offers automated fixes (`--fix character`, `--fix logical`, `--fix complex`, `--fix bind_c`) to make types C-compatible. All four fix modes are implemented (Phases 4A-4C).

- **View structs** (for ALLOCATABLE types): Types with ALLOCATABLE arrays (ControlParameters, PerformanceData, ExtControlType) use the pointer+size view pattern. A generated view struct exposes ALLOCATABLE fields as `type* field; int n_field;` pairs. A generated Fortran populator fills the view from the real type at runtime using `C_LOC()` + `SIZE()`. Configured in `vit.yaml` under `types: { TypeName: { strategy: view } }`. ALLOCATABLE field sizes are scanned from source via configurable allocation helpers (`allocatable.helpers` in `vit.yaml`) and universal `ALLOCATE` pattern matching. All ALLOCATABLE fields are included in the view struct — the scanner is informational only.

- **Derived type registry is auto-discovered and recursive**: `build_derived_type_registry()` searches the source directory for TYPE definitions used in function args, then recursively discovers nested TYPE(X) fields until all transitive dependencies are found. Types with `strategy: view` in config are marked for view struct treatment. Types not in the registry at all default to passthrough.

- **USE statement matching is string-based**: `_get_use_statements_for_derived_types()` checks if the derived type name appears in the USE line text. This works for `USE Module, ONLY: TypeName` but would miss bare `USE Module` (can't tell what it exports) or false-match on substring collisions.

### Assumed-shape arrays

- **Per-array size params**: VIT generates a separate `n_<name>` size parameter for every assumed-shape array arg, even when arrays are semantically the same length (e.g., `xData` and `yData`). The user's C++ must accept per-array sizes. A future improvement could coalesce size params when arrays share the same rank, but that requires inference VIT currently avoids.

- **No multi-dimensional assumed-shape handling**: `DIMENSION(:,:)` arrays would get a single `n_` size param via the same `:` detection, which is wrong for 2-D. There is no guard or warning — it would silently generate broken code. Add an error or proper handling when a multi-dimensional assumed-shape is encountered. Fixed in Phase 5.

### C++ → C++ call path

During the translation phase (mixed Fortran/C++ binary), every translated function has a Fortran wrapper. Fortran callers use the wrapper. But when a translated C++ function needs to call another translated C++ function (e.g., PitchControl calling PIController), it calls the `_c` entry point directly via a shared C header — no round-trip through Fortran.

- **Fortran callers** → Fortran wrapper → `_c` function → C++ implementation (unchanged)
- **C++ callers** → `_c` function directly via `#include` shared header (no wrapper)
- **Shared header**: `vit integrate --apply` emits/updates a header with `extern "C"` declarations for each integrated function's `_c` entry point
- **`vit translate`**: Scaffolds include `_c` declarations for already-translated callees (detected from `vit.yaml` translations registry)

In Phase 11 (pure C++), all Fortran wrappers become dead code and are removed. C++ calls C++ directly throughout.

### Kernel callee bridges

During kernel verification, only the target function is replaced with C++. If it calls another already-translated function (e.g., ForeAftDamping calls PIController), VIT auto-generates a BIND(C) bridge in the kernel that wraps C→Fortran:

```
C++ translation calls picontroller_c()
    → BIND(C) bridge (auto-generated in kernel)
        → original Fortran PIController() (in kernel, untouched)
```

VIT detects translated callees by scanning the target function's original source and cross-referencing against `vit.yaml` translations. For each callee, it generates: a Fortran bridge function, a C declarations header (`vit_kernel_callees.h`), and Makefile rules. This is automatic during `vit verify` — no manual configuration needed.

**View-type stash for callee bridges:** When a callee bridge needs a view-type argument (like `ControlParameters`), it cannot use `C_F_POINTER` on the view pointer — the view struct has a completely different memory layout than the original Fortran type. Instead, the kernel wrapper "stashes" a pointer to the original Fortran type in a module-level SAVE variable (`vit_original_controlparameters`) before calling C++. Callee bridges USE this variable to get the original type directly. The stash is set in the view populator module and valid for the entire wrapper call duration.

### KGen extraction limitations

- **Call site must be reachable at runtime**: KGen generates the kernel and instrumented code regardless, but zero state files are produced if the call site is never executed during the simulation. This was the original SecLPFilter_Vel issue — the `CALL CableControl` in DISCON.F90 is gated by `IF (CntrPar%CC_Mode > 0)`, and Scenario 3 originally had `CC_Mode=0`. Fixed by setting `CC_Mode=1` in vit_sim.py. KGen handles DO loops with derived-type iteration bounds correctly — the instrumented code is placed inside the loop body.

- **Invocation ordering across scenarios**: KGen counts invocations per enclosing subroutine. When `vit_sim.py` runs multiple scenarios, Scenario 1's invocations consume the `0:0:1-20` capture range. Code paths only active in later scenarios (e.g., Scenario 3's IPC/notch/cable control) are never captured. Fix: Scenario 3 runs first in `vit_sim.py` so its invocations fall within the capture range.

- **Kernel Makefile missing project Fortran flags**: KGen-generated kernel Makefiles don't include project-specific compiler flags like `-fdefault-real-8`. Without these, Fortran literal constants (e.g., `PI = 3.14159265359`) are evaluated as single-precision, causing coefficient mismatches. VIT's `fortran.flags` config field propagates these flags to the kernel Makefile.

- **PROCEDURE POINTER declarations** (fixed in our KGen fork): KGen's parser couldn't handle `PROCEDURE(...), POINTER` declarations (Fortran 2003+). Fixed by uncommenting and completing the `Procedure` class in KGen's parser (commit `31d653c`). Users of unpatched KGen will hit `'Line' object has no attribute 'analyze'` when extracting from files that USE modules with procedure pointers.

- **Assumed-size arrays in state capture**: KGen generates state capture write functions with `DIMENSION(:)` (assumed-shape) parameters. Fortran forbids passing assumed-size `(*)` arrays to assumed-shape parameters. VIT auto-detects assumed-size arrays from the enclosing subroutine's signature, scans source for index access evidence, and proposes an appropriate size via the confirm protocol (exit code 10). The user confirms with `--assumed-size NAME=SIZE`. Optionally, pre-configure `kgen.assumed_size_arrays` in vit.yaml to skip confirmation. **The size must cover ALL indices the function writes to** — undersizing causes silent heap corruption because Fortran cannot bounds-check assumed-size `(*)` arrays.

- **DLL SAVE variable persistence**: Within a single `vit_sim.py` run, Fortran SAVE variables may persist across scenarios (ctypes `dlclose()` may not truly unload the library). Project-specific guard variables (e.g., ROSCO's `AlreadyInitialized`) need resetting between scenarios so the controller re-initializes. Workaround: configure `kgen.dll_persistence.resets` in vit.yaml with anchor patterns and reset code. VIT patches the instrumented source via `sed` with `!VIT_DLL_RESET` tags (cleaned up after build). KGen's own SAVE arrays (`kgen_isstop`, `kgen_invoke`) do NOT need resetting — each extraction phase (elapsedtime, state capture) is a separate subprocess with fresh SAVE variables, and within a single run the invocation counters naturally exceed the capture range after the first scenario. Use `--run-args '--scenario N'` to limit extraction to a single scenario when the target function is gated by a mode flag.

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
