# CalculateUACoeffs: Buffer Overflow Fix + 1-ULP Bridge Artifact
**Date:** 2026-04-25/26
**Status:** resolved (see dev note `202604260357` for root cause and fix)
**Scope:** CalculateUACoeffs integration, Calculate_C_alpha buffer overflow, kernel bridge precision

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

## Production Blocker: Calculate_C_alpha Buffer Overflow (FIXED)

The 7/16 baseline regression failures were caused by a buffer overflow in `calculate_c_alpha.cpp`:

```cpp
std::memset(ErrMsg, ' ', 8196);  // BUG: writes 8196 bytes
```

The caller (`CalculateUACoeffs`) allocates `char ErrMsg2[1025]` on the C++ stack. Calculate_C_alpha writes 8196 spaces (Fortran `ErrMsgLen`) into this 1025-byte buffer, overwriting 7171 bytes of stack including the `p` view pointer. The view pointer became `0x2020202020202020` (ASCII spaces), causing segfault in Compute_iLoweriUpper when it tried to dereference `p->Alpha`.

**Fix:** Changed all 3 `std::memset(ErrMsg, ' ', 8196)` occurrences in `calculate_c_alpha.cpp` to `std::memset(ErrMsg, ' ', 1024)`.

**Why it only affected some test cases:** BAR_SineMotion has UA=0 (no unsteady aero), so CalculateUACoeffs returns early (InclUAdata=FALSE) before reaching the Calculate_C_alpha call. MHK_RM1_Fixed and other failing cases have UA enabled, entering the full computation path.

After fix: **16/16 baselines pass.**

## Detailed 1-ULP Investigation Data (for continuation)

### Failing fields (5/20 kernel cases)

All 20 cases have `coefs` array diffs (129-377 elements, rms 9.65e-17 to 1.1e-14). Only 5 exceed tolerance:
- Case 7: cnbreakupper diff=1.24e-14, alphabreakupper diff=2.2e-15
- Case 8: coefs rms=1.1e-14 (no cnbreak diff)
- Case 9: cnbreakupper diff=1.24e-14
- Case 11: cnbreakupper diff=1.24e-14, cnbreaklower diff=1.07e-14
- Case 14: cnbreakupper diff=1.42e-14

### Key data points

**CalcDefaults raw values (all 20 cases identical):**
```
[0]=0 [1]=0 [2]=0 [3]=0 [4]=0 [5]=0 [6]=1 [7]=1 [8]=1 [9]=1
```
Fields 0-5 (alpha0 through C_lalpha) are FALSE (provided by airfoil file). Fields 6+ (T_f0 through alphaLower) are TRUE (use defaults).

**All alpha-related outputs are IDENTICAL** for all 20 cases:
alpha0, alpha1, alpha2, alphaUpper, alphaLower, c_alphaLower, c_alphaUpper, C_nalpha — no diffs.

**alpha0ReverseFlow: IDENTICAL** for all 20 cases.

**alphabreakupper: 1-ULP diff** in cases 7, 11, 14 (2.2e-15 each). Amplified by CnSlopeUpper (~6-7) to cnbreakupper diff of 1.2-1.4e-14.

### What was tested

| Test | Result | Conclusion |
|------|--------|------------|
| Calculate_Cn bridge vs inline C++ | 0/200 diffs | Bridge cn is bit-identical |
| C++ kernelSmoothing vs Fortran bridge | 32/199 diffs (~2.78e-17 each) | C++ smoothing has 1-ULP diffs |
| Switch to Fortran bridge smoothing | Same 5 failures | Smoothing is NOT the cause |
| std::pow → integer multiplication | Same 5 failures | pow is NOT the cause |
| Constants (Pi, D2R, TwoPi) | Bit-identical | Constants are NOT the cause |
| -fno-tree-vectorize | Same 5 failures | Vectorization is NOT the cause |
| AttachedFlow standalone kernel | ALL IDENTICAL | Diff only when called via bridge |

### Remaining hypothesis

The 1-ULP diff in `alphabreakupper` originates in ComputeUA360_AttachedFlow (Fortran via callee bridge) despite inputs appearing bit-identical. The diff may come from:
1. gfortran treating the bridge's `cn_cl(1:n_cn_cl)` array section differently from the direct local array `cn`
2. A subtle difference in the flush-reload cycle's scalar copy (REAL→C_DOUBLE→REAL roundtrip)
3. Something we haven't tested yet

### Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `1deb7e3` | 6 infrastructure fixes |
| VIT | `5693b2f` | Nested-type field tag reset + dev note |
| OpenFAST | `d6bab33e9` | CalculateUACoeffs translation + kernel fixture |
| OpenFAST | `a8fd0efbc` | Buffer overflow fix + clean up + 16/16 baselines |

## Resolution (2026-04-26)

Root cause was NOT a bridge artifact, C++ translation bug, or compiler optimization difference. It was **glibc libmvec** — the production build vectorized the `Calculate_Cn` cos/sin loop using SIMD implementations from `libmvec.so`, while the KGen-reduced kernel module's smaller compilation unit caused gfortran's cost model to skip vectorization, using scalar `libm` cos/sin instead. Vectorized and scalar trig differ by 1 ULP for certain alpha values.

**Proof:** Pure Fortran kernel (no C++ at all) at `-O0` produced the same 5 failures. Golden-vs-golden comparison gave 20/20 (state data correct). Adding `-fvect-cost-model=unlimited` + `-lmvec -lm` to the kernel build gave 20/20.

**Fix:** VIT's `kernel_verify.py` now adds these flags to all kernel builds. CalculateUACoeffs kernel re-extracted from clean source: **20/20 IDENTICAL + 16/16 baselines**.

See dev note `202604260357` for the full investigation.
