# Phase 8 Retrospective: Reflections on the Journey

**Date:** 2026-03-31
**Status:** completed
**Scope:** Phase 8 retrospective — debugging, architecture, and lessons learned

## The Investigation

We started this session at the beginning of Phase 8C — 36 functions integrated, Scenarios 1-6 identical, Scenarios 7 and 9 diverging. The WSE kernel verification passed 21/21 but the integrated simulation diverged. A paradox.

What followed was one of the most intensive debugging sessions of the project. The investigation required multiple context compactions and spanned an entire day. Each layer we peeled back revealed something unexpected:

**The pow() discovery** was elegant — gfortran compiles `x**3.0` as a call to `pow()`, not as `x*x*x`. A 1-ULP difference in a single matrix element, amplified through an Extended Kalman Filter feedback loop over thousands of timesteps. The fix was one line.

**The MATMUL bridge** seemed necessary at the time — gfortran's MATMUL produced different results from C++ triple loops. We wrote a Fortran bridge to perform all EKF math in Fortran. It worked, but it was a crutch. Later, when we pushed to remove it, we discovered the real issue was likely aliasing — Fortran's array expression semantics use temporaries implicitly, and the original C++ code was modifying P in-place while reading it. The inline C++ with explicit temporaries matched perfectly. The bridge was never needed.

**The interp2d dimension check disaster** was a cautionary tale. We misread the Fortran comments (which described the wrong dimension) instead of the actual IF conditions (which were correct). Based on that misreading, we "fixed" the C++ to match what we thought was Fortran's behavior — actually introducing a bug that set `aviFAIL=-1` and caused a controller lockup. Then we tested the hypothesis by running with the broken code and concluded "aviFAIL is not the cause" when in fact we'd just created a new, worse problem. The fix was to read the code, not the comments.

**The POSIX `shutdown()` collision** was the most insidious bug. The dynamic linker silently replaced our C++ function with libc's socket shutdown. No warning, no error, no crash — just `SD_BlPitchF` stuck at 0.0. It took adding fprintf at multiple layers to discover that `shutdown_c()` was being called but `shutdown()` wasn't executing. The root cause: we named a C++ function after a POSIX system call and compiled it into a `-fPIC` shared library.

## The Architecture Work

After fixing Scenario 9, we pivoted to cleaning up the technical debt the investigation had created. This is where the session became about architecture, not debugging:

**The trig bridges were unnecessary.** We proved it empirically — `std::cos` and gfortran `COS` produce identical results on aarch64 with `-ffp-contract=off`. The hypothesis was never confirmed, just kept "because it's harmless." Removing it made the translations self-contained C++.

**Source casing preservation** was a key insight. Rather than maintaining a hardcoded list of POSIX names or adding `_impl` workarounds, the question was: why are we lowercasing at all? VIT already preserved casing for types, result names, and modules — the function name was the one place it didn't. Fixing this eliminated the POSIX collision class entirely (PascalCase `Shutdown` doesn't collide with lowercase `shutdown`), and it preserves developer intent.

**The auto-detection workaround** was a symptom. We built it to bridge old lowercase translations and new source-cased ones. Identifying it as a workaround led to the right question: why not just fix the translations? Renaming all 30 functions to source casing let us delete the workaround entirely. The system became simpler, not more complex.

**The file naming cleanup** (`_impl.hpp` to `.hpp`, `_cpp.cpp` to `.cpp`) was the same principle — removing unnecessary suffixes that confused rather than clarified.

## Where We Are Now

39 functions, 9 scenarios, 672,000 values, ALL IDENTICAL. No Fortran bridges. All translations use source casing. Kernel verification passes 39/39. The C++ translations are fully self-contained — they call `std::cos`, use inline EKF math with proper temporaries, and don't depend on any Fortran utility code. VIT's codegen is clean: `sig.cpp_impl_name` flows directly to the wrapper with no scanning or auto-detection.

## What's Ahead

**Phase 9 (Type Ownership Transfer)** shifts from "C++ translations verified against Fortran" to "C++ owns the data." Currently, Fortran allocates all ALLOCATABLE arrays (ControlParameters, PerformanceData) and C++ accesses them through view pointers. Phase 9 reverses this — C++ allocates with `std::vector`, Fortran accesses through thin shims. This is a structural change, not a function-by-function translation.

**Phase 10 (I/O and Platform Modules)** is a different kind of challenge. ReadSetParameters parses input files. ROSCO_IO writes restart/debug files. These can't be verified through KGen's function-level state capture — VIT needs new machinery for file I/O verification. This is the one remaining real technical limitation identified in the debt cleanup.

**Phase 11 (Entry Point)** translates DISCON.F90 itself — the orchestrator that calls all 39 functions. After this, the entire controller is C++. The Fortran wrappers become dead code.

## The Deeper Insight

The bugs we found were all at the boundaries. `pow()` vs `x*x*x` — a boundary between Fortran's operator semantics and C++ library calls. MATMUL aliasing — a boundary between Fortran's implicit temporaries and C++'s explicit memory model. `shutdown()` PLT collision — a boundary between C++ symbol names and the POSIX namespace. `interp2d` comments — a boundary between what the code says and what the developer wrote about it.

The translation itself was never wrong — the C++ logic matched the Fortran logic. Every bug was in the *interface* between the two worlds. As we move toward a pure C++ ROSCO, these boundary issues disappear. The bridges we built and then removed were temporary scaffolding for a building that's almost ready to stand on its own.
