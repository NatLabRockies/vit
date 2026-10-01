# Extraction Internals

What `vit extract` does, step by step, what can fail at each step, and how to find out which step
did. Extraction spans two tools, VIT (Python 3) and KGen (Python 2.7), and many steps. A failure at
almost any of them used to look the same: "no state data captured". VIT now reports a failed
extraction with exit code 1 and the error lines it found, but the step that failed still has to be
found.

**When to read this:** `vit extract` reports `✗ Extraction captured no state data`, or captures
fewer calls than expected.

## 1. Overview

```
vit extract FuncName -f caller.f90 -l LINE

VIT, before KGen
   1. Find the call site
   2. Remove stale KGen pragmas from the sources
   3. Back up the file holding the call site
   4. Insert the KGen pragma around the call
   5. Check for editable Python installs that could load the wrong library
   6. Remove stale KGen artifacts
   7. Run clean_cmd, then build_cmd, so module (.mod) files exist
   8. Write include.ini, KGen's module search path
   9. Write .vit_build_wrapper.sh, which KGen runs as its build command
  10. Assemble the KGen command

KGen
  11. Parse the sources reachable from the call site, resolving USE chains
  12. Write kernel/ (the replay program) and state/ (instrumented sources)
  13. Run state/Makefile: save, replace, build, run, recover

VIT, after KGen
  14. Count the captured state files
  15. Undo the build-file patch: restore CMakeLists.txt, remove kgen_utils.f90, reconfigure CMake
  16. Move the kernel into kernel/<FuncName>/, delete state/
  17. Restore the call-site file from its backup
  18. Report: success with the number of cases, or failure (exit 1) with the errors found
```

`vit extract ... --dry-run` prints the KGen command and the wrapper script without running them.

## 2. VIT before KGen

### Find the call site

With `-f` and `-l`, VIT uses them, re-finding the call within a few lines if earlier edits shifted
it. Without them, it searches `project.source_dir` and stops if it finds no call site, or more than
one.

**Extract at a call site, never at the definition.** With the pragma around a module procedure's
definition, KGen's parent block is the module itself, the generated code is invalid, and nothing is
captured. `vit find` lists the definition along with the calls; pick a call.

**What can fail:** a line number shifted by earlier integration (extract from clean source); a call
site the run never reaches.

### Remove stale pragmas

VIT removes every `!$kgen` line from the `.f90` and `.F90` files at the **top level** of
`project.source_dir`. KGen reads all reachable sources and acts on any pragma it finds, so a pragma
left by an interrupted extraction redirects the next one.

**Limitation:** files in subdirectories of `source_dir` are not swept. With a broad `source_dir`,
clear stale pragmas yourself: `grep -rl '!$kgen' <source_dir>` should return nothing.

### Insert the pragma

```fortran
!$kgen begin_callsite FuncName
      call FuncName(...)
!$kgen end_callsite
```

Continuation lines (`&`) are kept inside the pair.

### Check editable installs

If the program is a Python package installed with `pip install -e`, Python may import it from a
different checkout, which then loads an uninstrumented library. VIT warns when it sees this.

### Remove stale artifacts

VIT removes `kgen.log`, `model.ini`, `elapsedtime/` and `model/`, all of which KGen would otherwise
reuse with data from an earlier extraction. It removes `.kgen_org` files (KGen's backups of
instrumented sources) from the top level of `source_dir`, with the same limitation as the pragma
sweep. And it writes a dummy `strace.log`, so that KGen skips its own build-tracing phase: VIT
supplies the module paths in `include.ini` instead.

### Clean, then build

VIT runs `kgen.clean_cmd`, then `kgen.build_cmd`, so the module files KGen needs to resolve `USE`
statements exist. A failure here means KGen cannot resolve modules; check the build.

### include.ini

KGen's module search path: every directory under `project.build_dir` that holds `.mod` files, and
every directory under `project.source_dir` that holds Fortran sources (both recursive).

**What can fail:** a missing directory leaves symbols unresolved; `kgen.log` then shows
`!!! UNRESOLVED !!!`. Unresolved intrinsic-module constants (`real64`, `int8`) and external
libraries such as LAPACK are usually harmless: KGen writes stubs for them.

### The build wrapper

