# Extraction Internals: KGen + VIT Pipeline Reference

This document describes every step of the KGen+VIT extraction pipeline, what can fail at each step, and how to diagnose failures. It exists because the extraction pipeline has ~15 interacting steps spanning two tools (KGen in Python 2.7, VIT in Python 3), and when something goes wrong, the failure often manifests as a silent "no state data captured" with no indication of which step failed.

**When to consult this document:** Any time `vit extract` reports "No state data captured" or produces fewer cases than expected.

## 1. Pipeline Overview

```
User invokes: vit extract FuncName -f file.f90 -l LINE

VIT Pre-KGen:
  1. Detect/validate call site
  2. Clean stale pragmas from ALL source files
  3. Back up clean source file
  4. Insert KGen pragma at call site
  5. Clean stale KGen artifacts (kgen.log, model.ini, elapsedtime/, strace.log)
  6. Pre-build to generate .mod files
  7. Generate include.ini (module search paths for KGen)
  8. Generate .vit_build_wrapper.sh (injects kgen_utils.f90 into cmake)
  9. Construct KGen command line

KGen (Python 2.7):
  10. Parse all source files, resolve USE chains (~120s)
  11. Generate kernel/ (replay code) and state/ (instrumented source)
  12. Generate state/Makefile
  13. Execute state/Makefile: save → replace → build → run → recover

VIT Post-KGen:
  14. Locate state files in kernel/ directory
  15. Clean up: restore CMakeLists.txt, remove kgen_utils.f90, reconfigure cmake
  16. Move kernel files to kernel/<FunctionName>/ subdirectory
  17. Delete state/ directory
  18. Restore source file from backup
```

## 2. VIT Pre-KGen Steps

### Step 1: Call site detection and validation (`cmd_extract` in cli.py)

VIT finds the call site for the target function. If `--file` and `--line` are provided, uses those. Otherwise auto-detects by scanning source files for `CALL FuncName` or `= FuncName(` patterns.

**What can fail:**
- Wrong line number (shifted by previous integrations)
- File not found
- Multiple call sites found without `--line` specified

**Diagnostic:** Check that `grep -n 'call FuncName\|= FuncName(' file.f90` shows the expected line.

### Step 2: Clean stale pragmas (`remove_kgen_pragma`)

Scans ALL `.f90`/`.F90` files in `source_dir` and removes any lines containing `!$kgen`. This is critical because KGen reads ALL source files during parsing and will process any pragma it finds — including stale ones from a previous interrupted extraction.

**What can fail:** If extraction was interrupted (ctrl-C during KGen run), `.kgen_org` backup files may remain on disk. These contain instrumented source with pragmas that will contaminate the next extraction.

**Diagnostic:** `grep -r '!$kgen' modules/` — should return nothing before extraction starts.

### Step 3: Insert KGen pragma (`insert_kgen_pragma`)

Inserts `!$kgen begin_callsite FuncName` and `!$kgen end_callsite` around the call site. Handles Fortran continuation lines (lines ending with `&`).

Format:
```fortran
!$kgen begin_callsite Calculate_C_alpha
               call Calculate_C_alpha(alpha, Cn, Cl, ...)
!$kgen end_callsite
```

**What can fail:** Wrong indentation (cosmetic), continuation line boundaries missed.

### Step 4: Clean stale artifacts (`clean_kgen_artifacts`)

Removes:
- `kgen.log` — stale extraction log
- `model.ini` — stale KGen state
- `elapsedtime/` — stale ETime profiling data (contains function-specific data that gets misattributed on re-extraction)
- `model/` — stale profiling raw data
- Creates dummy `strace.log` — so KGen skips its ~100s compflag build phase (VIT provides include.ini independently)
- All `.kgen_org` files in source_dir — stale instrumented source backups

### Step 5: Pre-build (`subprocess.run(build_cmd)`)

Runs the project's build command to ensure `.mod` files exist. KGen needs compiled module files to resolve `USE` statements.

**What can fail:** Build failure here means KGen can't resolve module dependencies. Check build output.

### Step 6: Generate include.ini (`generate_include_ini`)

Writes KGen's module search path configuration:
```ini
[include]
/workspace/openfast/build/ftnmods=
/workspace/openfast/modules/aerodyn/src=
/workspace/openfast/modules/nwtc-library/src=
...
```

