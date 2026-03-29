# Phase 8C Complete: All 9 Scenarios Bit-Identical

**Date:** 2026-03-28
**Status:** completed
**Scope:** Phase 8C integration test — resolving Scenario 7, 8, and 9 divergences with 39 integrated C++ functions

## Summary

The Phase 8C integration test produced bit-identical results across all 9 scenarios and 672,000 total values after fixing four root causes. This note chronicles the entire investigation — from initial divergence through three false starts and two context compactions to the final resolution.

## Background

Phase 8 integrated the remaining ControllerBlocks module functions into the C++ translation: PitchSaturation (8A), StateMachine/SetpointSmoother/PowerControlSetpoints/RefSpeedExclusion/ComputeVariablesSetpoints (8B), and Shutdown/Startup/WindSpeedEstimator (8C). With all 39 functions integrated, Scenarios 1-6 were immediately identical, but Scenarios 7, 8, and 9 diverged. The divergence was isolated to the WindSpeedEstimator (WSE) — removing only WSE from integration made all 9 scenarios identical.

**The paradox:** WSE kernel verification passed 21/21 IDENTICAL (per-step behavior matched Fortran exactly). Yet the integrated simulation diverged after thousands of timesteps.

## Fix 1: `v_m**3.0` → `std::pow(v_m, 3.0)` (Scenario 7)

### Discovery

Per-timestep debug output in both Fortran and C++ WSE narrowed the first divergence to `v_t = xh(2,1)` at EKF step 512 — a sub-ULP (1.69e-21) difference that grew every step. Decomposing the P matrix prediction update into intermediate terms (FP, PFt, KRKt) isolated the source to PFt(2,1), which differed by exactly 1 ULP at step 5. This traced back to P(2,2) from the previous step, which depended on Q(2,2):

```fortran
Q(2,2) = (PI * (LocalVar%WE%v_m**3.0) * (Ti**2.0)) / L
```

The C++ translation used `v_m * v_m * v_m`. But gfortran compiles `x**3.0` (REAL exponent) as a call to libm `pow(x, 3.0)`, NOT as `x*x*x`. Confirmed via hex comparison on aarch64:
```
v=6.4246: pow()=407092DB0FE608E6, x*x*x=407092DB0FE608E5 (1 ULP difference)
```

gfortran optimizes `x**2.0` → `x*x` and `x**2` → `x*x` and `x**3` (INTEGER) → `x*x*x`, but NOT `x**3.0` (REAL exponent).

### Fix
```cpp
// Before: (PI * (v_m * v_m * v_m) * (Ti * Ti)) / L
// After:  (PI * std::pow(v_m, 3.0) * (Ti * Ti)) / L
```

**Impact:** Fixed Scenario 7.

## Fix 2: EKF MATMUL via Fortran bridge (Scenario 8)

### Discovery

After the pow() fix, Scenario 8 still diverged. The same P-matrix decomposition technique found that PFt(2,1) diverged by 1 ULP at EKF step 5 despite ALL inputs (P and F matrix elements) being identical. The divergence was in the matrix multiplication itself: gfortran's `MATMUL` intrinsic for 3×3 matrices produces subtly different results from explicit C++ triple loops for some element computations.

### Fix

Created `vit_ekf_update` in `vit_math_bridge.f90` — a Fortran BIND(C) subroutine that performs the entire EKF prediction + measurement update using Fortran's native MATMUL. The C++ WSE calls this bridge instead of doing matrix math in C++.

**Impact:** Fixed Scenario 8.

## Disproven hypotheses (before Scenario 9 work)

- **cos/sin/atan2**: Created Fortran bridge wrappers (`vit_cos`, `vit_sin`, `vit_atan2`). Divergence unchanged. NOT the cause.
- **LPFilter instance counter interaction**: Call order determined by DISCON.F90 (Fortran), so instance slots consistent.

## Scenario 9: The long investigation

### Phase 1: Misaligned diagnostic (abandoned)

The initial diagnostic used per-EKF-step output, which only fired when `WE_Mode==2 && WE_Op>0 && RestartWSE!=0`. This produced 9,651 baseline lines vs 11,229 integrated lines — a 1,578-step difference because `WE_Op` transitions happen at different simulation timesteps. Step N in baseline ≠ step N in integrated. All conclusions from this comparison were **invalid**.

**Lesson learned:** Always align debug output by simulation time (sequential counter or absolute timestamp), never by internal state transitions.

