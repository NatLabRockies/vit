# Midpoint Reflection: 28 Functions Verified, Infrastructure Complete
**Date:** 2026-03-26
**Status:** completed
**Scope:** project retrospective, architectural assessment, forward outlook

## The Real Work Was Never the Translations

The C++ translations themselves are almost anticlimactic. Most verify on the first attempt. The actual engineering challenge has been building the machinery to *prove* they're correct. Consider the ratio: PitchControl's C++ translation is 185 lines and took one attempt. The infrastructure to verify it — view-type stash for callee bridges, assumed-size array patching, DLL persistence workarounds, KGen parser fixes, the entire callee bridge system — represents thousands of lines across dozens of sessions. The translation is the easy part. Trust is the hard part.

## Every Phase Revealed Something KGen Couldn't Handle

KGen was built for a different use case — profiling and kernel extraction for performance analysis, not bit-identical verification of mixed-language binaries. We've been pushing it into territory its authors never intended. The PROCEDURE POINTER parser was incomplete. Assumed-size arrays broke its state capture. Its invocation counter model doesn't account for conditional code paths across scenarios. DLL SAVE persistence created phantom state. Each of these was a wall that looked like it might end the project, and each time we found a workaround that was general enough to configure in vit.yaml rather than hardcode. The KGen fork now has real fixes (BIND(C) parser, PROCEDURE POINTER), and VIT's extraction pipeline handles the rest through configuration.

## The DLL Persistence Saga: How Debugging Actually Works

That thread spanned five dev notes and multiple sessions. It started with avrSWAP heap corruption, which led to discovering the assumed-size array was undersized, which led to re-extraction, which exposed the DEALLOCATE contamination bug, which led to the scope-guarded insertion fix, which led to the realization that the DEALLOCATE was vestigial in the first place. Each fix peeled back a layer and revealed the next problem. The final answer — just delete the DEALLOCATE entirely, keep only the project-specific AlreadyInitialized reset — was simple. Getting there required understanding KGen's execution model (two separate subprocesses), Python ctypes' dlclose behavior, Fortran SAVE semantics, and the interaction of all three across multi-scenario simulation runs. None of that understanding existed at the start.

## The Type System Was the Real Architectural Decision

Phase 4's decomposition into 4A-4D was the project's pivotal moment. The decision that every type becomes a C struct — not opaque pointers, not accessor APIs, not field extraction — set the trajectory for everything after. The view struct pattern for ALLOCATABLE types was the key insight: you don't need to copy data or change ownership during the translation phase. Just take `C_LOC()` of the Fortran-owned arrays and hand C++ raw pointers. Ownership transfers later, when everything is C++. This meant we could translate controller functions that access 30-70 fields without rewriting the type system first.

## Where We Are Now: A Genuine Inflection Point

28 of ~65 functions translated — roughly 43% by count, but more than that by difficulty. The functions we've done include the hardest ones: PitchControl (the master orchestrator), ActiveWakeControl (5 modes, complex decomposition), the entire filter bank, all the PI/PII/Res controllers. The infrastructure handles every pattern we've encountered: BIND(C) structs, view structs, callee bridges with view-type stash, OPTIONAL arguments, LOGICAL conversion, COMPLEX decomposition, assumed-shape arrays, fixed-size arrays, array-valued returns, SAVE variables, instance-based state indexing.

## What's Ahead, and Where the Character Changes

### Phase 8: ControllerBlocks (should go smoothly)

WindSpeedEstimator is the most complex — a 3-state Kalman filter with 3x3 matrix operations through the WE struct — but matinv3 and identity are already translated, and WE is a BIND(C) struct nested in LocalVariables. The state machines (StateMachine, Startup, Shutdown) are control flow heavy but type-simple. All the infrastructure they need exists.

### Phase 5B: Remaining Functions.f90 (bounded VIT enhancements)

unwrap, interp2d, AeroDynTorque need genuine VIT enhancements — 2D assumed-shape array handling and array-valued returns where the result size depends on an input. These are bounded problems with clear solutions.

### Phase 10: I/O Modules (fundamental character change)

This is where the character of the work fundamentally changes. I/O modules can't be verified through KGen's extract-replay model. ReadControlParameterFileSub reads a file and populates a struct — KGen captures the struct state, but the file dependency is invisible. WriteRestartFile produces a binary file — its output is a side effect, not a return value. Debug has timestamps and SAVE-variable state. Four verification categories were designed in the project plan (pure transformers, file readers, file writers, diagnostics), each with its own VIT enhancement. That's the right decomposition, but it's also a significant infrastructure build — comparable in scope to what we just finished for the controller pipeline.

### Phases 9 and 11: Architectural bookends

Phase 11 (DISCON entry point) is ceremonial if everything before it is done. Phase 9 (ownership transfer) is architecturally interesting — flipping from Fortran-owns-data to C++-owns-data — but it's a refactoring of something that already works, not a new capability.

## The Deeper Project

This project is really about two things at once. On the surface, it's translating ROSCO from Fortran to C++. But underneath, it's building a general-purpose tool for verified incremental translation of any scientific Fortran codebase. Every time we hit a ROSCO-specific problem, the solution went into vit.yaml configuration, not into VIT's code. The generality audits confirmed it. When this is done, VIT should be able to point at a different Fortran project — different types, different build system, different runtime — and the same extract-verify-integrate pipeline should work. That's the real deliverable.

## Related Dev Notes

- `202603202109-midpoint-reflection.md` — earlier midpoint reflection at 15 functions
- `202603201015-phase-7a-reflection.md` — Phase 7A reflection
- `202603241054-phase-7b-extraction-session-reflection.md` — Phase 7B extraction reflection
- `202603252225-deallocate-removal-and-full-reextraction.md` — DEALLOCATE investigation
- `202603260840-40-40-verification-fix-and-final-reextraction.md` — final re-extraction to 40/40
