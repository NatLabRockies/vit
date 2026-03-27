# Session Reflection: Scenario 3/8 Investigation and 8-Scenario Integration Test
**Date:** 2026-03-26
**Status:** completed
**Scope:** Session summary, technical insights, project status

## What Was Accomplished

This session began with a known problem — Scenario 3 non-determinism producing a binary flip between two gen_torque states — and ended with all 8 scenarios passing: 612,000 float64 values byte-identical between pure Fortran and 27-function integrated C++. Two distinct bugs were found and fixed along the way.

### Bug 1: Scenario 3 Non-Determinism (scipy FITPACK)

The investigation followed a systematic elimination of hypotheses:

| Hypothesis | Test | Result |
|-----------|------|--------|
| Uninitialized Fortran variables | `-finit-local-zero` on ROSCO build | Still non-deterministic |
| FPCR modification | ARM64 register reads via inline asm | FPCR unchanged (0x00000000) |
| Spline construction | Cache `RectBivariateSpline` | Still non-deterministic |
| Spline evaluation reading stale stack | 64KB stack scrubber before `bispev` | **Fixed** (10/10 deterministic) |

**Root cause:** scipy's FITPACK `fpbisp` (Fortran spline evaluator) reads an uninitialized local variable from the stack. The value at that stack position depends on residuals from the preceding ROSCO CableControl Fortran call, whose stack layout varies with ASLR across process invocations. The 1-ULP Cp interpolation difference accumulates through 218 seconds of closed-loop physics feedback until gen_torque crosses a quantization boundary.

**Key insight:** The non-determinism is not in ROSCO at all — the Fortran controller produces identical outputs in both states. The 1-ULP difference originates in the Python physics loop's Cp interpolation, and only manifests when both `tune_controller()` (scipy optimization) and the CableControl Fortran code path have modified the stack state.

