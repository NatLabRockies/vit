# VIT — Verified Incremental Translator

VIT moves a Fortran code base to C++ one function at a time, and checks each step against the
original program's own behaviour. For each function it captures the inputs and outputs of real
calls during a run of the program, checks a C++ translation against them, and patches the verified
C++ into the build in place of the Fortran body. The program keeps running, mixed Fortran and C++,
after every step.

VIT does not write the translations. You do, or a coding agent does; VIT generates the scaffold and
the prompt, verifies the result, and integrates it. It is designed to be driven by a coding agent,
with a person making the judgment calls.

## How it works

```
  extract ──▶ translate ──▶ check ──▶ verify ──▶ integrate ──▶ rebuild and compare
  (KGen)      (scaffold     (known    (kernel)   (patches)     (the project's
               + prompt)     defects)                            own run)
```

1. **Extract** (`vit extract`): KGen instruments the source at a call site, and VIT rebuilds and runs
   the program, capturing the function's inputs and outputs for a set of calls. The result is a
   standalone kernel that replays them.
2. **Translate** (`vit translate`): VIT writes a C++ scaffold with the exact signature, and prints a
   prompt with the Fortran source, the signatures of already-translated callees, the shared functions
   available to call, and guidance generated from its defect checks.
3. **Check** (`vit check`): looks for defect shapes known to change results, such as `a*x**2`
   translated as `a*x*x`, which can round differently.
4. **Verify** (`vit verify`): builds the kernel with the C++ in place of the Fortran and compares
   every output of every captured call. It then perturbs an input and confirms the kernel reports
   a mismatch: a kernel that cannot fail proves nothing, and VIT says so.
5. **Integrate** (`vit integrate --apply`): generates the C interface, replaces the Fortran body with
   a call to the C++, and adds the C++ to the build.
6. **Rebuild and compare**: run the program and compare its output with a baseline from the
   original Fortran. The kernel checks a sample of calls; the full run checks all of them.

The kernel is fast and pinpoints which output differs; the full run catches what the sampled calls
did not exercise. For code a kernel cannot capture well, VIT has other methods: generated test
harnesses for branches no run reaches (`vit test-validate`), and simulation-level comparison of
saved outputs (`vit sim-compare`).

## Getting started

[`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) builds VIT's container image, runs the whole cycle on a
small bundled example ([`examples/quickstart`](examples/quickstart)), and describes what your own
project needs. Extraction runs KGen, which needs Linux and Python 2.7, so it runs in the container
described in [`docker/README.md`](docker/README.md).

## Working with a coding agent

VIT's commands print what an agent needs to act on, and stop with a specific exit code when a person
must decide:
- **10:** VIT inferred a value, such as a buffer size, and needs it confirmed.
- **11:** VIT found code it cannot safely rewrite, and needs a rewrite approved.

In both cases nothing is written until the decision is made. An agent should never make either
decision itself. [`docs/VIT_REFERENCE.md`](docs/VIT_REFERENCE.md) describes both protocols.

## Commands

| Command | What it does |
|---|---|
| `vit init` | Create a starter `vit.yaml` |
| `vit find` | Find the call sites of a function |
| `vit parse` | Print a function's parsed signature |
| `vit extract` | Capture a function's inputs and outputs with KGen |
| `vit translate` | Write the C++ scaffold and print the translation prompt |
| `vit check` | Run the defect checks on a translation (`--list` shows them) |
| `vit verify` | Verify a translation against its kernel |
| `vit integrate` | Plan, or with `--apply` make, the changes that integrate a translation |
| `vit run` | Verify and integrate in one step |
| `vit interface` | Generate the Fortran↔C interface files on their own |
| `vit analyze-types` | Report which derived types can cross to C, and apply fixes |
| `vit test-validate` | Generate a test harness for branches no run reaches |
| `vit sim-compare` | Compare simulation outputs saved as `.npz` arrays |
| `vit reachability` | Settle whether a procedure is called, by removing it and rebuilding |
| `vit status` | Show the translation status recorded in `vit.yaml` |

`vit <command> --help` lists each command's options. [`docs/VIT_REFERENCE.md`](docs/VIT_REFERENCE.md)
covers the configuration file and the known limitations.

## Case studies

VIT has been developed on two Fortran code bases, recorded in
[`docs/case-studies/`](docs/case-studies/README.md):
- **ROSCO**, a wind turbine controller (complete, April 2026): all 52 functions and the controller's
  entry point translated to a pure-C++ library. The plan, as a historical record, is
  [`PROJECT_PLAN.md`](docs/case-studies/rosco/PROJECT_PLAN.md); it includes what was found after it
  was written.
- **AeroDyn**, the aerodynamics module of OpenFAST (active): 32 functions in its AirfoilInfo and
  DBEMT sub-modules are integrated. The plan is
  [`PROJECT_PLAN.md`](docs/case-studies/aerodyn/PROJECT_PLAN.md), and the operating manual an agent
  works from is [`AGENT_MANUAL.md`](docs/case-studies/aerodyn/AGENT_MANUAL.md).

## Documentation

| Document | What it covers |
|---|---|
| [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) | Install, the full cycle on an example, setting up your own project |
| [`docs/VIT_REFERENCE.md`](docs/VIT_REFERENCE.md) | Configuration, commands, exit codes, known limitations |
| [`docs/VERIFICATION_METHODS.md`](docs/VERIFICATION_METHODS.md) | The verification methods and when to use each |
| [`docs/EXTRACTION_INTERNALS.md`](docs/EXTRACTION_INTERNALS.md) | What extraction does step by step, and how to debug it |
| [`docs/CREATING_PROJECT_PLANS.md`](docs/CREATING_PROJECT_PLANS.md) | How to plan the translation of a whole code base |
| [`docs/case-studies/`](docs/case-studies/README.md) | ROSCO and AeroDyn: plans, the AeroDyn agent manual, inventory and coverage data |
| [`docker/README.md`](docker/README.md) | The container image |
| [`dev/`](dev/README.md) | Dated development notes: the decisions, investigations and corrections behind the code |

## Requirements

- **Docker** to run extraction and verification. The image provides gfortran, g++, CMake, strace,
  Python 2.7 and KGen; it is tested on ARM64.
- **Python 3.10 or later** for VIT itself.
- **A Fortran project built with CMake and gfortran**, compiled with `-ffp-contract=off`, with a
  program or test that calls the functions to translate. `docs/GETTING_STARTED.md` lists the details.

## Developing VIT

```bash
conda env create -f environment.yml
conda activate vit
python -m pytest tests/
```

The tests that compile generated Fortran and C++ need gfortran, gcc and g++ on `PATH`. On macOS with
Homebrew's compilers, also `export SDKROOT=$(xcrun --show-sdk-path)`. The suite does not need KGen.

## License

BSD 3-Clause; see [`LICENSE`](LICENSE).

VIT uses [KGen](https://github.com/kevinmenear/KGen), a fork of NCAR's
[KGen](https://github.com/NCAR/KGen) carrying the fixes VIT depends on. KGen is a separate project
with its own license.