Discovers paths by:
- `discover_mod_dirs(build_dir)` — recursively finds all directories containing `.mod` files
- `discover_source_dirs(source_dir)` — recursively finds all directories containing `.f90`/`.F90` files

**What can fail:** Missing `.mod` directories → KGen `UNRESOLVED` warnings for module symbols. Check kgen.log for `!!! UNRESOLVED !!!` messages.

### Step 7: Generate build wrapper (`.vit_build_wrapper.sh`)

Creates a shell script that KGen will invoke as its `build_cmd`. The wrapper handles several things that KGen doesn't do natively:

1. **Copy kgen_utils.f90** from `kernel/` to `modules/aerodyn/src/`
2. **Patch CMakeLists.txt** to add `src/kgen_utils.f90` to the source list
3. **Reconfigure cmake** (`cd build && cmake ..`) to pick up the new source file
4. **Touch all .f90 files** in `modules/aerodyn/src/` to force recompilation (Docker filesystem timestamps may not update on `cp`)
5. **Fix cross-module instrumentation** (step 2e) — detect files KGen instrumented outside the cmake target's directory tree, extract their `kw_*` subroutines, and transplant them into the in-target module file that defines the same Fortran MODULE. This ensures all KGen-generated symbols are compiled by the build target.
6. **Reconfigure cmake again** (picks up touched files + any transplanted subroutines)
7. **Run the actual build** (`cmake --build . --target aerodyn_driver -j$(nproc)`)

All actions are logged to `/tmp/vit_wrapper.log`.

