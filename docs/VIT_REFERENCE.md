# VIT Reference

Configuration, commands, exit codes and known limitations. `vit <command> --help` is authoritative
for options; this document says what they are for.

## Configuration (`vit.yaml`)

Each project has a `vit.yaml` at its root. `vit init` writes a starter. VIT rewrites the file when
it records a result (after `verify` and `integrate`), so comments in it do not survive.

Relative paths are resolved against the directory holding `vit.yaml`. Commands in `kgen.*` run
through a shell, from directories KGen chooses, so give them absolute paths.

| Field | Default | Meaning |
|---|---|---|
| `project.name` | `my_project` | A label. |
| `project.source_dir` | `src` | Where VIT looks for Fortran sources and derived-type definitions. It can be broader than the code being translated, so that KGen finds the modules it USEs. |
| `project.build_dir` | `build` | The CMake build directory. It may be absolute, for example inside a container. |
| `kgen.path` | `kgen` | The KGen entry point. The container image puts the KGen fork on `PATH`. |
| `kgen.build_cmd` | | Rebuilds the program, run with KGen's instrumentation in place. |
| `kgen.run_cmd` | | Runs the program so that it reaches the call site being extracted. `vit extract --run-args` appends to it. |
| `kgen.clean_cmd` | | Cleans the build. |
| `kgen.install_cmd` | | Copies the built library to where the program loads it, if not from the build directory. Without it, the run can load the uninstrumented library and capture nothing. |
| `kgen.invocation` | `0:0:1-20` | Which calls to capture: process, thread, call range. The default captures the first 20 calls, which can be dominated by start-up behaviour (dev note `202608080640`). |
| `kgen.assumed_size_arrays` | | `name: size` for assumed-size (`*`) array arguments. Without an entry, VIT infers the size and asks for confirmation (exit code 10). The size must cover every index the function touches; too small corrupts the heap silently. |
| `kgen.dll_persistence` | | Reset statements for SAVE guards, applied between runs in one process, for a program that reloads a shared library whose SAVE state survives. |
| `kgen.add_source_cmd` | | A command that adds `src/kgen_utils.f90` to the build file, for a build file VIT cannot patch (see "Build layout" below). |
| `build_system.type` | `cmake` | `vit integrate` patches CMake builds only; with any other value it changes the sources but leaves the build file to you. |
| `build_system.cmake_file` | `CMakeLists.txt` | The CMake file that lists the sources. |
| `build_system.target_name` | (empty) | Fallback only. `vit integrate` finds the `add_library` target that lists the source file, and stops with an error asking for this field if it cannot. |
| `fortran.kind_aliases` | (none) | The project's kind parameters, as `ReKi: REAL(8)`. VIT assumes none. |
| `fortran.compiler`, `fortran.cxx_compiler` | `gfortran`, `g++` | Compilers for kernels. |
| `fortran.runtime_libs` | (from the compiler) | Fortran runtime libraries to link. |
| `fortran.flags` | `-ffp-contract=off` from `vit init` | Flags for compiling kernels. Keep `-ffp-contract=off`, and add what the project's build uses. |
| `fortran.preprocessor_defines` | | Macros passed to KGen as `-D`, to resolve `#ifdef`. |
| `verification.tolerance` | `1.0e-14` | The difference above which a kernel output counts as out of tolerance. |
| `types` | | Per derived type, `strategy: bind_c` (the default) or `strategy: view`. `vit translate` sets `view` itself for a type with ALLOCATABLE fields. |
| `allocatable.helpers` | | Routines that allocate arrays, as `name`, `array_arg` and `size_arg` positions, for `vit analyze-types --scan-allocatable`. |
| `shared_files` | | Shared headers and sources. VIT copies them into each kernel, and beside the integrated source after `vit integrate --apply`; `vit translate` lists the headers' declarations in its prompt. |
| `translations` | | Written by VIT: each function's status, case counts and red-test result. |

## Commands

**Global options** (before the command): `--config PATH` / `-c PATH` uses another `vit.yaml`;
`--verbose` / `-v` prints stack traces on error.

