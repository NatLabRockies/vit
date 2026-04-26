# CalculateUACoeffs 5/20 Kernel Failure: libmvec Vectorized Trig Root Cause
**Date:** 2026-04-26
**Status:** completed
**Scope:** KGen kernel verification, vectorized math library, VIT infrastructure

## Problem

CalculateUACoeffs kernel verification passed 15/20. Five cases (7, 8, 9, 11, 14) failed with `cnbreakupper` exceeding the 1e-14 tolerance. The underlying `alphabreakupper` diff was 2.22e-15, amplified ~7x by `CnSlopeUpper` to 1.24e-14. Production 16/16 baselines always passed.

## Investigation Path

### What was ruled out
| Test | Result | Conclusion |
|------|--------|------------|
| Remove `-fno-tree-vectorize` debugging leftover from Makefile | 15/20 | Flag was irrelevant (confirmed original investigation) |
| Switch C++ to Fortran bridge kernelSmoothing | 15/20 | Smoothing implementation not the cause |
| Switch to C++ kernelSmoothing (production code) | 15/20 | Same result either way |
| Compile kernel Fortran with `-O0` | 15/20 | Not an optimization artifact |
| Re-extract from completely clean source | 15/20 | Not an extraction-order issue |
| **Run pure Fortran kernel (NO C++ at all)** | **15/20** | **Not a C++ translation issue** |
| Pure Fortran at `-O0` | 15/20 | Same diff at zero optimization |
| Compare golden against itself (`p = kgenref_p`) | **20/20** | State data is self-consistent |

### The breakthrough: pure Fortran fails too
The definitive test was replacing the C++ wrapper with the original Fortran from AirfoilInfo.f90.kgen. Even the pure Fortran kernel with zero optimization produced the same 5 failures with the same exact diffs. This proved the issue was NOT in the C++ translation, but in the kernel infrastructure.

### Root cause: libmvec vectorized trig functions
The production build's `aerodyn_driver` links against `libmvec.so.1` (glibc's SIMD math library):
```
nm aerodyn_driver | grep _ZGV
  U _ZGVnN2v_cos@GLIBC_2.38
  U _ZGVnN2v_sin@GLIBC_2.38
```

These are ARM64 NEON-vectorized implementations of cos/sin that process 2 doubles simultaneously. They are selected by gfortran when `-O2 -ftree-vectorize` (default) is active AND the vectorization cost model determines it's profitable.

The KGen kernel compiles a **reduced** AirfoilInfo.f90 with fewer functions than the production module. gfortran's vectorization heuristics (cost model) decided NOT to vectorize the `Calculate_Cn` loop in this smaller compilation unit, despite vectorizing it in the full production build. The kernel's `AirfoilInfo.o` called scalar `sincos` instead of `_ZGVnN2v_cos`/`_ZGVnN2v_sin`.

**Vectorized and scalar cos/sin produce different results for certain inputs.** This is by design — libmvec implementations trade maximal ULP accuracy for SIMD throughput, and the IEEE 754 standard allows 1-ULP error for transcendental functions. The difference is typically 1 ULP, which for the `Calculate_Cn` loop propagates through:

```
cn(Row) = Cl*cos(alpha) + (Cd-Cd0)*sin(alpha)
    ↓ (1-ULP diff in cos/sin for specific alpha values)
CnSlopeUpper = (cn(iU-1) - cn(iU)) / (alpha(iU-1) - alpha(iU))
    ↓ (amplified by division)
alphaBreakUpper = (CnSlopeRF * a0RF - CnSlopeUpper * a0Upper) / (CnSlopeRF - CnSlopeUpper)
    ↓ (2.22e-15 diff)
CnBreakUpper = CnSlopeUpper * (alphaBreakUpper - alpha0Upper)
    ↓ (amplified by CnSlopeUpper ~7)
1.24e-14 > tolerance 1e-14 → FAIL
```

## Fix

Two changes to VIT's `kernel_verify.py`:

1. **Fortran flags:** Added `-fvect-cost-model=unlimited` to `base_flags`. This forces gfortran to vectorize all profitable loops regardless of the compilation unit size, matching the production CMake build's vectorization behavior.

2. **Linker flags:** Added `-lmvec -lm` to the kernel link command. This provides the SIMD math library that the vectorized cos/sin calls resolve to.

```python
# kernel_verify.py line 686
base_flags = "-O2 -ffree-form -ffree-line-length-none -fvect-cost-model=unlimited"
```

```python
# kernel_verify.py line 800
if "-lmvec" not in line:
    lines[i] = lines[i].rstrip() + " -lmvec -lm"
```

## Result
- CalculateUACoeffs kernel: **20/20** (was 15/20)
- All other kernels: still passing (spot-checked AttachedFlow 20/20, Calculate_Cn 20/20)
- Production 16/16 baselines: PASS

## Broader Implications

This fix applies to ALL future KGen kernel extractions, not just CalculateUACoeffs. Any function that calls transcendental math functions (cos, sin, exp, log) in a loop is at risk of the same vectorized-vs-scalar divergence if the kernel's reduced module changes gfortran's vectorization cost decisions.

The `-fvect-cost-model=unlimited` flag is aggressive — it forces vectorization even when the cost model says it's not worth it. This is correct for verification (matching the production build) but might slow down kernel execution slightly. Since kernels run in seconds, this is negligible.

**Note for other platforms:** This issue is specific to ARM64 Linux with glibc's libmvec. On x86-64, libmvec provides AVX/AVX2 vectorized math with similar ULP characteristics. The fix should work on both platforms. On macOS (Accelerate framework), the issue may not exist since Apple's libm doesn't have a separate vectorized math library.

## Debugging leftovers cleaned up
- Removed `-fno-tree-vectorize` from committed kernel Makefile
- Removed manual Fortran bridge smoothing modification from kernel `.hpp`
- Removed debugging WRITE/fprintf statements
- Re-extracted kernel from clean source (pre-integration AirfoilInfo.f90)
- Added `shared_files` config to vit.yaml (was missing)

## Commits
| Repo | Commit | What |
|------|--------|------|
| VIT | `544e8e8` | Fix kernel vectorization flags + libmvec |
| OpenFAST | `3074e38d2` | Re-extracted clean kernel + shared_files config |
