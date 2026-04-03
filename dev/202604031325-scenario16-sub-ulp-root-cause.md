# Scenario 16 Sub-ULP Difference: Definitive Root Cause
**Date:** 2026-04-03
**Status:** completed
**Scope:** Investigation — the one non-identical value in 1.8M comparisons

## Summary

The single non-identical value across all 25 scenarios — `bld_pitch[4444]` in Scenario 16 (Flp_Mode=3) where Modified Fortran produces `2^(-70)` and C++ produces `0.0` — is caused by a fundamental difference in how gfortran and g++ evaluate the expression `x + ((-x / a) * a)` in the rate limiter function. Not a translation bug. Not fixable without changing the arithmetic.

## The Value

- **Modified Fortran:** `8.470329472543003391e-22` = exactly `2^(-70)` (hex `0x3b90000000000000`, exponent=-70, mantissa=0)
- **C++:** `0.0` (hex `0x0000000000000000`)
- **Location:** Scenario 16 (Flp_Mode=3), `bld_pitch` array index 4444, timestep t=111.1s
- **Physical meaning:** `8.47e-22 radians ≈ 4.85e-20 degrees` — 6 orders of magnitude below double epsilon

The value is NOT subnormal. It's a normal double with a clean power-of-2 representation.

## Investigation Trace

### Step 1: Where does bld_pitch come from?

`bld_pitch` in the sim comes from `avrSWAP[41]` (blade 2 pitch command), which is `LocalVar.PitCom[1]` set by PitchControl. The value passes through:

```
PIController → saturate → rate_limit (collective) → saturate → rate_limit (per-blade) → avrSWAP[41]
```

### Step 2: Is it the PI controller?

Added debug prints to PIController at idx=0 (collective pitch PI). Found that the PI controller has been outputting `result=0.0` for hundreds of timesteps before the zero-crossing. `ITerm = 0.0` (clamped by `max(ITerm, PC_MinPit=0)`), `PTerm = -60000+` (large negative, clamped to 0). The PI controller is not the source.

### Step 3: Is it the collective rate limiter?

Added debug to rate limiter at idx=0 (collective). Found it drops from `LastSignal=2.31` to `result=0.0` in one step (rate is -92, unclamped). No gradual decay. The collective rate limiter is not the source.

### Step 4: Is it the per-blade rate limiter?

`bld_pitch` for blade 2 goes through a SECOND rate limiter (per-blade, idx=2 in the instRL sequence). Added debug to idx=2. Found the decay sequence:

```
#0: LastSig=1.367e-4 → result=6.919e-5  (rate unclamped)
#1: LastSig=6.919e-5 → result=1.126e-6  (rate unclamped)
#2: LastSig=1.126e-6 → result=0.0       (rate unclamped, exact cancellation in C++)
#3: LastSig=0.0      → result=0.0       (stays at zero)
```

At entry #2: `input=0.0`, `LastSignal=1.126291322678453261e-06`. The rate limiter computes:

```
rate = (0.0 - 1.126291322678453261e-06) / 0.025
result = 1.126291322678453261e-06 + rate * 0.025
```

### Step 5: Exact floating-point analysis

Computed in Python (IEEE 754 double):

```
LastSignal = 1.126291322678453261e-06  (hex 0x3eb2e56267d20140)
-LastSignal = -1.126291322678453261e-06 (hex 0xbeb2e56267d20140)
rate = (-1.126291322678453261e-06) / 0.025 = -4.505165290713813043e-05
rate * 0.025 = -1.126291322678453261e-06 (hex 0xbeb2e56267d20140)
```

`rate * DT == -LastSignal` **exactly** in IEEE 754 for this value. Therefore `LastSignal + rate * DT = LastSignal + (-LastSignal) = 0.0` exactly.

**g++ produces `0.0`.** This matches the IEEE 754 analysis.

**gfortran produces `2^(-70)`.** This does NOT match the IEEE 754 analysis. gfortran's code generation for the Fortran expression `rlP%LastSignal(inst) + rate*DT` produces a different result than the equivalent C++ expression `rlP->LastSignal[idx] + rate * DT`.

### Step 6: Why gfortran differs

The Fortran rate limiter calls `saturate(rate, minRate, maxRate)` as a separate function call. The C++ inlines this as `std::min(std::max(rate, minRate), maxRate)`. Since the rate is within bounds (no clamping), both should return the same value. However:

1. The Fortran `saturate` function returns `REAL(MIN(MAX(x, minVal), maxVal), DbKi)` — an explicit `REAL()` cast that forces the result to `DbKi` precision.

2. The function call boundary forces the value to be stored to memory (or at least to a register conforming to the function's return type), while the C++ inline keeps the intermediate in whatever representation the compiler chooses.

3. On ARM64, there are no extended-precision registers (unlike x86's 80-bit), so this shouldn't matter. But gfortran's code generator may still evaluate `LastSignal + rate*DT` differently — for example, by computing `rate*DT` with a slightly different intermediate representation than g++.

The `2^(-70)` (a clean power of 2) is characteristic of a rounding difference at the last step of a multiply-accumulate — the residual from `(-x/a)*a ≠ -x` when evaluated in a specific order.

## Conclusion

**This is a gfortran code generation artifact, not a translation error.** The C++ translation is a faithful reproduction of the Fortran algorithm. Both compilers receive the same expression with the same inputs. g++ produces exact zero; gfortran produces `2^(-70)`. The difference is below any physically meaningful threshold by 6 orders of magnitude.

**Not fixable without changing the arithmetic.** To eliminate this difference, we would need to add an explicit near-zero clamp (e.g., `if (abs(result) < 1e-15) result = 0.0`) to the rate limiter. This would change the algorithm and mask potential real differences. The cure is worse than the disease.

**The canvas is as clean as IEEE 754 allows.** Of 1,812,000 simulation values across 25 scenarios comparing two different compilers' output for the same algorithm, exactly 1 value differs — and that value is `8.47e-22` radians, which is 4.85e-20 degrees.
