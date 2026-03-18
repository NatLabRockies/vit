# Subroutine Design Discussion and Wrapper Strategy
**Date:** 2026-03-18
**Status:** completed
**Scope:** Architecture decision — how C++ translations interact with Fortran through the wrapper layer

## The Design

Subroutines use a single C-style signature that passes straight through from the Fortran BIND(C) interface to the extern "C" wrapper to the user's C++ implementation. All three layers share the same parameter list.

For INTENT(OUT/INOUT) scalar args, the C++ receives pointers and dereferences them (`*axTOut = value`). Arrays are always pointers regardless of intent. INTENT(IN) scalars are pass-by-value. This follows directly from Fortran's BIND(C) interop rules — scalars without the VALUE attribute are passed by reference.

The extern "C" wrapper is a thin pass-through:
```cpp
extern "C" {
    void colemantransform_c(...args...) {
        colemantransform(...args...);
    }
}
```

## The Alternative Considered

The wrapper could do more work — converting between C-friendly and C++-friendly representations. For example, unpacking C struct pointers into `std::span`, converting OPTIONAL flag+value pairs into `std::optional`, returning scalars by value instead of through output pointers.

This would give the user a cleaner, more idiomatic C++ signature to implement against. But it adds complexity to the wrapper (which itself needs to be correct) and creates another surface area for bugs.

## Why Raw C Pointers Are the Right Choice

1. **Verification proves correctness regardless of code style.** The VIT pipeline verifies bit-identical output. Whether the user writes `*result = value` or `return value` doesn't affect the verification's rigor.

2. **The translations are intermediate artifacts.** They exist to be verified against captured Fortran state. Once everything is translated and the full simulation is proven identical, the Fortran wrapper layer gets deleted entirely and the C++ can be refactored freely.

3. **The final refactor is straightforward.** When all functions are C++, the calling chain (C++ caller → extern "C" → user C++) collapses to (C++ caller → C++). The user's implementation bodies move intact — only the calling convention at boundaries changes. `void foo(double* result)` becomes `double foo()`, but the body logic is the same.

4. **No landmines for later phases.** Walked through every remaining phase (3-11) and the current approach holds:
   - **Derived types (Phases 3-5):** C structs mirror Fortran TYPE layout. `params->field` is unambiguous. BIND(C) guarantees layout compatibility.
   - **Large modules (Phases 6-8):** Verbose but clear. `localVar->PC_PitComT` maps directly to `LocalVar%PC_PitComT`.
   - **I/O (Phase 10):** File parsing through raw pointers is ugly but functional. The real complexity is ALLOCATABLE handling, which is a type system question (Phase 4), not a wrapper question.
   - **Entry point (Phase 11):** Last thing to translate. At that point all wrappers become dead code and get removed.

## The One Thing to Be Deliberate About

The C struct definitions created in Phase 4 become the permanent C++ types in the final all-C++ codebase. They must be designed as final types, not temporary interop scaffolding — reasonable field names, logical grouping, no Fortran-isms that would need unwinding later.

Added this as a design constraint in PROJECT_PLAN.md Phase 4 section.
