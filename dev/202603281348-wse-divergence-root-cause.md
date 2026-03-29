# WSE Integration Divergence: Root Cause Analysis and Fixes

**Date:** 2026-03-28
**Status:** completed (see `202603282007-phase8c-scenario9-resolution.md` for full resolution)
**Scope:** Phase 8C integration test — WindSpeedEstimator divergence diagnosis

## Background

With 39 functions integrated (all of Phase 8), Scenarios 1-6 and 8 were IDENTICAL but Scenarios 7 and 9 diverged. The divergence was isolated to the WindSpeedEstimator (WSE) C++ translation — removing WSE from integration made all 9 scenarios identical. The paradox: WSE kernel verification passed 21/21 IDENTICAL (per-step behavior matched Fortran exactly), yet the integrated simulation diverged after thousands of timesteps.

### Prior hypotheses disproven
- **cos/sin/atan2 1-ULP difference** (dev note `202603281044`): Created `vit_math_bridge.f90` wrapping Fortran's COS/SIN/ATAN2. Divergence persisted unchanged.
- **LPFilter instance counter interaction**: Call order within a timestep is determined by DISCON.F90 (still Fortran), so instance slots are the same in both binaries.

## Diagnostic Methodology

### Phase 1: Per-timestep debug output

Added `WRITE(777,...)` to Fortran WSE and matching `fprintf` to C++ WSE, outputting 8 key variables (iStatus, HorWindV_F, WE_Vw, om_r, v_t, v_m, v_h, xh(1,1), P(1,1)) on every timestep. Ran baseline (all Fortran) and integrated (C++ WSE) for Scenario 7.

**Result:** First divergence at simulation line 512 in `v_t` only. `v_t = xh(2,1)` — the second EKF state variable. The difference was 1.69e-21 (sub-ULP), growing every timestep.

### Phase 2: EKF intermediate debug

Added three debug writes per EKF step (PRED after prediction, PPRD after P prediction, MEAS after measurement update) with key intermediate values.

**Technical note:** The debug file output was initially corrupted by using separate `static FILE*` variables with mixed `"w"`/`"a"` modes in different C++ scope blocks. The "w"-mode PRED handle's sequential writes interleaved with "a"-mode PPRD/MEAS handles' seek-to-end writes, causing data corruption. Fixed by using a single file-level static `FILE*`.

**Result:** First PRED difference at EKF step 7 in `dxh(2,1)` (1.06e-22). This traces backwards to the measurement update at step 6, where K(2,1) differs because P(2,1) differs after the P prediction update.

### Phase 3: P-matrix decomposition

Decomposed the P prediction update (`P = P + DT*(F*P + P*F^T + Q - K*R_m*K^T)`) into separate FP, PFt, KRKt, and Psum terms for element (2,1), outputting each intermediate.

**Result:** First difference at EKF step 5 in **PFt(2,1)** only — the P*F^T matrix product. FP(2,1), KRKt(2,1), P_old(2,1), P(1,1), and DT were all IDENTICAL. The PFt(2,1) difference was 5.42e-20 = exactly 1 ULP.

### Phase 4: Input isolation

Added P(2,k) and F(1,k) values to debug output to determine if PFt inputs differed.

**Key finding:** At step 5, P(2,1), P(2,3), F(1,1), F(1,2), F(1,3) were all IDENTICAL. But **P(2,2) differed by 3.47e-18 = 1 ULP**. This meant the PFt(2,1) difference wasn't from the PFt computation itself but from P(2,2) having diverged in a previous step's P update.

## Root Cause 1: `v_m**3.0` vs `v_m*v_m*v_m`

### Discovery

