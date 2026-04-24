# AeroDyn Translation Scope Decisions
**Date:** 2026-04-23
**Status:** completed
**Scope:** Step 2 of CREATING_PROJECT_PLANS.md — scope negotiation for PROJECT_PLAN_AERODYN.md

## Summary

Settled the five scope decisions that shape the AeroDyn translation plan. These are **committed** decisions per the methodology — they will not be revisited without a Change Log entry in PROJECT_PLAN_AERODYN.md.

## Decisions

### 1. Translation target breadth: All of AeroDyn

**Decision:** Translate all 507 hand-written top-level procedures across all 5 sub-systems (BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, core AeroDyn).

**Alternative considered:** Start with BEMT + UnsteadyAero + Core (~226 procedures) — the primary physics path exercised by all 16 regression tests. FVW/OLAF and AeroAcoustics deferred. This was the recommendation. User chose full scope instead.

**Implication:** The plan must cover all 5 sub-systems. Phasing should still translate leaf sub-systems first and work inward, but no sub-system is out of scope.

### 2. Registry-generated types: Build a C++ code generator

**Decision:** Write a C++ code generator that modifies the OpenFAST Registry to emit C++ types instead of Fortran. This replaces 740 procedures / 28,490 lines of auto-generated Pack/Unpack/Copy/Destroy boilerplate.

**Alternative considered:** Leave types as Fortran with interop wrappers (ROSCO's view struct pattern at larger scale). This was the recommendation. User chose the C++ generator — the right long-term answer.

**Implication:** This is a significant sub-project. The Registry tool (separate from AeroDyn) needs to be understood, extended, and tested. The C++ generator must produce types that are structurally compatible with what the hand-translated physics code expects. This should be an early phase — the physics translation depends on having C++ types to work with.

### 3. Drivers and C-binding: Translate everything

**Decision:** Translate the AeroDyn Driver, UA/AA drivers, and the C-binding layer to C++. Full C++, no Fortran remaining.

**Alternative considered:** Leave drivers and C-binding as Fortran (they're test infrastructure and integration code). This was the recommendation. User chose full translation.

**Implication:** Drivers are needed throughout translation as KGen extraction targets and regression test runners. They should be translated last — after all physics code is verified and integrated. The C-binding layer depends on the type system, so it should come after the Registry generator.

### 4. Verification rigor: Three-tier (same as ROSCO)

**Decision:** Same three-tier verification as ROSCO:
- **Tier 0:** KGen float64 kernel verification per function
- **Tier 1:** 16-case simulation comparison, bit-identical against our committed baselines
- **Tier 2:** NREL regression at 1e-2 tolerance (correctness gate, 16/16 pass required)

**Infrastructure status:** Tier 1 and Tier 2 infrastructure already built and verified (dev notes `202604140330`, `202604232054`). KGen infrastructure (VIT extract/verify) is proven from ROSCO but needs adaptation for AeroDyn's build system.

### 5. Done criteria: Pure C++ AeroDyn module

**Decision:** Done when:
- All AeroDyn Fortran source files removed from the build
- No libgfortran dependency
- 16 regression test cases pass bit-identical against baselines
- NREL regression 16/16 pass
- AeroDyn Driver executable works for all 16 test cases
- C-binding layer functional (Exawind interface preserved)

This is the direct analog of ROSCO's `integrated` branch.

## What these decisions mean for the plan

1. **Scale:** ~507 hand-written procedures + 740 Registry-generated procedures + 36 driver procedures + 20 C-binding procedures = ~1,300 total procedures. This is ~25× ROSCO's 52 functions.

2. **The Registry generator is a critical-path dependency.** Physics translation needs C++ types. This should be one of the first phases.

3. **Phasing still follows the call graph.** Leaf functions first (247 identified), then mid-level, then orchestrators. But all 5 sub-systems are in scope from the start.

4. **Verification infrastructure is mostly built.** The 16-case baselines and NREL regression wrapper are ready. KGen adaptation for AeroDyn's build chain is needed.

5. **The AeroDyn Driver must keep working throughout.** It's the regression test runner. Translate it last.

## Related

- `vit/analysis/aerodyn/` — structural inventory data (basis for these decisions)
- Dev note `202604232137-aerodyn-structural-inventory.md` — inventory findings
- Dev note `202604232318-uncalled-classification-refinement.md` — uncalled procedure analysis
- `CREATING_PROJECT_PLANS.md` — methodology (Step 2: Scope Negotiation)