| Command | Purpose | Notable options |
|---|---|---|
| `vit init` | Write a starter `vit.yaml` | `--name`, `--source-dir`, `--build-cmd`, `--run-cmd` |
| `vit find` | Find a function's call sites | `--source-dir` |
| `vit parse` | Print a function's parsed signature | `-f FILE` |
| `vit extract` | Capture a function's calls with KGen | `-f FILE -l LINE` (the call site), `--run-args`, `--assumed-size NAME=SIZE`, `--dry-run` |
| `vit translate` | Write the C++ scaffold and print the prompt | `-f FILE` (the definition), `--module`, `-o DIR` |
| `vit check` | Run the defect checks on a translation | `-f FILE` (enables the checks that read the Fortran), `--list`, `--function` |
| `vit verify` | Verify a translation against its kernel | `-f FILE`, `--kernel-dir DIR`, `--no-field-log` |
| `vit integrate` | Plan, or with `--apply` make, the integration | `-f FILE`, `--apply`, `--dry-run`, `--reverse-copy`, `--copy-arrays ARG,...`, `--auto-allocate` |
| `vit run` | Interface, verify and integrate in one step | see `--help` |
| `vit interface` | Generate the Fortran↔C interface files on their own | `-f FILE`, `-o DIR` |
| `vit analyze-types` | Report which derived types can cross to C; apply fixes | `--fix {character,logical,complex,bind_c}`, `--type`, `--field`, `--length`, `--rewrite`, `--apply`, `--scan-allocatable` |
| `vit test-validate` | Generate a test harness for branches no run reaches | `-f FILE`, `--module`, `--run`, `--force` |
| `vit sim-compare` | Compare simulation outputs saved as `.npz` arrays | `--tolerance` |
| `vit reachability` | Settle whether a procedure is called, by removing it and rebuilding | `-f FILE` |
| `vit status` | Show the recorded translation status | |

`-f` names the file with the function's **definition**, except for `vit extract`, where it names the
file with the **call site**.

## Exit codes

| Code | Meaning | Written to disk |
|---|---|---|
| 0 | Success | the changes made |
| 1 | Failure. Among others: `vit extract` captured no state; `vit verify` found a mismatch, or the pass was `NON_DISCRIMINATING` (below); `vit check` reported findings | nothing beyond diagnostics |
| 2 | `vit check`: the translation or the Fortran file does not exist | nothing |
| 10 | Confirmation needed | nothing |
| 11 | Manual rewrite needed | nothing |

### Exit code 10: confirmation

VIT inferred a value from evidence in the source but cannot be certain, and prints a
`[VIT:CONFIRM]` block saying what it inferred, from where, and the value it would use. Examples: the
length of a CHARACTER field (`vit analyze-types --fix character`), or the size of an assumed-size
array (`vit extract`).

The rule VIT follows: with evidence, infer and ask (10); without evidence, fail (1); a technical
default with no effect on results may be hard-coded.

**The agent's part:** show the person what was inferred and why, wait for a confirmed or corrected
value, and re-run with it given explicitly (`--length 1024`, `--assumed-size avrSWAP=3000`). An agent
must never confirm on the person's behalf.

### Exit code 11: manual rewrite

VIT can rewrite most references automatically but found code it cannot rewrite safely, for example
an expression using a complex intrinsic while splitting a COMPLEX field, and prints a `[VIT:MANUAL]`
block with each location.

**The agent's part:** read each flagged line in context, propose a rewrite, wait for approval, and
re-run with `--rewrite 'file:line=replacement'`. An agent must never approve a rewrite on the
person's behalf.

## Known limitations

### Verification

- **A kernel samples calls.** By default it captures a function's first 20 calls
  (`kgen.invocation`). Input-dependent defects can pass a kernel and show up only in a full run, so
  verify, then integrate and compare full runs.
- **A pass must be able to fail.** After a kernel passes, `vit verify` changes an input and expects
  a mismatch. When every input arrives behind a pointer or inside a derived type, no safe
  perturbation exists; the pass is reported as `NON_DISCRIMINATING` and exits 1. That says the
  kernel's agreement could not be shown to mean anything, not that the translation is wrong.
