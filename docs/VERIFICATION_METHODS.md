# Verification Methods

How VIT establishes that a C++ translation matches the Fortran it replaces, and which method fits
which kind of function. The commands are in [`VIT_REFERENCE.md`](VIT_REFERENCE.md); the case
studies show each method used on real code.

## Two layers, both needed

- **Function level.** A kernel or a test harness compares the function's outputs, for specific
  inputs, with the Fortran's. It is fast and says exactly which output differs. But it covers only
  the inputs it has: by default a kernel holds the function's first 20 calls.
- **Program level.** After integration, the whole program runs and its outputs are compared with a
  baseline produced by the original Fortran. This covers every call, and catches differences too
  small for a kernel to notice that feedback loops amplify over many time steps.

A function-level pass with a program-level failure is a translation defect until shown otherwise.
Look at the translation first, not the compiler or the tooling.

Before either, `vit check` looks for defect shapes known to change results, some of which no sampled
input happens to expose.

## Choosing a method

| The function | Function-level method |
|---|---|
| Does arithmetic, and a run reaches it | KGen kernel: `vit extract`, then `vit verify` |
| Checks its inputs and reports errors (a validator) | `vit test-validate` |
| Writes files | File-output comparison, with a hand-written harness |
| Is reached by no run, and is not a validator | A hand-written dual-call harness |
| Was rewritten as idiomatic C++, not translated line by line (for example file I/O) | None: program-level comparison only |

Every integrated function also passes the program-level comparison. To decide whether a function no
run reaches is called at all, `vit reachability` removes it and rebuilds.

## 1. KGen kernel

`vit extract` captures the function's inputs and outputs at a call site during a real run. `vit
verify` builds the resulting kernel with the C++ in place of the Fortran, replays the captured calls,
and compares every output field.

- **Results per output:** `IDENTICAL`, `IN_TOL` (within `verification.tolerance`) or out of
  tolerance. With fused multiply-add disabled (below), anything but `IDENTICAL` usually means a
  translation defect. A per-field log is written unless `--no-field-log` is given.
- **The red test.** After a pass, VIT changes one input slightly and expects the kernel to report a
  mismatch. If it does not, the pass proves nothing. When every input arrives behind a pointer or
  inside a derived type, VIT cannot construct a safe change; the result is `NON_DISCRIMINATING` and
  the command exits 1. Such a function then rests on the other layer.
- **Uninitialized memory.** Captured state can contain NaN from uninitialized fields. VIT treats NaN
  as equal to NaN, and reports cases whose reference data is all NaN as skipped.

## 2. Test-validate (validators)

A kernel replays the calls a run made, which for an input validator is usually only the happy path.
`vit test-validate` generates what is needed to test the error branches:

- a test skeleton, `<name>_test.cpp`, for an agent or a person to fill in with one test per branch
- a Fortran `BIND(C)` bridge, `<name>_bridge.f90`, that calls the original Fortran. A derived type
  with ALLOCATABLE fields is passed field by field and rebuilt as a real Fortran type in the bridge.
- a copy of the Fortran source with the function made `PUBLIC`, if it was `PRIVATE`
- a Makefile that links against the project's built libraries

They are written to `translations/<Module>/<name>_test/`, and the command prints a prompt for writing
the tests. Each test calls both implementations with the same input and asserts that they report the
same error status, and that it is the expected one. `--run` builds and runs the tests. Running the
command again keeps a test file that has been filled in (no `// --- TODO:` markers left), unless
`--force` is given; the other files are regenerated.

## 3. File-output comparison (functions that write files)

Neither a kernel nor test-validate sees what a function writes to a file. Write a harness that builds
the same input for both implementations, calls each so it writes its own file, and compares the
files. VIT has no command for this.

What usually needs care:
- **Format descriptors.** Fortran `WRITE` formats (`F20.6`, `I17`, `ES17.8`) must be reproduced
  exactly by the C++ format strings.
- **Timestamps.** Dates and times differ between runs; skip those lines in the comparison, or fix
  the time.
- **I/O helpers.** The Fortran may write through the code base's own I/O routines, which the C++
  replaces with the standard library.

## 4. Dual-call harness (functions no run reaches)

For a function no test case calls, typically on an initialization or linearization path, a kernel
captures nothing and the program-level comparison passes trivially. Write a Fortran program that:
1. builds real Fortran inputs, including derived types with ALLOCATABLE fields
2. makes a deep copy for the C++ call, with the code base's own copy routines
3. calls the original Fortran on one copy, and the C++ through its views and `_c` entry point on the
   other
4. compares the results field by field

Compare LOGICALs with `.NEQV.`, not `/=`. The view structs cannot be turned back into Fortran types
with `C_F_POINTER` when they have ALLOCATABLE fields, which is why the program owns real Fortran types
and copies them.

## 5. Program-level comparison

Run the program's test cases and compare their outputs with baselines from the original Fortran,
after every integration. Generate baselines from the clean Fortran build, never from an integrated
one. For outputs saved as `.npz` arrays, `vit sim-compare <baseline_dir> <integrated_dir>` compares
them exactly by default (`--tolerance` sets an absolute tolerance).

## Floating point

**Fused multiply-add.** Compile both the Fortran and the C++ with `-ffp-contract=off` while
translating. Otherwise each compiler fuses different multiply-adds, producing one-ULP differences
that closed-loop code amplifies. The flag is for verification; once a code base is entirely C++,
fused multiply-add can be enabled again.

**Kernel failures that do not depend on the translation.** If the same cases fail with the same
differences whether the kernel runs the C++ or the original Fortran (restore the `.kgen` copy of the
Fortran), the problem is the kernel's build, not the translation. The usual cause is vectorized
math: the production build calls SIMD `sin`, `cos` or `exp` from libmvec, while the reduced kernel
falls back to scalar libm, one ULP apart. Compare `nm <production binary> | grep _ZGV` with the
kernel's. VIT compiles kernels with `-fvect-cost-model=unlimited` and links `-lmvec -lm` for this
reason.
