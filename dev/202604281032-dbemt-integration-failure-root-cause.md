# DBEMT ComputeTau2 Integration — Root Cause and Fix
**Date:** 2026-04-28
**Status:** completed
**Scope:** Integration architecture, translation bug, vectorization fix

## Summary

Integrating ComputeTau2 (20/20 IDENTICAL KGen kernel) into DBEMT.f90 caused `ad_MHK_RM1_Fixed_UA` to diverge catastrophically (max_abs=5.73e+06). Two root causes identified and fixed. **17/17 baselines now pass bit-identical.**

## Root Cause 1: Translation Bug — Operator Precedence

The C++ translation used `0.26 * spanRatio * spanRatio` which C++ evaluates left-to-right as `(0.26 * spanRatio) * spanRatio`. The Fortran `0.26*spanRatio**2` computes `spanRatio**2` first, then multiplies by 0.26. The different intermediate rounding produces 1-3 ULP differences in `k_tau` for certain `spanRatio` values.

**Fix:** `0.26 * (spanRatio * spanRatio)` — parentheses force Fortran-equivalent evaluation order.

**Why KGen missed it:** The 20 captured invocations happened to use spanRatio values that don't produce a ULP difference, or the difference didn't propagate to the verified fields in UpdateStates. The mismatch manifests at specific node indices (26, 27, 30) with specific spanRatio values.

**Lesson:** Fortran `x**2` is NOT the same as C++ `a * x * x` when `a` is a preceding coefficient. The exponentiation binds tighter than multiplication in Fortran, so `a*x**2` means `a * (x*x)`. In C++, `a * x * x` means `(a*x) * x`. Always parenthesize: `a * (x * x)`.

## Root Cause 2: Vectorization Divergence

After fixing the translation bug, `ad_MHK_RM1_Fixed_UA` still failed. Assembly comparison revealed:
- **Clean source:** gfortran inlines ComputeTau2 → loop body is large → NOT vectorized → scalar `bl exp`
- **Integrated source:** ComputeTau2 is a wrapper call → loop body is small → vectorized → SIMD `bl _ZGVnN2v_exp`

The glibc libmvec SIMD `exp()` and scalar libm `exp()` produce different results (1 ULP) for certain inputs. The DBEMT exponential filter feedback loop amplifies this through 600 UA-coupled timesteps.

**Fix:** `set_source_files_properties(src/DBEMT.f90 PROPERTIES COMPILE_FLAGS "-fno-tree-loop-vectorize")` in CMakeLists.txt. This ensures consistent scalar `exp()` regardless of whether function bodies are present or replaced by wrappers.

**Why this is the same issue as kernel verification:** Dev note `202604260357` documented the identical scalar-vs-SIMD exp divergence in KGen kernel verification. The kernel fix was `-fvect-cost-model=unlimited` (force vectorization in kernels to match production). The production fix is `-fno-tree-loop-vectorize` (prevent vectorization from changing when wrappers are inserted).

## Investigation Timeline

1. Initial attempt: integration → 16/17 pass, `ad_MHK_RM1_Fixed_UA` diverges (max_abs=5.73e+06)
2. NREL regression: passes at 1e-2 → physics correct, not a fundamental error
3. Assembly comparison: clean has 2x `bl exp` (scalar), integrated has 2x `bl _ZGVnN2v_exp` (SIMD)
4. Tried `-fno-tree-loop-vectorize` → still fails (5.56e+06)
5. Tried `-fno-inline` → still fails
6. Tried `-O2` → still fails
7. Added dual-computation debug prints → found 7,833 k_tau/tau2 mismatches between Fortran and C++
8. Identified `0.26 * spanRatio * spanRatio` vs `0.26*spanRatio**2` operator precedence bug
9. Fixed parentheses → zero mismatches, but baselines still fail (vectorization)
10. Applied `-fno-tree-loop-vectorize` + parentheses fix → **17/17 pass bit-identical**

## Files Changed

- `openfast/translations/DBEMT/computetau2.cpp` — parentheses fix
- `openfast/modules/aerodyn/CMakeLists.txt` — `-fno-tree-loop-vectorize` on DBEMT.f90
- `openfast/baselines/aerodyn/` — all 17 regenerated from clean with new flag
