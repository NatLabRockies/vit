# Scenario 16 2^-70 Residual: Reproduced, and the True Root Cause
**Date:** 2026-07-24
**Status:** completed
**Scope:** Verification — definitive reproduction of the one tolerated ROSCO mismatch; correction of the recorded root cause

## Summary

The single tolerated mismatch in the ROSCO migration — `bld_pitch[4444] = 2^-70` in
Scenario 16 (Fortran) versus `0.0` (C++) — was reproduced from a clean build, and its
operands were captured directly from the running controller. The result **overturns the
mechanism recorded in `202604031325-scenario16-sub-ulp-root-cause.md` and
`SCENARIO16_RATE_LIMITER_ARTIFACT.md`.**

Findings, all from live captures (not reconstruction):

1. **The residual is trivially reproducible** — it is the ordinary, correctly-rounded,
   non-FMA IEEE result of `LastSignal + rate*DT` for the *actual* operands. No compiler
   magic, no FMA, no vectorization, no "full-program compilation context" is required.

2. **The earlier "reduced probe returns zero" conclusion was an artifact of wrong operands.**
   The prior investigation reconstructed `LastSignal = 1.126e-6` and `DT = 0.025` (a full
   double). Both are wrong. The true `LastSignal` at the store is `7.5367e-6`, and the true
   `DT` is **float32-precision 0.025** (`0.02500000037…`, the value delivered through the
   float32 `avrSWAP` interface), not double `0.025`. Feed the real operands and the residual
   appears immediately — in **both** languages.

3. **It is NOT a rate-limiter code-generation difference.** gfortran and g++ compile the rate
   limiter to bit-identical arithmetic: given identical operands, standalone Fortran and C++
   probes both produce `2^-70` (and both produce `0` on the old wrong operands). The earlier
   explanation, that the compiled Fortran leaves a 4-ULP residual of the cancelled operand
   where the C++ cancels exactly because of the reference build's code generation, is
   incorrect as stated.

4. **The true cause is a sub-float32 divergence in the internal *double* state**, seeded in the
   wind-speed-estimator EKF. At the store, the two builds carry different `LastSignal` doubles —
   Fortran `0x3EDF9C7998447C18`, C++ `0x3EDF9C799841480A` — differing by `1.78e-16` (`2.4e-11`
   relative), far below the float32 spacing (`~5.7e-11`). Both round to the *same* float32, so every
   float32 `avrSWAP` output is bit-identical (including `bld_pitch[4443]`). The difference is
   invisible until the rate-limiter **zero crossing** at t=111.1 s, where Fortran's double state
   lands on `2^-70` and C++'s on exact `0`. Traced to its seed (see "Upstream origin" below): a
   **1-ULP difference in the EKF covariance `MATMUL`** (Fortran intrinsic vs C++ hand-unrolled
   loops) at t≈13.8 s, amplified through the wind estimate → torque → pitch chain. This is exactly
   closed-loop amplification — but it relocates the effect from the rate limiter's code
   generation to a small-matrix `MATMUL` compiler difference upstream.

The engineering conclusion is unchanged (physically negligible, compiler-level not a translation
bug, do not add a near-zero clamp). What changes is the *mechanism* and the false claim of
non-reproducibility.

## Reproduction (all in `vit-dev`, ARM64 Linux, gfortran/g++ 13.3.0, `-ffp-contract=off`)

Procedure and scripts committed under `ROSCO/scripts/repro_scenario16/`. Steps:

1. Extract upstream Fortran controller (`e8010f0`) and integrated C++ controller
   (`integrated` branch) into isolated scratch trees — the tracked working tree is never
   modified.
2. Instrument each `ratelimit` to dump the exact IEEE bit patterns of `inputSignal`,
   `LastSignal` (before overwrite), `DT`, the saturated `rate`, and `result`, for every call
   whose result is near the zero crossing (`|result| < 1e-9`).
3. Build each `libdiscon.so` with `-ffp-contract=off`; run `vit_sim.py --scenario 16`.
4. Grep the dump for the store whose result is `0x3B90000000000000` (`2^-70`).
5. Feed the captured operands to standalone Fortran and C++ probes (`probe.f90`, `probe.cpp`)
   that replay `rate=(input-LastSignal)/DT; rate=saturate(...); result=LastSignal+rate*DT`.

### Confirmed reproduction of the artifact

The isolated upstream Fortran build reproduces the committed baseline exactly:

```
bld_pitch[4442] = 0x3ef64c7920000000
bld_pitch[4443] = 0x3edf9c79a0000000
bld_pitch[4444] = 0x3b90000000000000   = 2^-70   <-- the residual
bld_pitch[4445] = 0x0
```

### Captured operands at the store (inst 2, the creation event)

| Operand | Fortran build | C++ build |
|---|---|---|
| `inputSignal` | `0x0` (0.0) | `0x0` (0.0) |
| `LastSignal`  | `0x3EDF9C7998447C18` (7.536704633075694e-06) | `0x3EDF9C799841480A` (7.536704632897873e-06) |
| `DT`          | `0x3F999999A0000000` (0.02500000037252903 = float32 0.025) | same |
| `rate` (saturated) | `0xBF33C1CBFA3A5A90` | `0xBF33C1CBFA385A08` |
| `result`      | `0x3B90000000000000` (**2^-70**) | `0x0` (**0.0**) |

