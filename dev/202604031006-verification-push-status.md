# Verification Push: Status and FloatingFeedback Bug
**Date:** 2026-04-03
**Status:** in-progress
**Scope:** Post-Phase-11 verification hardening

## What We Did

After completing Phase 11 (pure C++ libdiscon.so), we systematically pushed on the verification claims from every angle:

### 1. Clean Fortran Reset Infrastructure
- Fixed `reset_to_clean.sh` to produce a truly clean Fortran build — upstream CMakeLists.txt (`e8010f0`, no .cpp files), `-ffp-contract=off`, all C++ artifacts removed from `src/`
- Created `reset_to_upstream.sh` for unmodified upstream ROSCO builds
- Fixed `prepare_commit.sh` to restore CMakeLists.txt to committed (pure C++) state
- Fixed git staging side effect in `reset_to_clean.sh` (`git checkout <commit> --` stages changes)
- Added `ROSCO_Types.f90` restoration to `reset_to_clean.sh` (discovered when upstream checkout broke the type-dependent build)

### 2. Upstream Verification
- Built unmodified upstream ROSCO (`e8010f0`) and compared against our modified fork
- **672,000 values IDENTICAL** across 9 scenarios (excluding Scenario 4, upstream bug)
- Proved all ROSCO_Types.f90 modifications are behaviorally neutral: `BIND(C)`, `LOGICAL→INTEGER(C_INT)`, `CHARACTER(:) ALLOCATABLE→CHARACTER(1024)`, `AlreadyInitialized` default removal

### 3. AWC_complexangle Verification (Scenario 11)
- Created Scenario 11 (AWC_Mode=1) to exercise the COMPLEX→REAL decomposition — the only type modification without prior runtime verification
- **80,000 values IDENTICAL** across upstream, modified Fortran, and C++
- Closed the last gap in the type modification verification chain

### 4. Mode Coverage Audit
- Systematically audited all 27 ROSCO mode flags
- Identified 11 flags that were never varied from DISCON.IN defaults across all scenarios
- Created 12 new scenarios (12-23) organized by priority:
  - **High priority (12-14):** VS_ControlMode=1, VS_FBP=1, OL_Mode=1
  - **Medium priority (15-18):** AWC_Mode=2, Flp_Mode=3, WE_Mode=1, IPC_ControlMode=2
  - **Low priority (19-23):** PA_Mode=1/2, PF_Mode=1/2, VS_ConstPower=1, Fl_Mode=2, PRC_Mode=1, AWC_Mode=3/5, PS_Mode=0, SS_Mode=0

## Current Verification Totals

| Comparison | Values | Scenarios | Status |
|---|---|---|---|
| Upstream vs Modified Fortran | 1,672,000 | 22 (1-3, 5-23, excl. 4) | ALL IDENTICAL |
| Modified Fortran vs C++ | 1,292,000 | 18 (1-18) | 1 subnormal artifact (Scenario 16) |
| Modified Fortran vs C++ | — | 19-23 | Scenario 19 NaN bug; 20-23 IDENTICAL |

## Bug Found: FloatingFeedback NaN with Non-Zero Gains

### Symptom
Scenario 19 (Fl_Mode=2 + PA_Mode=1 + PF_Mode=1 + VS_ConstPower=1) produces NaN in the C++ output for `bld_pitch`, `gen_power`, `gen_speed` starting at timestep 1-2. Modified Fortran and upstream Fortran produce correct output.

### Isolation
- Fl_Mode=2 alone with default `Fl_Kp=0.0`: **no NaN** (zero gain masks the bug)
- Fl_Mode=2 alone with `Fl_Kp=0.01`: **NaN** (non-zero gain exposes it)
- Fl_Mode=1 alone with `Fl_Kp=0.01`: **also NaN** (not Fl_Mode=2-specific)
- Fl_Mode=1 alone with default `Fl_Kp=0.0`: **no NaN** (all previous scenarios used this)

### Root Cause (pending investigation)
The NaN originates from the FloatingFeedback C++ translation. With `Fl_Kp=0.0` (the DISCON.IN default), the pitch contribution is `0.0 * velocity = 0.0`, masking any NaN in the velocity computation. With non-zero gains, the NaN propagates to the pitch command and from there to the entire simulation.

