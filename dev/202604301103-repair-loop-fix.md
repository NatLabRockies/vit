# Kernel Repair Loop — Off-by-One + Non-Idempotent Fix
**Date:** 2026-04-30
**Status:** completed
**Scope:** VIT kernel_verify.py `_repair_kernel_build`

## Problem

`vit verify DBEMT_UpdateStates` failed with "Reached max iterations (5)" even though the kernel was correct. Building the kernel manually with `make build` succeeded, and all 20 cases passed IDENTICAL.

## Root Cause

Two bugs compounding:

**Bug 1 — Off-by-one in the repair loop.** The loop ran N iterations of `{build, fix}`. Iteration 5 (the last) applied a fix but the loop ended before trying a 6th build to verify it. The fix was correct — the kernel just needed one more build attempt.

The DBEMT_UpdateStates kernel is a cross-module extraction (BEMT.f90 + DBEMT.f90 + full NWTC Library chain). It needed 5 repair iterations to resolve cascading dependencies: `CHARACTER(ErrMsgLen)` constants in 3 files, undefined symbols for KGen state variables, and a linker reference to `SetErrStat`. Each iteration could only fix errors from files that successfully compiled, so deeply-nested dependencies required multiple passes.

**Bug 2 — `_fix_undefined_symbol` was not idempotent.** The PARAMETER path inserted `INTEGER, PARAMETER :: symbol = value` without checking if the declaration already existed. If the same symbol triggered the same error twice, it would insert a duplicate declaration, causing a new compile error — potentially looping forever. This made it unsafe to simply remove the iteration cap.

The READ path and the other two fix functions (`_fix_missing_constant`, `_fix_link_undefined`) were already idempotent — they check for existing declarations/content before modifying.

## Fix

1. **Made `_fix_undefined_symbol` idempotent.** Added a duplicate guard on the PARAMETER path:
   ```python
   if re.search(rf'PARAMETER\s*::\s*{symbol}\b', content, re.IGNORECASE):
       return False
   ```

2. **Removed the `max_iterations` cap.** Changed `for iteration in range(max_iterations)` to `while True`. The loop now terminates naturally via two existing exit conditions:
   - `build.returncode == 0` — build succeeded
   - `fixes_applied == 0` — build failed with no fixable errors

   With all fix functions idempotent, the loop always converges: each iteration either reduces the error count or returns (no fixes possible). No artificial cap needed.

## Verification

- VIT tests: 644/644
- DBEMT_UpdateStates kernel: `vit verify` now succeeds — 5 repair iterations, build succeeds on iteration 6, 20/20 IDENTICAL
