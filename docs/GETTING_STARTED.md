# Getting Started with VIT

VIT (Verified Incremental Translator) moves a Fortran code base to C++ one function at a time, and
checks each step against the original. For each function it:

1. **extracts** the function's real inputs and outputs from a run of your program, using KGen
2. **translates**: generates a C++ scaffold and a translation prompt
3. **verifies** a C++ translation against the captured calls, in a standalone kernel
4. **integrates** the verified C++ into your build, replacing the Fortran body

VIT does not write the translation. You do, or a coding agent does.

This guide builds VIT's container image, runs the whole cycle on a small bundled example, and then
covers what your own project needs.

## Requirements

- **Docker**: Docker Desktop, Colima or Docker Engine. Extraction runs KGen, which needs Linux and
  Python 2.7; the image provides both, along with gfortran, g++ and CMake. This guide was tested
  with Colima on Apple Silicon.
- **git**, to get VIT.

## 1. Build the image

```bash
git clone https://github.com/NatLabRockies/vit.git
cd vit
docker build -f docker/Dockerfile -t vit .
```

The first build compiles Python 2.7 and takes several minutes. [`docker/README.md`](../docker/README.md)
describes the image and its build options.

## 2. Run the cycle on the example

[`examples/quickstart`](../examples/quickstart) is a projectile slowed by air drag, in about 40 lines of
Fortran:
- `src/physics.f90` holds `drag`, the function to translate, and `step`, which calls it
- `app/main.f90` runs 500 steps and writes speed and distance to `trajectory.txt` at full precision
- `CMakeLists.txt` builds `src/` as a library, `physics`, and the program, `simulate`, with
  `-ffp-contract=off` for both Fortran and C++
- `vit.yaml` is VIT's configuration for the project; section 3 explains its fields

Copy it somewhere outside the VIT checkout, then start a container with the copy mounted at
`/workspace`, and open a shell in it:

```bash
cp -r examples/quickstart ~/vit-quickstart
cd ~/vit-quickstart
docker run -d --name vit-quickstart -v "$PWD":/workspace vit sleep infinity
docker exec -it vit-quickstart bash
```

Every command from here to the end of section 2 runs in that shell, in `/workspace`. The files stay
on your machine, so you can read and edit them with your own tools.

### Build, and record a baseline

```bash
cmake -S . -B /build/quickstart
cmake --build /build/quickstart
(cd /build/quickstart && ./simulate)
cp /build/quickstart/trajectory.txt baseline.txt
```

`baseline.txt` is the pure-Fortran result. The rebuilt program is compared with it at the end.

The build directory is inside the container, not on the mount. On macOS, a CMake rebuild on a
host-mounted directory can fail when gfortran rewrites a Fortran module file (`Error copying Fortran
module`), and extraction rewrites one. The build directory is lost if the container is removed;
rerun the two `cmake` commands to recreate it.

### Find a call site

```bash
vit find drag
```

This lists the call in `step` (line 17), not the definition (line 8): extraction needs a call site
inside a procedure, never the definition.

### Extract

```bash
vit extract drag -f src/physics.f90 -l 17
```

VIT marks the call site for KGen. KGen instruments the source, and VIT rebuilds and runs the
program, which captures the inputs and outputs of the first 20 calls to `drag`. The result is
`kernel/drag/`: the captured calls (`drag.0.0.1` to `drag.0.0.20`) and a standalone kernel that
replays them. The source is restored afterwards. Extraction also leaves `kgen.log`, `strace.log`
and `include.ini` in the project.

Expect `Cases:  20`. If extraction reports `✗ Extraction captured no state data`, it exits non-zero
and lists the errors it found; [`EXTRACTION_INTERNALS.md`](EXTRACTION_INTERNALS.md)
section 7 is the debugging checklist.

### Translate

```bash
vit translate drag -f src/physics.f90
```

This writes a scaffold, `translations/physics/drag.cpp`, and prints a translation prompt: the
Fortran source, the C++ signature, and guidance generated from VIT's defect checks. Give the prompt
to a coding agent, or write the translation yourself. Replace the scaffold's body:

```cpp
double drag(double v, double cd, double area) {
    return 0.5 * 1.225 * cd * area * (v * v);
}
```

Running `vit translate` again refreshes the scaffold only while it still holds the `TODO`
placeholder. Once you have written an implementation, VIT keeps it.

### Check

```bash
vit check translations/physics/drag.cpp -f src/physics.f90
```

`vit check` looks for defect shapes known to change results. `vit check --list` shows them all.
Try it with `v * v` in place of `(v * v)`: the `exponent-grouping` check reports that Fortran's
`a*v**2` means `a*(v*v)`, while C++ evaluates `a*v*v` as `(a*v)*v`, which can round differently.
Here that version also passes verification and the full-run comparison: for this example's inputs,
the two groupings happen to round the same. The check catches what testing may not reach.

### Verify

```bash
vit verify drag translations/physics/drag.cpp -f src/physics.f90 --kernel-dir kernel/drag
```

VIT builds the kernel with your C++ in place of the Fortran and compares every output of the 20
captured calls. Expect `✓ VERIFICATION PASSED: 20/20 passed`. It then runs a red test: it perturbs
an input and confirms the kernel reports a mismatch. A kernel that cannot fail proves nothing, so a
pass without a working red test is reported as `NON_DISCRIMINATING` and exits non-zero.

### Integrate

```bash
vit integrate drag translations/physics/drag.cpp -f src/physics.f90 --apply
```

