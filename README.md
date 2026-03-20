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

**Note on the `-f` flag:** `extract` takes the file containing the **call site** (e.g., `-f Controllers.f90`), while `parse`, `verify`, and `integrate` take the file containing the function **definition** (e.g., `-f Functions.f90`).

## Quick Start

```bash
# Install
pip install -e .

# Initialize in your project directory
cd /path/to/my-fortran-project
vit init --name MyProject --source-dir src

# Edit vit.yaml to configure:
#   - kgen.path: absolute path to KGen (e.g., /workspace/KGen/bin/kgen)
#   - kgen.build_cmd, run_cmd, clean_cmd: use absolute paths
#   - kgen.install_cmd: copy built library to where simulation loads it
#   - fortran.kind_aliases: map project-specific types to standard Fortran

# Step 1: Find where the function is called
vit find saturate

# Step 2: Parse the function signature
vit parse saturate -f rosco/controller/src/Functions.f90

# Step 3: Extract with KGen (captures runtime state)
vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102

# Step 4: [YOU PROVIDE C++ TRANSLATION] → save as saturate.cpp

# Step 5: Verify C++ against captured state
vit verify saturate saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/

# Step 6: Generate and apply integration patches
vit integrate saturate saturate.cpp -f rosco/controller/src/Functions.f90 --apply
```

For a complete end-to-end walkthrough using the ROSCO wind turbine controller, see [guide_ROSCO.md](guide_ROSCO.md).

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

2. **Verifies against KGen kernel**: 40/40 cases PASSED, bit-identical at 1E-14
   - KGen captures state for all invocations including initialization calls with uninitialized struct fields (NaN heap residue). Cases where all reference data is NaN are automatically skipped rather than counted as failures.

3. **Generates integration patches** for ROSCO:
   - New file: `saturate_cpp.cpp`
   - Modify: `Functions.f90` (add interface + wrapper)
   - Modify: `CMakeLists.txt` (enable CXX, add source, link stdc++)

4. **Result**: Full ROSCO simulation output byte-for-byte identical to original Fortran.

## Current Status

VIT has been validated on **18 functions** across 4 Fortran modules in the ROSCO wind turbine controller, all KGen-verified 40/40 IDENTICAL:

| Module | Functions | Complexity |
|--------|-----------|------------|
| **Functions.f90** | `saturate`, `wrap_180`, `wrap_360`, `ratelimit`, `ColemanTransform`, `ColemanTransformInverse`, `identity`, `sigma`, `interp1d` | Scalars, arrays, `ErrorVariables` struct, trig |
| **Filters.f90** | `LPFilter`, `HPFilter`, `SecLPFilter`, `NotchFilter`, `NotchFilterSlopes`, `SecLPFilter_Vel` | `FilterParameters` struct pointer, instance-indexed state, OPTIONAL args |
| **Controllers.f90** | `PIController`, `PIIController`, `ResController` | `piParams`/`resParams` struct R/W, inlined callees |
| **ControllerBlocks.f90** | `identity` | Cross-module integration |

The progression from simple scalar functions to struct-pointer-based filters and controllers demonstrates that the pipeline scales beyond trivial cases. With all 18 functions integrated and rebuilt, the ROSCO simulation produces **byte-identical output** across 5 scenarios (with `-ffp-contract=off` for deterministic FP).

## Configuration (vit.yaml)

```yaml
project:
  name: ROSCO
  source_dir: rosco/controller/src    # Fortran source tree
  build_dir: rosco/controller/build   # Build output directory

kgen:
  path: /workspace/KGen/bin/kgen      # Absolute path to KGen entry point (requires Python 2.7)
  build_cmd: "cd /workspace/ROSCO/rosco/controller/build && cmake --build ."
  run_cmd: "cd /workspace/ROSCO/Examples && python3 vit_sim.py"
  clean_cmd: "cd /workspace/ROSCO/rosco/controller/build && make clean"
  install_cmd: "cp /workspace/ROSCO/rosco/controller/build/libdiscon.so /workspace/ROSCO/rosco/lib/libdiscon.so"
  invocation: "0:0:1-20"             # rank:thread:start-end — selects which KGen invocations to capture

build_system:
  type: cmake                         # Build system type (cmake or make)
  cmake_file: rosco/controller/CMakeLists.txt
  target_name: discon                 # CMake library target name

fortran:
  kind_aliases:                       # Map project-specific kinds to standard Fortran types
    ReKi: "REAL(8)"
    DbKi: "REAL(8)"
    SiKi: "REAL(4)"
    IntKi: "INTEGER(4)"
  compiler: gfortran
  flags: "-fdefault-real-8 -fdefault-double-8"  # Propagated to kernel Makefile

verification:
  tolerance: 1.0e-14                  # Maximum acceptable floating-point difference

translations:                         # Auto-populated by VIT after verify/integrate
  saturate:
    status: integrated
    cpp_file: saturate.cpp
```

**Key fields:**
- **`kgen.install_cmd`** — Copies the built library to where the simulation loads it from. Without this, extraction may capture 0 cases because the simulation runs the old (uninstrumented) library.
- **`kgen.invocation`** — Format is `rank:thread:start-end`. Selects which KGen invocations to capture state for. `0:0:1-20` means MPI rank 0, thread 0, invocations 1 through 20.
- **`fortran.kind_aliases`** — Maps project-specific type kinds (e.g., `ReKi`) to standard Fortran types (e.g., `REAL(8)`). Required for correct type mapping in interface generation.
- **`kgen.build_cmd` / `run_cmd`** — Use absolute paths. KGen may execute these from unpredictable working directories.

## CLI Reference

### Global flags

