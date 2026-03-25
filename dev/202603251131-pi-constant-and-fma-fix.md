# Fix PI Constant Mismatch and Missing -ffp-contract=off
**Date:** 2026-03-25
**Status:** completed
**Scope:** C++ translations, vit.yaml, TRANSLATION_TIPS.md, CLAUDE.md

## Discovery

While investigating the CableControl heap crash, we examined why CableControl and ActiveWakeControl had IN_TOL fields instead of IDENTICAL. The earlier explanation — "callee bridge overhead" — was questioned and found to be unsubstantiated. Investigating the actual cause revealed two bugs.

## Bug 1: PI Constant Mismatch

**Root cause:** ROSCO's `Constants.f90` defines `PI = 3.14159265359` (12 significant digits). Two C++ translations used the C standard library's `M_PI = 3.14159265358979323846` (full double precision) instead. The difference is `2.07e-13`.

```
ROSCO PI: 3.14159265359000006157e+00
Full  PI: 3.14159265358979311600e+00
Diff:     2.06945571790129179135e-13
```

This propagated through `CornerFreq = 2*PI/CC_ActTau` in CableControl and through angle/frequency computations in ActiveWakeControl, producing ~1e-15 differences in downstream fields (FilterParameters coefficients, trig outputs).

**Affected files:**
- `translations/Controllers/cablecontrol.cpp` — used `M_PI` via `#define`
- `translations/Controllers/activewakecontrol.cpp` — used `M_PI` via `#define`

**Already correct:**
- `translations/Controllers/rescontroller.cpp` — `const double PI = 3.14159265359;`
- `translations/Functions/colemantransform.cpp` — `const double PI = 3.14159265359;`
- `translations/Functions/colemantransforminverse.cpp` — `const double PI = 3.14159265359;`

**Fix:** Replaced `#ifndef M_PI / #define M_PI ... / #endif` with `const double PI = 3.14159265359;` in both files. Updated all `M_PI` references to `PI`.

## Bug 2: Missing -ffp-contract=off in Kernel Fortran

**Root cause:** ROSCO's `CMakeLists.txt` sets `-ffp-contract=off` on BOTH Fortran and C++ compilers:
```cmake
set(CMAKE_Fortran_FLAGS "... -ffp-contract=off")
set(CMAKE_CXX_FLAGS "... -ffp-contract=off")
```

But `vit.yaml`'s `fortran.flags` only had `-fdefault-real-8 -fdefault-double-8` — missing `-ffp-contract=off`. VIT's `_patch_kernel_makefile()` propagates these flags to the kernel Makefile's `FC_FLAGS_SET_0`. The C++ side (`CXXFLAGS`) already had `-ffp-contract=off` hardcoded by VIT.

This meant the kernel Fortran was compiled WITH FMA contraction while the kernel C++ was compiled WITHOUT it. If any Fortran code in the kernel (including callee bridge targets like SecLPFilter_Vel) had FMA-contractible expressions, the Fortran and C++ paths could produce sub-ULP differences.

**Fix:** Changed `vit.yaml` line 30 from:
```yaml
flags: -fdefault-real-8 -fdefault-double-8
```
to:
```yaml
flags: -fdefault-real-8 -fdefault-double-8 -ffp-contract=off
```

## Results After Fix

**CableControl:** 5 IN_TOL fields (`lpfv_a2`, `lpfv_a0`, `lpfv_b2`, `lpfv_b0`, `zmq_r_pitch`) became IDENTICAL. Only 1 OUT_TOL remains (`we_fopoles` — a separate KGen state capture issue).

**ActiveWakeControl:** All non-IDENTICAL fields became IDENTICAL. Zero IN_TOL, zero OUT_TOL.

**All other functions:** No regressions, all 40/40 PASSED.

## Documentation

- **TRANSLATION_TIPS.md:** Added tip "Use Fortran's constant values, not C/C++ standard library values" with the PI example.
- **CLAUDE.md:** Updated the FMA section to note that `vit.yaml`'s `fortran.flags` must include `-ffp-contract=off`.

## Commits

| Repo | Commit | Description |
|------|--------|-------------|
| ROSCO | `1e0eb2a` | Fix PI in 2 translations, add -ffp-contract=off to vit.yaml, TRANSLATION_TIPS |
| VIT | `2376736` | Document -ffp-contract=off requirement in CLAUDE.md |

## Lesson

Don't accept IN_TOL as "expected bridge overhead" without evidence. The bridge mechanism (VALUE→reference conversion for doubles, C_F_POINTER for structs) is lossless. IN_TOL in bit-identical verification with `-ffp-contract=off` means a real bug — investigate the expression values and compiler flags, not the calling convention.
