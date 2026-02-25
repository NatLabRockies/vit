# VIT — Verified Incremental Translator

**A pipeline orchestrator for trustworthy Fortran→C++ incremental translation in scientific computing.**

VIT automates everything *around* the translation step: extracting functions with KGen, generating deterministic Fortran↔C interfaces, wiring C++ into KGen kernels for verification, and integrating verified translations back into the codebase.

The translation itself is intentionally external — use [CodeScribe](https://github.com/akashdhruv/CodeScribe), any LLM, or hand-write the C++. VIT verifies it's correct.

## Why VIT?

Existing Fortran→C++ tools can produce translations, but none verify correctness against actual runtime data. For safety-critical scientific codes (wind turbine controllers, nuclear simulations, weather models), the output code needs to be rigorously validated.

VIT closes this gap by leveraging KGen's ability to capture real runtime state from simulations and verify that the C++ translation produces bit-identical results.

## The Pipeline

```
                    ┌──────────────────────────────┐
                    │  YOU PROVIDE THE TRANSLATION │
                    │  (CodeScribe / LLM / manual) │
                    └──────────────┬───────────────┘
                                   │
    ┌──────────┐    ┌──────────┐   │   ┌──────────┐    ┌───────────┐
    │ EXTRACT  │───▶│INTERFACE │───┼──▶│ VERIFY   │───▶│ INTEGRATE │
    │ (KGen)   │    │ (determ) │   │   │ (KGen)   │    │ (patches) │
    └──────────┘    └──────────┘   │   └──────────┘    └───────────┘
         │                         │         │
    Captures real           Mechanical    Bit-identical
    runtime state           type mapping  verification
    from simulation         (no LLM)     against runtime
```

### What each step does:

1. **Extract** (`vit extract`): Runs KGen to capture runtime state for a target function from an actual simulation. Produces kernel/ and state/ directories.

2. **Interface** (`vit interface`): Deterministically generates the Fortran↔C binding layer — `extern "C"` wrapper, `ISO_C_BINDING` interface block, and Fortran wrapper function. This is a mechanical transform, not an LLM task.

3. **Verify** (`vit verify`): Wires the C++ translation into the KGen kernel build, compiles, and runs verification against captured runtime state. Reports pass/fail per case with tolerance checking.

4. **Integrate** (`vit integrate`): Generates a complete integration plan (file patches) for the real project: C++ source file, Fortran module modifications, CMakeLists.txt updates.

## Quick Start

```bash
# Install
pip install -e .

# Initialize in your project directory
cd /path/to/my-fortran-project
vit init --name ROSCO \
         --source-dir rosco/controller/src \
         --build-cmd "cmake --build build" \
         --run-cmd "python Examples/04_simple_sim.py"

# Edit vit.yaml to set kind aliases, KGen path, etc.

# Step 1: Find where the function is called
vit find saturate

# Step 2: Parse the function signature
vit parse saturate

# Step 3: Extract with KGen (captures runtime state)
vit extract saturate

# Step 4: [YOU PROVIDE C++ TRANSLATION] → save as saturate.cpp

# Step 5: Full pipeline — verify + integrate
vit run saturate saturate.cpp --kernel-dir kernel/ --apply
```

## Example: ROSCO `saturate`

This is the function we proved the pipeline on. The original Fortran:

```fortran
FUNCTION saturate(val, minval, maxval) RESULT(saturate_result)
    REAL(ReKi), INTENT(IN) :: val, minval, maxval
    REAL(ReKi) :: saturate_result
    saturate_result = MIN(MAX(val, minval), maxval)
END FUNCTION saturate
```

User-provided C++ translation (`saturate.cpp`):

```cpp
double saturate(double val, double minval, double maxval) {
    return std::min(std::max(val, minval), maxval);
}
```

VIT then:
1. **Generates the interface** (deterministic — no LLM):
   - `extern "C" { double saturate_c(double, double, double); }` wrapper
   - Fortran `INTERFACE` block with `BIND(C)` and `ISO_C_BINDING`
   - Fortran wrapper that calls `saturate_c` with type conversions

2. **Verifies against KGen kernel**: 20/20 cases PASSED, bit-identical at 1E-14

3. **Generates integration patches** for ROSCO:
   - New file: `saturate_cpp.cpp`
   - Modify: `Functions.f90` (add interface + wrapper)
   - Modify: `CMakeLists.txt` (enable CXX, add source, link stdc++)

4. **Result**: Full ROSCO simulation output byte-for-byte identical to original Fortran.

## Configuration (vit.yaml)

```yaml
project:
  name: ROSCO
  source_dir: rosco/controller/src
  build_dir: build

kgen:
  path: /path/to/kgen
  build_cmd: "cd build && cmake --build ."
  run_cmd: "cd Examples && python 04_simple_sim.py"
  clean_cmd: "cd build && make clean"

build_system:
  type: cmake
  cmake_file: rosco/controller/CMakeLists.txt
  target_name: discon

fortran:
  kind_aliases:
    ReKi: "REAL(8)"
    DbKi: "REAL(8)"
    IntKi: "INTEGER(4)"
  compiler: gfortran

verification:
  tolerance: 1.0e-14
```

## Key Design Decisions

**Interface generation is deterministic.** Given a Fortran function signature, the `extern "C"` wrapper and `ISO_C_BINDING` interface are fully determined by the type mapping.

**Translation is external.** VIT doesn't care how you produce the C++. This makes it composable with any translation tool.

## Architecture

```
vit/
  cli.py              # Click-style CLI with subcommands
  config.py           # YAML project configuration
  type_map.py         # Deterministic Fortran↔C type mapping
  fortran_parser.py   # Parse function signatures from source
  interface_gen.py    # Generate extern "C" + ISO_C_BINDING interfaces
  extract.py          # KGen extraction orchestration
  kernel_verify.py    # Wire C++ into kernel, build, verify
  integrate.py        # Generate codebase integration patches
```

## Requirements

- Python ≥ 3.10
- PyYAML
- KGen (for extraction and verification)
- gfortran + g++ (for kernel builds)
- CMake (for project integration, if applicable)

## What VIT Is NOT

- **Not a translator.** Use CodeScribe, Claude, GPT, or hand-coding.
- **Not a full Fortran parser.** It parses signatures, not arbitrary Fortran.
- **Not a replacement for testing.** KGen verification checks numerical equivalence for captured cases. Integration testing is still your responsibility.