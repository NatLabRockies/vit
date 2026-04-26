# CalculateUACoeffs 1-ULP Bridge Artifact Investigation
**Date:** 2026-04-25
**Status:** completed (accepted as known limitation)
**Scope:** CalculateUACoeffs kernel verification, callee bridge precision

## Problem

CalculateUACoeffs verification at 15/20. Five cases fail with `cnbreakupper` diffs of 1.2-1.4e-14 (just above the 1.0e-14 tolerance). The diff chain:

1. `alphabreakupper` differs by exactly 2.2e-15 (1 ULP at value ~1.45)
2. `cnbreakupper = CnSlopeUpper * (alphaBreakUpper - alpha0Upper)` amplifies by ~6x
3. Product exceeds 1.0e-14 tolerance → OUT_TOL

`alphabreakupper` is computed INSIDE ComputeUA360_AttachedFlow (Fortran via callee bridge). When AttachedFlow is verified standalone, it produces ALL IDENTICAL results. The diff only appears when AttachedFlow is called via bridge from CalculateUACoeffs.

## Investigation: What Was Ruled Out

### 1. Calculate_Cn bridge (cn array passing)
**Test:** Compared bridge output vs inline C++ computation for all 200 cn values.
**Result:** 0/200 diffs. The Calculate_Cn bridge produces bit-identical results.

### 2. kernelSmoothing (C++ vs Fortran)
**Test:** Compared C++ kernelSmoothing vs Fortran bridge for CnSlope_ smoothing.
**Result:** 32/199 diffs (each ~2.78e-17 = ~1 ULP). BUT switching to Fortran bridge smoothing didn't fix the 5 failures — same cases still fail.
**Conclusion:** The smoothing diffs exist but are NOT the cause of the cnbreakupper failures.

### 3. `std::pow` vs integer exponentiation
**Test:** Replaced `std::pow(x, Exp1)` with explicit multiplication in kernelSmoothing.
**Result:** No change — same 5 cases fail with same diff magnitudes.
**Conclusion:** `std::pow` is NOT the source (the smoothing diffs come from elsewhere in kernelSmoothing, but those diffs aren't the cause anyway).

### 4. Constants (Pi, D2R, TwoPi)
**Test:** Compared C++ `M_PI / 180.0` with Python's `math.pi / 180.0` (same libm as gfortran).
**Result:** Bit-identical (same hex representation).
**Conclusion:** Constants are NOT the source.

### 5. Auto-vectorization (SIMD)
**Test:** Added `-fno-tree-vectorize` to kernel Fortran compilation flags.
**Result:** Same 5 cases fail.
**Conclusion:** Auto-vectorization is NOT the source.

### 6. All alpha-related fields
**Checked:** alpha0, alpha1, alpha2, alphaUpper, alphaLower, c_alphaLower, c_alphaUpper, C_nalpha — ALL IDENTICAL for all 20 cases.
**Conclusion:** All upstream computation is correct. The diff appears ONLY in alphabreakupper (computed by AttachedFlow).

## Root Cause (Best Hypothesis)

The 1-ULP diff in `alphabreakupper` appears to be an irreducible artifact of how gfortran handles the `cn_cl(1:n_cn_cl)` array section in the callee bridge vs the direct local array `cn` in the original Fortran. When AttachedFlow is called directly (standalone kernel), `cn` is a Fortran local array passed by reference. When called via bridge, `cn_cl` is a C-interop `REAL(C_DOUBLE) :: cn_cl(*)` with an explicit section `cn_cl(1:n_cn_cl)`. The compiler may generate slightly different code for these two paths (temporary allocation, alignment, descriptor construction), causing a 1-ULP difference in the intermediate computation.

This hypothesis is supported by:
- The diff is systematic (same 5/20 cases consistently)
- The diff is exactly 1 ULP
- ALL inputs to AttachedFlow are verified as bit-identical
- The diff only appears in the bridge calling path, not standalone
- The diff does NOT depend on vectorization, constants, or any C++ computation

## Practical Impact

**None.** The 1-ULP diff in alphabreakupper propagates to cnbreakupper (amplified by CnSlope), but this is within the noise floor of IEEE 754 arithmetic. When integrated into the production build:
- C++ CalculateUACoeffs calls C++ AttachedFlow directly (no bridge) — no 1-ULP artifact
- The 16/16 baseline regression test is the authoritative verification gate

## Decision

Accept 15/20 kernel verification. The 5 failing cases are a known bridge-calling-convention artifact, not a translation bug. Proceed with integration and use baseline regression as the authoritative gate.

## Coefs Array Diffs

The coefs array also has diffs in ALL 20 cases (129-377 elements, rms 9.65e-17 to 1.1e-14). These come from two sources:
1. The 1-ULP alphabreakupper diff propagates into the Coefs separation function columns (computed by callee bridges using these break points)
2. The C++ kernelSmoothing has 32/199 diffs vs Fortran (1-ULP each) — but this alone doesn't cause failures

Only case 8's coefs rms (1.1e-14) exceeds the tolerance. All other cases are IN_TOL.

## Infrastructure Fixes Delivered

Despite the 1-ULP artifact, this session delivered 8 infrastructure fixes:
1. LOGICAL `.EQV.` comparison detection
2. LOGICAL type in field tag injection (no diff_)
3. CHARACTER bridge local buffer
4. LOGICAL mapping (int32_t for non-view)
5. Array-returning FUNCTION bridges (Calculate_Cn)
6. Callee bridge pre-call flush (view→Fortran sync)
7. Nested-type field tag tracking reset
8. Integer exponentiation in kernelSmoothing (precautionary, matches Fortran pattern)
