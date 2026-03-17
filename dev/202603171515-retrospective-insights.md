# Retrospective Insights: Patterns in VIT's Development

**Date:** 2026-03-17
**Status:** completed
**Scope:** project-wide reflection

## Background

After bootstrapping the dev notes system by reconstructing 14 notes from the git history (Feb 25–27, 2026), this note captures higher-level patterns and insights that emerge from reading the development narrative as a whole.

## The 3-Day Sprint Was Remarkably Structured

The entire initial development — 4,100 lines of core code, 207 tests, 3 verified functions — happened in 3 days. But it wasn't a chaotic sprint. There's a clear arc:

- **Day 1 (Feb 25)**: Make it work. Inception → immediate real-world failures → extraction overhaul → verification compilation fixes. Six commits, each responding to what broke when running against ROSCO for real.
- **Day 2 (Feb 26)**: Solve the hardest conceptual problem. Four commits entirely on NaN handling — the only issue that required *thinking*, not just fixing.
- **Day 3 (Feb 27)**: Harden and extend. Tests first, then cleanup, then two major features (multi-function support, derived types). The test suite was deliberately placed before the complex features.

That sequencing — stabilize, then test, then extend — is disciplined for a 3-day effort.

## VIT Is Mostly a KGen Compatibility Layer

This is the most striking takeaway. Count the KGen workarounds across the notes:

- Pragma format must use underscores (`begin_callsite`)
- `include.ini` must be pre-generated (strace-based discovery is unreliable)
- `kgen_utils.f90` must be injected into the build (KGen generates it but doesn't add it to CMake)
- `FC_0` / `FC_FLAGS_SET_0` Makefile variables must be defined (KGen references but doesn't define them)
- `USE kgen_utils_mod, ONLY:` clauses must be stripped (references non-existent symbols)
- USE statements must be reordered (KGen puts them after IMPLICIT NONE)
- NaN comparisons must be patched (KGen captures uninitialized state, then fails on IEEE 754)
- `verboseLevel` must be forced to 100 (default output lacks field-level detail)
- Stale artifacts must be cleaned before each run

That's 9 distinct workarounds. The extraction and verification modules (`extract.py`, `kernel_verify.py`) are ~1,560 lines combined — the two largest modules — and a significant fraction of that code exists purely to make KGen behave. VIT's *own* logic (parsing, type mapping, interface generation, integration) is clean and straightforward. The complexity lives at the boundary with KGen.

This has implications for the project's future: if KGen is ever replaced or significantly updated, much of VIT's most fragile code becomes irrelevant. Conversely, every new Fortran pattern VIT targets will likely surface new KGen quirks.

## The NaN Saga Is the Best Engineering Story

Four commits, three wrong (or incomplete) approaches, ending at the right abstraction. The progression:

1. Skip entire cases → too coarse (loses valid field comparisons)
2. Expose field-level output → prerequisite, not a solution
3. Post-hoc text classification → correct but fragile (depends on KGen's output format)
4. Fortran-level `x /= x` patch → minimal code, maximum robustness

The final solution is 20 lines. But those 20 lines required the three prior attempts to understand *what* needed to be patched and *where*. See [202602260809-nan-handling.md](202602260809-nan-handling.md) for the full four-commit evolution.

## The Passthrough Decision Defines VIT's Scope

The choice to treat non-BIND(C) derived types as passthroughs (accepted by the Fortran wrapper, invisible to C++) is the most consequential architectural decision in the project. It's correct for ROSCO's `ErrorVariables` but it draws a hard line: VIT can only translate functions whose *core algorithm* operates on C-compatible types. Functions that read or mutate derived type fields in their main logic path are out of scope.

This is a reasonable constraint for a proof-of-concept, but it's worth noting that many real scientific Fortran functions *do* operate on derived types (e.g., updating fields of a state struct). Crossing that boundary — full derived type marshalling or opaque pointer passing — would be a fundamentally different level of complexity. See [202602272155-derived-types-assumed-shape.md](202602272155-derived-types-assumed-shape.md) for the design rationale.

## What's Missing From the Retrospective Notes

Since the 14 historical notes are reconstructions from commit messages and diffs rather than real-time notes, there are gaps:

- **Failed approaches that didn't become commits**: We see the NaN handling evolution because each attempt was committed. But there were likely extraction attempts, Makefile configurations, or interface generation approaches that failed and were discarded without a trace.
- **Decision context**: Why was `saturate` chosen as the first target? Why ROSCO at all? Why KGen over other instrumentation approaches? The notes capture *what* was decided but not always the full *why*.
- **Timing of the AI collaboration**: The co-author tags suggest Claude was involved throughout, but the notes don't capture which parts were human-directed vs. AI-generated, or where the human had to correct the AI's approach.

Going forward, real-time dev notes will capture this richer context that can't be reconstructed after the fact.
