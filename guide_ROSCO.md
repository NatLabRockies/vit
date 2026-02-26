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
| conda/mamba | Any | Environment management |
| KGen | NCAR/KGen (patched) | Runtime state extraction |
| PyYAML | Any | VIT config files |

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

### ROSCO Conda Environment

```bash
# Create the ROSCO environment (Python 3.x)
conda create -p /path/to/envs/rosco python=3.14
conda activate /path/to/envs/rosco

# Install ROSCO dependencies
pip install numpy matplotlib openfast rosco  # or as needed
```

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

All commands assume you are in the ROSCO project root directory.

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
cmake --install .
cd ../../..

# Verify the shared library was created
ls -la rosco/controller/build/libdiscon.so
ls -la rosco/lib/libdiscon.so
```

### Step 2: Install ROSCO as Editable Package (Critical)

The simulation must load the library from **this** checkout, not another one. Install as an editable package from the current directory:

```bash
conda run -p /path/to/envs/rosco pip install -e .
```

**Verify it points to the right place:**

```bash
conda run -p /path/to/envs/rosco pip show rosco | grep -i location
```

The "Editable project location" must match your current ROSCO directory. If it points elsewhere, the simulation will load an uninstrumented library and state capture will silently fail.

### Step 3: Verify the Simulation Runs

```bash
cd Examples
conda run -p /path/to/envs/rosco python 01_turbine_model.py
conda run -p /path/to/envs/rosco python 04_simple_sim.py
cd ..
```

Save this output as your baseline for comparison after integration:

```bash
cp Examples/04_simple_sim_results.txt baseline_output.txt
```

### Step 4: Initialize VIT Configuration

```bash
vit init --name ROSCO \
         --source-dir rosco/controller/src \
         --build-cmd "cd rosco/controller/build && cmake --build ." \
         --run-cmd "cd Examples && conda run -p /path/to/envs/rosco python 04_simple_sim.py"
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
  run_cmd: "cd /absolute/path/to/ROSCO/Examples && conda run -p /absolute/path/to/envs/rosco python 04_simple_sim.py"
  clean_cmd: "cd /absolute/path/to/ROSCO/rosco/controller/build && make clean"
  install_cmd: "cp /absolute/path/to/ROSCO/rosco/controller/build/libdiscon.so /absolute/path/to/ROSCO/rosco/lib/libdiscon.so"

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

verification:
  tolerance: 1.0e-14

translations: {}
```

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
  Returns: saturate :: REAL(DbKi)
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
  Cases:  1
```

**Verify state was captured:**

```bash
ls kernel/saturate.0.0.1
```

If "Cases: 0" or no `saturate.0.0.1` file, see the Troubleshooting section.

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
✓ VERIFICATION PASSED: 1/1 cases
```

### Step 10: Generate and Apply Integration Plan

First, review the plan:

```bash
vit integrate saturate saturate.cpp \
    -f rosco/controller/src/Functions.f90
```

This prints 7 patches. Review them, then apply:

```bash
vit integrate saturate saturate.cpp \
    -f rosco/controller/src/Functions.f90 \
    --apply
```

VIT auto-applies 5 patches and flags 2 as "manual review needed" (CMakeLists.txt insertions). Apply those manually:

**Edit `rosco/controller/CMakeLists.txt`:**

1. Add `src/saturate_cpp.cpp` to the `set(SOURCES ...)` list:

```cmake
set(SOURCES
    src/Constants.f90
    src/ControllerBlocks.f90
    src/Controllers.f90
    src/DISCON.F90
    src/ROSCO_Types.f90
    src/Functions.f90
    src/Filters.f90
    src/saturate_cpp.cpp       # <-- ADD THIS
    ...
```

2. Add `target_link_libraries` after `add_library(discon SHARED ${SOURCES})`:

```cmake
add_library(discon SHARED ${SOURCES})
target_link_libraries(discon PRIVATE stdc++)    # <-- ADD THIS
```

### Step 11: Rebuild and Verify Simulation Output

```bash
cd rosco/controller/build
rm -rf *
cmake .. -DCMAKE_INSTALL_PREFIX=install
cmake --build .
cmake --install .
cd ../../..

# Copy to where simulation loads it
cp rosco/controller/build/libdiscon.so rosco/lib/libdiscon.so

# Run simulation
cd Examples
conda run -p /path/to/envs/rosco python 04_simple_sim.py
cd ..
```

Compare against baseline:

```bash
diff baseline_output.txt Examples/04_simple_sim_results.txt
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

KGen's Makefile uses `${FC_0}` and `${FC_FLAGS_SET_0}` but doesn't define them. VIT patches these automatically. If you see this error, the Makefile patcher didn't run — ensure you're using the latest VIT.

### Kernel build fails: ROSCO_Types.f90 "Invalid character in name"

KGen sometimes generates multi-line `USE kgen_utils_mod, ONLY:` statements with Fortran continuation lines (`&`). VIT's fixer must remove the ONLY clause **and** all continuation lines. This is fixed in the latest `kernel_verify.py`. If you hit this on an existing kernel:

```bash
# Find the orphaned continuation line
grep -n "&CHECK_IDENTICAL\|&kgen_" kernel/ROSCO_Types.f90
# Delete it (replace N with the line number)
sed -i 'Nd' kernel/ROSCO_Types.f90
```

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
| `vit parse <func>` | Parse function signature | `-f <definition_file>` |
| `vit find <func>` | Find call sites in source tree | `--source-dir` |
| `vit extract <func>` | KGen extraction + state capture | `-f <call_site_file>`, `-l <line>` |
| `vit verify <func> <cpp>` | Verify C++ against captured state | `-f <definition_file>`, `--kernel-dir` |
| `vit integrate <func> <cpp>` | Generate/apply integration patches | `-f <definition_file>`, `--apply` |
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

### What VIT Patches in the Kernel

When you run `vit verify`, VIT modifies the KGen kernel directory:

- **Makefile:** Adds `FC_0`/`FC_FLAGS_SET_0` definitions, C++ compilation rule, adds `.o` to `ALL_OBJS`, adds `-lstdc++`
- **Fortran files:** Fixes `USE kgen_utils_mod, ONLY:` → `USE kgen_utils_mod`, adds `USE ISO_C_BINDING` before `IMPLICIT NONE`, inserts interface block above `CONTAINS`, replaces function body with C-calling wrapper
- **New files:** `saturate_impl.hpp` (your C++ code), `saturate_cpp.cpp` (extern "C" wrapper)
