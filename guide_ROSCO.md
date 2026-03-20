# VIT Pipeline Guide: Verified Fortran→C++ Translation

## Overview

VIT (Verified Incremental Translator) automates the extraction, verification, and integration of Fortran→C++ translations in scientific codebases. It uses KGen to capture runtime state from real simulations, then verifies that a C++ translation produces bit-identical results before integrating it into the codebase.

This guide walks through the complete pipeline using the ROSCO wind turbine controller's `saturate` function as a worked example. Every command has been tested and produces a verified, byte-identical simulation output.

**What VIT does:**

- Extracts a target function's runtime inputs/outputs from a real simulation (via KGen)
- Generates the Fortran↔C interop layer deterministically (no LLM involved)
- Wires your C++ translation into a standalone verification kernel
- Runs bit-identical verification against captured runtime data
- Generates and applies integration patches to the real codebase

**What VIT does NOT do:**

- Translate Fortran to C++. Use CodeScribe, an LLM, or write it by hand — then feed it to VIT for verification.

---

## Prerequisites

### Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 2.7 (for KGen) + 3.10+ (for VIT, ROSCO) | KGen requires Python 2.7; everything else uses 3.x |
| gfortran | Any recent | Compiles KGen kernels |
| g++ | Any recent | Compiles C++ translations |
| CMake | ≥ 3.0 | ROSCO build system |
| KGen | NCAR/KGen (patched) | Runtime state extraction |
| PyYAML | Any | VIT config files |

> **macOS users:** KGen's Fortran parser does not work on macOS. Run all VIT commands inside a Linux Docker container. See `CLAUDE.md` for the recommended Docker setup with the `vit-dev` container.

### KGen Setup

KGen requires Python 2.7. The shebang in `KGen/bin/kgen` must point to a Python 2.7 interpreter:

```bash
# Clone KGen
git clone https://github.com/NCAR/KGen.git
cd KGen

# Edit the shebang to use Python 2.7 explicitly
# Change first line of bin/kgen from:
#   #!/usr/bin/env python
# to:
#   #!/usr/bin/env python2.7
```

Verify KGen can run:

```bash
python2.7 -c "import sys; print(sys.version)"
# Should print 2.7.x
```

### ROSCO Python Environment

You need Python 3.10 or newer. Any environment manager works (conda, venv, system Python, Docker):

```bash
# Install ROSCO dependencies
pip install numpy matplotlib openfast rosco  # or as needed
```

<details>
<summary>Example using conda</summary>

```bash
conda create -n rosco python=3.12
conda activate rosco
pip install numpy matplotlib openfast rosco
```
</details>

### VIT Installation

```bash
git clone <vit-repo-url>
cd vit
pip install -e .

# Verify
vit --help
```

---

## Step-by-Step Pipeline

All commands assume you are in the ROSCO project root directory and running in a Linux environment (native or Docker container).

### Step 0: Fresh ROSCO Checkout

```bash
cd /path/to/your/workspace
git clone https://github.com/NREL/ROSCO.git
cd ROSCO
```

### Step 1: Build ROSCO

```bash
cd rosco/controller
mkdir -p build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=install
cmake --build .
cp libdiscon.so ../../lib/libdiscon.so
cd ../../..

# Verify the shared library is in place
ls -la rosco/lib/libdiscon.so
```

The explicit `cp` is important — the simulation loads the library from `rosco/lib/`, not from the build directory. This same copy step is what `install_cmd` automates in `vit.yaml`.

### Step 2: Install ROSCO as Editable Package (Critical)

The simulation must load the library from **this** checkout, not another one. Install as an editable package from the current directory:

```bash
pip install -e .
```

**Verify it points to the right place:**

```bash
pip show rosco | grep -i location
```

The "Editable project location" must match your current ROSCO directory. If it points elsewhere, the simulation will load an uninstrumented library and state capture will silently fail.

### Step 3: Verify the Simulation Runs

On a fresh checkout, the pickled turbine model doesn't exist yet. Run the turbine model script once to create it:

```bash
cd Examples
python3 01_turbine_model.py
cd ..
```

This creates `Examples/examples_out/01_NREL5MW_saved.p`, which `04_simple_sim.py` loads. You only need to do this once per fresh checkout.

Now run the simulation and save the output as a baseline:

```bash
cd Examples
python3 vit_sim.py > ../baseline_output.txt 2>&1
cd ..
```

Verify the baseline was captured:

```bash
wc -l baseline_output.txt
# Should show several hundred lines of simulation output
```

### Step 4: Initialize VIT Configuration

```bash
vit init --name ROSCO \
         --source-dir rosco/controller/src \
         --build-cmd "cd rosco/controller/build && cmake --build ." \
         --run-cmd "cd Examples && python3 vit_sim.py"
```

Then **edit `vit.yaml`** to use absolute paths and add all required fields. KGen runs build/run commands from unpredictable working directories, so relative paths will break.

```yaml
project:
  name: ROSCO
  source_dir: rosco/controller/src
  build_dir: rosco/controller/build

kgen:
  path: /absolute/path/to/KGen/bin/kgen
  build_cmd: "cd /absolute/path/to/ROSCO/rosco/controller/build && cmake --build ."
  run_cmd: "cd /absolute/path/to/ROSCO/Examples && python3 vit_sim.py"
  clean_cmd: "cd /absolute/path/to/ROSCO/rosco/controller/build && make clean"
  install_cmd: "cp /absolute/path/to/ROSCO/rosco/controller/build/libdiscon.so /absolute/path/to/ROSCO/rosco/lib/libdiscon.so"
  invocation: "0:0:1-20"

build_system:
  type: cmake
  cmake_file: rosco/controller/CMakeLists.txt
  target_name: discon

fortran:
  kind_aliases:
    ReKi: "REAL(8)"
    DbKi: "REAL(8)"
    SiKi: "REAL(4)"
    IntKi: "INTEGER(4)"
  compiler: gfortran
  flags: "-fdefault-real-8 -fdefault-double-8"  # Must match project's Fortran flags

verification:
  tolerance: 1.0e-14

translations: {}
```

> **Note on `fortran.flags`:** These flags are propagated to the kernel Makefile during `vit verify`. Without them, Fortran literal constants (e.g., `PI = 3.14159265359`) may be evaluated as single-precision, causing coefficient mismatches. Match whatever flags your project's CMakeLists.txt sets for Fortran compilation.

**Important:** Replace every `/absolute/path/to/` with your actual absolute paths.

### Step 5: Parse the Function Signature (Sanity Check)

```bash
vit parse saturate -f rosco/controller/src/Functions.f90
```

Expected output:

```
FUNCTION saturate
  File: rosco/controller/src/Functions.f90:...
  Arguments:
    inputValue: REAL(DbKi) INTENT(IN)
    minValue: REAL(DbKi) INTENT(IN)
    maxValue: REAL(DbKi) INTENT(IN)
  Returns: saturate_result :: REAL(DbKi)
  C name: saturate_c
```

This confirms VIT can read the function signature and resolve the `DbKi` kind alias.

### Step 6: Find the Call Site

```bash
vit find saturate
```

This searches the source tree and reports where `saturate` is called. Note the file path and line number — you'll need them for extraction.

### Step 7: Extract with KGen (State Capture)

This is the slow step. KGen instruments the code, rebuilds, runs the simulation, and captures the runtime inputs/outputs of `saturate`.

```bash
vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102
```

**Note:** Use the file with the **call site** (Controllers.f90), not the definition (Functions.f90). The line number (102) is where `saturate` is called — adjust if your version differs.

Expected output:

```
✓ Extraction successful
  Kernel: ./kernel
  State:  ./state
  Cases:  20
```

**Verify state was captured:**

```bash
ls kernel/saturate.0.0.*
```

You should see 20 state files (`saturate.0.0.1` through `saturate.0.0.20`), one per captured invocation. The invocation range is controlled by `kgen.invocation` in `vit.yaml` (default: `0:0:1-20`, meaning MPI rank 0, thread 0, invocations 1-20).