| Flag | Description |
|------|-------------|
| `--config`, `-c` | Path to vit.yaml (default: search up from cwd) |
| `--verbose`, `-v` | Print full stack traces on error |

### Commands

| Command | Usage | Description |
|---------|-------|-------------|
| `init` | `vit init [--name NAME] [--source-dir DIR] [--build-cmd CMD] [--run-cmd CMD]` | Create a starter vit.yaml in the current directory |
| `find` | `vit find <function> [--source-dir DIR]` | Search source tree for call sites of a function |
| `parse` | `vit parse <function> [-f FILE]` | Parse and display a Fortran function signature |
| `extract` | `vit extract <function> [-f FILE] [-l LINE] [--dry-run]` | Instrument code with KGen, run simulation, capture runtime state |
| `interface` | `vit interface <function> [-f FILE] [-o DIR]` | Generate Fortran↔C interface files (extern "C" + ISO_C_BINDING) |
| `verify` | `vit verify <function> <cpp_file> [-f FILE] [--kernel-dir DIR]` | Wire C++ into KGen kernel, build, and verify against captured state |
| `integrate` | `vit integrate <function> <cpp_file> [-f FILE] [--apply] [--dry-run]` | Generate (and optionally apply) codebase integration patches |
| `translate` | `vit translate <function> [-f FILE] [-o DIR]` | Generate C++ translation scaffold and LLM prompt |
| `run` | `vit run <function> <cpp_file> [-f FILE] [--kernel-dir DIR] [--apply] [--skip-verify]` | Full pipeline: parse → verify → integrate in one command |
| `analyze-types` | `vit analyze-types [--fix character] [--type TYPE] [--field FIELD] [--apply]` | Analyze derived type C-compatibility; fix CHARACTER(:) ALLOCATABLE fields |
| `status` | `vit status` | Show translation status for all functions tracked in vit.yaml |

**Flag details:**
- `-f` / `--file` — For `extract`: the file containing the **call site**. For all other commands: the file containing the function **definition**.
- `-l` / `--line` — Line number of the call site (used with `extract`).
- `--kernel-dir` — Path to KGen kernel/ directory. If omitted, VIT searches for `kernel/` in the current directory.
- `--apply` — Actually modify files on disk (without this flag, `integrate` and `run` only print the plan).
- `--dry-run` — Show what would happen without doing it (`extract`, `integrate`).
- `--skip-verify` — Skip verification step in `run` (use when no kernel/ exists yet).
- `-o` / `--output-dir` — Output directory for generated interface files (default: `.`).

## Key Design Decisions

**Interface generation is deterministic.** Given a Fortran function signature, the `extern "C"` wrapper and `ISO_C_BINDING` interface are fully determined by the type mapping.

**Translation is external.** VIT doesn't care how you produce the C++. This makes it composable with any translation tool.

**NaN-safe verification.** KGen captures state for all invocations of the target function, including initialization calls where struct fields contain uninitialized heap residue (NaN values). VIT patches verification comparisons so that NaN==NaN is treated as identical, and reports these as "skipped" cases rather than failures. This prevents false negatives from KGen's faithful capture of uninitialized memory.

## Architecture

```
vit/
  cli.py              # CLI with subcommands
  config.py           # YAML project configuration
  type_map.py         # Deterministic Fortran↔C type mapping
  fortran_parser.py   # Parse function signatures from source
  interface_gen.py    # Generate extern "C" + ISO_C_BINDING interfaces
  extract.py          # KGen extraction orchestration
  kernel_verify.py    # Wire C++ into kernel, build, verify
  integrate.py        # Generate codebase integration patches
  translate.py        # Generate translation scaffolds + prompts
  confirm.py          # Assumption confirmation protocol (exit code 10)
  analyze_types.py    # Derived type C-compatibility analysis + fixes
  derived_type_parser.py  # Parse TYPE definitions, generate C struct headers
  build_fixer.py      # Auto-fix compiler errors during type fixes
```

## Requirements

- Python ≥ 3.10 (for VIT)
- Python 2.7 (for KGen — its Fortran parser requires it)
- PyYAML
- KGen (for extraction and verification)
- gfortran + g++ (for kernel builds)
- CMake (for project integration, if applicable)
- **Docker or Linux container recommended** — KGen's Fortran parser does not work on macOS. Run all VIT commands inside a container.

## Troubleshooting

**Extraction returns 0 cases.**
The simulation ran but used the old (uninstrumented) library. Set `kgen.install_cmd` in vit.yaml to copy the built library to where the simulation loads it. For ROSCO: `cp .../build/libdiscon.so .../lib/libdiscon.so`.

**Kernel build fails with unknown type errors.**
The KGen kernel uses project-specific type kinds (e.g., `ReKi`) that aren't standard Fortran. Add all custom kinds to `fortran.kind_aliases` in vit.yaml.

**Wrong file passed to `-f`.**
`vit extract` needs the **call site** file (where the function is called), not the definition file. All other commands (`parse`, `verify`, `integrate`) need the **definition** file. Use `vit find <function>` to locate call sites.

## What VIT Is NOT

- **Not a translator.** Use CodeScribe, Claude, GPT, or hand-coding.
- **Not a full Fortran parser.** It parses signatures, not arbitrary Fortran.
- **Not a replacement for testing.** KGen verification checks numerical equivalence for captured cases. Integration testing is still your responsibility.

## Links

- [ROSCO Walkthrough](guide_ROSCO.md) — Full end-to-end tutorial using the ROSCO wind turbine controller
- [KGen](https://github.com/NCAR/KGen) — NCAR's Fortran kernel generator (used by VIT for extraction and state capture)
