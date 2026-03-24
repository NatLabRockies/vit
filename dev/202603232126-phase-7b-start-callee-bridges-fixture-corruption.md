# Phase 7B Start: Callee Bridge Generalization and Golden Fixture Corruption
**Date:** 2026-03-23
**Status:** completed (blockers resolved, Phase 7B ready)
**Scope:** Kernel callee bridges, golden fixture integrity, verification idempotency

## Starting Point

Phase 4D was complete. 19 functions verified 40/40, byte-identical simulation output. ForeAftDamping was the proof — first ControlParameters view type function through the full pipeline. The plan was to proceed to Phase 7B: translate the 10 main controller subroutines (PitchControl, VariableSpeedControl, etc.).

## Phase 7B Planning Revealed a Gap

Analysis of PitchControl (185 lines, the most complex Phase 7B function) showed it calls:
- 6 already-translated functions (interp1d, saturate, ratelimit, PIController, LPFilter, SecLPFilter)
- 3 untranslated Phase 7B functions (IPC, ActiveWakeControl, FloatingFeedback)
- 1 Phase 8 function (PitchSaturation, in ControllerBlocks.f90)

The callee bridge system (built during Phase 4D Step 9) only generated bridges for **translated** callees — functions found in `vit.yaml` translations. Untranslated callees had no bridges. PitchControl's C++ couldn't call IPC, ActiveWakeControl, FloatingFeedback, or PitchSaturation.

## Callee Bridge Generalization

### The Change

Extended `detect_translated_callees()` → `detect_callees()`. Instead of checking only translated function names, the detector now:
1. Scans all `.f90` files in the source directory for FUNCTION/SUBROUTINE definitions to build a known-functions set
2. Scans the target function body for CALL statements and function-call patterns
3. Filters to known project functions (excludes Fortran intrinsics)
4. Excludes VIT artifacts (names ending in `_c`, starting with `vit_`)
5. Generates bridges for ALL detected callees, with try/except per callee for unsupported types

### Why This Is Correct

In the kernel, ALL callees are original Fortran regardless of translation status. The bridge just provides a C-callable entry point (`callee_c`). Whether the callee is translated or not doesn't matter for the kernel — the bridge always calls the kernel's original Fortran.

### What Broke

The generalized detector found callees from the **integrated** source file (which has VIT wrappers, not original algorithm bodies). This produced false detections:
- `foreaftdamping_c` — the target function's own C entry point (from the wrapper)
- `vit_populate_controlparameters` — the populator call (from the wrapper)

Fixed by filtering names ending in `_c` and starting with `vit_`.

But a deeper problem emerged: the ForeAftDamping golden fixture was corrupted.

## Golden Fixture Corruption: Root Cause Chain

### What Happened

1. **Step 9 (successful):** ForeAftDamping extracted from CLEAN source (fde0498, no integration wrappers). KGen captured the original algorithm body. Kernel built and verified 40/40. Golden fixture committed to git.

2. **Callee bridge work:** During debugging, `vit extract` and `vit verify` were run multiple times. Some runs inadvertently re-extracted ForeAftDamping from the INTEGRATED source (which has the C++ wrapper, not the original algorithm). This overwrote the golden fixture with a broken kernel.

3. **KGen selective type capture:** The broken re-extraction captured different types because the function body was the VIT wrapper (calls `foreaftdamping_c`, `vit_populate_controlparameters`) instead of the original algorithm (calls `PIController`, reads `CntrPar%FA_KI`). KGen traces runtime type usage — the wrapper's type dependencies differ from the algorithm's. Critically, ControlParameters was NOT defined in the re-extracted kernel's ROSCO_Types.f90 because the wrapper doesn't access CntrPar fields directly.

4. **Git restore insufficient:** `git checkout 0e9313d -- kernel/ForeAftDamping/` restored the committed .f90 source files and state files. But the Makefile was NEVER committed to git (excluded by gitignore or git add pattern). The Makefile on disk was from the broken re-extraction, with wrong ALL_OBJS and missing dependency rules.

5. **Double-patching corruption:** Even if the Makefile had been correct, `vit verify` would have double-patched it — adding VIT rules to an already-patched Makefile. The CXX compilation rules, populator rules, and bridge rules from the first verify run would be re-added, creating duplicate entries or corrupted rules.

### The Three Interleaved Issues

