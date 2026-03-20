# Post-Phase 4C Strategic Assessment
**Date:** 2026-03-19
**Status:** completed
**Scope:** Project-wide — what we've built, where we are, where we're going

## What VIT Has Become

VIT started as a pipeline wrapper around KGen: instrument Fortran, capture runtime state, compare C++ output. Over the course of Phases 1–4C, it became something qualitatively different.

The tool now has:
- **A Fortran type system** — parser, registry, C-compatibility analysis, recursive nested type checking, C struct header generation, topological ordering for dependency chains
- **Automated source transformation** — four `--fix` modes that rewrite Fortran TYPE definitions and all downstream references across an entire codebase, with context-sensitive regex classification (assignments, IF conditions, function arguments, binary I/O, complex intrinsics)
- **A build-fix loop** — uses the compiler as an oracle: build → parse gfortran errors → apply fixes from extensible registry → retry. Safety net for transformations with unpredictable downstream consequences
- **Two human-in-the-loop protocols** — EXIT_CONFIRM (10) for inferred values the human must validate, EXIT_MANUAL (11) for code VIT can't auto-rewrite where the human provides replacement via `--rewrite`. Both are structured (parseable blocks), stateless (re-run with CLI flags), and documented in CLAUDE.md
- **A translation prompt system** that learns from failures (TRANSLATION_TIPS.md fed into `vit translate`)

The transformation capability is what Phase 4 was really about. The plan framed it as "make types C-compatible so we can pass struct pointers." What we built was a general Fortran source transformation engine. The four `--fix` modes (logical, complex, character, bind_c) are not ROSCO-specific — they address patterns that recur across scientific Fortran codebases. A generality audit confirmed zero project-specific logic in production code.

## Where We Are: By the Numbers

**Translated and verified:** 15 functions across 3 modules
- Functions.f90: saturate, wrap_180, wrap_360, ratelimit, ColemanTransform, ColemanTransformInverse, identity, sigma, interp1d (9 of 14; 1 dead code; 3 blocked on Phase 4D/5B)
- Filters.f90: LPFilter, HPFilter, SecLPFilter, NotchFilter, NotchFilterSlopes, SecLPFilter_Vel (6 of 7; PreFilterMeasuredSignals blocked on Phase 4D)
- ControllerBlocks.f90: identity (1 of 8; rest blocked on Phase 4D)

**Type system status:**
- BIND(C) compatible (9 types): ErrorVariables, LocalVariables (168 fields + 5 nested), FilterParameters, piParams, resParams, rlParams, WE, ObjectInstances, DebugVariables
- Needs Phase 4D pointer+size view (3 types): ControlParameters (213 fields, 76 ALLOCATABLE), PerformanceData (5 fields, all ALLOCATABLE), ExtControlType (1 ALLOCATABLE)

**VIT codebase:** ~7,000 lines, 446 tests, 17 modules

**All 15 translations integrated:** Clean build test from fresh source → byte-identical simulation output across 3 scenarios.

## What's Immediately Ahead: Phase 7A

The next step is translating PIController, PIDController, PIIController, and ResController. These are mechanically new territory:

1. **First read+write struct access from C++.** All previous translations either took scalar args or read-only struct access. These functions modify piParams/resParams state arrays through LocalVariables — `piP->OutputSignalLast(inst)`, etc. C++ writes to the struct in place, and those writes must be visible to Fortran on the next call since the struct is shared memory.

2. **First C++ calling already-translated C++.** PIController is called by PitchControl, VariableSpeedControl, and many other controllers. Once PIController is C++ and its callers are C++, the call path is C++ → Fortran wrapper → C function → C++ implementation. This round-trip works but raises the question of whether direct C++ → C++ calls should bypass the wrapper.

3. **Proof-of-concept for the 10 main subroutines.** If PI/PID/PII/Res work cleanly through VIT's pipeline, the pattern is proven for PitchControl et al. (after Phase 4D unblocks ControlParameters).