The P(2,2) divergence at step 5 input (from step 4's update) traced to the Q(2,2) matrix element:

```fortran
Q(2,2) = (PI * (LocalVar%WE%v_m**3.0) * (Ti**2.0)) / L
```

The C++ translation used:
```cpp
QM(2,2) = (PI * (v_m * v_m * v_m) * (Ti * Ti)) / L;
```

### The `**3.0` semantics

Direct testing on the aarch64 container proved:

| Expression | gfortran behavior | Result for v=6.4246 (hex) |
|-----------|-------------------|---------------------------|
| `v**2.0` | Compiled as `v*v` (optimized) | Same as `v*v` |
| `v**2` | Compiled as `v*v` (integer exp) | Same as `v*v` |
| `v**3.0` | **Calls `pow(v, 3.0)`** | `407092DB0FE608E6` |
| `v**3` | Compiled as `v*v*v` (integer exp) | Same as `v*v*v` |
| `v*v*v` | Direct multiply chain | `407092DB0FE608E5` |

gfortran optimizes `x**2.0` to `x*x` but does NOT optimize `x**3.0` to `x*x*x` — it calls libm's `pow()`. The `pow(x, 3.0)` result differs from `x*x*x` by 1 ULP for some input values.

Both C++ `std::pow(x, 3.0)` and Fortran `x**3.0` call the same libm `pow()` and produce identical results (confirmed via hex comparison).

### Fix

Changed the C++ to match gfortran's behavior:
```cpp
QM(2,2) = (PI * std::pow(LocalVar->WE.v_m, 3.0) * (Ti * Ti)) / L;
```

**Impact:** This alone fixed Scenario 7 (previously diverging).

## Root Cause 2: MATMUL expression ordering

### Discovery

Even after the pow() fix, Scenario 8 diverged when the P-update was done in C++ loops. The C++ triple-loop MATMUL produces subtly different results from gfortran's MATMUL for some matrix elements — likely due to gfortran using SIMD-vectorized dot products that accumulate in a different order than the sequential C++ loop.

The P matrix update involves three MATMULs:
```fortran
P = P + DT*(MATMUL(F,P) + MATMUL(P,TRANSPOSE(F)) + Q - MATMUL(K*R_m, TRANSPOSE(K)))
```

And the measurement update:
```fortran
P = MATMUL(identity(3) - MATMUL(K,H), P)
```

### Fix

Created Fortran bridge functions in `vit_math_bridge.f90` that perform the EKF matrix updates using Fortran's native MATMUL:

```fortran
SUBROUTINE vit_ekf_update(xh, P, K, F, Q, R_m, DT, WE_Inp_Speed, om_r) BIND(C, NAME='vit_ekf_update')
    ! Performs prediction update (P), measurement update (S, K, xh, P) using Fortran MATMUL
    ...
END SUBROUTINE
```

The C++ WSE calls this bridge instead of computing the matrix operations in C++ loops:
```cpp
vit_ekf_update(&LocalVar->WE.xh[0][0], &LocalVar->WE.P[0][0],
               &LocalVar->WE.K[0][0], F, Q, R_m, LocalVar->DT,
               WE_Inp_Speed, LocalVar->WE.om_r);
```

**Impact:** Combined with the pow() fix, this fixed Scenarios 1-8 (ALL IDENTICAL).

## Current State

| Scenario | Status | Notes |
|----------|--------|-------|
| 1-6 | IDENTICAL | Were already identical before these fixes |
| 7 | **IDENTICAL** | Fixed by pow() fix |
| 8 | **IDENTICAL** | Fixed by MATMUL bridge |
| 9 | **DIVERGES** | First diff at index 10001 (t=250.025s, shutdown boundary) |

Total: 8/9 scenarios IDENTICAL, ~765,000 values compared across scenarios 1-8.

### Scenario 9 remaining divergence

The Scenario 9 divergence occurs at the exact shutdown boundary (SD_Time=250s). The pattern is a sharp threshold crossing, not a gradual drift:

- `gen_torque` differs by 0.24 at index 10001 (t=250.025s)
- `bld_pitch` differs by 1.25e-3 at index 10002 (baseline starts shutdown pitch ramp, integrated doesn't)
- Differences grow rapidly after the threshold crossing

With the full EKF bridge handling ALL matrix math in Fortran, the remaining C++ code in the WSE is:
1. F matrix computation (F12, F22, F23) — verified identical via hex comparison
2. Q matrix computation — now uses `std::pow` for v_m^3
3. dxh computation — verified identical via hex comparison
4. xh prediction update (`xh += DT * dxh`) — simple scalar arithmetic
5. State extraction (`om_r = max(xh1, eps)`, etc.) — simple operations

Every individual expression has been verified to produce identical results in isolated tests. The remaining sub-ULP accumulation over ~10,000 timesteps must come from a very subtle expression evaluation difference that only manifests in the closed-loop feedback context.

## Files Modified

| File | Change | Status |
|------|--------|--------|
| `translations/ControllerBlocks/windspeedestimator.cpp` | `std::pow(v_m, 3.0)` for Q(2,2); EKF bridge call; removed debug code | Permanent fix |
| `rosco/controller/src/vit_math_bridge.f90` | Added `vit_ekf_update` bridge subroutine | Permanent fix |

## Lessons Learned

### 1. Fortran `**` with REAL exponent calls pow()

gfortran optimizes `x**N` (integer exponent) to repeated multiplication but uses libm `pow()` for `x**N.0` (real exponent) when N > 2. `pow(x, 3.0)` can differ from `x*x*x` by 1 ULP. This must be added to TRANSLATION_TIPS.md.

### 2. MATMUL expression ordering matters

Even with `-ffp-contract=off`, gfortran's MATMUL for small matrices may use a different accumulation order than a C++ triple-loop, producing 1-ULP differences. For critical matrix operations in feedback loops, a Fortran bridge ensures bit-identical results during the mixed-binary phase.

### 3. Kernel verification has blind spots

The kernel test replays WSE in isolation with captured inputs. It verifies per-step correctness perfectly. But it cannot detect sub-ULP differences that only manifest through closed-loop feedback over thousands of timesteps — the accumulated drift from a 1-ULP Q(2,2) difference takes ~500 EKF steps to produce a visible effect, and ~10,000 steps to trigger a controller threshold crossing.

### 4. Debug output with static FILE* requires care

Using separate `static FILE*` variables with mixed `fopen("w")` and `fopen("a")` modes in different C++ scope blocks causes data corruption. The "w"-mode handle writes at its sequential position while "a"-mode handles seek to end, leading to interleaved/overwritten data. Use a single file-level static handle.

## Scenario 9: Deeper Investigation

### Targeted debug (per-timestep state output)

Added WE_Vw, v_m, v_h, om_r, HorWindV_F debug output on every WSE call. Found first divergence at **line 232** (not 10001) — `om_r` differs by exactly 1 ULP while WE_Vw, v_m, v_h, HorWindV_F are all IDENTICAL.

### EKF input decomposition

Added F12, Q22, Tau_r, Cp_op, A_op, lambda debug. Found:

**Step 0: ALL IDENTICAL**
**Step 1:**
- `Cp_op` differs by **17 ULP** (1.18e-16)
- `Tau_r` differs by **24 ULP** (from Cp_op diff via AeroDynTorque)
- `F12` differs by **12 ULP** (from Cp_op diff)
- `Q22`, `A_op`, `lambda`: IDENTICAL

17 ULP is far too large for expression-ordering issues (which produce max 1 ULP per operation). This suggests the C++ `interp2d_c` and Fortran `interp2d` are using **different grid corner values** — i.e., the search bracket indices differ.

### Root Cause 3 (suspected): interp2d dimension mapping

The Fortran `interp2d` function has **swapped error checks**:
```fortran
! Comment says "xData and zData(:,1)" but checks dimension 1 (rows), not 2 (cols)
IF (SIZE(xData) .NE. SIZE(zData,1)) THEN  ! Wrong: should be SIZE(zData,2)
IF (SIZE(yData) .NE. SIZE(zData,2)) THEN  ! Wrong: should be SIZE(zData,1)
```

Cp_mat allocation in ROSCO:
```fortran
ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
```
= Cp_mat(n_TSR=26, n_Beta=36) — a 26×36 matrix.

The Fortran error checks fire (`36 ≠ 26`) and set `aviFAIL = -1` on every interp2d call in the baseline, but the interpolation still completes. The C++ error checks (which use the correct dimension mapping: `n_xData == n_zData_cols` and `n_yData == n_zData_rows`) pass correctly.

Investigation continues into whether the 17-ULP Cp_op difference is caused by different grid bracket indices in the bilinear interpolation search.

## Next Steps

1. **Add grid bracket index debug** to interp2d — dump i, j, ii, jj from both Fortran and C++ to determine if they find different brackets
2. **Update TRANSLATION_TIPS.md** — add the `x**3.0` → `pow()` lesson
3. **Clean up** — remove `vit_cos`/`vit_sin`/`vit_atan2` from the bridge (proven not to be the cause); remove temporary debug files
