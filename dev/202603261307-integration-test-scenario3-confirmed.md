# Integration Test Re-Run: Scenario 3 Mismatch Confirmed as Pre-Existing
**Date:** 2026-03-26
**Status:** completed
**Scope:** Integration test execution, Scenario 3 non-determinism confirmation

## What Was Done

Ran the full integration test exactly as documented in `ROSCO/INTEGRATION_TEST.md`:
1. Reset sources to clean Fortran (dba7738, fde0498, e8010f0, stubs)
2. Applied K init bug fix
3. Verified clean state (0 `_c(` references, stubs)
4. Built clean Fortran baseline
5. Captured baseline arrays (6 scenarios, separate docker exec per scenario)
6. Integrated 27 functions (all 27 "Integration applied successfully")
7. Verified integration (18+12+24 `_c(` references, 27 `_cpp.cpp` in CMakeLists)
8. Clean rebuilt with C++
9. Captured integrated arrays (6 scenarios, separate docker exec per scenario)
10. Compared all arrays

## Results

| Scenario | Result |
|----------|--------|
| 1 (standard, 1000s) | ALL IDENTICAL (200,000 values) |
| 2 (wrap_360, 100s) | ALL IDENTICAL (12,000 values) |
| **3 (mode coverage, 400s)** | **MISMATCH** (gen_torque, bld_pitch, gen_speed, gen_power) |
| 4 (PIIController, 100s) | ALL IDENTICAL (20,000 values) |
| 5 (ResController, 400s) | ALL IDENTICAL (80,000 values) |
| 6 (NotchFilterSlopes, 100s) | ALL IDENTICAL (20,000 values) |

## Scenario 3 Mismatch: Confirmed Pre-Existing

The Scenario 3 gen_torque MD5s from this run:
- Baseline: `0f297bd1ad37d0e6f580a562ce29cf81` — the **outlier** state (29% probability)
- Integrated: `e078402d620bd512b407fe261545cc71` — the **normal** state (71% probability)

These are exactly the two values from the 100-run pure-Fortran study (dev note `202603261254`). The baseline happened to land on the outlier state, the integrated on the normal state. This is the pre-existing ROSCO non-determinism, not a translation difference.

## What This Means

- **Scenarios 1, 2, 4, 5, 6: integration proven correct** — 332,000 float64 values byte-identical between pure Fortran and 27-function integrated C++
- **Scenario 3: inconclusive this run** — the non-determinism makes byte-identical comparison unreliable (~41% chance of mismatch per test run, regardless of translation correctness)
- The INTEGRATION_TEST.md document needs to be updated to account for Scenario 3 non-determinism

## Related Dev Notes

- `202603261254-scenario3-nondeterminism-100-run-study.md` — 100-run study establishing the two-state flip
- `202603261122-definitive-integration-test-412k-values.md` — earlier test that passed (by chance for Scenario 3)
