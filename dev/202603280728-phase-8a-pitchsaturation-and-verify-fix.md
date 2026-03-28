# Phase 8A: PitchSaturation Translation + Verification Regression Fix
**Date:** 2026-03-28
**Status:** completed
**Scope:** Phase 8A (PitchSaturation + PitchControl unblock), verify_all.sh rewrite, root cause analysis of verification failures

## What Was Done

### PitchSaturation Translation (Tier 1 / Phase 8A)

PitchSaturation is the first ControllerBlocks.f90 function translated. It was the blocking dependency preventing PitchControl integration since Phase 7B.

**The function:** 28 lines. Looks up a minimum blade pitch angle via `interp1d` on a peak shaving wind speed table, then returns `max(PS_Min_Pitch, PRC_Min_Pitch)`. Structurally identical to FloatingFeedback (FUNCTION returning REAL(DbKi), 5 standard type args).

**Pipeline:**
- Extracted from `Controllers.f90:87` (call site inside PitchControl, gated by `PS_Mode > 0`), Scenario 3
- Translated via `vit translate` scaffold + manual implementation
- Verified: 21/21 passed
- Integrated into ControllerBlocks.f90
- PitchControl then integrated (previously blocked)
- Full integration test: 29/29, 612,000 values ALL IDENTICAL across 8 scenarios

**New directory:** `translations/ControllerBlocks/` — first ControllerBlocks translation. Pattern: defined in ControllerBlocks.f90, extracted from call site in Controllers.f90, integrated into ControllerBlocks.f90.

### Verification Regression Debugging

After the integration test passed, running `verify_all.sh` produced widespread failures: 23/29 functions failed with "no cases found". This consumed most of the session. The investigation went through several wrong hypotheses before finding the real cause.

#### Wrong Hypotheses (and why they were wrong)

1. **"Parallel race condition on kgen_statefile.lst"** — Each function has its own kernel directory, so there's no shared state file. The race condition theory assumed shared directories, which was incorrect.

2. **"Zombie verify process from killed run"** — We did find a zombie `verify_all.sh` process (PID 96656) from an earlier killed run. Killing it didn't fix the problem. The zombie was interfering (competing for kernel directories), but it wasn't the root cause — the failures persisted after all zombies were killed.

3. **"Colima filesystem mount latency"** — `git checkout -- kernel/` on Mac doesn't propagate instantly to the container. This was a real contributing factor (some functions saw stale files), but not the primary cause. Even with 5-second waits after checkout, failures persisted.

4. **"Stale build artifacts (.o, .mod) from killed runs"** — We added cleanup to verify_all.sh to remove `.o`, `.mod`, `.exe`, `.csv` files. This helped but didn't fix the failures. The pattern was deterministic (same functions always failed), not random as expected from stale artifacts.

5. **"Cleanup script deleting tracked golden fixture files"** — The cleanup script used `rm -f *.h vit_*.f90` which removed tracked struct headers and view populators. The script then called `git checkout -- kernel/` to restore them, but **git is not installed in the container** (`bash: git: command not found`). The `2>/dev/null` silenced the error. This was a real bug we introduced, but still not the root cause — even after fixing the cleanup to only remove `.o`/`.mod`/`.exe`/`.csv`, the failures persisted.

#### Actual Root Cause

**`vit verify` requires clean (pre-integration) ROSCO source files.** The source was left in the integrated state (with `_c()` wrappers) after the integration test.

VIT's `setup_kernel_cpp()` scans the ROSCO source directory (`rosco/controller/src/`) to detect translated callees and generate callee bridges. For example, ForeAftDamping calls PIController — VIT scans Controllers.f90 to find the PIController definition, cross-references it against `vit.yaml` translations, and generates a `vit_bridge_picontroller.f90` callee bridge in the kernel.

When Controllers.f90 has integration wrappers, the original function bodies are replaced with `_c()` call wrappers. VIT's callee detection fails because:
- The original function signatures are modified
- The `CALL PIController(...)` in ForeAftDamping's source may reference the wrapper instead of the original
- The callee bridge isn't generated, so `picontroller_c` is an undefined symbol at link time

The build fails with `undefined reference to 'picontroller_c'`, the kernel.exe isn't produced, and VIT reports "no cases found".

**Why did it work last session?** In the previous session, verification was always run after `reset_to_clean.sh` (as part of the clean build test flow). The source was always clean when verification ran. This session, we ran the integration test first (which applies wrappers to the source), then tried to run verification without resetting — exposing the dependency.

**Why did simple functions pass?** Functions like saturate, wrap_180, identity don't call other translated functions — they have no callees. No callee bridge is needed, so the integrated source doesn't affect them. Functions with callee bridges (ForeAftDamping→PIController, PitchControl→PitchSaturation, etc.) all failed.

#### Fix

1. **Added source check to verify_all.sh:** The script now checks for `_c(` in Controllers.f90 and exits with a clear error message if integration wrappers are detected.

2. **Documented prerequisite:** CLAUDE.md and verify_all.sh both document that clean source (`reset_to_clean.sh`) is required before verification.

3. **Cleanup step:** verify_all.sh removes `.o`/`.mod`/`.exe`/`.csv` from kernel directories before verification. This prevents stale compiler outputs from causing issues, but is not sufficient without clean source.

4. **No git dependency:** The cleanup step does NOT use `git checkout` (git is not installed in the container). It only removes compiler outputs that are regenerated by each build.

### verify_all.sh Rewrite

The script was rewritten to support batch mode for parallel execution:

```bash
bash scripts/verify_all.sh      # All 29 sequentially
bash scripts/verify_all.sh 1    # Batch 1: Functions + LPFilter (10)
bash scripts/verify_all.sh 2    # Batch 2: Filters + Controllers part 1 (10)
bash scripts/verify_all.sh 3    # Batch 3: Controllers part 2 + ControllerBlocks (9)
```

The `-j N` parallel approach was removed — it caused issues with Colima FS mount timing. Instead, Claude launches 3 separate `docker exec` calls in parallel, each running one batch. Each batch is sequential internally.

**Note on in-script parallelism:** The `bash -j N` approach using `wait -n` and background processes failed because:
- Colima's virtiofs mount has propagation delays when files are modified on Mac side
- `git checkout` on Mac doesn't instantly propagate to container filesystem
- Multiple `vit verify` processes in a single `docker exec` share the same process space, and killed processes leave zombies

The 3-batch pattern (3 separate `docker exec` = 3 separate OS processes) avoids all of these issues.

## Results

- PitchSaturation: extracted (21 cases), translated, verified (21/21), integrated
- PitchControl: unblocked, integrated (was verified in Phase 7B)
- Integration test: 29/29 passed, 612,000 values ALL IDENTICAL
- Verification regression: 29/29 passed (with clean source)
- verify_all.sh: rewritten with cleanup, source check, batch mode

## Key Lesson

**The most dangerous bugs are the ones that only appear when you change your workflow.** Verification always worked because it was always run with clean source — not because the dependency on clean source was explicit. The integration test changes the source, and nobody had tried running verification after integration before. The dependency was implicit and undocumented until it broke.

This is the same pattern from last session's "40/40 was never 40 cases" discovery: correct-looking results masked an unexamined assumption. Here, "28/28 passed" in previous sessions was true, but only because the workflow accidentally satisfied an undocumented precondition.
