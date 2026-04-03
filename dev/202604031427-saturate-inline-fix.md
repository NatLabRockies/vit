# Replace Inlined saturate with saturate_c Function Call
**Date:** 2026-04-03
**Status:** completed
**Scope:** Translation correctness — saturate function call structure

## Summary

All C++ controller translations (PIController, PIIController, PIDController, ResController, ratelimit) inlined the Fortran `saturate()` function as `std::min(std::max(...))`. The Fortran originals call `saturate()` as a separate function, which includes a `REAL(..., DbKi)` cast and a function call boundary. Changed all inlined instances to call `saturate_c()` to match the Fortran call structure.

This was identified during the Scenario 16 `2^(-70)` investigation. While the change doesn't fix that specific difference (confirmed by test), it eliminates a systematic deviation from the Fortran translation pattern.

## What Changed

| File | Inlined saturates replaced |
|------|--------------------------|
| `picontroller.cpp` | 2 (ITerm clamp + result clamp) |
| `piicontroller.cpp` | 3 (ITerm clamp + ITerm2 clamp + result clamp) |
| `pidcontroller.cpp` | 2 (ITerm clamp + result clamp) |
| `rescontroller.cpp` | 1 (result clamp) |
| `ratelimit.cpp` | 1 (rate clamp) |

## Why It Matters

The Fortran `saturate` function:
```fortran
FUNCTION saturate(inputValue, minValue, maxValue)
    saturate = REAL(MIN(MAX(inputValue, minValue), maxValue), DbKi)
END FUNCTION
```

The `REAL(..., DbKi)` cast and function call boundary can affect code generation — forcing intermediate values to memory or to the function's return type precision. The C++ inline `std::min(std::max(...))` keeps intermediates in whatever representation the compiler chooses, potentially differing from the Fortran behavior.

On ARM64 (our build platform), this distinction is unlikely to matter since there are no extended-precision registers. But it's a deviation from faithful translation, and on x86 platforms (80-bit FPU registers), the function call boundary would force a precision narrowing that the inline would not.

## Verification

Tested Scenario 16 before and after the change. The `2^(-70)` difference at `bld_pitch[4444]` persists — confirming this specific difference is NOT caused by the inlined saturate. The root cause is in gfortran's code generation for the rate limiter's `LastSignal + rate * DT` expression (see dev note `202604031325`).

## The Broader Principle

When translating Fortran to C++, function calls should remain function calls. Inlining a function changes the optimization surface available to the compiler. Even if the mathematical result is the same, the floating-point behavior can differ due to:
- Register allocation differences (extended precision on x86)
- Expression evaluation order
- Compiler optimization passes that treat function calls as optimization barriers

This is a lesson for future VIT translations: `vit translate` should generate `saturate_c()` calls rather than inlining `std::min(std::max(...))`.

## Commits

- ROSCO `ca2997d`: Replace inlined saturate with saturate_c function call in all controllers
