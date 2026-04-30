# DBEMT_InitStates_AllNodes — Phase C Complete
**Date:** 2026-04-30
**Status:** completed
**Scope:** DBEMT Phase C, test-validate for zero-coverage functions

## Context

DBEMT_InitStates_AllNodes is an 18-line PUBLIC orchestrator — a double loop calling the already-translated DBEMT_InitStates per (i,j) node. It's the second and final Phase C function. With this, all 10 DBEMT functions are in C++.

## Zero Coverage Problem

The function has zero gcov coverage across all 17 test cases. It's called from `AD_InitVars` (AeroDyn initialization), but the call path goes through `BEMT_InitStates` which is guarded by `(.not. OtherState%BEMT%nodesInitialized)`. By the time this code runs, the per-node `DBEMT_InitStates` has already been called through `DBEMT_UpdateStates` during timestepping, so nodes are already initialized.

The function exists for the full OpenFAST coupled solver's linearization path, where Jacobians are computed before timestepping initializes nodes. The AeroDyn driver never exercises this path with DBEMT enabled.

This means:
- KGen extraction impossible (no state data to capture)
- Baselines pass trivially (function never called)
- A duplicate test case won't help (structural initialization order, not configuration)

## Verification: Custom Fortran Test Harness

Since `vit test-validate`'s generated bridge uses `C_F_POINTER` which doesn't work for view types with ALLOCATABLE fields (different memory layout than Fortran derived types), we wrote a custom Fortran test program that:

1. Creates real Fortran DBEMT types with test data (using Registry's `ALLOCATE` patterns)
2. Deep-copies via `DBEMT_CopyContState` / `DBEMT_CopyOtherState` (Registry routines)
3. Calls original Fortran `DBEMT_InitStates_AllNodes` on the original set
4. Populates view structs from the copy, calls C++ `dbemt_initstates_allnodes_c`, reverse-copies
5. Compares field-by-field: `x%element(i,j)%vind`, `vind_1`, `OtherState%areStatesInitialized`

**4 test cases, all PASS (C++ ≡ Fortran):**
1. All nodes uninitialized (3 blades × 5 nodes, DBEMT_Mod=2) — verifies full grid initialization
2. Partial initialization (node (1,1) pre-initialized) — verifies skip-if-initialized logic
3. Continuous mode (2 × 3, DBEMT_Mod=3) — verifies k_0ye scaling of vind_1
4. Edge case (1 × 1 grid) — verifies minimal grid

## Fortran LOGICAL Gotchas

- `areStatesInitialized` is `LOGICAL` in Fortran but `int32_t` in the C view struct
- Fortran comparison: `.NEQV.` not `/=` for LOGICAL types
- Assignment: `.TRUE.` / `.FALSE.` not `1` / `0` (gfortran extension warning)

## Files

- `translations/DBEMT/test_dbemt_initstates_allnodes.f90` — Fortran test harness
- `translations/DBEMT/Makefile.test_allnodes` — Build against production libraries
- `translations/DBEMT/dbemt_initstates_allnodes.cpp` — C++ translation (includes `_c` wrapper for test)

## Phase C Summary

Both orchestrators complete:
- **DBEMT_UpdateStates:** 20/20 KGen + 17/17 baselines. VIT infrastructure: fixed-size view-type array params, repair loop convergence.
- **DBEMT_InitStates_AllNodes:** 4/4 test-validate + 17/17 baselines. Zero coverage → custom Fortran test harness.

All 10 DBEMT functions now in C++ (5 leaves + 3 integrators + 2 orchestrators). Phase D (init/validation/end) is next.