KGen builds the instrumented program by running a build command; VIT gives it
`.vit_build_wrapper.sh`, written in the current directory. In order, it:

1. **Copies `kgen_utils.f90`** from `kernel/` into the `src/` directory beside the build file. KGen
   instruments the code with calls into it but never arranges for it to be compiled.
2. **Adds `src/kgen_utils.f90` to the build file**, after the first line that is a single Fortran
   source path, or by running `kgen.add_source_cmd` if set. If neither adds it, the wrapper stops
   with an error naming `kgen.add_source_cmd`. It then re-runs CMake's configure step in the build
   directory.
3. **Rewrites assumed-size array arguments** in KGen's state-writing calls to explicit sections,
   using the sizes from `kgen.assumed_size_arrays` or confirmed at exit code 10.
4. **Applies `kgen.dll_persistence` resets**, for a library whose SAVE state survives between runs
   in one process.
5. **Touches the Fortran sources** beside the build file, so the build recompiles them even where a
   container mount does not update timestamps.
6. **Moves cross-module instrumentation into the build:** see failure F2 below.
7. **Re-runs CMake's configure step** if the build file changed.
8. **Runs `build_cmd`, then `install_cmd`.**
9. **Removes the persistence resets** it added.

Set `VIT_WRAPPER_LOG=/path/to/log` to have the wrapper log what it did; without it, the log is
discarded.

### The KGen command

```bash
kgen <call-site file> \
  --cmd-build 'bash <project>/.vit_build_wrapper.sh' \
  --cmd-run '<run_cmd>' --cmd-clean '<clean_cmd>' \
  --outdir <output dir> --invocation 0:0:1-20 \
  -D <each preprocessor define>
```

## 3. Inside KGen

1. **Parse.** KGen's parser reads the call-site file and every module it reaches through `USE`. On a
   large code base this takes minutes.
2. **Generate.** `kernel/` holds the replay program: `kernel_driver.f90`, which reads each state
   file, calls the function and checks its outputs, plus reduced copies of the modules involved and
   a Makefile. `state/` holds instrumented copies of the sources and a Makefile.
3. **Capture.** KGen runs `make` in `state/`. Its targets run in this order:
   - `save` backs up each source to `<file>.kgen_org`
   - `replace` copies the instrumented source over it
   - `build` runs the build command, then restores the originals: the instrumented code is already
     compiled into the program or library
   - `run` runs the program
   - `recover` restores the originals again, as a safety net

KGen as released did not report a failed `make`: a build error or a crashing run looked like success
with no state files. The fork VIT depends on prints the output of a failed `make` (`0705f2d`), and
VIT shows the error lines from it.

## 4. State files

The instrumented code writes one file per captured call, to an absolute path fixed when KGen
generates the code:

```
<output dir>/kernel/<FuncName>.<process>.<thread>.<call number>
```

The program can run from any directory; the files still land in the kernel directory. They are
unformatted big-endian streams holding the function's inputs and outputs.

`kgen.invocation` (`0:0:1-20` by default: process 0, thread 0, calls 1 to 20) selects which calls
are captured. KGen counts calls per call site, so earlier work in the run can use up the range.
After the run it lists the files in `kgen_statefile.lst`.

## 5. Afterwards

Whether or not KGen succeeded, VIT:
- restores the build file from its `.vit_backup`, removes the copied `kgen_utils.f90`, re-runs CMake's
  configure step (otherwise CMake keeps looking for `kgen_utils.f90`), and deletes the wrapper
- moves the kernel's files into `kernel/<FuncName>/` and deletes `state/`
- restores the call-site file from its backup
- counts the files named `<FuncName>.N.N.N` and reports the result: `✓` with the number of cases, or
  `✗ Extraction captured no state data` with exit code 1 and the error lines from KGen's output

Extraction leaves `kgen.log`, `strace.log` and `include.ini` in the project.

## 6. Failure modes

