# PROJECT_PLAN_AERODYN.md Drafted
**Date:** 2026-04-24
**Status:** completed
**Scope:** Steps 5-7 of CREATING_PROJECT_PLANS.md — phase roadmap, revisit triggers, plan document

## Summary

Completed the full CREATING_PROJECT_PLANS.md methodology (Steps 1-7) and drafted `PROJECT_PLAN_AERODYN.md`. The plan document incorporates the structural inventory (Step 1), five scope decisions (Step 2), six key architectural decisions (Step 3), ten open questions (Step 4), a nine-phase roadmap with confidence labels (Step 5), six revisit triggers (Step 6), and the standard Section 1-8 document structure (Step 7).

## Phase roadmap overview

| Phase | Sub-system | Procs | Lines | Confidence |
|-------|-----------|-------|-------|------------|
| 0 | Registry C++ Generator | 740 (replaced) | 28,490 | speculative |
| 1 | AirfoilInfo + DBEMT | 37 | 3,415 | planned |
| 2 | BEMTUncoupled + UnsteadyAero | 73 | 6,690 | planned |
| 3 | BEMT | 37 | 2,881 | planned |
| 4 | FVW/OLAF | 137 | 10,738 | speculative |
| 5 | AeroAcoustics | 51 | 4,424 | speculative |
| 6 | Core AeroDyn | 85 | 13,549 | speculative |
| 7 | Inflow/C-binding/Drivers | 87 | 8,278 | speculative |
| 8 | Integration + Cleanup | — | — | unknown |

Phases 0-3 form the critical path to a working BEMT translation. Phases 4-7 are speculative and will be revised after Phase 3 completes. Phase 8 depends on all prior phases.

## Open questions answered during this session

- **OQ3 (NWTC Library interop surface):** 287 unique external callees. ~150 need wrappers after excluding Registry boilerplate and Fortran intrinsics.
- **OQ4 (r-test coverage, partial):** 9 BEMT-path + 8 OLAF-path cases. AeroAcoustics, C-binding, and linearization NOT covered.
- **OQ5 (OpenMP in FVW):** Simple `!$OMP PARALLEL DO` loops. Embarrassingly parallel. Direct `#pragma omp` translation.

## Key revisit triggers

1. **After Phase 0 investigation:** Is the Registry generator viable?
2. **After Phase 1 first extraction:** Does KGen/VIT work on AeroDyn? (the "prove it works" gate)
3. **After Phase 3:** BEMT path complete — re-examine FVW/OLAF sub-phasing
4. **Before Phase 5:** AeroAcoustics verification strategy (no r-test coverage)

## Methodology assessment

This is the first full application of CREATING_PROJECT_PLANS.md. The methodology took ~10 hours across two sessions:
- Step 1 (structural inventory): ~4 hours including script development and 5 rounds of uncalled-procedure refinement
- Steps 2-4 (scope, decisions, questions): ~3 hours of conversation + documentation
- Steps 5-7 (roadmap, triggers, document): ~3 hours

The methodology's requirement for "research before prescription" (P1) proved valuable — the inventory surfaced facts (247 leaf functions, 1 SAVE procedure, 14 genuinely dead, OpenMP patterns) that directly shaped the phasing. The confidence labels (P2) force honest acknowledgment that Phases 4-8 are speculative.

## Related

- `PROJECT_PLAN_AERODYN.md` — the plan document itself
- Dev note `202604232342` — scope decisions
- Dev note `202604240012` — key decisions
- Dev note `202604240016` — open questions (updated with OQ3/OQ4/OQ5 answers)
- `CREATING_PROJECT_PLANS.md` — the methodology