### Phase 2: Time-aligned diagnostics (correct approach)

Added one debug line per WSE *call* (not per EKF step), producing 11,999 perfectly aligned lines each.

**Round 1 (WE_Vw + WE_Op):** First WE_Vw diff at call 321 — 1 ULP, intermittent. WE_Op doesn't differ until call 10422.

**Round 2 (+ Cp_op, pitch_R2D, lambda, Tau_r):** Cp_op first differs at call 212 by 17 ULP (from interp2d). pitch_R2D differs at call 212 by 2 ULP. lambda identical at 212 — pitch is the source.

**Round 3 (+ BlPitchCMeas):** BlPitchCMeas differs at call 203 by 2 ULP — **BEFORE any WSE output diverges** (WE_Vw identical through call 320). The simulation feeds different pitch into the controller before WSE has had any effect.

### Phase 3: The interp2d red herring

The Fortran `interp2d` has misleading **comments** and **error messages** that reference swapped dimensions:
```fortran
! Comment: "Are xData and zData(:,1) the same size?"
! Actual check:
IF (SIZE(xData) .NE. SIZE(zData,2)) THEN    ! ← checks dim 2, not dim 1
```

The dev note (written during the investigation) read the comments instead of the IF conditions and incorrectly concluded the Fortran checks were swapped. Based on this wrong premise, the C++ `interp2d_cpp.cpp` was modified to "match Fortran's swapped behavior" — actually introducing a real bug:

```cpp
// WRONG (introduced by investigation):
if (n_xData != n_zData_rows) {    // fires for non-square matrices!
// CORRECT (original translation):
if (n_xData != n_zData_cols) {    // passes correctly
```

**What actually happened:** The original Fortran has CORRECT checks that PASS for ROSCO's Cp_mat(nTSR, nBeta) with xData=Beta_vec, yData=TSR_vec. The "swap test" during investigation introduced aviFAIL=-1 into the integrated binary, making things worse (divergence at index 211 instead of 10001). This was misinterpreted as "the aviFAIL hypothesis is disproven" when actually the test created a new, worse bug. **The swapped checks were never reverted.**

### Phase 4: Context compaction and fresh investigation

After two context compactions (conversation exceeded context limits), we returned with the dev note `202603281741-wse-scenario9-fresh-start.md` as the knowledge anchor. The new investigation proceeded systematically:

#### Step 1: Fix the interp2d check swap (known broken state)

Reverted `interp2d_cpp.cpp` to use correct checks from `translations/Functions/interp2d.cpp`. Also removed leftover fprintf diagnostic code from `windspeedestimator_cpp.cpp`.

**Result:** Scenarios 1-8 ALL IDENTICAL, Scenario 9 still diverges (massive: 1.57 rad pitch, 2.45 MW power at index ~11000).

#### Step 2: DISCON-level diagnostics

Added WRITE statements to DISCON.F90 (Fortran, shared by both baseline and integrated) — after ReadAvrSWAP and after the main control block. Output: aviFAIL, BlPitchCMeas, PitComAct(1), HorWindV_F, WE_Vw, WE_VwI.

**Result:** First diff at n_DT=202 — `BlPitchCMeas` and `PitComAct(1)` differ by ~6 ULP. aviFAIL=0 in both (interp2d fix confirmed working). HorWindV_F, WE_Vw, WE_VwI all IDENTICAL.

Post-control-block file: PitComAct first differs at n_DT=201 (after PitchControl runs), while all WSE outputs are identical. Something between WSE and PitchControl produces the difference.

#### Step 3: Inter-function diagnostics

Added WRITE(779) after each function in the main control block (Shutdown, PowerControlSetpoints, Startup, ComputeVariablesSetpoints, VariableSpeedControl).

