# FMA Contraction Fix: Bit-Identical Fortran↔C++ Floating-Point Results
**Date:** 2026-03-19
**Status:** completed
**Scope:** ROSCO CMakeLists.txt, VIT kernel_verify.py

## Problem

Phase 6B's full clean build test revealed that Scenario 3 output differed between the pure-Fortran baseline and the C++-integrated build. The differences were small (0.1–0.3 kW in Power output, ~0.007%) but real and reproducible. Scenarios 1+2 were byte-identical — the issue only manifested in Scenario 3, which has a NotchFilter operating in a closed-loop feedback path on generator speed.

## Root Cause

**FMA (Fused Multiply-Add) contraction scheduling differences between gfortran and g++.**

Both compilers default to `-ffp-contract=fast` at `-O2`, which allows fusing `a*b + c` into a single FMA instruction with one rounding (instead of two separate operations with two roundings). However, gfortran and g++ independently decide which multiplications to fuse with which additions. For the same mathematical expression, they may produce different FMA schedules, leading to results that differ by 1 ULP (unit in the last place).

### Why It Was Invisible Before Scenario 3

In Scenarios 1+2, the LPFilter/HPFilter/SecLPFilter translations also had 1-ULP differences (visible in KGen field data as `IN_TOL` instead of `IDENTICAL`). But these differences didn't accumulate visibly because:

1. The filtered signals don't directly feed back into themselves through a fast control loop in Scenarios 1+2
2. The Power output format (`%.1f kW`) has enough display resolution headroom that the accumulated error stayed below the rounding threshold

In Scenario 3, the NotchFilter modifies GenSpeedF, which feeds into the torque controller, which changes generator speed, which feeds back into the NotchFilter. This closed-loop feedback amplifies 1-ULP differences over hundreds of timesteps until they cross the display rounding threshold.

### Diagnostic Evidence

KGen verification field data for LPFilter showed:
```
LPFilter.0.0.7,genspeedf,scalar,IN_TOL,24.042039462813207,24.042039462813211,3.55e-15
LPFilter.0.0.10,genspeedf,scalar,IN_TOL,30.472872602753299,30.472872602753306,7.11e-15
```

These are exactly 1 ULP and 2 ULP differences — the signature of FMA vs non-FMA rounding.

## Diagnostic Process

### Bisection

1. **Pure Fortran baseline is deterministic** — confirmed by running twice (identical output)
2. **Functions.f90 translations alone** — Scenario 3 byte-identical (9 Functions, no Filters)
3. **LPFilter alone** — Scenario 3 shows the drift (isolated to one function)
4. **LPFilter alone with `-ffp-contract=off` on C++** — Scenario 3 byte-identical (confirms FMA cause)

### Failed Fix: C++-Only `-ffp-contract=off`

Adding `-ffp-contract=off` only to C++ compilation made things **worse** for the full 15-function build. The baseline Fortran used FMA while the integrated C++ didn't, creating *more* differences (not fewer) for all translated functions.

### Working Fix: Both Languages `-ffp-contract=off`

Adding `-ffp-contract=off` to **both** gfortran and g++ ensures both compilers perform standard IEEE 754 rounding at each individual operation. This eliminates all FMA scheduling differences and produces bit-identical results.

## Fix Applied

### ROSCO repo: `rosco/controller/CMakeLists.txt`

Added `-ffp-contract=off` to the GNU Fortran flags (line 20) and a new `CMAKE_CXX_FLAGS` line:

```cmake
set(CMAKE_Fortran_FLAGS "${CMAKE_Fortran_FLAGS} -DIMPLICIT_DLLEXPORT -ffree-line-length-0 -fdefault-real-8 -fdefault-double-8 -cpp -ffp-contract=off")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -ffp-contract=off")
```

This is a permanent project-level setting. Both the baseline and integrated builds use the same flags, ensuring the diff is always valid.

### VIT repo: `vit/kernel_verify.py`

Updated kernel Makefile CXXFLAGS from `-O2 -fPIC` to `-O2 -fPIC -ffp-contract=off`. This ensures KGen kernel verification also builds C++ without FMA, matching the project flags.

### NOT in VIT's integration patch

The `-ffp-contract=off` flag is NOT added by VIT's `integrate.py` CMake patch. This was tried and rejected because it only affects the integrated build, not the baseline. The flag must be in the project's CMakeLists.txt to affect both builds equally.

## Performance Impact

Negligible. The ROSCO controller runs at dt=0.025s (40 Hz). At this rate, the filter computations take microseconds per timestep. FMA saves ~1 cycle per fused operation. The total impact is well under 1% of the controller's execution time.

## Verification After Fix

- All 11 golden fixtures pass 40/40 (unchanged)
- Full clean build test: 15 C++ translations, all 3 scenarios, **byte-identical output**
- The fix doesn't change the Fortran-only simulation behavior in a way that matters for the controller (same IEEE 754 operations, just without FMA fusion)

## Implications for Future Phases

**`-ffp-contract=off` is a verification-phase tool, not a permanent production setting.** During translation, we need bit-identical results to prove each function's correctness against KGen-captured Fortran state. Once all of ROSCO is translated to C++ and there's no mixed Fortran/C++ binary, FMA can be re-enabled freely — the C++ code will stand on its own with full compiler optimizations.

**Two-check clean build test:** The clean build test now has two steps:
1. **Verification check** (`-ffp-contract=off` on both): diff must be empty. Proves translation correctness.
2. **FMA sanity check** (default flags, FMA on both): diff should be small and bounded (a few lines in Scenario 3 with sub-0.01% differences). Confirms no structural translation bugs hiding behind the FMA mask.

**KGen verification tolerance (1e-14) is still necessary** as a safety net, but with `-ffp-contract=off` on both sides, all translations should show `IDENTICAL` (not `IN_TOL`) for all fields. If a future translation shows `IN_TOL`, it indicates a real translation bug (expression structure mismatch), not an FMA artifact.

**Golden kernels extracted before this fix** (with FMA-enabled Fortran) are still valid for verification because KGen captures the function's inputs and compares its outputs. The verification kernel is built with VIT's Makefile (which now includes `-ffp-contract=off`), so the C++ computation matches the new no-FMA Fortran behavior. The captured reference data is from FMA-enabled Fortran, but the differences are within the 1e-14 tolerance.
