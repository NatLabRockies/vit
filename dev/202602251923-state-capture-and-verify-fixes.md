# State Capture and Verification Fixes: Making the Kernel Compile and Run
**Date:** 2026-02-25
**Status:** completed
**Scope:** kernel_verify.py, extract.py, cli.py — verification pipeline and extraction refinements

## Summary

Two commits (1d56cca and c97504c, roughly 20 minutes apart) addressed the gap between "extraction produces output" and "verification produces correct results." The first commit (476 insertions, 165 deletions — the largest single commit to date) fixed a cascade of issues in kernel compilation, Fortran USE statement ordering, Makefile variable definitions, and output parsing. The second commit fixed a subtle KGen codegen bug involving ONLY clauses on USE statements.

Together, these commits represent the "make verification actually compile and run" phase of development.

## Background

After the extraction pipeline overhaul (previous dev note), KGen successfully captured runtime state for ROSCO's `saturate` function. The next step was verification: patch the KGen-generated kernel to also call the C++ translation, build the combined Fortran+C++ kernel, run it, and compare outputs.

KGen generates a self-contained kernel directory with:
- Fortran source files that replay captured state
- A Makefile to build the kernel
- Binary state files (inputs/outputs for each captured invocation)

VIT's job is to patch this generated code to additionally call the C++ function and compare its outputs against the captured Fortran outputs. This sounds simple but requires understanding — and working around — several KGen codegen quirks.

## Commit 1: Kernel Compilation and Verification Fixes (1d56cca)

### Makefile Variables: FC_0 and FC_FLAGS_SET_0

KGen generates Makefiles that reference variables `FC_0` and `FC_FLAGS_SET_0` (the Fortran compiler and flags for the first source file group) but doesn't define them. In KGen's intended workflow, these are set by a parent Makefile or environment. Since VIT runs the kernel standalone, they must be defined explicitly.

The fix adds these variable definitions to the generated Makefile:

```makefile
FC_0 = gfortran
FC_FLAGS_SET_0 = -O2
```

The compiler is taken from VIT's configuration (`fortran.compiler` in `vit.yaml`).

### C++ Object File in ALL_OBJS

KGen's Makefile defines `ALL_OBJS` with all the Fortran object files but obviously doesn't know about the C++ translation file VIT adds. The patching logic now appends the C++ `.o` file to `ALL_OBJS` so it gets linked into `kernel.exe`.

### Executable Permission on kernel.exe

After building, `kernel.exe` sometimes lacked execute permission depending on the umask and how `make` created the file. The fix adds an explicit `chmod +x kernel.exe` after a successful build. A minor issue but one that caused confusing "Permission denied" errors.

### USE Statement Ordering

Fortran requires all `USE` statements to appear before `IMPLICIT NONE` and any executable statements. VIT's kernel patches were inserting new `USE` statements (for the ISO_C_BINDING interface module) after `IMPLICIT NONE`, which is a compilation error.

The fix scans the kernel driver source for the position of existing `USE` statements and inserts new ones in the correct location — after the last existing `USE` but before `IMPLICIT NONE`.

### Incomplete USE Clauses

Some of VIT's generated `USE` statements referenced specific symbols via `ONLY:` clauses but missed symbols that the kernel driver actually needed. Rather than trying to enumerate all needed symbols (which would require deep analysis of the generated code), the fix changed certain USE statements to import the entire module without an ONLY clause.

### Output Parser: Double-Counting Fix

The kernel driver prints per-case results (one line per captured invocation) and a summary line. VIT's output parser was counting both, leading to inflated pass counts (e.g., reporting 40 passed when only 20 cases existed, because each case was counted once from the per-case output and once from the summary).

The fix separates the parsing into two passes: one for per-case results (used for detailed reporting) and one for the summary (used for the final pass/fail count). Only the per-case results contribute to the count; the summary is used for validation.

### Build Wrapper Script for kgen_utils.f90

KGen generates a utility module `kgen_utils.f90` that the instrumented code depends on, but KGen doesn't add this file to the project's build system. When KGen rebuilds the project with instrumentation, the build fails because `kgen_utils.f90` isn't compiled.

The fix creates a build wrapper script that:
1. Copies `kgen_utils.f90` into the source directory
2. Temporarily patches `CMakeLists.txt` to include it in the build
3. Runs the real build command
4. Restores `CMakeLists.txt` to its original state

This wrapper is passed to KGen as the `--cmd-build` argument, replacing the direct build command.

### Editable Install Detection

If VIT itself is not installed as an editable package (`pip install -e .`), the `vit` command might not be on the PATH inside the container, or it might be running a stale cached version. The fix adds a check at startup that warns if the installation looks incorrect, saving debugging time.

### Pre-Build and include.ini Optimization

Refinements to the pre-build logic from the extraction overhaul. The include.ini generation now runs after the pre-build to ensure all `.mod` files are current. Also added error handling for cases where the build directory doesn't contain any `.mod` files (indicating a build failure).

## Commit 2: Fix USE kgen_utils_mod ONLY Clause Stripping (c97504c)

### The Problem

KGen generates USE statements like:

```fortran
USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck
```

But the ONLY clause references symbols (`kgen_dp`, `kgen_array_sumcheck`) that may not exist in the version of `kgen_utils_mod` that gets compiled. KGen's code generation assumes a specific version of its utility module, but the actual module shipped with the KGen version VIT uses has different exported symbols.

This caused compilation errors like:
```
Error: Symbol 'kgen_dp' not found in module 'kgen_utils_mod'
```

### The Fix

VIT strips the ONLY clause from all `USE kgen_utils_mod` statements, converting them to bare `USE kgen_utils_mod`. This imports all symbols from the module, avoiding the missing-symbol problem.

The implementation had to handle two cases:

1. **Single-line ONLY clause:**
   ```fortran
   USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck
   ```

2. **Multi-line ONLY clause with Fortran continuation:**
   ```fortran
   USE kgen_utils_mod, ONLY: kgen_dp, &
       kgen_array_sumcheck, &
       kgen_perturb_real
   ```

The fix uses a state machine that detects `USE kgen_utils_mod, ONLY:` and then skips continuation lines (lines ending with `&` or lines that are continuations of the previous `&`-terminated line) until the statement is complete, replacing the entire multi-line statement with a single `USE kgen_utils_mod`.

### Why Not Fix KGen Instead?

KGen is a third-party tool (NCAR) running under Python 2.7. Modifying KGen would create a maintenance burden and make VIT dependent on a forked version. Patching KGen's output in VIT is more sustainable — VIT already patches the generated Makefile and kernel driver, so patching USE statements is consistent with the existing approach.

## Outcome

After these two commits, the full pipeline worked end-to-end for the first time:

```
$ vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102
  Extraction successful — Cases: 20

$ vit verify saturate saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/
  VERIFICATION PASSED: 40/40 passed
```

The 40/40 count reflects 20 captured invocations tested in both directions (Fortran-then-C++ and C++-then-Fortran) to ensure the comparison is symmetric. This was the first successful verification of a C++ translation against captured Fortran runtime state.
