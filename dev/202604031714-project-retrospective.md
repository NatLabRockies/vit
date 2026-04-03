# Project Retrospective: From saturate to 5.25 Million Values
**Date:** 2026-04-03
**Status:** completed
**Scope:** Full project reflection — 127 dev notes, February 25 to April 3

## The Arc

The project started on February 25 with a working prototype committed in a single shot: 14 files, 3,279 lines, the full extract-verify-integrate pipeline. `saturate` — a three-line min/max clamp — was the proof of concept. By the end of that day, it worked. If the story had ended there, it would have been a nice demo.

It didn't end there. It took five more weeks.

## The Hardest Problems Were Never in the Translations

The C++ code for each function was usually straightforward — sometimes mechanical, sometimes requiring domain understanding (Euler's formula decomposition, Coleman transforms, EKF matrix updates), but always tractable once the Fortran source was understood. What nearly derailed the project, repeatedly, was everything *around* the translations:

- KGen's parser couldn't handle `BIND(C)` declarations. One uninitialized variable in `parse_bind()`.
- FMA contraction scheduling differed between gfortran and g++, creating 1-ULP drift that accumulated through closed-loop feedback until gen_torque diverged by 16,900 N·m.
- Scipy's FITPACK `bispev` read uninitialized stack bytes, producing platform-dependent Cp values that made Scenario 3 flip between two states with a 71/29 probability split.
- KGen's ElapsedTime profiler duplicated invocation entries, inflating "40/40 passed" into a chimera that masked the fact that 8 functions had been verified with all-zero inputs.
- The avrSWAP buffer was 500 elements but ROSCO wrote to index 2600, silently corrupting heap memory in every run.

Each of these looked, at first, like it might be a translation bug. Each turned out to be something deeper — a compiler behavior, a third-party library defect, a toolchain assumption, an undefined behavior. The pattern was consistent: the symptom appeared in the translation output, but the cause lived in the infrastructure. Solving them required understanding the full stack — Fortran semantics, IEEE 754, ARM64 ASLR, ctypes dlopen handles, gfortran's code generation choices for `x**3.0` vs `x*x*x`.

## The Turning Points Were Moments of Refusal

When PIIController extraction failed, the easy path was "Flp_Mode=2 is untested research code, skip it." Instead: seven layers of excavation, a KGen parser fix, two ROSCO bug discoveries, and a complete understanding of why nobody had ever run this code path.

When "40/40 passed" was questioned — "why is 0/40 even possible?" — it unraveled the entire verification infrastructure and led to a rebuild that produced honest numbers.

When Scenario 3 was non-deterministic, the easy answer was "floating-point is like that." Instead: a 100-run probability study, FPCR register inspection, and a stack scrubber that proved the root cause was uninitialized memory in a 40-year-old Fortran spline library.

The phrase that recurs through the dev notes: **"We shouldn't accept things because they're hard."**

## Verification Evolved in Three Phases

First, "it passes the tests" — KGen kernel verification showed IDENTICAL for each function.

Then, "it passes the integration test" — simulation output matched between Fortran and C++ builds.

Finally, today: "we can explain every bit." 5.25 million values across 27 scenarios, 13 output arrays, three independent builds (upstream, modified, C++), with an adversarial audit that identified the honest caveats (51% trivially zero, float32 precision ceiling) alongside the strengths (deterministic, three distinct binaries, closed-loop feedback amplification).

The one remaining artifact — `8.47e-22` radians at Scenario 16 bld_pitch index 4444 — was traced to its exact root cause: gfortran and g++ evaluate `x + ((-x/a)*a)` differently for one specific input value at one specific timestep, producing `2^{-70}` vs exact zero. Not a translation error. A compiler code generation difference at the boundary of floating-point representability.

## Two Artifacts

**VIT** is a tool — it extracts runtime state, wires C++ translations into verification kernels, and generates integration patches.

But the more important artifact is the **process**: the dev notes, the scripts, the scenarios, the verification chain. The process is what makes the claim "bit-identical" meaningful rather than aspirational. `reset_to_upstream.sh` → build → run → `upstream_arrays/`. `reset_to_clean.sh` → build → run → `baseline_arrays/`. `integrate_all.sh` → build → run → compare. Anyone can reproduce it. The scripts are the proof.

## Performance

The translation is 25% faster. Not through optimization — through language. The same algorithms, faithfully translated, run faster in C++ because g++ generates better machine code than gfortran on ARM64. The speedup came for free.

## What "Done" Means

Looking at the full arc — from `saturate` (3 lines, February 25) to the 11-mode stress test (Scenario 27, 6 simultaneous pitch contributions, April 3) — what stands out most is how much the project's definition of "done" evolved. Early on, "done" meant the diff was empty. By the end, "done" meant we could explain why the diff had exactly 3 entries, what caused each one, why they weren't translation errors, and what the float32 precision ceiling meant for the claim.

That's the difference between testing and verification.

## By the Numbers

| Metric | Value |
|--------|-------|
| Dev notes written | 127 |
| Calendar days | 38 (Feb 25 — Apr 3) |
| Functions translated | 53 (52 subroutines/functions + DISCON entry point) |
| Fortran source files eliminated | 16 |
| C++ source files in final build | 53 |
| KGen-verified functions (float64) | 42 |
| Simulation-verified functions (float32) | 53 |
| Scenarios | 27 |
| Output arrays per scenario | 13 |
| Total values compared | 5,252,000 |
| Non-trivial values compared | 2,564,000 |
| Mismatches | 3 (1 root cause: gfortran code gen artifact) |
| Controller modes exercised | All 27 flags, all testable values |
| Performance improvement | 25% faster (C++ vs Fortran) |
| libgfortran dependency | Eliminated |
