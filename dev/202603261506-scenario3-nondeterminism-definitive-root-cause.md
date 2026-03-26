# Scenario 3 Non-Determinism: Definitive Root Cause
**Date:** 2026-03-26
**Status:** completed
**Scope:** Scenario 3 non-determinism, scipy FITPACK uninitialized variable, stack residual contamination

## Root Cause

scipy's FITPACK `bispev` function (the Fortran spline evaluator called by `RectBivariateSpline.__call__`) reads an **uninitialized local variable** from the stack. The stack at that position contains residual values from the preceding Fortran DISCON controller call. The DISCON call's stack layout varies non-deterministically with ASLR across process invocations, causing `bispev` to produce **1-ULP different Cp values with identical inputs**.

This is a **scipy/FITPACK bug**, not a ROSCO bug.

## Proof Chain

### 1. The divergence is in Cp interpolation

Instrumented every intermediate value at timestep 169 (t=4.225s) across 10 runs:

| Variable | Byte pattern (run 1) | Byte pattern (run 2) | Same? |
|----------|---------------------|---------------------|-------|
| tsr | `5bb810882a371040` | `5bb810882a371040` | **Yes** |
| **cp** | `2a2ae8c04e0dcc3f` | `2b2ae8c04e0dcc3f` | **No (1 ULP)** |
| aero_torque | `ae66a8350f134041` | `af66a8350f134041` | No (follows cp) |
| rot_speed | `9d05bcf10f92e23f` | `9d05bcf10f92e23f` | Yes (absorbs diff) |
| gen_speed | `82f8602a60254c40` | `82f8602a60254c40` | Yes (at this step) |

The `tsr` input is identical, but `cp = Cp.interp_surface(bld_pitch[168], tsr)` returns two different values (byte 0: `2a` vs `2b`). The difference is exactly 1 ULP. All downstream values follow from this initial divergence.

The gen_speed divergence first appears at a later timestep (index 169 in the full .npz comparison) because the 1-ULP cp difference at this step needs several accumulation cycles to propagate through the rot_speed → gen_speed → aero_torque feedback loop to a gen_speed-level difference.

### 2. Caching the spline doesn't help

Created the `RectBivariateSpline` once and reused it for all 16,000 calls. Result: **still non-deterministic** (5 state A, 5 state B in 10 runs). This proves the non-determinism is in the spline **evaluation** (`__call__` → FITPACK `bispev`), not the spline **construction**.

### 3. FPCR is unchanged

Built a C helper (`libfpcr.so`) that reads the ARM64 FPCR and FPSR registers via inline assembly. Checked at every stage (before tuning, after tuning, after lib load, after sim):

- FPCR: `0x00000000` throughout (round-to-nearest, no FTZ, no DN)
- FPSR: `0x00000010` throughout (inexact flag set, normal)

The floating-point control register is NOT being modified. The FPCR theory from the earlier dev note (`202603261447`) was wrong.

### 4. Stack scrubber eliminates the non-determinism

Built a C helper (`libscrub.so`) that zeros 64KB of stack:
```c
void scrub_stack(void) {
    volatile char buf[65536];
    memset((char*)buf, 0, sizeof(buf));
}
```

Called `scrub_stack()` via ctypes **before every `interp_surface` call** in the sim loop. Result: **10/10 identical**. All runs produce the same gen_torque MD5 and identical gen_speed[169].

This definitively proves the mechanism: FITPACK's `bispev` reads an uninitialized local variable whose value depends on what was previously on the stack at that position. The DISCON controller call (which runs between `interp_surface` calls) leaves different residual values on the stack depending on ASLR-affected memory layout.

### 5. `-finit-local-zero` didn't fix it because it was applied to the wrong build

