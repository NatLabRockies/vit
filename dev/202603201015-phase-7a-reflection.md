# Phase 7A Reflection
**Date:** 2026-03-20
**Status:** completed
**Scope:** Lessons from the Phase 7A debugging journey

## What Happened

We started this session trying to translate 4 controller functions. That was supposed to be the straightforward part — PIController is a textbook PI controller, PIIController adds one integral term, ResController is a resonant filter. The algorithms are simple. The translations were written in minutes.

Then everything went sideways.

The first sign of trouble was just getting the source files into a state where extraction could work. Phase 4C had changed ROSCO_Types.f90 with `BIND(C)` attributes and `INTEGER(C_INT)` conversions, but the source files that reference those types were tangled up with integration wrappers from earlier phases. We burned a lot of time trying different combinations — Phase 4C sources (had wrappers), upstream sources (type mismatch), mixed states (various build errors). It felt like a puzzle with no valid solution.

PIController and ResController extracted fine. PIIController produced 0 state files. That's when the real trouble started.

The initial response was to accept the failure. "KGen extraction of gated code paths is unreliable." "Fall back to sim-diff." "2-strike rule." PIIController was marked as sim-diff verified and we moved on. The translations were integrated, the simulation output was byte-identical, everything looked green.

But it wasn't right. The challenge was issued — if PIIController truly can't be extracted, that means the function never gets called, which makes no sense. That single observation reframed the entire problem. It wasn't a KGen limitation. It was something we hadn't found yet.

## The 7-Layer Excavation

What followed was a 7-layer debugging excavation:

1. **KGen's BIND(C) parser bug** — a one-line fix in `utils.py` that had been forcing the entire upstream-reset workaround. Found by reading 20 lines of Python.

2. **F_FlpCornerFreq=0** — Scenario 4 "passed" but FlapControl was never called. The controller returned an error that Python silently absorbed. The scenario was exercising nothing.

3. **Uninitialized K** — a loop variable used without a loop in the init block. Classic Fortran bug.

4. **The write_discon regex** — multi-value parameters silently failing to patch.

5. **ACC_INFILE character assignment** — Phase 4A's ALLOCATABLE→fixed-size change broke a string copy that gfortran's bounds checker caught as `(1024/13)`.

6. **Uninitialized n** — the deepest bug. `PreFilterMeasuredSignals` crashed before FlapControl was ever entered. A notch filter call used a loop variable from a different loop that never executed. This was the actual segfault. Found by adding WRITE statements between subroutine calls and reading the source at the crash point.

7. **kgen_array_sumcheck STOP** — subnormal values in large structs causing false kernel crashes.

Each fix revealed the next bug. The F_FlpCornerFreq fix unmasked the uninitialized K. Fixing K unmasked the segfault in PreFilterMeasuredSignals. The `-fcheck=bounds` error at the ACC_INFILE line was a distraction that initially looked like the root cause but was actually a different issue.

## The Turning Point

The most important moment was the shift from "this is an upstream ROSCO bug outside our scope" to "let me add debug prints and find the actual line." The uninitialized `n` took about 10 minutes to find once we committed to looking. We'd spent hours before that accepting that it couldn't be found.

## What We Ended Up With

18 functions KGen-verified 40/40, byte-identical simulation across 5 scenarios, a KGen parser fix that unblocks all future extractions with `BIND(C)` types, two ROSCO bug fixes that make Flp_Mode=2 work in the 1-DOF sim, and a `TRANSLATION_TIPS.md` that's no longer empty.

## The Lesson

The technical output matters, but the real lesson is about standards. VIT exists to provide rigorous verification. Accepting a weaker verification because the stronger one is hard undermines the tool's entire purpose. Every shortcut we almost took — the 2-strike rule, the sim-diff fallback, the "upstream bug" framing — would have left a crack in the foundation that future phases would build on.
