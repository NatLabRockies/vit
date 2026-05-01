# Fixing --auto-allocate: From Hand-Written Wrapper to Automated Init Integration
**Date:** 2026-05-01
**Status:** completed
**Scope:** VIT infrastructure (allocate_extract.py, interface_gen.py), DBEMT_Init integration

## Picking Up From Dev Note 202605010708

That note identified the problem: DBEMT_Init had a hand-written Fortran wrapper because VIT's standard wrapper crashes on INTENT(OUT) types with ALLOCATABLE fields. The wrapper duplicated ~60 lines of allocation logic, broke integrate_all.sh automation, and wouldn't scale to other init functions. The note listed four possible solutions.

## What We Found

VIT already had the infrastructure to solve this — it just wasn't wired up:

- `--auto-allocate` CLI flag existed on `vit integrate` (cli.py:116)
- `allocate_extract.py` could parse ALLOCATE statements from source into structured `AllocationInfo` records
- `interface_gen.py:398-415` accepted allocations and emitted ALLOCATE code before view populate

The feature was built but had three gaps that prevented end-to-end operation.

## Three Gaps Fixed

### Gap 1: Conditional allocation guards

`extract_allocations` captured ALLOCATEs but not their enclosing IF conditions. DBEMT_Init conditionally allocates `p%spanRatio` (Mod=1/3) and `OtherState%n` (Mod=3 only).

**Fix:** Added `guard_expr` field to `AllocationInfo`. New `_find_guard_for_line()` walks backwards from the ALLOCATE line, tracking IF/END IF depth, to find the enclosing condition. `interface_gen.py` emits `IF (guard) THEN ... ALLOCATE ... END IF`.

### Gap 2: Error handling

The emission was bare `ALLOCATE(...)` with no error checking. Original Fortran uses `STAT=ErrStat2` + `SetErrStat` + `RETURN`.

**Fix:** Added `error_msg` field to `AllocationInfo` (extracted from the SetErrStat call following each ALLOCATE). `interface_gen.py` now emits the full error-handling pattern and declares `ErrStat2`, `ErrMsg2`, `RoutineName` locals.

### Gap 3: Initialization-via-CALL (deep copy)

DBEMT_Init calls `DBEMT_CopyContState(x, OtherState%xdot(i), MESH_NEWCOPY, ...)` which allocates the destination as a side effect. This isn't an ALLOCATE statement — `extract_allocations` missed it.

**Fix:** Added `_scan_copy_allocates()` to detect CALL statements where an argument matches `arg%field(...)` and `arg` is a function parameter. Captures the full CALL with its enclosing DO loop. `interface_gen.py` emits it in the wrapper with renamed loop variable (`vit_alloc_i`) to avoid conflicts.

**Important:** The initial implementation used OpenFAST-specific patterns (`_Copy` naming, `MESH_NEWCOPY` constant). These were removed in a follow-up — the detection is now fully generic: any CALL that passes `arg%field` where `arg` is a function parameter.

## Result

DBEMT_Init now integrates via:
```bash
vit integrate DBEMT_Init ... --reverse-copy --auto-allocate
```

No hand-written wrapper. integrate_all.sh uses this command directly. Full cycle verified: 25/25 integrate + 17/17 baselines.

The auto-generated wrapper contains:
- 5 ALLOCATE statements with conditional guards and error handling
- 1 Registry Copy call with DO loop
- View populate calls (only for types that need views)
- C++ call for computation
- Reverse-copy for scalar fields

All extracted automatically from the original Fortran source.

## Files Changed

| File | Change |
|------|--------|
| `vit/vit/allocate_extract.py` | guard_expr, error_msg, copy_call fields; _find_guard_for_line, _find_error_msg, _scan_copy_allocates functions; generic copy detection |
| `vit/vit/interface_gen.py` | Emit guards, error handling, copy calls, allocation locals; import re |
| `openfast/scripts/integrate_all.sh` | `--auto-allocate` for DBEMT_Init |
| `openfast/modules/aerodyn/src/DBEMT.f90` | Auto-generated wrapper replaces hand-written |

## Impact on AFI_Init

AFI_Init has a more complex two-pass pattern (pass 1 returns sizes, Fortran allocates, pass 2 fills). The current `--auto-allocate` handles the simpler single-pass case (all sizes known from inputs). AFI_Init's two-pass pattern may need additional work — the allocation sizes come from C++ computation, not directly from input parameters. This is a separate issue to address when AFI_Init is revisited.
