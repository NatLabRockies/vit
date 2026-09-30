# ComputeUASeparationFunction_onCl + Callee Bridge Re-Sync + integrate_all.sh
**Date:** 2026-04-25
**Status:** completed
**Scope:** AeroDyn Phase 1, VIT callee bridge infrastructure

## Summary

Integrated the 9th AirfoilInfo function (ComputeUASeparationFunction_onCl), discovered and fixed a callee bridge view-struct desynchronization bug, cleaned up a rabbit hole, built `integrate_all.sh`, and established the correct verification workflow.

## ComputeUASeparationFunction_onCl

First callee-dependent function (calls `ComputeUASeparationFunction_zero`, which is already integrated). The C++ translation implements the Kirchhoff separation function model: computes `f_st`, `cl_fs`, and `cl_fa` from the Cl-based formulation, then calls `ComputeUASeparationFunction_zero` to enforce monotonicity at the boundaries.

- **Verification:** 20/20 IDENTICAL on clean source
- **Integration:** Standard (no `--reverse-copy` needed — scalars are modified by the callee via the bridge, not directly by this function)
- **Regression:** 16/16 baselines pass

## Infrastructure Fix: Callee Bridge INOUT View Re-Sync

**Problem discovered during onCl verification.** When a callee bridge calls a Fortran function that modifies INOUT view-type scalars (e.g., `ComputeUASeparationFunction_zero` modifies `p%UA_BL.alphaBreakUpper`), the Fortran function modifies the original Fortran type via the stash — but the C++ caller's view struct still has the old values. When the kernel wrapper's final reverse-copy runs, it copies the stale view values back, overwriting the callee's correct modifications.

**Fix:** After the Fortran call in the bridge, re-sync the view from the updated Fortran type using `C_F_POINTER` + `vit_populate`. Only for INOUT/OUT view args — IN-only args must not be re-synced (the `INTENT(OUT)` populator zeros the struct, corrupting the view mid-execution).

**Generated bridge code (for INOUT callees):**
```fortran
CALL ComputeUASeparationFunction_zero(vit_original_afi_table_type, ColUAf, cn_cl(1:n_cn_cl))
! Re-sync view struct from Fortran type after callee modified it
CALL C_F_POINTER(p, p_view)
CALL vit_populate_afi_table_type(vit_original_afi_table_type, p_view)
```

**Impact:** Any function that calls a callee with INOUT view-type args now gets correct behavior. The callee's scalar modifications propagate through the view to the final reverse-copy.

## Infrastructure Fix: Stale Bridge File Cleanup

**Problem:** `_backup_or_restore_kgen_originals` restores KGen-generated files from `.kgen` backups but skips `vit_` files. Callee bridge files (`vit_bridge_*.f90`) persisted from previous runs with potentially wrong signatures. The bridge generator should overwrite them, but if callee detection fails (e.g., integrated source has wrapper bodies), no bridge is generated and the stale file is used.

**Fix:** Delete all `vit_bridge_*.f90` files during backup/restore. They're always regenerated fresh by `_generate_callee_bridges`.

## Rabbit Hole: .kgen Backup Callee Detection (REVERTED)

Attempted to make `vit verify` work on integrated source by reading callee names and signatures from the kernel's `.kgen` backup files. This would have eliminated the need for clean source during verification.

**Why it was reverted:**
1. Callee detection from `.kgen` worked, but callee signature parsing still needed the live source for type registry building
2. The type registry for the callee didn't have `view_strategy='view'` when parsed from `.kgen` files (no vit.yaml context)
3. The approach added complexity for a use case we shouldn't support — the documented workflow is to use clean source

**Lesson:** The documented requirement ("Requires clean source") exists for good reason. Trying to work around it created a cascade of progressively deeper fixes. The right answer was to follow the workflow.

## integrate_all.sh

Created `openfast/scripts/integrate_all.sh` — integrates all 9 translated functions in dependency order from clean source. Prevents the stale `.cpp` stub problem that caused a broken commit earlier in the session (commit `981b98c80` had stubbed `.cpp` files instead of real production wrappers).

**Canonical workflow:**
```bash
bash scripts/reset_to_clean.sh
bash scripts/integrate_all.sh           # 9/9 passed
cd build && cmake --build . --target aerodyn_driver -j$(nproc)
bash scripts/verify_aerodyn_baselines.sh all  # 16/16 pass
```

**Dependency order in the script:**
1. Leaf functions (Calculate_Cn, FindBoundingTables, Compute_iLoweriUpper, ComputeUA360_CnOffset, Calculate_C_alpha)
2. Reverse-copy functions (ComputeUASeparationFunction_zero with `--reverse-copy`)
3. Callee-calling functions (ComputeUA360_updateCnSeparated, ComputeUA360_updateSeparationF)
4. Callee-dependent functions (ComputeUASeparationFunction_onCl)

## Other Fix: vit_translated.h Self-Contained Include

The existing `vit_translated.h` was created before the auto-include fix and was missing `#include "vit_types.h"`. Added manually. New files created by `vit integrate` will have it automatically (from the `_apply_header_patch` fix in `688104c`).

## Other Fix: Translation Files Should NOT Include vit_translated.h

Earlier in the session, I added `#include "vit_translated.h"` to the translation source files for `computeua360_updatecnseparated.cpp` and `computeua360_updateseparationf.cpp`. This broke kernel verification (the kernel doesn't have `vit_translated.h` — it uses `vit_kernel_callees.h`). Removed. The production wrapper auto-includes `vit_translated.h` at integration time; translation files should only include `vit_types.h`.

## Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `6854838` | Callee bridge INOUT view re-sync + stale bridge cleanup |
| OpenFAST | `7ff6dd72d` | 9th function integrated, all production .cpp files fixed |
| OpenFAST | `b3afeb111` | integrate_all.sh script |

## State

9 AirfoilInfo functions integrated. Two remaining before the orchestrator:
- ComputeUA360_AttachedFlow (calls Compute_iLoweriUpper + unknown fZeros)
- CalculateUACoeffs (orchestrator, calls most of the above)