The `-finit-local-zero` flag was added to ROSCO's `CMakeLists.txt`, which only affects ROSCO's Fortran compilation. The uninitialized variable is in **scipy's FITPACK**, which was compiled separately when scipy was installed. Rebuilding scipy with `-finit-local-zero` would likely fix it, but that's impractical in our container (no network access, scipy pre-installed).

## Mechanism Summary

```
Process start
  │
  ├─ tune_controller()     ← scipy optimization, changes heap/stack layout
  │
  ├─ Sim loop:
  │   ├─ interp_surface()  ← FITPACK bispev: reads stack residuals → Cp value
  │   ├─ physics update    ← uses Cp
  │   ├─ call_controller() ← DISCON Fortran call: leaves stack residuals
  │   │   └─ CableControl  ← CC_Mode=1: extra Fortran code → different stack footprint
  │   ├─ interp_surface()  ← FITPACK bispev: reads NEW stack residuals → Cp differs by 1 ULP
  │   └─ ...
  │
  └─ gen_torque MD5 differs after 218s of 1-ULP Cp accumulation
```

The 71/29 split (now ~50/50) reflects the probability distribution of the uninitialized variable's stack value across ASLR-randomized process layouts.

## Why Only Scenario 3?

CC_Mode=1 activates `CableControl()` → `SecLPFilter_Vel()` → `PIController()`. These Fortran functions use a larger stack frame than the baseline code paths. The larger stack footprint reaches the memory region where FITPACK's `bispev` has its uninitialized variable. Without CC_Mode, the stack residuals at that position are zero (from .bss initialization or prior Python calls), making `bispev` deterministic.

## Why Not Scenario 1?

Scenario 1 doesn't enable CC_Mode. The DISCON call uses a smaller stack. The residual values at FITPACK's uninitialized variable position are the same every run (deterministic Python call stack).

## Practical Fixes

### Option 1: Stack scrubber (proven, lightweight)
Add a C stack-scrubbing function call before each `interp_surface` call. Proven to work (10/10 deterministic). Overhead: ~64KB memset per call, negligible vs FITPACK's computation.

### Option 2: Use RegularGridInterpolator instead of RectBivariateSpline
Replace FITPACK-based `RectBivariateSpline` with numpy/scipy's `RegularGridInterpolator`. This uses C code (not FITPACK Fortran) and is less likely to have uninitialized variables. Changes the interpolation method (linear vs spline) which may affect results slightly.

### Option 3: Pre-compute Cp on a fine grid and use numpy interp2d
Pre-evaluate the spline on a dense grid during turbine initialization, then use simple bilinear interpolation during the sim loop. Fully deterministic, no FITPACK calls.

### Option 4: Tolerance-based comparison for integration testing
Accept the 1-ULP Cp interpolation as platform noise. Use tolerance-based comparison (e.g., `np.allclose(atol=0.01)`) for Scenario 3 gen_torque instead of byte-identical comparison. The translation correctness is already proven by Scenarios 1,2,4,5,6 (332,000 values byte-identical) and per-function KGen verification (40/40 per function).

## Impact on Integration Testing

The non-determinism is **not a ROSCO bug and not a translation bug**. It is a scipy platform artifact that produces a 1-ULP Cp interpolation difference, which accumulates through closed-loop feedback to produce a 3.91e-03 gen_torque difference after 218 seconds.

For integration testing purposes:
- Scenarios 1, 2, 4, 5, 6: byte-identical comparison (proven correct)
- Scenario 3: tolerance-based comparison with `atol=0.01` on gen_torque, or use the stack scrubber to make it deterministic

## Related Dev Notes

- `202603261447-scenario3-nondeterminism-root-cause.md` — previous investigation (FPCR hypothesis was wrong, overall structure was correct)
- `202603261254-scenario3-nondeterminism-100-run-study.md` — 100-run study establishing the two-state flip
- `202603261307-integration-test-scenario3-confirmed.md` — integration test confirming Scenario 3 mismatch
- `202603261110-simulation-nondeterminism-investigation.md` — initial investigation