This creates `src/drag.cpp` (your implementation, plus an `extern "C"` wrapper, `drag_c`), replaces
the body of the Fortran `drag` with a call to `drag_c` through a `BIND(C)` interface, and adds the
C++ source to the `physics` target in `CMakeLists.txt`. Without `--apply`, it prints the plan and
changes nothing.

### Rebuild and compare

```bash
cmake --build /build/quickstart
(cd /build/quickstart && ./simulate)
cmp /build/quickstart/trajectory.txt baseline.txt && echo IDENTICAL
vit status
```

The program now runs your C++, and all 500 steps must match the baseline byte for byte. The kernel
checked 20 calls in isolation; the full run checks every call, and everything computed from it.
`vit status` reports `drag: integrated (verified 20/20 cases)`.

To start over, remove `~/vit-quickstart` and copy the example again. To stop the container:
`docker rm -f vit-quickstart`.

## 3. Your own project

### What VIT needs

- **A CMake build compiled with gfortran**, with `-ffp-contract=off` for both Fortran and C++, as in
  the example's `CMakeLists.txt`. Verification is bit for bit, and fused multiply-add contraction
  changes results. `vit integrate` enables C++ in the build but does not set its flags.
- **The Fortran source listed in an `add_library` target, one file per line, with a `src/`
  directory beside `CMakeLists.txt`.** During extraction VIT copies KGen's support file into `src/`
  and adds it to the source list. For a layout it cannot patch, set `kgen.add_source_cmd` to a
  command that adds `src/kgen_utils.f90` to the build file.
- **A command that runs the program and reaches the call site**, such as a simulation or a test.
- **A build directory that CMake has already configured.**
- **A git branch to work on.** Extraction and integration edit your sources, `CMakeLists.txt` and
  `vit.yaml`, and `git diff` shows exactly what changed.

### Configure

Start the container with your project mounted, then run `vit init` from the project root:

```bash
vit init --name myproject --source-dir src \
    --build-cmd "cd /build/myproject && cmake --build ." \
    --run-cmd "cd /build/myproject && ./my_program"
```

It writes `vit.yaml`. Then edit it:

| Field | What to set |
|---|---|
| `project.source_dir` | The directory holding the Fortran sources. |
| `project.build_dir` | The CMake build directory. Use an absolute path inside the container, such as `/build/myproject`. |
| `kgen.build_cmd`, `kgen.run_cmd` | Commands that rebuild and run the program. Use absolute container paths. |
| `kgen.install_cmd` | A command that copies the built library to where the program loads it from, if it is not loaded from the build directory. |
| `kgen.invocation` | Which calls to capture. `0:0:1-20` is process 0, thread 0, calls 1 to 20. |
| `build_system.cmake_file` | The `CMakeLists.txt` that lists the sources, relative to the project root. |
| `build_system.target_name` | Leave empty. `vit integrate` finds the `add_library` target that lists the source file, and stops with an error asking for this field if it cannot. |
| `fortran.kind_aliases` | The project's kind parameters, such as `ReKi: REAL(8)`. VIT needs these to map types, and assumes none. |
| `fortran.flags` | Flags for compiling kernels. Keep `-ffp-contract=off`, and add what the project's build uses, such as `-fdefault-real-8`. |

[`VIT_REFERENCE.md`](VIT_REFERENCE.md) lists every field and command.

### Going further

- **Derived types:** `vit analyze-types` reports which types can cross to C++ unchanged. For a type
  with ALLOCATABLE fields, `vit translate` sets up a view struct itself, and records it in
  `vit.yaml`.
- **Branches no run reaches:** a kernel replays the calls a run made, which for an input
  validator is usually only the happy path. `vit test-validate` generates a prompt and a harness
  skeleton for tests of the error branches, to be filled in by an agent or by you.
  `vit sim-compare` compares simulation outputs saved as `.npz` arrays.
  [`VERIFICATION_METHODS.md`](VERIFICATION_METHODS.md) covers when to use which.
- **Choosing a test case to extract with:** with several test cases, line coverage per case says
  which one runs a call site, and how often. Build with `--coverage`, run each case, and write
  `gcov -j` output (gcc 9 or later) into one directory per case. Then
  `vit coverage import --gcov-dir DIR --source-root . --output coverage.json`, and
  `vit find FUNC --coverage coverage.json` lists each call site with the cases that run it.
- **Planning a whole code base:** [`CREATING_PROJECT_PLANS.md`](CREATING_PROJECT_PLANS.md)
  describes how to inventory a code base and order the work.

## 4. Working with a coding agent

VIT is designed to be driven by a coding agent that runs its commands and reads their output, with
you making the judgment calls. When VIT needs one, it stops with a specific exit code:

- **10, confirmation needed:** VIT inferred a value, such as a buffer size, and prints it with its
  source in a `[VIT:CONFIRM]` block. Nothing is written. The agent must show you the value and
  re-run with the one you confirm.
- **11, manual rewrite needed:** VIT found code it cannot safely rewrite and lists it in a
  `[VIT:MANUAL]` block. Nothing is written. The agent proposes a rewrite for you to approve.

An agent should never confirm either on your behalf. [`VIT_REFERENCE.md`](VIT_REFERENCE.md)
describes both protocols.

Give the agent an operating manual for your project: its workspace, the per-function workflow, the
test cases to extract with, and the rules it must follow. The AeroDyn case study's
[`AGENT_MANUAL.md`](case-studies/aerodyn/AGENT_MANUAL.md) is a full example.

## Where next

- [`case-studies/`](case-studies/README.md): the two code bases VIT has been used on, ROSCO and
  AeroDyn, with their plans
- [`EXTRACTION_INTERNALS.md`](EXTRACTION_INTERNALS.md): what extraction does, step by
  step, and how it fails
