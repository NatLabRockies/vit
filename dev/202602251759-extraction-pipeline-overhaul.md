# Extraction Pipeline Overhaul: Making KGen Actually Work Against ROSCO
**Date:** 2026-02-25
**Status:** completed
**Scope:** cli.py, config.py, extract.py — extraction pipeline

## Summary

Commit f7fddfc was a major revamp of the extraction pipeline (324 insertions, 58 deletions) after the first several attempts to run KGen against ROSCO failed in various ways. This was the commit that transformed extraction from "theoretically correct" to "actually works." The changes addressed four distinct failure modes: pragma format incompatibility, missing include paths, stale artifact interference, and unrecoverable source file corruption on failure.

## Background

KGen (NCAR's Kernel Generator) is a powerful but finicky tool. It instruments Fortran source code to capture runtime state during a simulation, then generates a standalone kernel that can replay those captured calls. VIT wraps KGen, but KGen has very specific requirements about how it's invoked, what files it can find, and what state exists in the working directory.

After the interface generator fixes (previous dev note), VIT could correctly parse Fortran signatures and generate pragmas with the right function name. But the extraction pipeline itself — the orchestration of KGen invocation — had multiple issues that only surfaced when running against the full ROSCO build system.

## Changes in Detail

### Pragma Format: Underscores Required

KGen's pragma parser is more rigid than its documentation suggests. The initial VIT implementation used:

```fortran
!$kgen begin-callsite saturate
```

KGen actually requires underscores:

```fortran
!$kgen begin_callsite saturate
```

The hyphenated form was silently accepted by KGen but produced incorrect or incomplete instrumentation. The fix was straightforward — change hyphens to underscores in the pragma generation code — but diagnosing the issue required tracing through KGen's Python 2.7 source to find the regex that matches pragma directives.

### Auto-Generated include.ini

KGen needs to know where Fortran module files (`.mod`) and source files (`.f90`) live so it can resolve `USE` statements and follow the module dependency graph. The original approach relied on strace to discover file accesses during compilation, but this was unreliable in the Docker container environment.

The fix was to auto-generate `include.ini` by scanning the project's source and build directories for `.mod` and `.f90` files and listing their containing directories. The scan covers:

- The project source directory (`rosco/controller/src/`) for `.f90` files
- The build directory (`rosco/controller/build/`) for `.mod` files (generated during compilation)
- Any additional include paths specified in the config

The generated `include.ini` is written to the project root before KGen invocation. This is deterministic and doesn't depend on strace working correctly.

### Clean Environment Before Extraction

Leftover artifacts from previous VIT or KGen runs would cause mysterious failures. Files that needed to be cleaned:

- `kgen.log` — KGen appends to this file; stale entries confuse VIT's output parsing
- `model.ini` — KGen's generated configuration; a stale version from a different function or different source state causes extraction to produce wrong results
- `strace.log` — No longer used (replaced by auto-generated include.ini) but its presence could confuse older KGen code paths
- `state/` and `kernel/` directories — Partial results from failed extractions

The extraction pipeline now removes all of these before starting. This makes extraction idempotent — running it twice produces the same result regardless of what happened before.

### Combined Build/Install Commands and Pre-Extraction Rebuild

The original pipeline ran KGen's build and install commands separately and assumed the library was already built. The revamped pipeline:

1. Runs a clean build before extraction to ensure the `.mod` files exist (needed for `include.ini` generation)
2. Combines the build and install commands so that the freshly-built library is always installed before the simulation run
3. Ensures the simulation runs against the instrumented library, not a stale pre-instrumentation copy

This ordering is critical because KGen modifies Fortran source files to add instrumentation, then needs the project rebuilt with those modifications, then needs the simulation to run with the instrumented binary.

### Automatic Backup and Restore on Failure

KGen modifies Fortran source files during instrumentation — it inserts state-capture calls around the target function. If extraction fails partway through (compilation error, simulation crash, KGen internal error), the source files are left in a modified state. Without restoration, the user would need to manually revert the files before trying again.

The fix adds automatic backup of all source files that KGen might modify before instrumentation begins. On any failure (exception or non-zero exit code), the originals are restored. On success, the originals are also restored because the instrumented versions are only needed during the extraction run — the kernel and captured state are the outputs, not the modified source.

This backup/restore mechanism operates on specific files rather than the entire source directory, because KGen only modifies the file containing the call site and any files in its module dependency chain.

## Why This Was a Single Large Commit

These four changes are logically distinct but practically entangled. The include.ini generation depends on a successful pre-build (for .mod files). The clean environment logic determines what exists before the pre-build. The backup/restore wraps the entire sequence. Splitting them into separate commits would have left the pipeline in a broken state between commits, which was undesirable since this was the active development branch.

## Outcome

After this commit, `vit extract saturate -f rosco/controller/src/Controllers.f90 -l 102` ran successfully against ROSCO for the first time, producing a `kernel/` directory with the generated kernel code and a `state/` directory with 20 captured invocations. The extraction output showed `Cases: 20`, matching the `kgen.invocation: 0:0:1-20` configuration.

However, the extracted kernel did not yet compile and run correctly for verification — that required the fixes documented in the next dev note.