If "Cases: 0" or no state files, see the Troubleshooting section.

### Step 8: Write the C++ Translation

Create your C++ implementation. VIT doesn't generate this — you provide it.

```bash
cat > saturate.cpp << 'EOF'
#include <algorithm>

double saturate(double val, double minval, double maxval) {
    return std::min(std::max(val, minval), maxval);
}
EOF
```

### Step 9: Verify Against Captured State

```bash
vit verify saturate saturate.cpp \
    -f rosco/controller/src/Functions.f90 \
    --kernel-dir kernel/
```

**Note:** Use the file with the function **definition** (Functions.f90) for `verify` and `integrate`.

Expected output:

```
Setting up C++ verification in kernel/...
  Modified 10 files

Running verification...

✓ VERIFICATION PASSED: 40/40 passed
```

The 20 captured invocations produce 40 verification points (each invocation checks both input and output state). All 40 must pass for verification to succeed.

If some cases contain NaN reference data (from uninitialized struct fields during initialization calls), VIT classifies them as "skipped" rather than "failed". In that case the output includes a skip note: `✓ VERIFICATION PASSED: 40/40 passed (2 skipped: NaN reference data)`. See the Architecture Notes for details on NaN-safe verification.

### Shortcut: `vit run`

Steps 9 and 10 can be combined using `vit run`, which runs the full pipeline (verify + integrate) in one command:

```bash
vit run saturate saturate.cpp \
    -f rosco/controller/src/Functions.f90 \
    --kernel-dir kernel/ \
    --apply
```

Omit `--apply` to preview the integration plan without modifying files. Use `--skip-verify` to skip the verification step (not recommended for production use).

### Step 10: Generate and Apply Integration Plan

First, review the plan:

```bash
vit integrate saturate saturate.cpp \
    -f rosco/controller/src/Functions.f90
```

This prints the integration plan. Review the patches, then apply:

```bash
vit integrate saturate saturate.cpp \
    -f rosco/controller/src/Functions.f90 \
    --apply
```

All patches are applied automatically — Fortran source modifications (interface block, wrapper function), CMakeLists.txt updates (`enable_language(CXX)`, C++ source file, `stdc++` link), and the C++ wrapper file. No manual edits needed. Multiple functions can be integrated sequentially; each `--apply` reads the current file state and skips items already present.

### Step 11: Rebuild and Verify Simulation Output

```bash
cd rosco/controller/build
rm -rf *
cmake .. -DCMAKE_INSTALL_PREFIX=install
cmake --build .
cp libdiscon.so ../../lib/libdiscon.so
cd ../../..

# Run simulation and capture output
cd Examples
python3 vit_sim.py > ../integrated_output.txt 2>&1
cd ..
```

Compare against baseline:

```bash
diff baseline_output.txt integrated_output.txt
```

**Empty diff = success.** The simulation output is byte-for-byte identical to the original Fortran.

---

## Troubleshooting

### State capture returns 0 cases

**Symptoms:** `vit extract` succeeds but reports "Cases: 0" or no `kernel/saturate.0.0.1` file.

**Common causes:**

1. **`install_cmd` missing or uses relative paths.** KGen builds the instrumented library but the simulation loads a different copy. Check `vit.yaml` has `install_cmd` with absolute paths:
   ```yaml
   install_cmd: "cp /abs/path/to/build/libdiscon.so /abs/path/to/lib/libdiscon.so"
   ```

2. **Editable pip install points to wrong checkout.** Run:
   ```bash
   pip show rosco | grep -i location
   ```
   If it doesn't point to your current ROSCO directory, reinstall:
   ```bash
   cd /path/to/your/ROSCO
   pip install -e .
   ```

3. **Build commands use relative paths.** All paths in `vit.yaml` under `kgen:` must be absolute.

### Kernel build fails: "c: No such file or directory"