`inputSignal`, `DT`, `minRate`, `maxRate` are identical between builds. **Only the carried
double `LastSignal` differs** (and hence `rate`). The divergence is `1.78e-16` absolute — both
`LastSignal` values round to the identical float32 `0x3edf9c79a0000000`, which is why
`bld_pitch[4443]` is bit-identical in the two builds.

### Cross-language probe results

```
--- with the TRUE Fortran operands (float32 DT, LastSignal=7.54e-6) ---
FORTRAN result = 0x3B90000000000000   (2^-70)
C++     result = 0x3B90000000000000   (2^-70)      <-- both languages agree

--- with the DEV-NOTES operands (LastSignal=1.126e-6, DT=double 0.025) ---
FORTRAN result = 0x0000000000000000   (0.0)
C++     result = 0x0000000000000000   (0.0)        <-- why the old probe "returned zero"
```

`rate = (0 - LastSignal)/DT` recomputes bit-for-bit to the captured saturated `rate`, and is
within `[minRate, maxRate]` (unclamped), confirming the operand chain.

## Why the earlier investigation went wrong

`202604031325` reconstructed the operands from a debug print and computed, in Python:
`LastSignal = 1.126291322678453261e-06`, `DT = 0.025`, concluding `rate*DT == -LastSignal`
exactly so the result "should" be `0`, and that gfortran's nonzero result must be a codegen
property of `x + ((-x/a)*a)`. `SCENARIO16_RATE_LIMITER_ARTIFACT.md` then correctly noted the
reduced probe returns zero in both languages and honestly downgraded the claim to an
unlocalized "full-program compilation context" artifact.

Two operand errors produced that dead end:

