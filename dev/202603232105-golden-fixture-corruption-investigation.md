# Golden Fixture Corruption: Root Cause Analysis
**Date:** 2026-03-23
**Status:** in-progress (investigation complete, fix needed)
**Scope:** KGen kernel golden fixtures, verification pipeline integrity

## What Happened

During Phase 7B callee bridge generalization work, ForeAftDamping's golden kernel fixture was corrupted by an inadvertent re-extraction from integrated source. The integrated Controllers.f90 has a VIT wrapper (not the original algorithm), so KGen captured the wrapper's type dependencies instead of the original function's types. ControlParameters was missing from the re-extracted kernel's ROSCO_Types.f90.

Restoring from git (`git checkout 0e9313d -- kernel/ForeAftDamping/`) recovered the committed fixture, but `vit verify` still fails because the committed fixture is POST-PATCHED (from Step 9's successful verification), and running `vit verify` again tries to re-patch an already-patched kernel.

## Two Distinct Issues

### Issue 1: Re-extraction from integrated source produces wrong kernel

**Root cause:** ROSCO's Controllers.f90 on disk is integrated (has ForeAftDamping's C++ wrapper). Extraction captures the wrapper body, not the original algorithm. KGen's selective type capture produces different results for the wrapper vs the original.

**Already documented:** CLAUDE.md says "Extraction requires clean (pre-integration) source files." The protocol for clean extraction is well-documented. This was a process error (re-extracting when we shouldn't have), not a code error.

**Prevention:** Never re-extract over golden fixtures unless extraction logic has changed. Use `--kernel-dir` to point at existing fixtures.

### Issue 2: Golden fixtures store post-patch state, but `vit verify` re-patches

**Root cause:** The golden fixtures committed to git include VIT's Makefile patches, struct headers, bridge files, and wrapper code from `setup_kernel_cpp()`. When `vit verify` runs again on the same fixture, it applies the same patches to already-patched files:
- Makefile gets VIT variables/rules added twice
- The ALL_OBJS list may be corrupted
- KGen-original files (ControllerBlocks.o, Filters.o, etc.) that were in the original Makefile but not in VIT's patched version are missing

This is why the restored fixture can't build — the Makefile was committed in VIT-patched state and is being re-patched.

**The design expectation was:** Golden fixtures are PRE-PATCH (raw KGen output). `vit verify` patches them fresh each time. But we committed POST-PATCH state because Step 9 ran `vit verify` (which patched) and then we committed the result.

## Options

### Option A: Store pre-patch golden fixtures

Always commit the raw KGen output (before `vit verify` patches). `vit verify` patches fresh each time. This means:
- Fixtures contain only KGen-generated files (no VIT headers, bridges, etc.)
- `vit verify` is idempotent — can run multiple times safely
- Requires re-extracting to get clean fixtures, OR stripping VIT artifacts before committing

### Option B: Make `vit verify` idempotent on patched kernels

Make `setup_kernel_cpp()` detect that the kernel is already patched and skip re-patching. Check for VIT marker files or comments. This is fragile but preserves the current workflow.

### Option C: Store both states

Keep pre-patch and post-patch versions. `vit verify` checks if already patched and uses the existing state. More storage but most robust.

### Recommendation: Option A

Store pre-patch fixtures. This is cleaner and matches the intent: golden fixtures are raw KGen output that `vit verify` can operate on. The current corruption happened because we violated this invariant by committing post-patch state.

For existing fixtures (the 19 committed post-patch kernels): these work fine because `vit verify` was only run once on each. The issue only surfaces when re-running verify on the same fixture. For regression testing, we re-run verify each time — which has been working because the existing functions' simpler Makefile patches are more forgiving of double-patching.

The ForeAftDamping fixture is the first that fails on double-patching because it has more complex Makefile rules (populator, callee bridges, dependency chains).

## Next Steps

1. Fix the immediate blocker: make ForeAftDamping verifiable again
   - Either clean the fixture to pre-patch state
   - Or make setup_kernel_cpp idempotent for the critical patching steps
2. Establish the invariant: golden fixtures = pre-patch KGen output
3. Then proceed with Phase 7B