KGen's Makefile uses `${FC_0}` and `${FC_FLAGS_SET_0}` but doesn't define them. VIT patches these automatically during `vit verify`. If you see this error, ensure you're using the latest VIT.

### Kernel build fails: ROSCO_Types.f90 "Invalid character in name"

KGen sometimes generates multi-line `USE kgen_utils_mod, ONLY:` statements with Fortran continuation lines (`&`). VIT automatically fixes these during `vit verify` by removing the ONLY clause and all continuation lines (handled by `_fix_kgen_utils_mod_only()` in `kernel_verify.py`).

If you encounter this error, ensure you are using the latest VIT. Running `vit verify` again on a fresh kernel should fix it automatically.

### Verification reports 0/0 cases

The kernel built but didn't produce output. Run manually to see the actual error:

```bash
cd kernel/
make clean && make build 2>&1
chmod +x kernel.exe
./kernel.exe
```

### KGen fails: "No strace file is found"

KGen uses strace to discover compiler flags. Common causes:

- Build command failed (check that `cmake --build .` works from the absolute path in `vit.yaml`)
- Build directory doesn't exist (run `mkdir -p build && cd build && cmake ..` first)
- KGen's Python 2.7 shebang not set correctly

---

## File Reference

### Key Files After Pipeline Completion

| File | Purpose |
|------|---------|
| `vit.yaml` | Project configuration (kind aliases, build commands, paths) |
| `kernel/` | KGen-generated verification kernel |
| `kernel/saturate.0.0.1` | Captured runtime state (function inputs/outputs) |
| `saturate.cpp` | Your C++ translation (user-provided) |
| `rosco/controller/src/saturate_cpp.cpp` | Generated C++ file with `extern "C"` wrapper |
| `rosco/controller/src/Functions.f90` | Modified: interface block + wrapper function |
| `rosco/controller/CMakeLists.txt` | Modified: CXX enabled, C++ source added |

### VIT Commands Quick Reference

| Command | Purpose | Key flags |
|---------|---------|-----------|
| `vit init` | Create `vit.yaml` | `--name`, `--source-dir`, `--build-cmd`, `--run-cmd` |
| `vit find <func>` | Find call sites in source tree | `--source-dir` |
| `vit parse <func>` | Parse function signature | `-f <definition_file>` |
| `vit extract <func>` | KGen extraction + state capture | `-f <call_site_file>`, `-l <line>`, `--dry-run` |
| `vit interface <func>` | Generate Fortran↔C interface files | `-f <definition_file>`, `-o <output_dir>` |
| `vit verify <func> <cpp>` | Verify C++ against captured state | `-f <definition_file>`, `--kernel-dir` |
| `vit integrate <func> <cpp>` | Generate/apply integration patches | `-f <definition_file>`, `--apply`, `--dry-run` |
| `vit run <func> <cpp>` | Full pipeline (verify + integrate) | `-f <definition_file>`, `--kernel-dir`, `--skip-verify`, `--apply` |
| `vit status` | Show translation status | |

**Important distinction:** `extract` takes the file with the **call site**. `parse`, `verify`, and `integrate` take the file with the function **definition**.

---

## Architecture Notes

### Why the Interface Layer is Deterministic

The Fortran↔C binding is a mechanical type mapping, not a translation task:

```
Fortran REAL(DbKi)  →  ISO_C_BINDING REAL(C_DOUBLE)  →  C double
```

Given a function signature and kind aliases, the `extern "C"` wrapper, `ISO_C_BINDING` interface block, and Fortran wrapper function are fully determined. VIT generates these without any LLM involvement, which means they are guaranteed correct.

### How KGen Verification Works

1. **During extraction:** KGen instruments the Fortran code to write function arguments to a state file on each call during a real simulation run.

2. **During verification:** KGen builds a standalone kernel that reads the state file, calls the function with the captured inputs, and compares the output against the captured output. VIT replaces the original Fortran function body with a wrapper that calls your C++ code through the `extern "C"` interface.