**Breakthrough:** The very first SD line (n_DT=2) showed `SD_BlPitchF` (Shutdown's filtered blade pitch) differs: baseline ≈ 5e-5, integrated = **exactly 0.0**. The difference started at n_DT=2 and continued every single timestep — not a 1 ULP drift but a total failure.

## Fix 3: interp2d dimension checks (reverted swap)

As described above — the `interp2d_cpp.cpp` had been modified during investigation to swap the dimension checks. This set `aviFAIL=-1` on every interp2d call, which caused a controller lockup because `SetParameters` (the only aviFAIL reset) is gated by `ErrVar%aviFAIL >= 0` at DISCON.F90 line 84. Reverted to correct checks.

**Impact:** Fixed the aviFAIL lockup. Scenarios 1-8 ALL IDENTICAL. Scenario 9 still diverges due to the Shutdown bug.

## Fix 4: POSIX `shutdown()` symbol collision (Scenario 9)

### Discovery

The C++ Shutdown translation defined a function named `shutdown()`. When `shutdown_c()` (the Fortran-callable wrapper) called `shutdown()`, the dynamic linker resolved it through the PLT (Procedure Linkage Table) to **libc's `shutdown(int sockfd, int how)`** — the POSIX socket shutdown system call — instead of our C++ function.

### How it was found

1. Added `WRITE(888)` to the Fortran Shutdown wrapper in ControllerBlocks.f90 — **it fired** (wrapper is called).
2. Added `fprintf(stderr)` inside the C++ `shutdown()` function — **it never fired** (C++ function not reached).
3. Added `fprintf(stderr)` inside `shutdown_c()` (the wrapper) — **it fired**, confirming `shutdown_c` is entered.
4. Added `fprintf` before AND after the `shutdown()` call inside `shutdown_c()` — "before" fired, "after" fired, but `shutdown()` internal prints never fired.
5. **Diagnosis:** `shutdown_c` calls `shutdown()`, but the PLT resolves `shutdown` to libc. The POSIX `shutdown(int, int)` receives pointer args reinterpreted as ints, does nothing useful (EBADF), and returns. Our function never executes. `SD_BlPitchF` stays at its default 0.0.

**Docker timestamp issue:** During this investigation, several rounds of edits appeared to have no effect because Docker's volume mount caching prevented cmake from detecting source file changes. The fix was to `touch` source files from inside the container before rebuilding.

### Why kernel verification didn't catch it

The Shutdown kernel test extracted cases from Scenario 7 (where `SD_Mode=0`). With SD_Mode=0, Shutdown is gated by `IF (CntrPar%SD_Mode > 0)` in DISCON.F90 and never called. The kernel captured trivial (no-op) state. The bug only manifests when Shutdown is actively filtering signals, which only happens in Scenario 9 (SD_Mode=1).

### Why it only affected Scenario 9

Scenarios 1-8 all have `SD_Mode=0`. The Shutdown function is never called, so the POSIX collision has no effect. Scenario 9 has `SD_Mode=1`, so Shutdown runs every timestep. The missing `SD_BlPitchF` filter output accumulates: the shutdown pitch threshold is never approached via the filtered value (it's stuck at 0.0), while the Fortran baseline computes the correct filtered pitch and eventually triggers shutdown.

### Fix

Renamed `shutdown()` to `shutdown_impl()` in both `translations/ControllerBlocks/shutdown.cpp` and `rosco/controller/src/shutdown_cpp.cpp`:

```cpp
// Before: void shutdown(localvariables_t* ...) { ... }
// After:  void shutdown_impl(localvariables_t* ...) { ... }

// Wrapper updated:
void shutdown_c(...) {
    shutdown_impl(LocalVar, CntrPar, objInst, ErrVar);
}
```

**Impact:** Fixed Scenario 9. All 9 scenarios now bit-identical.

## Final Results

```
ALL 9 SCENARIOS IDENTICAL — 672,000 total values compared
```

| Scenario | Values | Status | Fix(es) that resolved it |
|----------|--------|--------|--------------------------|
| 1 | 200,000 | IDENTICAL | (already identical) |
| 2 | 12,000 | IDENTICAL | (already identical) |
| 3 | 80,000 | IDENTICAL | (already identical) |
| 4 | 20,000 | IDENTICAL | (already identical) |
| 5 | 80,000 | IDENTICAL | (already identical) |
| 6 | 20,000 | IDENTICAL | (already identical) |
| 7 | 120,000 | IDENTICAL | Fix 1 (pow) |
| 8 | 80,000 | IDENTICAL | Fix 2 (MATMUL bridge) |
| 9 | 60,000 | IDENTICAL | Fix 3 (interp2d revert) + Fix 4 (shutdown rename) |

## Files Modified (Permanent)

| File | Change |
|------|--------|
| `translations/ControllerBlocks/windspeedestimator.cpp` | `std::pow(v_m, 3.0)` for Q(2,2); `vit_ekf_update` bridge call |
| `translations/ControllerBlocks/shutdown.cpp` | Renamed `shutdown` → `shutdown_impl` to avoid POSIX collision |
| `translations/Functions/interp2d.cpp` | Correct dimension checks (unchanged, was already correct) |
| `rosco/controller/src/vit_math_bridge.f90` | `vit_ekf_update` Fortran bridge subroutine |
| `rosco/controller/src/interp2d_cpp.cpp` | Reverted swapped dimension checks to match translations/ |
| `rosco/controller/src/shutdown_cpp.cpp` | Renamed `shutdown` → `shutdown_impl` |
| `rosco/controller/src/windspeedestimator_cpp.cpp` | Removed diagnostic fprintf code |

## Lessons Learned

### 1. Never name C++ functions after POSIX system calls

`shutdown`, `close`, `open`, `read`, `write`, `select`, `poll`, `socket`, `connect`, `bind`, `listen`, `accept`, `send`, `recv` — all are POSIX names. In shared libraries compiled with `-fPIC`, function calls go through the PLT, and the dynamic linker may resolve them to libc instead of the local definition. This happens silently — no linker warning, no runtime error, just wrong behavior.

**Rule for VIT:** Add `shutdown` to the list of reserved names in TRANSLATION_TIPS.md. Consider adding a VIT lint check that flags C++ function names matching common POSIX/libc functions.

### 2. Fortran comments lie; read the IF conditions

The Fortran `interp2d` had correct checks but misleading comments and error messages that said "zData(:,1)" while actually checking `SIZE(zData,2)`. Reading the comments instead of the code led to a wrong diagnosis and an unreverted "test" that introduced a new bug. Always read the actual control flow, not the comments.

### 3. `x**3.0` ≠ `x*x*x` generalizes

Any Fortran `x**N.0` (REAL exponent, N > 2) compiles to `pow()`. Add to TRANSLATION_TIPS.md:
- `x**2` or `x**2.0` → `x*x` (gfortran optimizes)
- `x**3` (integer) → `x*x*x` (gfortran optimizes)
- `x**3.0` (real) → `std::pow(x, 3.0)` (gfortran calls libm pow)

### 4. Always align diagnostics by simulation time

Per-internal-state-step output creates alignment assumptions that fail when state transitions differ between baseline and integrated. Use a sequential counter that fires on every DISCON call.

### 5. Docker volume mount caching

When editing files on the Mac that are mounted into a Docker container, cmake may not detect changes due to timestamp caching. Fix: `touch` the file from inside the container, or use `rm -rf *` in the build directory for a full rebuild.

### 6. Kernel verification has blind spots

Kernel tests capture state from specific scenarios. If a function has behavior gated by mode flags (like `SD_Mode`), the kernel test may only exercise the trivial path. Scenario 9 was specifically created to exercise Startup/Shutdown/TRA, but the kernel extraction used Scenario 7 (where SD_Mode=0). A future improvement: ensure each function's kernel extraction uses a scenario that exercises all non-trivial code paths.

### 7. Test your "reverts"

When doing an experiment that changes production code (like swapping dimension checks), always verify the revert actually happened before proceeding. The unrevereted swap persisted for the entire Scenario 9 investigation, causing confusion and masking the real root cause.

## Related Dev Notes

- `202603281044` — cos/sin/atan2 bridge investigation (disproven)
- `202603281348` — WSE divergence root cause analysis (Fixes 1 & 2)
- `202603281741` — Scenario 9 fresh start (time-aligned diagnostics, aviFAIL investigation)

## Timeline

The entire investigation spanned a single day (2026-03-28) across multiple conversation sessions with two context compactions. The bulk of the time was spent on Scenario 9 — the first two fixes (pow, MATMUL) were identified relatively quickly through systematic EKF decomposition, but Scenario 9 required:

1. Discovering and abandoning the misaligned EKF-step diagnostic
2. Building the time-aligned DISCON-call diagnostic framework
3. Tracing BlPitchCMeas → PitComAct feedback loop
4. Investigating and incorrectly "disproving" the aviFAIL hypothesis (due to misreading Fortran comments)
5. Context compaction #1 → fresh start from dev note
6. Continued time-aligned tracing → BlPitchCMeas differs at call 203
7. Context compaction #2 → fresh investigation
8. Discovering the unrevereted interp2d swap
9. Discovering the POSIX `shutdown()` collision via inter-function diagnostics
10. Confirming the fix across all 9 scenarios
