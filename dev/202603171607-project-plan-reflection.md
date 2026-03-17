# Project Plan Reflection: Honest Assessment Before Execution

**Date:** 2026-03-17
**Status:** completed
**Scope:** project planning, risk assessment

## Background

After creating PROJECT_PLAN.md (11 phases, 80+ checkbox items, covering full ROSCO translation from today's 4 verified functions to a pure C++ `libdiscon.so`), this note captures a critical reflection on the plan before beginning execution. The goal is to identify where the plan is strong, where it's likely to break, and what to watch for.

## What's Strong

### Phases 1–4 are well-defined and achievable

Phase 1 is essentially free — three functions (`wrap_360`, `sigma`, `CPfunction`) that fit patterns VIT already handles. Phase 2 is real work but scoped: fixed-size arrays and subroutines are well-understood Fortran/C interop problems with known solutions. Phase 3 is a natural extension of existing assumed-shape handling. Phase 4 (load-bearing derived types) is the first genuinely hard problem, but it's bounded to only two Functions.f90 targets (`ratelimit`, `AeroDynTorque`).

Getting to "14/14 Functions.f90 complete" is a credible near-term goal and a powerful statement for the upcoming meeting.

### Incremental verification is the secret weapon

At every step, the mixed Fortran/C++ codebase must produce byte-identical simulation output. There's never a "big bang" moment where everything breaks. You can stop at any phase and have a working system. This is what makes the project feasible.

## Where It Gets Hard

### Phase 4 is the real inflection point

Everything before Phase 4 is interface plumbing — type conversions, array layout, calling conventions. Phase 4 is where VIT has to understand what the Fortran code *does* with its arguments, not just what types they are. The field extraction strategy (decomposing a derived type into individual C args) is conceptually clean but mechanically complex — every function needs a custom extraction pattern based on which fields it actually accesses.

### Phases 5–7 are where scope explodes

The plan lists Filters, Controllers, and ControllerBlocks as separate phases, but the real challenge is shared: almost every function in these modules takes `LocalVariables` and `ControlParameters` — the two largest, most complex derived types in ROSCO (~170 and ~115 fields respectively, with nested types and ALLOCATABLE arrays). By Phase 5, you need a working solution for passing these across the language boundary, not just for one function but for dozens.

The difference between "translate `sigma`" and "translate `PitchControl`" isn't just complexity — it's a different category of problem. PitchControl reads and writes dozens of fields on LocalVariables, calls PIController (which reads and writes piParams), calls ratelimit (which reads and writes rlParams), and all of this state persists across timesteps. The C++ translation needs access to the entire state graph, not just a few extracted fields.

### Phase 8 (Type System Translation) might need to move earlier

The plan puts the full type system rewrite after all algorithmic modules are translated, but in practice there's likely a wall in Phase 5 or 6 where the field-extraction approach becomes too cumbersome, and full C++ struct definitions are needed to make progress. At that point, the cleanest path may be to pause algorithmic translation, do the type system rewrite, and then resume — with all derived types available as C++ structs from the start.

**Key decision point**: After Phase 4, assess whether field extraction is scaling or whether it's time to pivot to full struct mapping. The plan should stay flexible here.

### Phases 9–10 are large but straightforward

The ~4,700 lines of I/O code are mostly mechanical translation — Fortran file I/O to C++ `fstream`, Fortran string manipulation to `std::string`. Tedious but not technically challenging. The interesting question is whether VIT should grow to handle these, or whether they're better done as direct rewrites outside VIT.

## Things to Watch For

### The derived type problem could change the plan

If field extraction works cleanly for Phases 4–5, the plan holds. If every function needs 15+ extracted fields and the wrappers become unmanageable, the pivot to full struct mapping needs to happen earlier. This is the biggest uncertainty in the entire plan.

### Phase ordering may shift

The dependency graph says bottom-up, but practical needs (demonstrating progress, proving patterns early) may pull things around. For instance, translating one filter (LPFilter) before finishing all of Functions.f90 could make sense if it proves the state-passing pattern works.

### KGen will surprise you

Every new Fortran pattern VIT has attempted has surfaced at least one new KGen workaround. Subroutines (Phase 2), 2D arrays (Phase 3), and stateful functions (Phase 4+) will each likely need KGen patches that can't be predicted in advance. Budget time for this.

## Overall Assessment

The plan is credible through Phase 4 (all of Functions.f90). That alone is significant — a complete Fortran module fully translated, verified, and integrated. Beyond that, the plan is directionally correct but will need to adapt as the derived type strategy evolves in practice.

The hardest unsolved problem isn't any individual function — it's how to give C++ code efficient, type-safe access to ROSCO's state types while maintaining the Fortran interop shims needed during the transition.

The meeting should go well. There's a working demo, a clear roadmap with concrete near-term targets, and an honest assessment of where the hard problems are.