3. **The result:** If verification passes, your C++ produces the exact same output as the original Fortran for every captured invocation, within the specified tolerance (default: 1E-14).

### NaN-Safe Verification

KGen captures state for every call to the target function within the enclosing routine, not just the pragma'd call site. Some invocations occur during initialization where struct fields are uninitialized, containing heap/stack residue that may be NaN. This causes two problems:

1. **IEEE 754 NaN != NaN.** Standard floating-point comparison reports NaN as not equal to itself. When both the computed and reference values are NaN (from the same uninitialized field), KGen reports a failure even though the code is correct.

2. **Non-deterministic results across runs.** The NaN pattern in uninitialized memory varies between runs (due to ASLR, allocator behavior, stack depth). A field might be NaN in one run but a valid float in another, causing verification to pass sometimes and fail other times.

VIT solves this with two patches applied during `vit verify`:

- **`verboseLevel=100`**: Forces KGen to print per-field comparison details. At the default verbosity, "Difference is NaN" lines are suppressed, making it impossible to detect NaN cases programmatically.

- **NaN==NaN guard**: Extends each field comparison from `IF (var%X == kgenref_var%X)` to also accept the case where both sides are NaN, using the IEEE 754 property that `x /= x` is true only for NaN. For integer fields, the guard is harmlessly always-false.

Cases where all differences are NaN are classified as "skipped" in the verification summary rather than "failed". At least one valid (non-NaN) case must pass for verification to succeed overall.

### What VIT Patches in the Kernel

When you run `vit verify`, VIT modifies the KGen kernel directory:

- **Makefile:** Adds `FC_0`/`FC_FLAGS_SET_0` definitions, C++ compilation rule, adds `.o` to `ALL_OBJS`, adds `-lstdc++`, ensures `kernel.exe` gets execute permission after linking
- **Fortran files — structural:** Fixes `USE kgen_utils_mod, ONLY:` → `USE kgen_utils_mod` (including multi-line continuation cleanup), adds `USE ISO_C_BINDING` before `IMPLICIT NONE`, inserts interface block above `CONTAINS`, replaces function body with C-calling wrapper
- **Fortran files — verification correctness:** Sets `verboseLevel=100` so per-field comparison output is visible, patches NaN comparisons with IEEE 754 guards so NaN==NaN is treated as IDENTICAL
- **New files:** `saturate_impl.hpp` (your C++ code copied in), `saturate_cpp.cpp` (extern "C" wrapper)

---

## Beyond Scalar Functions

The `saturate` walkthrough above covers the simplest case — a pure function with scalar arguments. The same pipeline handles substantially more complex functions without any workflow changes.

### Filter functions (Filters.f90)

Filter functions like `LPFilter` and `NotchFilterSlopes` take a `FilterParameters` derived-type struct by pointer, with instance-indexed state fields (e.g., `objInst%instLPF` selects which filter instance's state to read/write). VIT auto-generates the struct as a C header and passes it through the `extern "C"` interface. OPTIONAL Fortran arguments are handled with sentinel values.

```bash
vit verify NotchFilterSlopes translations/Filters/notchfilterslopes.cpp \
    -f rosco/controller/src/Filters.f90 \
    --kernel-dir kernel/NotchFilterSlopes
# ✓ VERIFICATION PASSED: 40/40 passed
```

### Controller functions (Controllers.f90)

Controller functions like `PIController` and `ResController` take read/write struct pointers (`piParams`, `resParams`) whose fields are updated on each call. Some controllers inline calls to other translated functions (e.g., `saturate`). VIT generates a shared C header so translated C++ callers invoke the `_c` entry point directly — no round-trip through Fortran.

```bash
vit verify PIController translations/Controllers/picontroller.cpp \
    -f rosco/controller/src/Controllers.f90 \
    --kernel-dir kernel/PIController
# ✓ VERIFICATION PASSED: 40/40 passed
```

In all cases the workflow is identical: `extract` → provide C++ → `verify` → `integrate`. VIT handles the interface generation, struct mapping, and kernel wiring automatically.
