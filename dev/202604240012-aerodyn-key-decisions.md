# AeroDyn Translation Key Decisions
**Date:** 2026-04-24
**Status:** completed
**Scope:** Step 3 of CREATING_PROJECT_PLANS.md — key decisions for PROJECT_PLAN_AERODYN.md

## Summary

Six architectural decisions that shape how the AeroDyn translation will be executed. These build on the five scope decisions (dev note `202604232342`). Together, the 11 decisions define the full project architecture.

## Decisions

### KD1: Type strategy — Registry generator + VIT

**Decision:** Two tools, clean separation:
- **Registry-generated types** (`_Types.f90` files, 740 procedures, 28,490 lines): handled by a new C++ code generator that modifies the OpenFAST Registry to emit C++ structs instead of Fortran derived types. Produces Pack/Unpack/Copy/Destroy equivalents in C++.
- **Hand-written types** (local structs in physics code): handled by VIT's existing `analyze-types` / BIND(C) / view struct machinery, proven on ROSCO.

**Rationale:** Each tool does what it's good at. The Registry already has a schema (the `.txt` input files that define types) — the generator reads the same schema and emits C++ instead of Fortran. VIT handles the smaller, simpler hand-written types it already knows how to process. No tool needs to learn the other's job.

**Alternative rejected:** VIT handles everything (B) — would require extending VIT to handle POINTER, nested ALLOCATABLE-of-derived-type, and all Registry Pack/Unpack patterns. Massive VIT scope expansion for boilerplate code. Also rejected: all types through the Registry generator (C) — hand-written types aren't in the Registry schema.

### KD2: KGen extraction target — Both (AeroDyn Driver + full OpenFAST)

**Decision:** Use AeroDyn Driver (`aerodyn_driver`) as the primary KGen extraction target. Use full OpenFAST executable for functions only reachable in coupled mode (linearization routines, mesh mapping, any function not exercised by the 16 r-test cases).

**Rationale:** AeroDyn Driver exercises the same physics code paths as full OpenFAST with a self-contained build and fast iteration. The 16 r-test cases cover BEMT, OLAF, UA, MHK, and multi-rotor configurations. But some functions (Jacobian routines, constraint state residuals) are only called from the OpenFAST glue code — the structural inventory identified 3 confirmed framework interface procedures (`AD_JacobianPInput`, `AD_JacobianPContState`, `AD_VarsPackExtInput`) that need coupled-mode extraction.

**User correction:** Initial recommendation was option A (Driver only, handle others "individually later"). User noted this kicks the can down the road — both targets should be in scope from the start. Corrected to option C.

### KD3: Phasing order — Bottom-up by dependency layer

**Decision:** Translate from leaves to roots, following the call graph:

```
Phase 0: Registry C++ generator (types — needed by all subsequent phases)
Phase 1: AirfoilInfo + DBEMT (leaf modules, no AeroDyn dependencies)
Phase 2: BEMTUncoupled + UnsteadyAero (depend on Phase 1)
Phase 3: BEMT (depends on Phase 2)
Phase 4: FVW/OLAF (depends on Phases 1-2)
Phase 5: AeroAcoustics (depends on Phase 1)
Phase 6: Core AeroDyn (depends on everything above)
Phase 7: AeroDyn_Inflow / C-binding / Drivers (final integration layer)
```

**Rationale:** Mirrors what worked for ROSCO (leaves first, then callers). Each layer can be fully verified before building on it. The Registry generator must come first because C++ types are needed by every physics translation.

**Alternative rejected:** By sub-system top-down (B) — produces a "done" sub-system faster but requires translating shared dependencies in the first phase anyway. Horizontal slices (C) — maximizes parallelism but doesn't produce a working translated sub-system until late.

### KD4: FMA handling — Same as ROSCO

**Decision:** `-ffp-contract=off` on both Fortran and C++ compilers during the translation/verification phase. Re-enable FMA on the pure C++ build after all translation is complete.

**Rationale:** Proven approach from ROSCO. Eliminates FMA scheduling differences between gfortran and g++ that cause 1-ULP artifacts, making it possible to verify bit-identical translation. The flag is only needed during the mixed Fortran/C++ phase — the pure C++ build can use any FP optimization.

### KD5: NWTC Library — Leave as Fortran, C interop boundary

**Decision:** NWTC Library stays as Fortran. AeroDyn's C++ code calls NWTC Library routines through `extern "C"` / `BIND(C)` wrappers.

**Rationale:** NWTC Library is shared infrastructure across all of OpenFAST (every module depends on it). Translating it is a separate project with different stakeholders and much wider blast radius. The interop boundary is clean — AeroDyn calls NWTC routines, it doesn't extend them. Analogous to C++ calling libc without translating libc.

### KD6: Other external modules — Same as NWTC Library

**Decision:** InflowWind, SeaState, and LinDyn stay as Fortran. AeroDyn receives their data through view structs or C interop wrappers.

**Rationale:** Same as KD5. These are separate modules with their own maintainers. AeroDyn consumes their output (wind field, wave field, linear dynamics state) as read-only data provided via function arguments. The interop boundary is clean.

## The translation boundary

Everything inside AeroDyn gets translated to C++:
- 507 hand-written procedures (physics, I/O, orchestration)
- 740 Registry-generated procedures (via C++ code generator)
- 36 driver procedures
- 20 C-binding procedures

Everything outside stays Fortran:
- NWTC Library (~53 files, ~48K lines)
- InflowWind
- SeaState
- LinDyn
- OpenFAST glue code

The boundary is crossed via C interop (BIND(C) wrappers, view structs). This is the same pattern as ROSCO's boundary with OpenFAST's DLL interface — just at a different level in the architecture.

## Related

- Dev note `202604232342-aerodyn-scope-decisions.md` — the 5 scope decisions these build on
- Dev note `202604232137-aerodyn-structural-inventory.md` — inventory data that informed these decisions
- Dev note `202604232318-uncalled-classification-refinement.md` — framework interface confirmation
- `CREATING_PROJECT_PLANS.md` — methodology (Step 3: Key Decisions)
