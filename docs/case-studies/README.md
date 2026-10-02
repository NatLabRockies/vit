# Case studies

The two Fortran codebases VIT has been developed on. Everything specific to one codebase lives here;
the rest of `docs/` applies to any.

Dev notes about both are in [`dev/`](../../dev/README.md), cited by timestamp.

## ROSCO (complete, April 2026)

A wind turbine controller. All 52 functions and the controller's entry point were translated,
giving a pure-C++ library. Translation fork: `https://github.com/kevinmenear/ROSCO`.

| File | What |
|---|---|
| [`rosco/PROJECT_PLAN.md`](rosco/PROJECT_PLAN.md) | The plan, kept as a historical record, with a retrospective and the later findings that change what it claims |

## AeroDyn (active)

The aerodynamics module of OpenFAST. 32 functions in its AirfoilInfo and DBEMT sub-modules are
integrated. Translation fork: `https://github.com/kevinmenear/openfast`.

| File | What |
|---|---|
| [`aerodyn/PROJECT_PLAN.md`](aerodyn/PROJECT_PLAN.md) | The living plan: scope, key decisions, phases and current state |
| [`aerodyn/AGENT_MANUAL.md`](aerodyn/AGENT_MANUAL.md) | The operating manual an agent works from: the workspace, the per-function workflow, extraction and test cases |
| [`aerodyn/NWTC_UTILITIES.md`](aerodyn/NWTC_UTILITIES.md) | The shared C++ translations of NWTC Library routines |
| [`aerodyn/data/`](aerodyn/data/) | The structural inventory from `vit inventory` (procedures, call graph, types, modules, line counts), the entry points it is run with, and line coverage of 17 test cases for `vit find --coverage`, the work order and, as `coverage.file`, `vit extract` |