1. **Re-extraction from integrated source** corrupts the kernel's type definitions (KGen captures wrapper dependencies, not algorithm dependencies)
2. **Makefile not in git** means golden fixtures are incomplete (can't be fully restored from version control)
3. **`vit verify` not idempotent** means running verify twice on the same kernel corrupts the Makefile and Fortran patches

## Fixes Applied

### Fix 1: Generalized callee detection with artifact filtering

`detect_callees()` in `kernel_verify.py` now detects ALL project callees, filtered to exclude:
- Names ending in `_c` (VIT C entry points)
- Names starting with `vit_` (VIT-generated populators, bridges)
- Fortran intrinsics (WRITE, READ, SIZE, MIN, MAX, etc.)

Per-callee try/except skips callees whose signatures can't be bridged (e.g., CHARACTER arguments).

### Fix 2: Backup/restore for idempotent verification

New `_backup_or_restore_kgen_originals()` function at the start of `setup_kernel_cpp()`:

```python
def _backup_or_restore_kgen_originals(kernel: Path) -> None:
    # Makefile
    if makefile.exists() and not makefile_backup.exists():
        shutil.copy2(makefile, makefile_backup)      # First run: save original
    elif makefile_backup.exists():
        shutil.copy2(makefile_backup, makefile)       # Subsequent run: restore original

    # Fortran source files (skip VIT-generated vit_*.f90)
    for f90_file in sorted(kernel.glob("*.f90")):
        if f90_file.name.startswith("vit_"):
            continue
        backup = f90_file.with_suffix(".f90.kgen")
        if not backup.exists():
            shutil.copy2(f90_file, backup)            # First run: save original
        else:
            shutil.copy2(backup, f90_file)            # Subsequent run: restore original
```

On first run: saves `Makefile.kgen` and `*.f90.kgen` backups of original KGen output.
On subsequent runs: restores from backups before VIT patches, ensuring fresh clean patching.

Result: `vit verify` is now fully idempotent. Running it twice produces 40/40 both times.

### Fix 3: Re-extract ForeAftDamping from clean source

1. Reset Controllers.f90 to clean pre-integration state (fde0498)
2. Re-extracted ForeAftDamping — KGen captured full ControlParameters definition
3. Verified 40/40 (first run creates .kgen backups)
4. Verified 40/40 again (second run restores from backups — idempotency proof)
5. Restored integrated Controllers.f90
6. Committed fixture with .kgen backups to git

## Key Insights

### KGen's selective type capture is source-dependent

KGen doesn't capture all types in a function's signature. It traces runtime access patterns and captures only types whose fields are actually referenced in the function body. When the body is the original algorithm (reads `CntrPar%FA_KI`), KGen includes ControlParameters. When the body is a VIT wrapper (calls `foreaftdamping_c(C_LOC(CntrPar_view))`), KGen captures the wrapper's dependencies instead — and ControlParameters may be missing because the wrapper accesses it only through C_LOC (an opaque pointer operation).

This means: **extraction must always use clean (pre-integration) source files.** An integrated source file's function body is a wrapper, not the algorithm. KGen captures the wrapper's type dependencies, which are wrong for verification.

### Golden fixtures must include .kgen backups

The Makefile and .f90 files get patched by `vit verify`. Without backups of the original KGen output, there's no way to re-run verification cleanly. The `.kgen` files serve as the authoritative "KGen produced this" state that VIT's patching restores from.

### Verification idempotency is essential, not optional

The failure mode was: re-running `vit verify` on a golden fixture corrupts it. This happens in normal workflows — debugging a translation failure, running regression tests, testing VIT code changes. The backup/restore mechanism ensures every run starts from clean KGen state, making corruption impossible.

## Commits

**VIT repo:**
- `26f93ca` — Generalize callee bridges to ALL callees (with artifact filtering + per-callee error handling)
- `f235cce` — Make vit verify idempotent via backup/restore of original KGen files

**ROSCO repo:**
- `2912a65` — Re-extract ForeAftDamping golden fixture from clean source with .kgen backups

## Status After Fixes

- ForeAftDamping: 40/40, idempotent (passes on repeated runs)
- PIController regression: 40/40
- 501 unit tests pass
- Callee bridge system handles ALL callees (translated and untranslated)
- Golden fixtures include .kgen backups for clean verification
- Phase 7B is unblocked — extraction protocol and verification idempotency are established
