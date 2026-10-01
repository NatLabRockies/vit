# Extraction Internals

What `vit extract` does, step by step, and what it reports when a step fails. Extraction spans two
tools, VIT (Python 3) and KGen (Python 2.7), and many steps. Every step records how it ended, and
`vit extract` reports the first one that failed, with its exit code and the last lines of its own
output. When every step succeeds and nothing is captured, it says what the evidence shows.

**When to read this:** to know what a step in the report does, or to work through a failure the
report does not settle.

## 1. Overview

```
vit extract FuncName -f caller.f90 -l LINE

VIT, before KGen
   1. Find the call site; refuse a line that defines the function
   2. Remove stale KGen pragmas from the sources
   3. Back up the file holding the call site
   4. Insert the KGen pragma around the call
   5. Check for editable Python installs that could load the wrong library
   6. Remove stale KGen artifacts, and settle KGen's source backups
   7. Run clean_cmd, then build_cmd, so module (.mod) files exist      [clean, prebuild]
   8. Write include.ini, KGen's module search path
   9. Write the build script and the run script KGen runs
  10. Assemble the KGen command

KGen                                                                  [kgen]
  11. Parse the sources reachable from the call site, resolving USE chains
  12. Write kernel/ (the replay program) and state/ (instrumented sources)
  13. Run state/Makefile: save, replace, build, run, recover          [build script, run]

VIT, after KGen
  14. Count the captured state files, and read whether the call site was reached
  15. Undo the build-file patch: restore CMakeLists.txt, remove kgen_utils.f90, reconfigure  [restore]
  16. Settle KGen's source backups
  17. Move the kernel into kernel/<FuncName>/ if calls were captured, delete state/
  18. Restore the call-site file from its backup
  19. Report: success with the number of cases, or failure (exit 1) naming the step
```

The names in brackets are the steps' names in the report and in `.vit/extract/` (section 2).
`vit extract ... --dry-run` runs steps 1 to 10, including the clean and the build, then prints the
KGen command and both scripts without running KGen.

## 2. Step records

Each step appends two records to `.vit/extract/steps.jsonl`, in the directory `vit extract` runs
in, one when it starts and one when it ends, and keeps its output in `.vit/extract/<step>.log`:

```json
{"step": "build", "status": "started", "rc": null, "log": "build.log", "time": 1790000000}
{"step": "build", "status": "failed", "rc": 2, "log": "build.log", "time": 1790000042}
```