**Fix:** Monkey-patch in `vit_sim.py` — cache the `RectBivariateSpline` (so the FITPACK constructor doesn't refill the stack between scrub and evaluation) and scrub 64KB of stack before each `bispev` call via a C helper library.

### Bug 2: Scenario 8 NotchFilterSlopes Expression Grouping

**Root cause:** Fortran `DT**2.0*CornerFreq_**2.0` groups as `(DT²)*(CF²)` because `**` has higher precedence than `*`. The C++ translation had `Damp*DT*DT*CF*CF` which evaluates left-to-right as `(((Damp*DT)*DT)*CF)*CF` — different intermediate products, different IEEE 754 rounding. With `Moving=.TRUE.` (coefficients recomputed every timestep for the rotor-speed-tracking notch filter), this 1-ULP coefficient difference accumulated over 14,791 timesteps until gen_torque diverged by 16,900.

**Why KGen didn't catch it:** The KGen verification captured state from Scenario 6 where rootMOOP was zero. `0 * wrong_coefficient = 0 * correct_coefficient = 0`. Scenario 8 is the first test with non-zero rootMOOP through the NotchFilterSlopes filter path.

**Fix:** Pre-compute `DT2=DT*DT` and `CF2=CF*CF`, then use `Damp*DT2*CF2` to match the Fortran grouping.

### Scenarios 7 and 8 Added

Designed and implemented two synthetic input scenarios that exercise the 8 previously under-exercised functions with non-zero, varying inputs:

- **Scenario 7** (600s): Injects NacVane, NacHeading, FA_Acc_TT, NacIMU_FA_RAcc, rootMOOP via avrSWAP. Exercises YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl with real signals. First scenario to produce non-zero nac_yaw output.

- **Scenario 8** (400s): IPC with real gains (0.1, 0.01) + AWC_Mode=4 with rootMOOP feedback. Exercises IPC, ActiveWakeControl, NotchFilterSlopes with non-zero blade root moments. Immediately caught the NotchFilterSlopes expression grouping bug.

## Where We Are Now

- **27 of 28 functions** translated, KGen-verified (40/40 each), and integration-tested
- **8 scenarios**, 612,000 float64 values, ALL IDENTICAL across 3 consecutive runs
- **PitchControl** (28th) is translated and KGen-verified but excluded from integration because it depends on PitchSaturation (ControllerBlocks.f90, Phase 8), which is not yet translated
- **Test infrastructure** is robust: per-scenario process isolation, array-level byte-identical comparison, FITPACK stack scrubber for determinism, synthetic input scenarios for coverage

## Technical Insights

### Zero-input verification is necessary but not sufficient

The 1-DOF simulation has no yaw, tower, platform, or blade root moment dynamics. Functions processing these signals were "verified" against zero inputs — both in KGen (40/40) and the 6-scenario integration test. This proves the plumbing works (wrappers, struct passing, library loading) but not the arithmetic. Scenario 8's NotchFilterSlopes bug was invisible with zero inputs and only appeared with non-zero rootMOOP. Future translations should consider whether the KGen capture state exercises the function with non-trivial inputs, and synthetic scenarios should be designed proactively.

### Fortran `**` precedence is a recurring trap

The `**` precedence issue was already documented in TRANSLATION_TIPS.md from an earlier phase. It still got through in NotchFilterSlopes because the translation was done before that tip existed (or the tip wasn't applied). The `vit translate` command now includes TRANSLATION_TIPS.md in the prompt, which should prevent recurrence. But human-written translations need the same discipline.

### Platform-specific non-determinism is real

The FITPACK stack contamination bug is specific to our platform (aarch64 under QEMU/Colima on Apple Silicon). It might not reproduce on native x86_64 or native ARM64. This doesn't make it less real — it's undefined behavior in FITPACK that happens to be benign on most platforms. But it means our test infrastructure needs platform-specific workarounds, and results may not be portable without the scrubber.

### Binary search by function integration is the most reliable diagnostic

When code review of 3 suspect functions (IPC, AWC, ResController) found no obvious bug, binary search — integrating functions one at a time and testing — immediately identified NotchFilterSlopes as the culprit. For the Scenario 3 investigation, the mode flag bisection (removing one flag at a time) identified CableControl as the triggering code path. These empirical techniques are more reliable than reading code when the bug is subtle.

## What's Ahead

1. **Phase 8: ControllerBlocks.f90** — PitchSaturation is the gate for PitchControl integration. ControllerBlocks also contains WindSpeedEstimator, ComputeVariablesSetpoints, StateMachine, SetpointSmoother, and other orchestration functions.

2. **scipy bug report** — The FITPACK `fpbisp` uninitialized variable bug has a comprehensive writeup (dev note `202603261512`) ready for upstream reporting.

3. **INTEGRATION_TEST.md update** — Should reflect the 8-scenario protocol with Scenarios 7/8 and the scrubber requirement.

4. **CLAUDE.md update** — Clean build test steps should be updated for 8 scenarios.

## Dev Notes From This Session

| Timestamp | Topic |
|-----------|-------|
| `202603261447` | Scenario 3 root cause investigation (FPCR hypothesis — later disproven) |
| `202603261506` | Definitive root cause: stack scrubber proof |
| `202603261512` | scipy FITPACK bispev bug report writeup |
| `202603261539` | Scenario 3 fix implementation (cache + scrub) |
| `202603261543` | 6-scenario integration test all pass |
| `202603261628` | 8-scenario test (1-7 pass, 8 mismatch) |
| `202603261747` | Scenario 8 NotchFilterSlopes fix |
| `202603261820` | 8-scenario integration test ALL PASS (612k values) |

## ROSCO Commits This Session

| Hash | Description |
|------|-------------|
| `034c656` | Fix Scenario 3 non-determinism: stack scrubber for FITPACK bispev |
| `b046e00` | Add Scenarios 7 and 8: synthetic inputs for under-exercised functions |
| `cf004df` | Fix NotchFilterSlopes: match Fortran operator precedence |
| `0864ba5` | Add C++ stub files for Phase 7B functions |
| `914e23d` | Add integration test array directories to .gitignore |
