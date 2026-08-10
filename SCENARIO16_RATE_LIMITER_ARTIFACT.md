# Scenario 16 Rate-Limiter Artifact

This note documents the specific `2^-70` versus `0.0` difference observed in ROSCO Scenario 16 after the Fortran-to-C++ translation. The goal here is to be precise about what happened, what was verified, what was corrected, and what should not be overclaimed.

## Short Answer

The observed mismatch is a compiler/code-generation artifact in the ROSCO rate limiter at a zero crossing:

```text
Fortran reference: 0x1.0000000000000p-70  = 8.470329472543003e-22
C++ translated:   0x0.0p+0               = 0.0
```

It appears in Scenario 16 at `bld_pitch[4444]`, `bld_pitch_2[4444]`, and `bld_pitch_3[4444]`. These are three manifestations of one root cause, because Scenario 16 has no IPC and all three blade pitch commands follow the same collective path.

The C++ result is the cleaner mathematical result. The Fortran result is the reference behavior produced by the compiled Fortran code. The difference is physically meaningless but real at bit level.

## Key Clarifications

- This value is **not subnormal**. `2^-70` is much larger than the smallest normal double (`2^-1022`).
- "Sub-ULP" is not precise near zero. Relative to the local spacing at the value being cancelled, the residual is exactly four double ULPs.
- The difference is **not caused by inlined `saturate`** in the final integrated source. The inlined `saturate` pattern was a real translation-shape deviation and was fixed before the integrated controller branch.
- A reduced standalone Fortran/C++ probe using the reported `LastSignal` value produces exact zero in both languages. That means the artifact should be described as an observed full-program/compiler-context artifact, not as an inevitable result of the algebra alone.

## Source Locations

The Fortran rate limiter in `Functions.f90` computes:

```fortran
rate = (inputSignal - rlP%LastSignal(inst))/DT
rate = saturate(rate, minRate, maxRate)
ratelimit = rlP%LastSignal(inst) + rate*DT
rlP%LastSignal(inst) = ratelimit
```

The intended C++ translation in `translations/Functions/ratelimit.cpp` computes:

```cpp
double rate = (inputSignal - rlP->LastSignal[idx]) / DT;
rate = saturate_c(rate, minRate, maxRate);
result = rlP->LastSignal[idx] + rate * DT;
rlP->LastSignal[idx] = result;
```

In the sibling ROSCO workspace investigated at:

```text
vit_translation/ROSCO
```

the checkout was on `main`, not on `integrated`. That matters:

- On `main`, `rosco/controller/CMakeLists.txt` builds `rosco/controller/src/ratelimit.cpp`.
- On `main`, that source file currently contains an older inline `std::min(std::max(...))` clamp.
- On `main`, `translations/Functions/ratelimit.cpp` contains the corrected `saturate_c` call, but CMake does not build that translation file directly.
- The existing `main` build object `rosco/controller/build/CMakeFiles/discon.dir/src/ratelimit.cpp.o` calls `saturate_c`, but it is stale relative to the current `main` source: the object timestamp predates `rosco/controller/src/ratelimit.cpp`.
- On the `integrated` branch, CMake builds `rosco/controller/src/Functions/ratelimit.cpp`, and that production source calls `saturate(rate, minRate, maxRate)`.

So the reliable branch-level statement is: `main` is not the final integrated controller branch; `integrated` is. The final integrated production source does not inline the clamp.

The stale `main` object symbol table contains:

```text
T ratelimit(double, double, double, double, int, rlparams_t*, int*, int, double)
T ratelimit_c
U saturate_c
```

The stale `main` object assembly contains:

```asm
fsub    d0, d0, d3      ; inputSignal - LastSignal
fdiv    d0, d0, d8      ; / DT
bl      saturate_c
fmul    d0, d8, d0      ; rate * DT
ldur    d1, [x20, #-8]  ; LastSignal
fadd    d0, d0, d1      ; LastSignal + rate * DT
stur    d0, [x20, #-8]  ; store result
```

So the inspected stale object preserves a `saturate_c` call boundary and still performs a separate multiply and add for the final update. This object is useful evidence about one compiled artifact, but it should not be described as the current `main` source or as the final `integrated` branch source.

## Observed Output Evidence

The stored Fortran reference arrays contain the residual. Both `upstream_arrays` and `baseline_arrays` show the same value, which proves this is native Fortran behavior and not a VIT type-system side effect.

Around the failing index:

```text
index 4440: 0x1.9b441e0000000p-15
index 4441: 0x1.2668aa0000000p-15
index 4442: 0x1.64c7920000000p-16
index 4443: 0x1.f9c79a0000000p-18
index 4444: 0x1.0000000000000p-70
index 4445: 0x0.0p+0
index 4446: 0x0.0p+0
index 4447: 0x0.0p+0
```

This same pattern appears in all three blade pitch arrays. The C++ comparison arrays are not committed in the inspected workspace, but the dev notes record the C++ value as exact zero at the same index.

## Arithmetic at the Zero Crossing

The debug note identifies a per-blade rate limiter call where:

```text
inputSignal = 0.0
LastSignal  = 1.126291322678453261e-06
DT          = 0.025
```

The idealized computation is:

```text
rate   = (0.0 - LastSignal) / DT
result = LastSignal + rate * DT
```

Algebraically:

```text
result = x + ((-x / DT) * DT)
       = x + (-x)
       = 0
```

Using the reported `LastSignal` bit pattern:

```text
x        = 0x1.2e56267d20140p-20
ulp(x)   = 0x1.0000000000000p-72
residual = 0x1.0000000000000p-70 = 4 * ulp(x)
```

That explains why the residual is exactly `2^-70`: it is four representable double steps at the scale of the value being cancelled. It is not a magic physical value; it is a binary rounding residue.

In a standalone IEEE calculation with the reported values:

```text
rate     = -0x1.79ebb01c68190p-15
rate*DT  = -0x1.2e56267d20140p-20
x+rate*DT = 0x0.0p+0
```

That standalone result matches the C++ behavior.

## What the Minimal Probe Shows

A reduced test program using the reported `LastSignal`, the same `DT`, a Fortran `saturate` function, and the final update produced exact zero with:

- gfortran `-O0 -ffp-contract=off`
- gfortran `-O2 -ffp-contract=off`
- gfortran `-O2 -ffp-contract=fast`
- g++ with analogous settings

This matters. It means the phrase "gfortran evaluates `x + ((-x/a)*a)` as `2^-70`" should be understood as shorthand for the full ROSCO compiled context, not as a universal reduced-expression property.

The full-program artifact can still be real even when the tiny reproducer returns zero. The exact runtime value of `rate` entering the final expression, the function-call ABI, optimization boundaries, surrounding code, and register allocation can differ from the reduced test. The saved Fortran arrays prove the full Fortran run produced `2^-70`; the reduced probe shows only that this is not forced by IEEE arithmetic.

## Inlined `saturate`: What Happened

Originally, several C++ controller translations used direct clamp expressions:

```cpp
std::min(std::max(value, minValue), maxValue)
```

The Fortran originals call `saturate(...)` as a separate function. That matters for faithful translation because a function boundary can affect optimization, storage, and rounding. Dev note `202604031427-saturate-inline-fix.md` replaced those inlined clamps in the translation sources:

| Translation | Inlined clamps replaced |
| --- | ---: |
| `PIController` | 2 |
| `PIIController` | 3 |
| `PIDController` | 2 |
| `ResController` | 1 |
| `ratelimit` | 1 |

That fix was correct, but the Scenario 16 difference persisted. Therefore:

- Inlined `saturate` was a real translation-shape issue.
- It was not the cause of this specific `2^-70` residual.
- The residual lives in or after the final update `LastSignal + rate*DT`.

## Why C++ Gets Zero

For the reported values, the C++ computation follows the direct IEEE result:

```text
rate * DT == -LastSignal
LastSignal + (-LastSignal) == 0.0
```

The inspected stale `main` object assembly confirms that this particular C++ build was not using an FMA for the final expression. It performs a multiply followed by an add. The multiplication result for the reported values is exactly `-LastSignal`, so the final add cancels exactly.

From a numerical-analysis perspective, exact zero is the cleaner result. From a verification perspective, Fortran is the oracle.

## Why Fortran Gets `2^-70`

The Fortran reference run stores:

```text
0x1.0000000000000p-70
```

The evidence supports this interpretation:

1. The residual is in upstream Fortran and modified Fortran arrays, so it is not caused by C++ or by VIT's type modifications.
2. The inlined clamps were corrected before the integrated controller branch, and the Scenario 16 difference persisted, so this is not simply an inlining artifact.
3. The residual equals four ULPs of the reported cancelled value, consistent with a rounding residue near exact cancellation.
4. The reduced program does not reproduce it, so the exact mechanism is likely tied to the full ROSCO compiled context rather than the source expression alone.

The most honest statement is:

> In the full ROSCO Scenario 16 run, gfortran's compiled rate-limiter path leaves a four-ULP residual at the zero crossing, while the C++ path cancels exactly. The residual is compiler/code-generation behavior in the reference build, not a semantic translation error.

## Why Not "Fix" It?

There are ways to eliminate the difference, but each changes the problem:

- Add a near-zero clamp:

  ```cpp
  if (std::abs(result) < eps) result = 0.0;
  ```

  This would make C++ cleaner, but it changes the algorithm and no longer matches upstream Fortran source semantics.

- Add the same clamp to Fortran and C++.

  This would make both languages agree, but it is an intentional numerical change to ROSCO, not a translation.

- Try to force C++ to reproduce the Fortran residual through volatile temporaries, altered expression shape, compiler flags, or ABI tricks.

  This is brittle and platform-specific. It risks turning one explained artifact into a maintenance burden.

- Accept and document the artifact.

  This is the best engineering choice for the verified translation claim: the difference is fully localized, physically negligible, and explained as a compiler-level residual.

## Recommended Wording

When describing it, avoid saying "subnormal" or strict "sub-ULP." Use:

```text
The only remaining mismatch is a `2^-70` radian residual at a rate-limiter zero crossing. Upstream Fortran and modified Fortran both produce the residual; C++ cancels to exact zero. The residual is four double ULPs at the scale of the cancelled `LastSignal` value and is physically meaningless. It is an explained compiler/code-generation artifact in the reference build, not a translation bug.
```

## Practical Repository Note

If someone rebuilds from the inspected ROSCO checkout while still on `main`, they should be aware that `rosco/controller/src/ratelimit.cpp` currently contains an older inline clamp, while `translations/Functions/ratelimit.cpp` contains the corrected `saturate_c` call. The existing `main` build object is stale relative to the current `main` source and was compiled from a state that called `saturate_c`.

For the final controller shape, use the `integrated` branch. On that branch, CMake builds `rosco/controller/src/Functions/ratelimit.cpp`, and the production source calls the translated `saturate(...)` function rather than inlining the clamp.