- **Wrong `LastSignal`.** `1.126e-6` is not the operand at the store that writes `bld_pitch[4444]`;
  the true value is `7.5367e-6` (the prior step's internal double). The `1.126e-6` value belongs
  to a different invocation in the decay sequence.
- **Wrong `DT`.** The controller receives `DT` through the float32 `avrSWAP` interface, so it is
  `float32(0.025) = 0.02500000037…`, not double `0.025`. Using double `0.025` changes `rate` and
  the product enough to cancel exactly.

With either wrong operand, `LastSignal + rate*DT` cancels to `0`, which is why every reduced
reproducer "returned zero" and the effect looked unreproducible. It was reproducible all along;
the reproducer was fed the wrong numbers.

## The true root cause

- The rate limiter is a faithful translation and compiles to identical arithmetic in both
  toolchains (proven: identical operands → identical result, `2^-70` or `0`).
- The two full builds (gfortran-compiled original vs g++-compiled translation) accumulate a
  **sub-float32 difference in the internal double `LastSignal` state** along the Flp_Mode=3
  flap-pitch chain. Because every value written to `avrSWAP` is float32, this double-level
  difference is invisible in all committed output arrays — until the rate-limiter zero crossing,
  where `2^-70` versus `0.0` is the difference between the two builds' double states rounding
  across a float32-representable boundary.
- Both upstream and modified Fortran show `2^-70` (both gfortran); C++ shows `0` (g++). So the
  split is a compiler-level effect — but **upstream of the rate limiter**, not in it, and not the
  "4 ULPs of the cancelled operand" mechanism previously recorded. The `4 * ulp` framing is a
  coincidence of these particular operands, not a code-generation property.

This is a textbook instance of closed-loop amplification: a sub-ULP (here sub-float32) difference,
invisible at the deployment interface, amplified by closed-loop dynamics into a single detectable
value. It confirms the amplification effect while correcting the specific mechanism.

## Upstream origin — traced to the seed

Followed the divergence back through the whole simulation by instrumenting both builds (fresh
`e8010f0` Fortran and `integrated` C++ trees) and diffing doubles call-by-call. The complete
causal chain, seed to symptom:

1. **Seed — EKF prediction covariance `P(1,1)`, 1 ULP, EKF step ~4.** In `WindSpeedEstimator`
   (WE_Mode=2), the Kalman covariance prediction
   `P = P + DT*(MATMUL(F,P) + MATMUL(P,F^T) + Q - MATMUL(K*R_m,K^T))` (`ControllerBlocks.f90:448`)
   is a **Fortran `MATMUL` intrinsic** in the reference build and **hand-unrolled triple loops**
   in the C++ translation (`windspeedestimator.cpp:190–212`). For the 3×3 matrices these round
   differently, and `P(1,1)` diverges by 1 ULP first. Confirmed by capture at the gain step:
   `Cp_op` (the `interp2d` Cp lookup) and `P(2,1)` are **bit-identical**, while `S = P(1,1)+R_m`
   differs by 1 ULP (`0x3F987CF0906BC0BF` vs `…BE`). So `interp2d` is exonerated (its own later
   divergence at EKF call 321 is inherited, not causal); the seed is the covariance matrix
   arithmetic.
2. **`S = P(1,1) + R_m`** inherits the 1 ULP → **Kalman gain `K(2) = P(2,1)/S`** diverges (EKF call 4).
3. **`v_t = xh(2)`** (turbulent-wind state), updated by `K(2)*innovation`, crosses a ULP by
   **WSE call 551 ≈ t=13.8 s** — the first divergence in any controller output double.
   `WE_Vw = v_m + v_t`.
4. **`WE_Vw` → `VS_RefSpd` (setpoints) → `GenTq`** (VS-torque PI). `GenTq` is the **first
   rate-limited signal to diverge** — 3 ULPs at global rate-limiter call 12353 ≈ **t=38.6 s**
   (value ≈26854 N·m; confirmed inst 1 = the `GenTq` limiter, since VariableSpeedControl runs
   before PitchControl in `DISCON.F90`).
5. **Collective + per-blade pitch rate limiters** carry the difference in their internal **double**
   `LastSignal` state. It grows to ~2×10⁵ double-ULPs by t≈111 s while staying **below float32
   resolution the whole time** — every `avrSWAP` output is bit-identical, including `bld_pitch[4443]`.
6. **Zero crossing at t=111.1 s** (`bld_pitch[4444]`): the two builds' slightly different double
   `LastSignal` values round across a float32 boundary — gfortran to `2^-70`, g++ to exact `0`.
   Three blades because Scenario 16 has no IPC.

**Conclusion:** the origin is a **compiler codegen difference in the EKF covariance `MATMUL`
(Fortran intrinsic vs C++ unrolled loops)** — not a translation defect and not the rate limiter.
Both `-ffp-contract=off`, so it is summation-order/implementation, not FMA. The C++ EKF is a
faithful translation; the 1-ULP small-matrix rounding is inherent to the intrinsic-vs-loop choice.
The chase used the same instrumentation pattern as `run.sh` (dump doubles, diff call-by-call);
scratch trees `chase_f/` and `chase_cpp/` are gitignored and disposable.

## Disassembly — the reordering confirmed

Disassembled the compiled EKF covariance update in both builds and ran a decisive
mechanism test.

**gfortran routes the covariance `MATMUL` to libgfortran, which fuses and vectorizes.**
The WSE object emits a `bl _gfortran_matmul_r8` (it does *not* fully inline the covariance
matmuls). libgfortran is dynamically linked (`/lib/aarch64-linux-gnu/libgfortran.so.5`), and
`_gfortran_matmul_r8` contains **230 fused multiply-add instructions** (`fmla`/`fmadd`) and
**2-wide SIMD** (`v31.2d`) — i.e., it accumulates the dot products **fused** (one rounding per
step) and **reassociated** (SIMD partial sums). This routine is precompiled with the distro's
default flags, so ROSCO's `-ffp-contract=off` **does not reach it**.

**The C++ translation does neither.** Its hand-unrolled covariance loops
(`windspeedestimator.cpp`) compile under `-ffp-contract=off` to plain scalar `fmul`+`fadd`
(**0 FMA**) in source order.

**Behavioral confirmation — a three-way split.** Rebuilding only the C++ with FMA enabled
(`-ffp-contract=fast`) does **not** reproduce Fortran:

| Build | `MATMUL` implementation | `bld_pitch[4444]` |
|---|---|---|
| Fortran (reference) | gfortran → `_gfortran_matmul_r8`: **FMA + 2-wide SIMD** | `2^-70` (8.47e-22) |
| C++ `-ffp-contract=off` | hand loops, **no FMA**, scalar source order | `0.0` |
| C++ `-ffp-contract=fast` | hand loops, **FMA in C++ source order** | `~2^-71` (4.35e-22) |

Same algorithm, same inputs, **three different last-bit results**. Toggling C++ FMA alone can't
match Fortran because the residual depends on libgfortran matmul's *specific* fused-and-vectorized
accumulation order, which the hand-unrolled loops don't reproduce under any single flag. This
confirms both **fusion** (FMA) and **reordering** (SIMD reassociation) in gfortran's `MATMUL`
relative to the C++ translation — a small-matrix `MATMUL` effect, now
pinned to libgfortran `matmul_r8`.

(Capture note: the 1-ULP first appears in an *off-diagonal* covariance element `P(1,3)` at the
first EKF steps — the covariance MATMUL path — not the diagonal `P(1,1)` prediction, which is
identical at step 1. The seed is the covariance matrix multiply regardless of element.)

## Artifacts

- `ROSCO/scripts/repro_scenario16/` — instrumenters, probes, `run.sh`, README (committed procedure).
- Scratch build trees `ROSCO/repro_scratch/` (upstream Fortran) and `ROSCO/repro_scratch_cpp/`
  (integrated C++) are disposable and gitignored; regenerate with `run.sh`.
- Tracked working tree and `rosco/lib/libdiscon.so` were left byte-identical to their starting
  state (verified by md5).