The FloatingFeedback function computes velocity by integrating acceleration via PIController. The NaN likely comes from:
- The PIController integration of `FA_AccF` or `NACIMU_FA_AccF` producing NaN
- An initialization issue in the integrator state
- A field name or type mismatch causing uninitialized memory to be read

### Why It Wasn't Caught Earlier
1. **KGen verification** extracted FloatingFeedback from Scenario 7 with `Fl_Kp=0.0`. The function was verified with zero gains — arithmetically correct but trivially zero output.
2. **All 11 original sim-compare scenarios** used `Fl_Kp=0.0` (the DISCON.IN default). The floating feedback contribution was always zero, so any NaN in intermediate computations was multiplied by zero and vanished.
3. **The mode coverage audit** (Scenario 19) is the first test to exercise FloatingFeedback with non-zero gains in the C++ path.

### Impact
- Fl_Mode=1 or Fl_Mode=2 with non-zero gains is broken in the C++ build
- All previous verification (692,000 → 1,292,000 values) remains valid — those scenarios used `Fl_Kp=0.0`
- The bug does not affect upstream vs modified Fortran comparison (both work correctly)

## Remaining Gaps After Bug Fix

### Blocked by the bug (need re-verification)
- **Scenario 19's other modes**: PA_Mode=1, PF_Mode=1, VS_ConstPower=1 couldn't be verified C++ because Fl_Mode=2 in the same scenario produces NaN. Either fix the bug and re-run, or test these modes in a separate scenario without Fl_Mode.

### Not yet covered
- **CC_Mode=2, StC_Mode=2** — open-loop cable/structural control from OL file. Requires OL_Mode=1 with extra columns. Same interpolation pattern already tested by OL_Mode=1 (Scenario 14) and CC_Mode=1/StC_Mode=1 (Scenarios 3, 7).
- **PRC_Mode=2** — dynamic power rating with constant inputs. PRC_Mode=1 tested in Scenario 20. PRC_Mode=2 with PRC_Comm=0 would be straightforward.
- **AWC_Mode=3 with injected blade moments** — Scenario 21 runs AWC_Mode=3 in standard 1-DOF where rootMOOP is near-zero. A manual sim loop with synthetic moments (like Scenario 8 does for AWC_Mode=4) would exercise the closed-loop feedback more thoroughly.

### Not testable
- **Ext_Mode=1** — requires external DLL not available in test environment
- **ZMQ_Mode=1** — requires ZeroMQ server not available in test environment

## Next Steps

1. **Fix the FloatingFeedback NaN bug** — investigate root cause, fix the C++ translation, re-verify
2. **Re-run Scenario 19** — verify PA_Mode=1, PF_Mode=1, VS_ConstPower=1, Fl_Mode=2 all work
3. **Optionally add CC_Mode=2/StC_Mode=2 and PRC_Mode=2 scenarios**
4. **Update baseline_arrays** with the full scenario suite once all bugs are fixed

## Commits (this session)

| Repo | Hash | Description |
|------|------|-------------|
| ROSCO | `0beeb47` | Initial reset_to_clean.sh CMakeLists.txt restoration |
| ROSCO | `ee45cc8` | Fix git staging side effect |
| ROSCO | `6207239` | Clean Fortran reset (upstream CMake, remove C++ artifacts) |
| ROSCO | `b06d9cb` | Add ROSCO_Types.f90 restoration |
| ROSCO | `ed9f132` | Add reset_to_upstream.sh |
| ROSCO | `e55fa89` | Scenario 11: AWC_Mode=1 |
| ROSCO | `04c0fd6` | Scenarios 12-14: VS_ControlMode=1, VS_FBP=1, OL_Mode=1 |
| ROSCO | `173fd5d` | Scenarios 15-18: AWC_Mode=2, Flp_Mode=3, WE_Mode=1, IPC 2P |
| ROSCO | `b9332de` | Scenarios 19-23: PA/PF/ConstPower/Fl_Mode=2/PRC/AWC 3+5/PS/SS |
| VIT | `d6cb52e` | Dev note: clean Fortran reset |
| VIT | `fe1481f` | Dev note: upstream verification |
| VIT | `dcade91` | Dev note: Scenario 11 AWC_Mode=1 |
| VIT | `4795c42` | Dev note: mode coverage audit |
