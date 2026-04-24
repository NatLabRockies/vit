# AeroDyn Structural Inventory via fparser2
**Date:** 2026-04-23
**Status:** completed
**Scope:** Structural analysis of AeroDyn and NWTC Library source code as input to PROJECT_PLAN_AERODYN.md

## Summary

Completed the structural inventory prescribed by CREATING_PROJECT_PLANS.md Step 1 ("Research precedes prescription"). A Python script (`vit/scripts/fortran_inventory.py`) parses all 39 AeroDyn source files and 30 NWTC Library dependency files using fparser2 (PSyclone's Fortran AST toolkit), producing 7 JSON data files and a markdown summary at `vit/analysis/aerodyn/`.

The inventory is the factual foundation for the upcoming PROJECT_PLAN_AERODYN.md. No plan text is written yet.

## Key findings

### Scale

| Metric | Value |
|--------|-------|
| Total AeroDyn files | 39 |
| Total lines | 75,293 |
| Hand-written lines | 46,803 (62%) |
| Registry-generated lines | 28,490 (38%) |
| Modules | 35 |
| Hand-written top-level procedures | 507 |
| Internal procedures (CONTAINS blocks) | 190 |
| Registry-generated procedures | 740 |
| Derived types (AeroDyn) | 150 |
| Derived types (NWTC Library deps) | 26 |

**Translation scope is ~507 hand-written top-level procedures across 46,803 lines.** The 740 registry-generated procedures and 28,490 generated lines are boilerplate (Pack/Unpack/Copy/Destroy) that may be auto-generated for C++ rather than hand-translated. The 190 internal CONTAINS-block procedures are mostly error handlers (`Failed`, `Cleanup`) that will be absorbed into their parent procedures during translation.

### Module structure

AeroDyn decomposes into 5 major sub-systems, each with their own physics:

1. **Core AeroDyn** — `AeroDyn.f90` (64 procedures), `AeroDyn_IO.f90` (17), `AeroDyn_IO_Params.f90` (0 procedures, just constants)
2. **BEMT (Blade Element Momentum Theory)** — `BEMT.f90` (37), `BEMTUncoupled.f90` (27), `DBEMT.f90` (14)
3. **FVW (Free Vortex Wake / OLAF)** — `FVW.f90` (22), `FVW_Subs.f90` (41), `FVW_VortexTools.f90` (36), `FVW_BiotSavart.f90` (8), `FVW_Wings.f90` (10), `FVW_IO.f90` (13)
4. **UnsteadyAero (UA)** — `UnsteadyAero.f90` (44), `AirfoilInfo.f90` (23)
5. **AeroAcoustics (AA)** — `AeroAcoustics.f90` (32), `AeroAcoustics_IO.f90` (19), `AeroAcoustics_TNO.f90` (4)

Plus infrastructure: `AeroDyn_Inflow.f90` (wrapper), `AeroDyn_Inflow_C_Binding.f90` (C API), Driver/Test files.

### Leaf functions — first translation candidates

247 leaf functions (procedures with no internal call edges) were identified. These are the natural first translation targets since they have no downstream dependencies. Examples of small, pure-math leaves:

- `Log10AA` (AeroAcoustics, 1 arg, 7 lines)
- `Calc_Omega` (AeroDyn_IO, 1 arg, 7 lines)
- `f_int2` (TNO, 1 arg, 9 lines)
- `VelocityIsZero` (BEMTUnCoupled, 1 arg, 12 lines)
- `computeAirfoilAOA` (BEMTUnCoupled, 1 arg, 12 lines)

### Largest procedures

| Procedure | Module | Lines |
|-----------|--------|-------|
| Calc_WriteAllBldNdOutput | AeroDyn_AllBldNdOuts_IO | 1,938 |
| ParsePrimaryFileInfo | AeroDyn_IO | 824 |
| UA_CalcOutput | UnsteadyAero | 765 |
| ADI_C_Init | AeroDyn_Inflow_C_BINDING | 717 |
| Calc_WriteOutput | AeroDyn_IO | 557 |

### External dependencies

AeroDyn depends on 24 external modules beyond its own scope:

- **NWTC Library** (core framework): NWTC_Library, NWTC_LAPACK, NWTC_FFTPACK, NWTC_SLATEC, NWTC_Num, NWTC_IO, NWTC_C_Binding, PolynomialRoots, VTK, VersionInfo, YAML
- **InflowWind**: IfW_FlowField, IfW_FlowField_Types, InflowWind, InflowWind_IO, InflowWind_Types
- **SeaState**: SeaSt_WaveField, SeaSt_WaveField_Types, SeaState, SeaState_Types
- **LinDyn**: LinDyn, LinDyn_Types
- **Fortran intrinsics**: ISO_C_BINDING, ISO_FORTRAN_ENV, OMP_LIB

### Framework boundary

192 procedures form the external API. Categories:
- **Standard interface** (e.g., AD_Init, AD_End, AD_UpdateStates, AD_CalcOutput) — the OpenFAST module contract
- **Explicit public** — deliberate API exposures
- **C-binding** — entry points from C/C++ callers
- **Driver** — test harness entry points

### Dead code

113 procedures with zero call sites (excluding CONTAINS-block helpers and registry-generated). Categories:
- C-binding entry points (called from C, not Fortran — expected)
- Driver/test functions (called from main programs — expected)
- Linearization functions (called from OpenFAST glue code — expected)
- Potentially genuinely dead code (e.g., `BEMTU_InductionWithResidual`, `ui_expansion_order2`)

### Types landscape

150 AeroDyn types, 26 dependency types. The largest types:
- `RotMiscVarType` (79 fields, has ALLOCATABLE)
- `RotParameterType` (76 fields, has ALLOCATABLE)
- `AD_InputFile` (61 fields, has ALLOCATABLE)
- `AA_ParameterType` (60 fields, has ALLOCATABLE)
- `FVW_ParameterType` (54 fields, has ALLOCATABLE)

The OpenFAST state-type pattern (InitInput, Input, Output, Parameter, ContinuousState, DiscreteState, ConstraintState, OtherState, MiscVar) dominates the type landscape — each sub-system has a full set. These are registry-generated with ALLOCATABLE and POINTER fields throughout. This is more complex than ROSCO's type system by roughly an order of magnitude.

### Only 1 procedure with SAVE state

Across all of AeroDyn, only 1 procedure uses SAVE variables. This is good news for translation — SAVE state was a major source of complexity in ROSCO (DLL persistence, scenario isolation).

## Bugs found and fixed during development

1. **Function name extraction**: `first_name()` was returning kind specifiers (`IntKi`, `ReKi`) instead of function names from declarations like `PURE INTEGER(KIND=IntKi) FUNCTION OutParamFlags(ind)`. Fixed by using `stmt.items[1]` which fparser populates with the actual function name.

2. **CONTAINS-block inflation**: Internal procedures (like `Failed`, `Cleanup`) in CONTAINS blocks were appearing as dead code because they're only called from their parent procedure, not via module-level CALL statements. Fixed by detecting nested procedures and tagging them `is_internal: true`.

## Deliverables

| File | Description |
|------|-------------|
| `vit/scripts/fortran_inventory.py` | Inventory script (fparser2-based, ~500 lines) |
| `vit/analysis/aerodyn/function_inventory.json` | 1437 procedures with full metadata |
| `vit/analysis/aerodyn/call_graph.json` | 3222 caller→callee edges |
| `vit/analysis/aerodyn/type_inventory.json` | 176 derived type definitions |
| `vit/analysis/aerodyn/module_dependencies.json` | 35 modules, topologically sorted |
| `vit/analysis/aerodyn/dead_code.json` | 113 unreferenced procedures |
| `vit/analysis/aerodyn/framework_boundary.json` | 192 external API procedures |
| `vit/analysis/aerodyn/loc_metrics.json` | LOC breakdown by file/module/procedure |
| `vit/analysis/aerodyn/inventory_summary.md` | Human-readable digest |

## Observations that will shape PROJECT_PLAN_AERODYN.md

1. **The 507 vs 52 ratio.** AeroDyn has ~10× more procedures than ROSCO. But ~247 are leaves with zero internal callees — nearly half. Translation can start at the edges and work inward.

2. **Five sub-systems, not one flat module.** BEMT, FVW/OLAF, UnsteadyAero, AeroAcoustics, and core AeroDyn are semi-independent. The plan should treat them as separate phases with their own call-graph layers.

3. **Registry-generated code is 38% of lines.** VIT will need a strategy for the `_Types.f90` files — either auto-generate C++ equivalents from the OpenFAST Registry, or translate them manually (unwise — they're boilerplate with ~740 procedures).

4. **Type complexity is an order of magnitude beyond ROSCO.** The largest types have 60-79 fields with ALLOCATABLE and POINTER members. VIT's `view` struct strategy may need enhancement for types this large.

5. **External dependencies are narrower than feared.** Beyond NWTC Library (a framework dependency, not a translation target), AeroDyn only depends on InflowWind, SeaState, and LinDyn — all read-only data providers. The translation boundary is clean.

6. **SAVE state is minimal (1 procedure).** This eliminates the DLL persistence problem that consumed significant effort in ROSCO.

7. **OpenMP usage exists.** `FVW_BiotSavart.f90` uses `OMP_LIB`. Threading will need consideration in the C++ translation (either preserve OpenMP pragmas or convert to C++ threading).

## Next steps

1. Draft PROJECT_PLAN_AERODYN.md using this inventory data (CREATING_PROJECT_PLANS.md Steps 2-7)
2. The plan should define sub-system phases, type strategy decisions, and verification infrastructure
3. The inventory JSON files should be referenced from the plan document as the factual basis

## Related

- `CREATING_PROJECT_PLANS.md` — the methodology this inventory follows
- Dev note `202604140013-openfast-aerodyn-phase-start.md` — AeroDyn kickoff
- Dev note `202604232054-nrel-regression-16-of-16-pass.md` — verification infrastructure completion
