# Cross-Module KGen Instrumentation: Discovery, Diagnosis, and Fix
**Date:** 2026-04-25
**Status:** completed
**Scope:** VIT extraction pipeline, KGen integration

## Background

While attempting to extract Calculate_C_alpha (the fifth AirfoilInfo function, calling LAPACK_gels for least-squares fitting), extraction reported "No state data captured" — the same symptom as the wrong-test-case failure from earlier the same day. But this time the test case was correct (ad_MHK_RM1_Fixed, 56 hits confirmed by gcov coverage data). Something else was wrong.

## Discovery

The extraction produced a kernel directory with all the generated Fortran source but zero state data files (`Calculate_C_alpha.0.0.*`). KGen's log showed "Application built/run is finished" with no errors — the exact same silent failure pattern.

The first step was to fix the silence. KGen's `run_shcmd('make', cwd=state_dir)` captures stdout/stderr from the state capture build/run but never checks the return code or logs the output. We added error reporting to KGen's `extractor/main.py` (3 lines of Python 2.7) and error surfacing to VIT's CLI (scan KGen output for error lines when state_cases == 0). Immediately:

```
WARNING: No state data captured.
Build/run errors detected:
  ERROR: State capture build/run failed (return code 2)
  Error: Symbol 'kw_externs_in_nwtc_base' referenced at (1) not found in module 'nwtc_base'
```

The actual compile error, visible in seconds instead of hours.

## Root Cause

KGen traces USE dependency chains during source parsing and instruments every module it encounters — not just files in the build target. Calculate_C_alpha calls `LAPACK_gels`, which lives in the NWTC Library module `NWTC_LAPACK`, which depends on `NWTC_Base`. KGen followed this chain and instrumented `NWTC_Base_reduced.f90` — a file in the **icefloe** module directory (`modules/icefloe/src/interfaces/Console/`).

KGen generated a state-capture subroutine `kw_externs_in_nwtc_base` inside that file's `MODULE nwtc_base` and added `USE nwtc_base, ONLY: kw_externs_in_nwtc_base` to the instrumented AirfoilInfo.f90. But VIT's build command only builds the `aerodyn_driver` cmake target. That target compiles `NWTC_Base.f90` from `modules/nwtc-library/src/` — a **different file** that also defines `MODULE nwtc_base` with the same `AbortErrLev` variable, but without the KGen-generated subroutine. The icefloe copy was replaced on disk but never compiled.

Result: unresolved symbol `kw_externs_in_nwtc_base`, build failure, zero state files.

## Why This Is General

This will happen for any function whose transitive USE chain crosses cmake target boundaries. As we go deeper into AeroDyn (BEMT, FVW), dependency chains will only get longer. Any function that uses NWTC Library utilities beyond what's directly compiled by the aerodyn target will hit this.

## The Fix: Subroutine Transplant

The key insight: both the icefloe file and the nwtc-library file define the same Fortran MODULE (`nwtc_base`) with the same module-scope variables (including `AbortErrLev`). The `kw_externs_in_nwtc_base` subroutine only needs module-scope access to those variables — it works identically in either file.

VIT's build wrapper (step 2e, after `replace` copies instrumented source and `touch` marks files dirty, before cmake reconfigures and builds):

1. Parse `state/Makefile` to find all files KGen instrumented (from the `replace:` target's `cp -f` lines)
2. Compare each file's path against the cmake target's directory tree
3. For cross-module files: extract the MODULE name, extract all `kw_*` subroutines and PUBLIC declarations
4. Find the in-target file that defines the same module (recursive glob + regex search)
5. Inject the `kw_*` subroutines into that file (PUBLIC declarations after existing declarations, subroutine bodies before END MODULE, adding CONTAINS if needed)

The fix is general:
- Discovers cross-module files by path comparison, not hardcoded names
- Finds modules by name, not by path
- Works for any number of cross-module files with any number of `kw_*` subroutines
- No OpenFAST-specific logic

Implemented as `fix_cross_module_instrumentation()` in `extract.py` with four helper functions (`_parse_state_makefile_replace_files`, `_extract_module_name`, `_extract_kw_subroutines`, `_find_module_file_in_dir`, `_inject_kw_into_module`).

## Verification

- Calculate_C_alpha: 20/20 state files captured (ad_MHK_RM1_Fixed)
- Calculate_Cn (no cross-module deps): 20/20 state files, no regression
- Wrapper log shows: `VIT: Transplanted kw_externs_in_nwtc_base from NWTC_Base_reduced.f90 to NWTC_Base.f90`

## What Was Committed

| Repo | Commit | What |
|------|--------|------|
| KGen | `0705f2d` | Surface build/run errors from state capture (print stdout/stderr on non-zero return) |
| VIT | `073e363` | Surface errors in CLI output when 0 state files (scan KGen output for error lines) |
| VIT | `5ed3c52` | Cross-module instrumentation fix (`fix_cross_module_instrumentation` + 4 helpers) |
| OpenFAST | `6a655a64f` | Calculate_C_alpha golden kernel (20 cases) |

Also created `vit/docs/EXTRACTION_INTERNALS.md` (446 lines) documenting the entire extraction pipeline, all 9 known failure modes, and a step-by-step debugging checklist.

## Broader Lessons

1. **Silent failures are the most expensive failures.** KGen swallowing build output cost us hours twice. The 3-line KGen fix (print on non-zero return code) has infinite ROI.

2. **Tools that cross build system boundaries need build system awareness.** KGen resolves Fortran USE chains (language-level) but is oblivious to cmake targets (build-system-level). VIT bridges this gap.

3. **The write/read pairing constraint** means you can't strip state writes without breaking kernel reads. The transplant approach preserves the pairing by moving the subroutine rather than removing it.

4. **Both module files define the same variables** — this is the enabling fact for the transplant. If they didn't (e.g., if the icefloe version was truly "reduced" and lacked variables the subroutine writes), the transplant would fail at compile time with a clear error rather than silently.