- **NaN-safe comparison.** Captured state can include uninitialized memory. VIT treats NaN equal to
  NaN in kernel comparisons, and reports cases whose reference data is all NaN as skipped.
- **Vectorized math.** A KGen-reduced module can be vectorized differently from the full build,
  changing `exp`, `sin` and `cos` by an ULP. Kernels are compiled with
  `-fvect-cost-model=unlimited` and linked with `-lmvec -lm` to match.

### Build layout

- **Sources listed one per line, with `src/` beside the CMake file.** During extraction VIT adds
  `src/kgen_utils.f90` after the first line of the build file that is a single Fortran source path,
  and copies the file into that `src/`. When no line matches, extraction stops with an error naming
  `kgen.add_source_cmd`.
- **A configured build directory.** Extraction re-runs CMake inside the existing build directory.
- **macOS host mounts.** On Colima's virtiofs mount, CMake Fortran rebuilds can fail when gfortran
  rewrites a module file. Keep the CMake build directory inside the container (`docker/README.md`).

### Derived types

- **Two strategies, per type.** Types with only scalar and fixed-size fields cross as `BIND(C)`
  structs; `vit analyze-types` reports blockers and applies fixes (`--fix character`, `logical`,
  `complex`, `bind_c`). Types with ALLOCATABLE fields cross as *view* structs: a pointer and extents
  per ALLOCATABLE field, filled at run time by a generated Fortran populator using
  `C_LOC(field(LBOUND(...)))` and `SIZE`.
- **Arrays of derived types.** For an ALLOCATABLE array of a view type (`p%Table(:)`), the populator
  keeps a module-level array of child views, so C++ can index `p->Table[i].field`.
- **C++ that allocates.** When the C++ ALLOCATEs a component of a view argument, as every
  `INTENT(OUT)` initializer must, it allocates with `vit_adopt_alloc` or `vit_adopt_alloc_lb`, and
  the generated wrapper adopts the allocation into the Fortran component. Limits: one level of
  ALLOCATABLE `TYPE()` nesting; ALLOCATABLE CHARACTER arrays and rank above 3 are refused if replaced;
  lower bounds are only as right as the translation states them (dev note `202609242010`).
- **Type discovery is recursive.** VIT finds TYPE definitions under `project.source_dir`, then the
  types they contain.
- **USE matching is by name in the statement text.** A type is found through `USE Module, ONLY:
  TypeName`, not through a bare `USE Module`.

### Interfaces

- **One size argument per assumed-shape array.** Each assumed-shape argument gets its own extent
  parameters, even when the arrays are the same length.
- **Calls between translations.** A Fortran caller reaches the C++ through the Fortran wrapper and
  its `_c` function; a C++ caller calls the `_c` function directly. `vit integrate --apply` keeps the
  shared `vit_translated.h` of `extern "C"` declarations, and `vit translate` puts already-translated
  callees' signatures in the prompt.
- **Callee bridges in kernels.** When the function being verified calls an already-translated
  function, VIT generates a `BIND(C)` bridge for it in the kernel (`vit_kernel_callees.h`), makes
  callees hidden by a `PRIVATE` default visible, and passes view arguments through a module-level
  variable.

### Extraction

- **The call site must run.** KGen captures nothing from a call site the run never reaches.
- **Extract at a call site, not a definition.** With the pragma on a module procedure's definition,
  KGen generates invalid code and captures nothing (dev note `202605010623`).
- **Calls are counted per enclosing procedure.** Earlier work in the run can use up the capture
  range before the behaviour you want.
- **Project flags.** KGen's kernel Makefiles do not inherit the project's flags; `fortran.flags`
  supplies them.
- **Cross-module instrumentation.** KGen can instrument files outside the CMake target; VIT moves
  the generated routines into the target (`docs/EXTRACTION_INTERNALS.md`, section 6, F2).
- **Kernel repair.** When a kernel fails to build, VIT repairs missing constants, undefined symbols
  and link errors in a loop before giving up.
- **Fixes in the KGen fork.** Some KGen failures are fixed in the fork VIT depends on, for example
  PROCEDURE POINTER parsing (`31d653c`).