| # | Failure | Symptom | Cause | First check |
|---|---|---|---|---|
| F1 | Call site not reached | no state files | the run never executes the call, or not within the capture range | Which test case reaches the line, and how often (line coverage, if you have it) |
| F2 | Cross-module instrumentation | build fails, no state files | KGen instrumented a file outside the build target | Usually fixed by the wrapper (below); with `VIT_WRAPPER_LOG`, look for its messages |
| F3 | Stale pragmas | wrong function extracted | a `!$kgen` pragma or `.kgen_org` file left by an earlier run | `grep -rl '!$kgen' <source_dir>`; remove `*.kgen_org` |
| F4 | `kgen_utils.f90` not compiled | no state files | the build file was not patched | The wrapper now stops with an error; check the build file's source list |
| F5 | Stale objects | no state files | the build did not recompile the instrumented sources | The build log should show the instrumented files compiling |
| F6 | Build fails | no state files | a compile error in instrumented code | VIT prints the errors; to reproduce, run `make save replace` in `state/`, then the wrapper |
| F7 | Run fails | no state files | `run_cmd` fails: wrong path, missing inputs, a crash | Run `run_cmd` by hand |
| F8 | Wrong library loaded | no state files | the program loads a library other than the one just built | Set `kgen.install_cmd`; heed the editable-install warning |
| F9 | Pragma at the definition | no state files, or invalid code | `-l` points at the function definition | Point `-l` at a call |
| F10 | Wrong line | no state files, or the wrong function | the line moved after integration | Extract from clean source |
| F11 | macOS host mount | the build fails copying a `.mod` file | CMake's module step on Colima's virtiofs mount | Keep the build directory inside the container (`docker/README.md`) |

### F2 in detail

KGen resolves `USE` by searching every directory in `include.ini`; it does not know which build
target compiles which file. When a module the function depends on exists in more than one file in
the tree, for example a reduced copy inside another component, KGen can instrument the copy the
build never compiles. The instrumented call site then refers to a state-writing routine
(`kw_externs_in_<module>`) that exists only in that uncompiled file, and the build fails: `Symbol
'kw_externs_in_...' not found in module ...`.

The wrapper detects files KGen instrumented outside the build target, extracts their `kw_*`
routines, and moves them into the in-target file that defines the same module. To check by hand:
`grep 'kw_externs_in' state/*.f90` and compare the modules named with those the target builds.

## 7. Debugging checklist

When extraction captures nothing, in this order:

1. **Is the call reached?** Confirm that the run in `kgen.run_cmd` executes the line, within the
   capture range. With gcov line coverage of the test cases, this is a lookup.
2. **See the command.** `vit extract FuncName -f file -l LINE --dry-run` prints the KGen command and
   the wrapper. Check the paths, the defines and the test case.
3. **Generate without building.** Run KGen with no-op commands to get `kernel/` and `state/` for
   inspection:
   ```bash
   kgen <call-site file> --cmd-build 'echo skip' --cmd-run 'echo skip' --cmd-clean 'echo skip' \
       --invocation 0:0:1-20 [-D <define> ...]
   ```
4. **Read `state/Makefile`.** The paths should be absolute and right, the build and run commands
   the ones you expect, and every source it replaces part of the build.
5. **Step through the capture by hand.** In `state/`: `make save`, `make replace`; confirm the
   instrumented code is in place (`grep -c kgen_issave <source>`); run `bash .vit_build_wrapper.sh`
   from the project and read the end of its output for compile errors; run `run_cmd`; look for
   `<FuncName>.0.0.*` in `kernel/`; then `make recover`.
6. **Check where the state files go.** `grep kgen_filepath state/*.f90` shows the absolute path
   compiled in; the directory must exist.
7. **Read the wrapper's log.** Re-run with `VIT_WRAPPER_LOG=/tmp/vit.log` and read it: whether
   `kgen_utils.f90` was found, copied and added to the build, and the exit codes.

## 8. Notes

- **Executables and shared libraries.** When the program is an executable built in place, the
  instrumented code is in the binary as soon as the build finishes, and nothing needs installing.
  When the run loads a shared library from somewhere else, as a Python harness loading a controller
  library does, set `kgen.install_cmd` to copy the freshly built library there, or the run loads the
  old one.
- **Restoring sources after the build** is safe for the same reason: the instrumented code is
  already compiled.
- **`project.source_dir` breadth.** A broad `source_dir` lets KGen find the modules a function USEs
  from other parts of the code base; the include path is built from all of it. The two cleanup
  sweeps above cover only its top level.