**What can fail:**
- kgen_utils.f90 not found at expected path (kernel not generated yet when wrapper runs — but KGen generates kernel/ before state Makefile runs, so this shouldn't happen)
- CMakeLists.txt sed pattern doesn't match (no `src/*.f90` lines in expected format)
- cmake reconfigure fails
- **Build fails** — this is silent! The wrapper captures the return code but KGen's `run_shcmd` doesn't log it. See Section 7.

### Step 8: Generate KGen command (`generate_kgen_command`)

Constructs the final command line:
```bash
/workspace/KGen/bin/kgen modules/aerodyn/src/AirfoilInfo.f90 \
  --cmd-build 'bash /workspace/openfast/.vit_build_wrapper.sh' \
  --cmd-run 'cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/ad_MHK_RM1_Fixed && ...' \
  --cmd-clean 'cd /workspace/openfast/build/modules/aerodyn && make clean' \
  --invocation 0:0:1-20 \
  -D OPENFAST_DOUBLE_PRECISION \
  -D HAS_FORTRAN2008_FEATURES
```

Use `--dry-run` to see this command without executing it.

## 3. KGen Internal Phases

### Phase A: Parsing (~120s)

KGen's Python 2.7 fparser reads the target source file and recursively resolves all `USE` dependencies. This produces an AST of every module, type, and procedure reachable from the call site.

**Output:** In-memory AST. No files written yet.

**What can fail:** `!!! UNRESOLVED !!!` warnings in kgen.log. These mean KGen couldn't find a symbol's definition. Common for Fortran intrinsic modules (`iso_fortran_env` constants like `int8`, `real64`) and LAPACK externals (`dgels`, `sgels`). These warnings are usually harmless — KGen generates stub write subroutines for unresolved symbols.

### Phase B: Generation

KGen generates two sets of files:

1. **kernel/** — standalone replay code that reads state files and calls the extracted function
   - `kernel_driver.f90` — main program, reads state files, calls the function, verifies outputs
   - `AirfoilInfo.f90` — module with the extracted function + KGen verify/timing wrappers
   - `Makefile` — builds the kernel executable
   - Various dependency modules (types, NWTC_Library, etc.)

2. **state/** — instrumented source files that capture state during a real simulation run
   - Modified copies of the source files that have state-writing code injected around the call site
   - `Makefile` — orchestrates the save/replace/build/run/recover flow

### Phase C: State Makefile Generation

KGen generates `state/Makefile` with these targets:

```makefile
run: build
    cd <original_cwd>; <run_cmd>

build: replace
    cd <original_cwd>; <build_cmd>
    mv -f <file>.kgen_org <file>    # Restore after build

replace: save
    rm -f <original_path>
    cp -f <basename> <original_path>

save:
    if [ ! -f <file>.kgen_org ]; then cp -f <file> <file>.kgen_org; fi

recover:
    cp -f <basename>.kgen_org <original_path>
```

**Key detail:** The `build` target restores original source files AFTER the build completes (`mv -f file.kgen_org file`). This is safe because AeroDyn uses static linking — the instrumented code is baked into the executable during the build step.

### Phase D: State Capture Execution

KGen runs `make` from the `state/` directory. The default target is `run`, which depends on `build`, which depends on `replace`, which depends on `save`. So the execution order is:

1. **save** — Back up original source files to `.kgen_org`
2. **replace** — Copy instrumented source from `state/` over originals in the source tree
3. **build** — Run VIT's build wrapper (builds the instrumented executable)
4. (Source files restored by `mv -f` in build target)
5. **run** — Execute the simulation with the instrumented binary
6. **recover** — Restore original source files (belt-and-suspenders)

**Error reporting:** KGen runs `make` via `run_shcmd('make', cwd=state_dir)`. If the make command fails (non-zero return code), KGen now prints the captured stdout/stderr to its own stdout, which VIT captures. VIT's CLI then scans for error lines and displays them alongside the "No state data captured" warning. This was added after a silent build failure wasted hours of debugging — previously KGen swallowed all errors and reported "Application built/run is finished" regardless.

## 4. State File Generation

### How State Files Are Written

The instrumented Fortran code (generated by KGen, placed in `state/AirfoilInfo.f90`) contains code like:

```fortran
IF (kgen_issave(0)) THEN
    WRITE (kgen_filepath(kgen_mymid, 0), FMT="(A,I0,A,I0,A,I0)") &
        "/workspace/openfast/kernel/Calculate_C_alpha.", &
        kgen_mymid, ".", 0, ".", kgen_invoke(0)
    OPEN (NEWUNIT=kgen_unit, &
          FILE=TRIM(ADJUSTL(kgen_filepath(kgen_mymid, 0))), &
          STATUS="REPLACE", ACCESS="STREAM", FORM="UNFORMATTED", &
          ACTION="WRITE", CONVERT="BIG_ENDIAN", IOSTAT=kgen_ierr)
    ! ... WRITE statements for all input/output variables ...
    CLOSE (UNIT=kgen_unit)
END IF
```

### State File Location

State files are written to **absolute paths** that are **hardcoded at KGen generation time**:

```
<kernel_path>/<FunctionName>.<mpi_rank>.<thread_id>.<invocation_number>
```

Where:
- `kernel_path = os.path.realpath(outdir + '/kernel')` — resolved at KGen startup
- `outdir` defaults to `.` (current working directory when KGen was invoked)
- For AeroDyn: `/workspace/openfast/kernel/Calculate_C_alpha.0.0.1` through `.0.0.20`

The path is embedded as a string literal in the generated Fortran code. It does NOT depend on the simulation's working directory — even though the simulation runs from a different directory (the test case directory), the state files are written to the kernel directory using the absolute path.

### State File Format

- Unformatted binary stream
- Big-endian byte order (CONVERT="BIG_ENDIAN")
- Contains: all input variables, all output variables (before and after the call), invocation metadata
- Read by the kernel driver (`kernel_driver.f90`) during verification

### Invocation Control

The invocation range `0:0:1-20` means: MPI rank 0, OpenMP thread 0, capture invocations 1 through 20. KGen's runtime code tracks invocation count via `kgen_invoke(thread_id)` and checks against the range using `kgen_check_save()`.

After all invocations are captured, KGen writes `kgen_statefile.lst` — a listing of all state file paths.

## 5. Post-Extraction Cleanup

After KGen finishes (success or failure), VIT cleans up:

1. **`cleanup_kgen_utils_injection()`** (extract.py):
   - Restore CMakeLists.txt from `.vit_backup`
   - Delete injected `kgen_utils.f90` from source tree
   - Reconfigure cmake to clear stale dependency cache (critical — without this, subsequent builds fail with "No rule to make target kgen_utils.f90")
   - Delete `.vit_build_wrapper.sh`

2. **CLI cleanup** (`cmd_extract` in cli.py):
   - Move loose files from `kernel/` to `kernel/<FunctionName>/` subdirectory
   - Delete `state/` directory (intermediate artifacts)
   - Restore source file from `.vit_backup`
   - Count state files (`*.N.N.N` pattern) and report

3. **KGen's own cleanup** (state Makefile `recover` target):
   - Restore source files from `.kgen_org` backups
   - This is redundant with VIT's restore but provides a safety net

## 6. Failure Modes and Diagnostics

### Quick Reference Table

| # | Failure | Symptom | Root Cause | First Diagnostic Step |
|---|---------|---------|------------|----------------------|
| F1 | Wrong test case | 0 state files | Call site not reached in simulation | `query_coverage.py --call-sites FuncName` |
| F2 | Cross-module instrumentation | 0 state files | KGen instruments files outside the build target | **Auto-fixed by step 2e**: VIT transplants kw_* subroutines into the in-target module file. If still failing, check `/tmp/vit_wrapper.log` for transplant messages. |
| F3 | Stale pragmas | Wrong function extracted | `.kgen_org` files from previous run contain pragmas | `grep -r '!$kgen' modules/` |
| F4 | kgen_utils not compiled | 0 state files | Wrapper didn't inject or cmake didn't pick it up | Check `/tmp/vit_wrapper.log` |
| F5 | Docker timestamps | 0 state files | `cp` doesn't update timestamps; cmake skips recompile | Check build log for "Building Fortran object...AirfoilInfo.f90.o" |
| F6 | Build fails | 0 state files | Any compile error in instrumented code | VIT now shows the error automatically; for manual diagnosis: `cd state && make save && make replace && bash ../.vit_build_wrapper.sh` |
| F7 | Simulation fails | 0 state files | run_cmd errors (wrong path, missing inputs, crash) | Run simulation manually: `<run_cmd>` from original CWD |
| F8 | Wrong outdir | State files in wrong location | Relative outdir resolves differently | `grep kgen_filepath state/AirfoilInfo.f90` |
| F9 | Wrong line number | 0 state files or wrong function | Line shifted by integrations | Compare coverage data line vs clean source line |

### F2: Cross-Module Instrumentation (Detailed)

This is the most insidious failure because it's completely silent and affects functions with transitive dependencies on modules outside the build target.

**Example:** Calculate_C_alpha calls LAPACK_gels (in NWTC_LAPACK), which depends on NWTC_Base. KGen instruments all transitive dependencies, including `NWTC_Base_reduced.f90` in `modules/icefloe/src/interfaces/Console/`. The state Makefile replaces that file with the instrumented version. However:

1. The VIT build wrapper only builds the `aerodyn_driver` cmake target
2. The aerodyn target uses `NWTC_Base.f90` from `modules/nwtc-library/src/` — a **different file** than `NWTC_Base_reduced.f90`
3. The icefloe module's `NWTC_Base_reduced.f90` is never compiled as part of the aerodyn build
4. The instrumented AirfoilInfo.f90 has `USE nwtc_base, ONLY: kw_externs_in_nwtc_base` — a KGen-generated state-write subroutine
5. This symbol is defined in the instrumented `NWTC_Base_reduced.f90` (not compiled) but not in `NWTC_Base.f90` (compiled)
6. **Build fails** with: `Error: Symbol 'kw_externs_in_nwtc_base' referenced at (1) not found in module 'nwtc_base'`
7. KGen swallows the error and reports "Application built/run is finished"

**Affected functions:** Any function that transitively depends on modules outside the aerodyn cmake target. Known: functions calling LAPACK (Calculate_C_alpha via LAPACK_gels). Likely: functions with deep NWTC Library dependencies.

**How to diagnose:** After generating state/ (via dry run or dummy KGen), check for cross-module references:
```bash
grep 'kw_externs_in' state/AirfoilInfo.f90
# If this shows references to modules not in the aerodyn build chain, you have F2
```

**Potential fixes:**
1. **Build all targets** — change build_cmd to omit `--target aerodyn_driver`, building everything
2. **Merge instrumented code** — copy the `kw_externs_in_*` subroutine from the instrumented cross-module file into the correct module file in the aerodyn build chain
3. **Strip cross-module state writes** — post-process the instrumented source to remove USE/CALL for `kw_*` functions referencing modules outside the build target. These state writes capture external module state (e.g., NWTC_Base globals) which may not be needed for verification.
4. **Add cross-module files to the build** — temporarily add the instrumented cross-module file to the aerodyn CMakeLists.txt

## 7. Debugging Checklist

When `vit extract` reports "No state data captured", follow this checklist in order:

### Step 1: Verify coverage data

```bash
docker exec vit-dev-openfast bash -c \
  "python3 /workspace/vit/scripts/query_coverage.py --call-sites FunctionName"
```

This returns every call site with which test cases exercise it. If the line isn't covered by the test case in your `vit.yaml` `run_cmd`, that's the problem (failure F1). Fix `run_cmd` and re-extract.

### Step 2: Dry-run to see the command

```bash
vit extract FuncName -f file.f90 -l LINE --dry-run
```

Inspect the KGen command and wrapper script. Verify paths, flags, test case.

### Step 3: Generate kernel/state without building

Run KGen with dummy build/run to just generate the kernel and state directories:
```bash
cd /workspace/openfast
/opt/python27/bin/python2.7 /workspace/KGen/bin/kgen modules/aerodyn/src/AirfoilInfo.f90 \
  --cmd-build 'echo SKIP_BUILD' \
  --cmd-run 'echo SKIP_RUN' \
  --cmd-clean 'echo SKIP_CLEAN' \
  --invocation 0:0:1-20 \
  -D OPENFAST_DOUBLE_PRECISION \
  -D HAS_FORTRAN2008_FEATURES
```

This produces `kernel/` and `state/` for inspection without running the full extraction.

### Step 4: Inspect state/Makefile

```bash
cat state/Makefile
```

Verify:
- File paths are absolute and correct
- build_cmd matches expectation
- run_cmd points to the correct test case directory
- All source files listed are actually in the build chain

### Step 5: Run state Makefile manually, step by step

```bash
cd state

# Save original source files
make save

# Replace with instrumented source
make replace

# Verify instrumented code is in place
grep -c 'kgen_issave' /workspace/openfast/modules/aerodyn/src/AirfoilInfo.f90
# Should be > 0

# Check for cross-module instrumentation (failure F2)
grep 'kw_externs_in' /workspace/openfast/state/AirfoilInfo.f90
# If this references modules NOT in the aerodyn build chain, you have F2

# Run the build wrapper manually — LOOK FOR COMPILE ERRORS
cd /workspace/openfast
bash .vit_build_wrapper.sh 2>&1 | tail -30
# If you see "Error: Symbol ... not found in module", that's the problem

# If build succeeded, run the simulation manually
cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/ad_MHK_RM1_Fixed
/workspace/openfast/build/modules/aerodyn/aerodyn_driver ad_driver.dvr 2>&1 | tail -5
# Should complete normally

# Check for state files
ls /workspace/openfast/kernel/FunctionName.0.0.* 2>/dev/null
# Should show 20 files

# Recover original source
cd /workspace/openfast/state
make recover
```

### Step 6: Check state file path

```bash
grep 'kgen_filepath' state/AirfoilInfo.f90 | head -3
```

The hardcoded path should be an absolute path to the kernel directory. Verify the directory exists.

### Step 7: Check wrapper log

```bash
cat /tmp/vit_wrapper.log
```

Shows whether kgen_utils.f90 was found and copied, whether cmake was reconfigured, and exit codes.

## 8. Key Implementation Details

### AeroDyn vs ROSCO: Static Linking

AeroDyn's `aerodyn_driver` is a **statically linked executable** (all `.a` static libraries, no `.so`). This means:
- The instrumented code is baked into the executable during the build step
- Restoring source files after the build (step 4 of the state Makefile) is safe — the binary is already compiled
- There is no `install_cmd` needed — the built executable IS the instrumented binary
- This differs from ROSCO, which uses a shared library (`libdiscon.so`) that Python loads via ctypes

### KGen's `run_shcmd` Swallows Errors

KGen's `run_shcmd` function (kgutils.py:167):
```python
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, **kwargs)
out, err = proc.communicate(input=input)
# Return code checked ONLY if show_error_msg is set (it's not for state capture)
return out, err, proc.returncode
```

The `make` command for state capture (main.py:160) does NOT set `show_error_msg`. This means:
- Build compile errors are invisible
- Simulation crashes are invisible
- Permission denied errors are invisible
- The only symptom is "0 state files"

### KGen's Source Resolution vs CMake Targets

KGen resolves `USE` dependencies by searching ALL `.f90` files in the include.ini paths. It does NOT know which cmake target compiles which file. This means KGen may instrument files that are part of OTHER cmake targets (e.g., icefloe's `NWTC_Base_reduced.f90`).

When the state Makefile runs `replace`, it copies instrumented source over the originals — but the VIT build wrapper only compiles the aerodyn target. Instrumented files in other cmake targets are replaced on disk but never compiled, so their KGen-generated symbols don't exist in the binary.