These four functions don't need ControlParameters — they only take piParams/resParams (nested in LocalVariables) and ObjectInstances, all BIND(C).

## The Phase 4D Wall

After 7A, Phase 4D is the remaining blocker. ControlParameters has 213 fields including 76 ALLOCATABLE arrays. The plan describes a pointer+size view pattern: C++ receives a struct with `double*` pointers into Fortran-owned ALLOCATABLE arrays, populated by a BIND(C) Fortran routine called once at init.

Every main controller subroutine (Phase 7B), every ControllerBlocks function (Phase 8), AeroDynTorque (Phase 5B), and PreFilterMeasuredSignals all take ControlParameters. This is the last type system work — once 4D is done, the entire type system is solved and no function is blocked by types.

## Two Design Questions to Resolve Before Phase 7B

### 1. Should ReadSetParameters come earlier?

The plan has I/O modules as Phase 10 — after Controllers and ControllerBlocks. But ReadSetParameters is where ControlParameters gets populated. It's upstream of everything.

When we design the ControlParameters pointer+size view struct in Phase 4D, we're making decisions about how ALLOCATABLE arrays are exposed to C++ — which arrays share sizes, which are independent, how 2D arrays are laid out. Those decisions would be better informed by deeply understanding ReadSetParameters (the code that allocates and fills those arrays).

This doesn't mean translating ReadSetParameters in Phase 4D. But reading it carefully during the view struct design, and possibly translating ReadAvrSWAP (the simplest Category 1 function in Phase 10) as a pathfinder, would ground the design in reality rather than inference.

### 2. C++ → C++ call path through Fortran wrappers

Currently, every translated function is called from Fortran via a wrapper:
```
Fortran caller → Fortran wrapper → C function (extern "C") → C++ implementation
```

When both caller and callee are translated C++, this becomes:
```
C++ caller (PitchControl) → Fortran wrapper → C function → C++ callee (PIController)
```

This works — the Fortran wrapper is just a thin shim. But in a tight control loop called thousands of times per simulation timestep, the overhead of two function-pointer indirections and Fortran calling convention transitions adds up. More importantly, when ALL of ROSCO is C++, these wrappers become dead code that must be stripped.

Options:
- **Keep wrappers during translation, strip in Phase 11.** Simplest. Every function has one integration pattern. Wrappers are removed when Fortran is fully eliminated.
- **Direct C++ calls when both sides are translated.** Better performance, cleaner code. But requires VIT to track which functions are translated and generate different integration patches depending on whether the caller is Fortran or C++.
- **Hybrid: wrappers for Fortran callers, direct for C++ callers.** The translated C++ can `#include` the callee's header directly. VIT generates both paths — wrapper for Fortran, direct for C++. This is the most flexible but most complex.

Recommendation: keep wrappers during translation (option 1). The overhead is negligible for ROSCO's 80Hz control loop. Optimize in Phase 11 when Fortran is gone. Premature optimization of the call path would complicate the integration logic that's already working.

## Recommended Execution Path

```
Phase 7A (4 helper controllers — immediate)
  → Phase 4D (ControlParameters/PerformanceData view structs)
    → Phase 7B (10 main controller subroutines)
    → Phase 8 (8 ControllerBlocks functions)
    → Phase 5B (unwrap, interp2d, AeroDynTorque)
      → Phase 9 (type ownership transfer)
        → Phase 10 (I/O modules)
          → Phase 11 (DISCON entry point, pure C++)
```

Phase 7A is the proof-of-concept. Phase 4D is the last type system wall. Everything after 4D is translation work — writing C++, verifying, integrating. The hard infrastructure problems are solved.

## Related Notes
- [202603191921-generalize-complex-fix-and-manual-review-protocol.md](202603191921-generalize-complex-fix-and-manual-review-protocol.md) — EXIT_MANUAL protocol design
- [202603191827-phase4c-localvariables-c-compatible.md](202603191827-phase4c-localvariables-c-compatible.md) — Phase 4C implementation