The steps run in three kinds of process, and each records itself: VIT's own steps; KGen, whose
output VIT keeps in `kgen_output.log` (KGen's own log is `kgen.log` in the project); and the two
scripts KGen runs through its state Makefile, `.vit/extract/build.sh` and `.vit/extract/run.sh`.
The directory is emptied at the start of each extraction and kept afterwards.

| Step | What it is |
|---|---|
| `clean` | `kgen.clean_cmd`, before KGen |
| `prebuild` | `kgen.build_cmd`, before KGen, on the uninstrumented source |
| `kgen` | KGen: parse the sources, generate `kernel/` and `state/`, run the capture |
| `copy_kgen_utils` | Copy `kgen_utils.f90` into the source tree |
| `add_kgen_utils` | Add `kgen_utils.f90` to the build file |
| `assumed_size` | Rewrite assumed-size arrays in the instrumented code |
| `persistence_resets` | Apply the `kgen.dll_persistence` resets |
| `touch_sources` | Touch the Fortran sources so the build recompiles them |
| `cross_module` | Move cross-module instrumentation into the build target |
| `reconfigure` | Re-run CMake's configure step |
| `build` | `kgen.build_cmd`, on the instrumented source |
| `install` | `kgen.install_cmd` |
| `remove_resets` | Remove the `kgen.dll_persistence` resets |
| `run` | `kgen.run_cmd`, with the instrumented program |
| `restore` | Restore the build file and re-run CMake's configure step |

A failed extraction is reported like this:

```
✗ Extraction failed at step 'install' (kgen.install_cmd): exit code 1
  Last lines of its output (.vit/extract/install.log):
    cp: cannot stat '/nonexistent': No such file or directory
  Every step's record and output: .vit/extract/
```

Two failures leave no failed record, and the report says where they happened. When KGen's state
Makefile stops between steps (between `save` and the build script, or between the build script and
the run, where it moves the original sources back), the run never starts. When extraction stops at
its 20-minute limit, the step still running is the one named.

## 3. VIT before KGen

### Find the call site

With `-f` and `-l`, VIT uses them, re-finding the call within a few lines if earlier edits shifted
it. Without them, it searches `project.source_dir` and stops if it finds no call site, or more than
one.

**Extract at a call site, never at the definition.** With the pragma around a module procedure's
definition, KGen's parent block is the module itself and the generated code does not compile. The
search for the call near `-l` never takes the function's SUBROUTINE or FUNCTION statement for a
call. If it finds no call within five lines and `-l` is that statement, extraction stops before KGen
runs, and the error lists the calls in that file. `vit find` lists every call and leaves out the
definition.

If it finds no call and `-l` is any other line, the line is used as given, with a warning: a call
through a generic interface or a renamed import does not name the function either.

### Remove stale pragmas

VIT removes every `!$kgen` line from the `.f90` and `.F90` files anywhere under
`project.source_dir`, the same tree KGen searches. KGen acts on any pragma it finds, so a pragma left
by an interrupted extraction would redirect the next one. Files without a pragma are not rewritten,
and extraction's own `kernel/` and `state/` directories are skipped.

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

VIT removes KGen's log (`kgen.log`), `model.ini`, `elapsedtime/` and `model/`, all of which KGen
would otherwise reuse with data from an earlier extraction. It writes a dummy `strace.log`, so that
KGen skips its own build-tracing phase: VIT supplies the module paths in `include.ini` instead.

**KGen's source backups.** KGen's state Makefile backs up each source it instruments to
`<file>.kgen_org`. No backup may survive into an extraction: `save` keeps an existing one, and the
build then moves it over the source. VIT deletes each backup anywhere under `source_dir`, except
when the file it backs up still holds KGen's instrumentation and the backup does not. Then an
earlier extraction stopped before KGen restored the source, the backup is the only clean copy, and
VIT restores the file from it, with a warning.

### Clean, then build

VIT runs `kgen.clean_cmd`, then `kgen.build_cmd`, as steps `clean` and `prebuild`, so the module
files KGen needs to resolve `USE` statements exist. If either fails, extraction stops there: the
instrumented build would fail too, after KGen has spent minutes parsing.

### include.ini

KGen's module search path: every directory under `project.build_dir` that holds `.mod` files, and
every directory under `project.source_dir` that holds Fortran sources (both recursive).

**What can fail:** a missing directory leaves symbols unresolved; KGen's log, `kgen.log`, then shows
`!!! UNRESOLVED !!!`. Unresolved intrinsic-module constants (`real64`, `int8`) and external
libraries such as LAPACK are usually harmless: KGen writes stubs for them.

### The build script

KGen builds the instrumented program by running a build command; VIT gives it
`.vit/extract/build.sh`. Its steps, in order, each recorded under the name in brackets:

1. **Copies `kgen_utils.f90`** from `kernel/` into the `src/` directory beside the build file
   [`copy_kgen_utils`]. KGen instruments the code with calls into it but never arranges for it to
   be compiled.
2. **Adds `src/kgen_utils.f90` to the build file**, after the first line that is a single Fortran
   source path, or by running `kgen.add_source_cmd` if set [`add_kgen_utils`]. If neither adds it,
   the step fails with an error naming `kgen.add_source_cmd`.
3. **Rewrites assumed-size array arguments** in KGen's state-writing calls to explicit sections,
   using the sizes from `kgen.assumed_size_arrays` or confirmed at exit code 10 [`assumed_size`].
4. **Applies `kgen.dll_persistence` resets**, for a library whose SAVE state survives between runs
   in one process [`persistence_resets`].
5. **Touches the Fortran sources** beside the build file, so the build recompiles them even where a
   container mount does not update timestamps [`touch_sources`].
6. **Moves cross-module instrumentation into the build** [`cross_module`]: see F2 below.
7. **Re-runs CMake's configure step** [`reconfigure`].
8. **Runs `build_cmd`, then `install_cmd`** [`build`, `install`]. A failed install fails the
   extraction: the run would load the old library.
9. **Removes the persistence resets** when the script exits, whether or not a step failed
   [`remove_resets`].

The commands from `vit.yaml` keep their own exit status: they do not run under `set -e`.

### The run script

KGen runs the program by running a run command; VIT gives it `.vit/extract/run.sh`, which runs
`kgen.run_cmd` as step `run`. The run's output is in `.vit/extract/run.log`.

### The KGen command

```bash
kgen <call-site file> \
  --cmd-build 'bash <project>/.vit/extract/build.sh' \
  --cmd-run 'bash <project>/.vit/extract/run.sh' \
  [--cmd-clean '<clean_cmd>'] --invocation <kgen.invocation> \
  [-D <each preprocessor define>]
```

`<project>` here and below is the directory `vit extract` runs in. KGen runs there too, and writes
`kernel/` and `state/` into it.

## 4. Inside KGen

1. **Parse.** KGen's parser reads the call-site file and every module it reaches through `USE`. On a
   large code base this takes minutes.
2. **Generate.** `kernel/` holds the replay program: `kernel_driver.f90`, which reads each state
   file, calls the function and checks its outputs, plus reduced copies of the modules involved and
   a Makefile. `state/` holds instrumented copies of the sources and a Makefile.
3. **Capture.** KGen runs `make` in `state/`. Its targets run in this order:
   - `save` backs up each source to `<file>.kgen_org`
   - `replace` copies the instrumented source over it
   - `build` runs the build command, then moves the backups back: the instrumented code is already
     compiled into the program or library
   - `run` runs the program
   - `recover` restores the originals again, from KGen's own copies in `state/`, as a safety net

KGen exits with code 0 when its state Makefile fails, so VIT reads the step records rather than
KGen's exit status. KGen's output is kept in `.vit/extract/kgen_output.log`. If KGen exits without
writing `kernel/` and `state/Makefile`, the report names step `kgen` with the end of that output.

## 5. State files

The instrumented code writes one file per captured call, to an absolute path fixed when KGen
generates the code:

```
<project>/kernel/<FuncName>.<process>.<thread>.<call number>
```

The program can run from any directory; the files still land in the kernel directory. They are
unformatted big-endian streams holding the function's inputs and outputs. After writing each, the
program prints `Collected Kernel Input/Ouput state from: ...`.

`kgen.invocation` (`0:0:1-20` by default: process 0, thread 0, calls 1 to 20) selects which calls
are captured. KGen counts calls per call site, so earlier work in the run can use up the range.
After the run it lists the files in `kgen_statefile.lst`.

The first time the call site runs, whether or not any call falls in the range, the instrumentation
also writes `<project>/kernel/<FuncName>.reached`. This is a change in the KGen fork VIT depends
on (`62eb6e2`); VIT reads the marker and removes it.

## 6. Afterwards

Whether or not KGen succeeded, VIT:
- restores the build file from its `.vit_backup`, removes the copied `kgen_utils.f90`, and re-runs
  CMake's configure step (otherwise CMake keeps looking for `kgen_utils.f90`), as step `restore`.
  If that fails, the report warns that the next build may still look for `kgen_utils.f90`.
- settles KGen's source backups, as before KGen (section 3). A step that failed before the state
  Makefile moved them back leaves them beside the restored sources. After a timeout VIT leaves
  them, because the capture may still be running; the next extraction settles them.
- if calls were captured, moves the kernel's files into `kernel/<FuncName>/`, replacing an earlier
  kernel there. If none were, it discards them, and `kernel/<FuncName>/` from an earlier extraction
  stays as it was, captured calls included.
- deletes `state/`
- restores the call-site file from its backup
- counts the files named `<FuncName>.N.N.N` and reports the result: `✓` with the number of cases,
  or `✗` with exit code 1. A step that failed after calls were captured, such as a run that crashed
  after its first calls were written, is reported as a warning beside the `✓`.

Extraction leaves `.vit/extract/`, `include.ini` and the dummy `strace.log` in the project, and
KGen leaves its log, `kgen.log`, and an empty `model/` directory, which VIT removes at the start of
the next extraction.

### When every step succeeded and nothing was captured

The report says what the evidence shows, in this order:

1. **The run reported captured calls, and none is in `kernel/`:** the program printed the line it
   prints after writing each state file. The report gives the path compiled into the
   instrumentation; the files are wherever that path leads for the program, for example on the
   machine or in the container where it ran.
2. **No file the build wrote contains the instrumented call site:** the instrumented build did not
   recompile the instrumented source, or the target it builds does not include it. VIT searches the
   files under `project.build_dir` written since step `build` started, for a string constant that
   only the instrumented call site holds.
3. **Otherwise**, the instrumented call site was built and the run finished, and the reach marker
   says which of two things happened: the run never reached the call site, or it reached it and no
   call fell in the capture range. A program that loads its library from somewhere else
   (`kgen.install_cmd`) may have run another build, which also reads as never reached.

With a coverage file (`coverage.file`, written by `vit coverage run`), the report adds which test
cases run this call, and how often.

## 7. Failure modes

| # | Failure | What `vit extract` reports |
|---|---|---|
| F1 | Call site not reached, or not within the capture range | The run never reached the call site, or reached it and no call fell in the range; with coverage, which test cases run the call |
| F2 | Cross-module instrumentation | Step `build`: `Symbol 'kw_externs_in_...' not found in module ...`, when the build script's fix (below) does not apply; `cross_module.log` lists what it moved |
| F3 | Stale pragmas or backups | Prevented: VIT sweeps pragmas and settles backups before each extraction |
| F4 | `kgen_utils.f90` not added to the build | Step `add_kgen_utils`, with an error naming `kgen.add_source_cmd` |
| F5 | Stale objects | No file the build wrote contains the instrumented call site |
| F6 | The instrumented build fails | Step `build`, with the compiler's output |
| F7 | The run fails | Step `run`, with the run's output |
| F8 | Wrong library loaded | Step `install` if the install fails; otherwise it reads as never reached, with a pointer to `kgen.install_cmd`. VIT also warns about editable installs |
| F9 | Pragma at the definition | Refused before KGen runs, with the calls in that file |
| F10 | Wrong line | A warning if the line does not name the function; then as F1 or F6 |
| F11 | macOS host mount | Step `build` or `prebuild`: an error copying a `.mod` file. Keep the build directory inside the container (`docker/README.md`) |
| F12 | The plain build fails | Step `prebuild` (or `clean`), before KGen runs |
| F13 | KGen fails to parse or generate | Step `kgen`, with the end of KGen's output |

### F2 in detail

KGen resolves `USE` by searching every directory in `include.ini`; it does not know which build
target compiles which file. When a module the function depends on exists in more than one file in
the tree, for example a reduced copy inside another component, KGen can instrument the copy the
build never compiles. The instrumented call site then refers to a state-writing routine
(`kw_externs_in_<module>`) that exists only in that uncompiled file, and the build fails: `Symbol
'kw_externs_in_...' not found in module ...`.

The build script's `cross_module` step detects files KGen instrumented outside the build target,
extracts their `kw_*` routines, and moves them into the in-target file that defines the same
module. To check by hand: `grep 'kw_externs_in' state/*.f90` and compare the modules named with
those the target builds.

## 8. Debugging by hand

The report and `.vit/extract/` settle most failures. When they do not:

1. **Read the step's whole log.** The report shows the last lines; `.vit/extract/<step>.log` has
   all of them (KGen's are in `kgen_output.log`), and `steps.jsonl` shows which steps ran.
2. **See the command.** `vit extract FuncName -f file -l LINE --dry-run` prints the KGen command and
   both scripts, after running the clean and the build. Check the paths, the defines and the test
   case.
3. **Generate without building.** Run KGen with no-op commands to get `kernel/` and `state/` for
   inspection:
   ```bash
   kgen <call-site file> --cmd-build 'echo skip' --cmd-run 'echo skip' --cmd-clean 'echo skip' \
       --invocation 0:0:1-20 [-D <define> ...]
   ```
4. **Read `state/Makefile`.** The paths should be absolute and right, the build and run commands
   the ones you expect, and every source it replaces part of the build.
5. **Step through the capture by hand.** In `state/`: `make save`, `make replace`; confirm the
   instrumented code is in place (`grep -c kgen_issave <source>`); from the project, run
   `bash .vit/extract/build.sh`, then `bash .vit/extract/run.sh`, each of which records into
   `.vit/extract/` as it does during extraction; look for `<FuncName>.0.0.*` in `kernel/`; then
   `make recover`.

## 9. Notes

- **Executables and shared libraries.** When the program is an executable built in place, the
  instrumented code is in the binary as soon as the build finishes, and nothing needs installing.
  When the run loads a shared library from somewhere else, as a Python harness loading a controller
  library does, set `kgen.install_cmd` to copy the freshly built library there, or the run loads the
  old one.
- **Restoring sources after the build** is safe for the same reason: the instrumented code is
  already compiled.
- **`project.source_dir` breadth.** A broad `source_dir` lets KGen find the modules a function USEs
  from other parts of the code base; the include path and both cleanup sweeps cover all of it.
