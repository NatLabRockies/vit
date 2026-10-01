# Extraction Names the Step That Failed

**Date:** 2026-10-01
**Status:** completed
**Scope:** `vit extract` (step records, the build and run scripts, the report, KGen's source backups), the KGen fork's call-site instrumentation, `docs/EXTRACTION_INTERNALS.md` and the documents that point to it

## Why

Reading `EXTRACTION_INTERNALS.md`, the author noted its opening: "VIT now reports a failed
extraction with exit code 1 and the error lines it found, but the step that failed still has to be
found", and asked for the step to be reported instead.

## What extraction reported before

Each step of an extraction was broken in turn on the quickstart, in the `vit` image:

| Broken | Step that failed | Report |
|---|---|---|
| `kgen.build_cmd` | the build before KGen | `make: [build] Error 1`, after KGen had run in full |
| `kgen.add_source_cmd` | wrapper: add `kgen_utils.f90` | the same; the wrapper stopped under `set -e` without a word |
| CMake reconfigure | wrapper: reconfigure | the same; its output went to `/dev/null` |
| a compile error in the instrumented code | wrapper: build | the compiler's errors |
| `kgen.run_cmd` | the run | `make: [run] Error 127`, without the run's own message |
| `kgen.install_cmd` | wrapper: install | **`✓ Extraction successful`**: the wrapper ran it with `|| true` |
| `-l` at the definition | finding the call site | four compile errors from the instrumented build |
| capture range past the program's calls | none | "No build/run errors detected" and two guesses |
| `kgen.path` | KGen | `not found` |

The report was KGen's output filtered for lines containing "error", "failed" or "fatal". The
restore after KGen was printed as "Cleaned stale: CMakeLists.txt, kgen_utils.f90, ...".

## What changed

| Commit | Change |
|---|---|
| `0070724` | A line that defines the function stops extraction before KGen runs; the search for the call near `-l` uses `vit find`'s rule |
| `fceb3c2` | Every step records its outcome in `.vit/extract/`; the report names the first failed step; the swallowed failures; the build before KGen stops extraction; KGen's source backups settled |
| `b340f01` | The `.gitignore` entry for the old wrapper, dropped by `fceb3c2`, restored |
| `ca59290` | When every step succeeds and nothing is captured, the report says what the evidence shows, with the test cases coverage says run the call |
| KGen fork `62eb6e2` | The instrumentation writes `<kernel>/<name>.reached` the first time the call site runs |
| `8e30939` | VIT reads the marker; the image installs KGen at `62eb6e2` |
| `16c0428` | `EXTRACTION_INTERNALS.md` rewritten around the step records; `VIT_REFERENCE.md`, `GETTING_STARTED.md`, the AeroDyn manual |

After the change, each case in the table above names its step and shows that step's own output,
and every case restores the source and the build file. The capture-range case says "The run
reached the call site, and none of its calls fell in the capture range 0:0:600-620", with
coverage's `default: 500` beneath it.

## Findings

**The probe found a defect in the change itself.** The first run of the new build script failed at
`cross_module` with `IndentationError`: its inline Python had been indented inside a bash
function. The report named the step and showed the traceback, which is the point of the change;
the step is now one line.

**KGen exits 0 when its state Makefile fails.** `bin/kgen` returns `main()`, which returns None,
and the extractor prints the failed `make`'s output (fork commit `0705f2d`) without raising. Only an
exception gives a non-zero exit. VIT therefore reads the step records, not KGen's exit status.

**Deleting every `.kgen_org` could discard the only clean copy.** The stale-artifact sweep deleted
every backup before extraction. After an extraction stopped before KGen's `recover`, the source is
still instrumented and the backup is the original. The backups are now settled: deleted, except
that a source holding KGen's instrumentation is first restored from a backup that does not. The
same function runs after KGen, because a failed step leaves the backups beside the restored
sources; a failed install now does that too, since it now fails.

**A failed re-extraction deletes the previous kernel's captured calls.** Not changed here. After a
successful extraction of `drag` (20 cases), an extraction that failed at `run` replaced
`kernel/drag/` with a kernel holding no state files: `cmd_extract` moves any generated kernel into
`kernel/<FuncName>/`, deleting what was there, whenever KGen generated one.

## Decisions

**Each process records its own steps.** VIT, the build script and the run script append to one
record file, and VIT reads it after KGen returns. Rejected: having VIT run the state Makefile's
targets itself, with KGen only generating. That gives VIT every exit status directly, but
reimplements the Makefile's restore logic and changes the flow every campaign has used; the
records give the same report without either.

**User commands keep their own exit status.** The script's own steps stop at their first failing
command; `build_cmd`, `install_cmd` and `run_cmd` run with `set +e`, as they did when run
directly, so `make; echo done` still means what it meant.

**A failure after calls were captured is a warning.** The captured calls stand, as they did
before, and `vit verify` reads each one. The exit status is unchanged for that case.

**The reach marker is written by KGen, and read only when KGen writes it.** The marker belongs in
the generated instrumentation, so it is a fork change rather than a `sed` over KGen's output in
the build script, the pattern the build script's docstring already calls debt. It is written in
the block that initialises the state-capture variables, which runs once per process on the first
call. VIT checks that the instrumentation in `state/` opens the marker before reading its absence
as "never reached"; with an older KGen the report says "never reached, or reached only outside the
capture range", as before.

**Nothing captured is explained from evidence, in a fixed order.** The program's own capture
messages in the run's output first (the files went elsewhere), then whether any file the build
wrote holds the instrumented call site (a string constant only it has), then the marker. A program
that loads its library from elsewhere and ran an older build reads as "never reached"; the report
says so rather than claiming more.

**The scripts and logs are kept.** `.vit/extract/` is emptied at the start of each extraction, not
at the end, so the last extraction's records can be read after it. `VIT_WRAPPER_LOG` is gone:
every step's output is always kept.

## What remains

- Not run on ROSCO or AeroDyn: the new flow was exercised on the quickstart only. The AeroDyn
  restart will be its first run on real code.
- A failed re-extraction replaces `kernel/<FuncName>/` (above).
- At the 20-minute limit, VIT stops the shell running KGen, but `make` and the program it started
  can keep running, and the sources are not recovered; the next extraction's settle step restores
  any source left instrumented.
- The marker is per process, not per MPI rank or thread: "reached" means some process reached the
  call site.

## References

- Dev notes `202608071141` (the build-file patch that failed silently), `202605010623` (pragma at
  the definition), `202610011349` (coverage per test case)
- `vit/extract_steps.py`, `docs/EXTRACTION_INTERNALS.md` sections 2 and 6
