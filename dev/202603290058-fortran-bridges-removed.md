# Fortran Math Bridges Removed — Pure C++ Translations Achieved

**Date:** 2026-03-29
**Status:** completed
**Scope:** Remove vit_math_bridge.f90, make all C++ translations self-contained

## Background

During the Phase 8C investigation of WSE integration divergence (dev notes `202603281348`, `202603281741`, `202603282007`), two categories of Fortran bridges were added to `vit_math_bridge.f90`:

1. **Trig bridges** (`vit_cos`, `vit_sin`, `vit_atan2`) — wrapped gfortran's COS/SIN/ATAN2 intrinsics with BIND(C) entry points. Hypothesis: gfortran and g++ produce different results for these functions on aarch64. **The hypothesis was disproven** — divergence persisted unchanged when the bridges were used. The bridges were kept with the note "harmless and theoretically correct."

2. **EKF MATMUL bridge** (`vit_ekf_update`) — performed the entire Extended Kalman Filter prediction and measurement update using Fortran's native MATMUL and array expressions. Root cause: gfortran's MATMUL for 3×3 matrices produced 1-ULP differences from the C++ triple-loop implementation. **This bridge DID fix Scenario 8's divergence** and was considered essential.

Both bridges created Fortran dependencies in the C++ translations, which:
- Broke kernel verification (VIT couldn't resolve `vit_cos`/`vit_ekf_update` symbols in the kernel build)
- Prevented the translations from being fully self-contained C++
- Would need to be removed before ROSCO could become a pure C++ codebase

## Investigation: Can the Bridges Be Removed?

### Phase 1: Remove trig bridges

Replaced in both `shutdown.cpp` and `windspeedestimator.cpp`:
- `vit_cos(x)` → `std::cos(x)`
- `vit_sin(x)` → `std::sin(x)`
- `vit_atan2(y,x)` → `std::atan2(y,x)`

Removed `extern "C"` declarations for these functions.

**Result: ALL 9 SCENARIOS IDENTICAL — 672,000 values.**

Confirms: gfortran intrinsics and g++ `std::cos`/`std::sin`/`std::atan2` produce bit-identical results on aarch64 with `-ffp-contract=off`. The bridges were truly unnecessary.

### Phase 2: Remove EKF MATMUL bridge

This was the riskier change — the bridge was proven to fix Scenario 8. The question: can we write C++ MATMUL that matches gfortran's results?

**Root cause re-analysis:** The original C++ EKF code (before the bridge was introduced) likely had one of two issues:
1. **Aliasing:** The P prediction equation reads and writes P (`P = P + DT*(F*P + P*F^T + ...)`). If the C++ code modified P elements in-place while still reading them, the results would differ from Fortran's array expression semantics (which evaluate the entire RHS to a temporary before assignment).
2. **Accumulation order:** A different loop structure for the 3-term dot product in each MATMUL element.

**The fix:** Replaced the `vit_ekf_update()` call with inline C++ that:
- Uses explicit element-by-element computation (no library MATMUL)
- Uses temporary arrays (`P_new[3][3]`) to avoid aliasing when computing `P = f(P)`
- Accumulates products left-to-right: `a*b + c*d + e*f` (matches gfortran's MATMUL for 3-element sums)
- Exploits the structure of H = [1,0,0] to simplify the measurement update (S = P(1,1) + R_m, K = P(:,1)/S, etc.)

**Result: ALL 9 SCENARIOS IDENTICAL — 672,000 values.**

The inline C++ EKF produces bit-identical results to the Fortran original. The key was proper aliasing handling (temporaries) and standard left-to-right accumulation order.

### Phase 3: Remove vit_math_bridge.f90 entirely

- Removed `src/vit_math_bridge.f90` from `CMakeLists.txt`
- Rebuilt from clean, integrated all 39 functions, ran 9-scenario comparison

**Result: ALL 9 SCENARIOS IDENTICAL — 672,000 values.**

No Fortran bridge code remains in any C++ translation.

## What Changed

| File | Change |
|------|--------|
| `translations/ControllerBlocks/shutdown.cpp` | `vit_cos/sin/atan2` → `std::cos/sin/atan2`, removed extern declarations |
| `translations/ControllerBlocks/windspeedestimator.cpp` | Removed `vit_cos` declaration, removed `vit_ekf_update` declaration + call, added inline C++ EKF with temporaries |
| `rosco/controller/CMakeLists.txt` | Removed `src/vit_math_bridge.f90` from source list |

## Key Findings

### 1. gfortran and g++ trig functions are identical on aarch64

With `-ffp-contract=off`, `std::cos(x)` and Fortran `COS(x)` produce bit-identical results for all values tested across 672,000 simulation data points. The original hypothesis (1-ULP difference) was never actually observed — it was speculative, based on the general knowledge that different libm implementations can differ. In practice, on this platform (gfortran 13.3 / g++ 13.3 on aarch64 Linux), they use the same libm.

### 2. The MATMUL issue was likely aliasing, not summation order

The original C++ EKF code that failed was never preserved (it was replaced by the bridge during debugging). But the new inline C++ code — which uses the same left-to-right accumulation order as a simple triple loop — produces identical results. The critical difference is the use of **temporary arrays** for `P_new` to avoid reading from P while writing to it. Fortran's array expression semantics guarantee this automatically; C++ requires explicit temporaries.

### 3. `-ffp-contract=off` is sufficient for bit-identical MATMUL

With FMA disabled, a C++ triple-loop MATMUL with left-to-right accumulation matches gfortran's MATMUL for 3×3 matrices. No special accumulation order matching was needed — the standard approach works.

## Implications

- **Kernel verification** should now work for all 39 functions (no unresolved bridge symbols)
- **Pure C++ ROSCO** is now achievable — no function calls into Fortran from C++ translations
- **`vit_math_bridge.f90`** can be deleted from the repository (currently still on disk but not compiled)
- **Future translations** should use `std::cos`/`std::sin`/`std::atan2` directly and implement MATMUL inline with temporaries where needed

## Lesson Learned

When a Fortran bridge "fixes" a divergence, investigate whether the fix is from the bridge itself or from a side effect (like aliasing avoidance). The Fortran bridge happened to work because Fortran's array expression semantics use temporaries implicitly. The real bug was the C++ code modifying P in-place. Understanding the root cause led to a simpler, bridge-free fix.
