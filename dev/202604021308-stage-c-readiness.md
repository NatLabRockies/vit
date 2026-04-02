# Stage C Readiness Assessment: Questions Before Proceeding

**Date:** 2026-04-02
**Status:** in-progress
**Scope:** Pre-Stage C analysis — what we know, what we need to decide, and what we need to investigate before translating restart I/O and debug logging

## Stage C Scope (per PROJECT_PLAN.md)

Stage C covers 3 functions in 2 translation files, all from `ROSCO_IO.f90`:

1. **WriteRestartFile + ReadRestartFile** → `translations/IO/restart.cpp` — Matched pair for checkpoint I/O. Plan specifies "new binary format with version header" and roundtrip verification (write → read → compare struct contents).

2. **Debug** → `translations/IO/debug_logger.cpp` — Debug logging with static local state (matching Fortran SAVE semantics). Opens debug files on first call, writes tabulated data per timestep.

All three are non-KGen functions — they perform file I/O that KGen can't instrument. They'll use the simulation-level verification tooling built in Stage B (`vit sim-compare`, `--reverse-copy`, `--auto-allocate`).

## Open Questions

### 1. Restart File Format Compatibility

The plan says "new binary format with version header." This is an idiomatic C++ design choice — replacing Fortran's unformatted I/O with a structured binary format. The implication:

- Old Fortran restart files won't be readable by the C++ version
- New C++ restart files won't be readable by the old Fortran version
- During the mixed Fortran/C++ transition period (before Phase 11 full cutover), this could matter if users have existing restart files

**Decision needed:** Is backward compatibility required, or is a clean break acceptable? If backward compatibility is needed, the C++ version would need to read the Fortran format (Fortran unformatted sequential records with record-length markers). If a clean break is acceptable, the C++ version defines its own format from scratch.

**Context:** ROSCO restart files are used to resume wind turbine controller state across simulation restarts. They're typically generated and consumed within the same simulation campaign, so format changes between versions may be tolerable — users regenerate restart files when they update the controller.

### 2. Debug Function Verification Strategy

Debug writes log files but doesn't affect simulation output arrays. This means `vit sim-compare` (which compares `.npz` simulation output) won't detect regressions in debug logging — the simulation values are identical regardless of whether debug output is correct, incorrect, or completely absent.

**Options:**
- **Compare debug output files:** Run baseline (Fortran) and integrated (C++) simulations with debug enabled, then diff the debug log files. Exact match would prove correctness.
- **Roundtrip/structural test:** Verify that the debug output contains the expected columns and row count, without requiring bit-identical formatting.
- **Accept sim-compare only:** If debug logging doesn't affect controller behavior, sim-compare still proves the controller is correct. Debug output correctness could be a manual spot-check.

**Decision needed:** Which verification strategy is appropriate? The answer depends on how much we trust that debug logging is purely observational (no side effects on controller state).

### 3. Functions Missing from the Roadmap

The function survey found several untranslated functions not assigned to any stage in PROJECT_PLAN.md:

| Function | File | Description | Notes |
|----------|------|-------------|-------|
| `PreFilterMeasuredSignals` | Filters.f90 | Orchestrator that calls individual filters | Calls LPFilter, SecLPFilter, NotchFilter, NotchFilterSlopes — all already translated |
| `CPfunction` | Functions.f90 | Parameterized Cp(lambda) function | May be unused in main ROSCO loop — needs investigation |
| `matinv3` | Functions.f90 | 3x3 matrix inverse | Used by WindSpeedEstimator (EKF) — but WSE is already translated, so matinv3 must be called from C++ somehow |
| `unwrap` | Functions.f90 | Phase angle unwrapping | Need to check if called from any translated function |
| `BladedDLL_Legacy_Procedure` | ExtControl.f90 | Abstract interface definition | Not a function body — likely doesn't need translation |

**Decision needed:** These functions need to be slotted into a stage or explicitly excluded. PreFilterMeasuredSignals is an orchestrator like SetParameters — it may belong in Stage E. The utility functions (CPfunction, matinv3, unwrap) could be Stage C additions or folded into an existing stage.

### 4. ROSCO_IO.f90 Source Complexity (Not Yet Investigated)

We haven't read the actual source code for WriteRestartFile, ReadRestartFile, or Debug yet. Before creating a detailed implementation plan, we need to understand:

- **Size:** How many lines are these functions? WriteRestartFile and ReadRestartFile likely mirror each other field-by-field.
- **Derived types touched:** Which types do they read/write? Probably LocalVariables, ControlParameters, maybe FilterParameters. This determines which view structs and reverse-copy subroutines are needed.
- **Fortran I/O patterns:** Unformatted (binary) for restart, formatted (text) for debug. The C++ equivalents are `<fstream>` binary read/write and `<fstream>`/`<iomanip>` text output.
- **SAVE variables:** Debug almost certainly uses Fortran SAVE for file unit numbers and first-call flags. C++ equivalent is `static` local variables or a state struct.
- **Conditional compilation or mode flags:** Are there debug levels? Is restart writing conditional on a mode flag?

**Action needed:** Read ROSCO_IO.f90 and catalog the above before planning.

## What's Ready

The tooling built in Stage B is directly applicable:

| Capability | Stage C Use |
|------------|-------------|
| `vit translate` (with ALLOCATABLE hints) | Scaffold restart.cpp and debug_logger.cpp |
| `vit integrate --reverse-copy` | Restart reads back into Fortran types |
| `vit integrate --auto-allocate` | If restart reads allocate arrays |
| `vit sim-compare` | Verify simulation output unchanged |
| `integrate_all.sh` | Add Stage C functions to batch script |

The verification workflow is established: baseline arrays exist in `baseline_arrays/`, the comparison infrastructure works, and we've proven the pattern with 3 Stage B functions.

## Recommended Sequence

1. **Investigate:** Read ROSCO_IO.f90 source, catalog functions and complexity
2. **Decide:** Resolve the 3 open questions above (format compatibility, debug verification, missing functions)
3. **Plan:** Create detailed implementation plan with the decisions incorporated
4. **Execute:** Translate, integrate, verify — following the Stage B pattern

## Relationship to Previous Work

Stage C follows Stage B's pattern of non-KGen, simulation-verified translations. The key difference is that Stage B functions (config loading) are called once at startup, while Stage C functions (restart I/O, debug logging) are called repeatedly during simulation. This means:

- **Performance may matter more** — Debug is called every timestep
- **State management is more complex** — SAVE variables, open file handles
- **Verification is different** — Debug output doesn't feed back into simulation arrays

The Stage B tooling investment (6 new VIT capabilities) was specifically motivated by the remaining non-KGen stages (C through E). This is the first stage that benefits from that investment.
