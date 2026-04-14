# OpenFAST / AeroDyn — New Phase Kickoff
**Date:** 2026-04-14
**Status:** in-progress
**Scope:** Project transition from ROSCO (complete) to OpenFAST AeroDyn (new), workspace setup, initial recon

## Context

The ROSCO translation project is complete. Phases 0 through 11.5 shipped: 52 translatable functions + C++ DISCON entry point = 53 C++ files, all verified and integrated; pure C++ `libdiscon.so` on the `integrated` branch with no libgfortran dependency; three-build verification chain (upstream Fortran ≡ modified Fortran ≡ pure C++) across 27 scenarios × 13 output arrays ≈ 5.25M values (2.56M non-trivial) with one fully-explained tolerated compiler artifact; ~25% C++ speedup measured; adversarial audit completed. See dev note `202604031714-project-retrospective.md` for the full arc, and CLAUDE.md commit `ae63be2` / PROJECT_PLAN.md commit `6a43c2e` for the current documented state.

With ROSCO wrapping up, the user identified the next target: **AeroDyn, the aerodynamics module of OpenFAST** (NREL's wind turbine simulation framework). This dev note marks the formal start of that effort and records the setup decisions, workspace layout, and initial reconnaissance so a future reader can pick up the thread.

## Goal and scope framing

The goal is to translate AeroDyn — not all of OpenFAST, not a one-week proof of concept. AeroDyn is a focused module with a clean boundary: its own `modules/aerodyn/` directory, its own `CMakeLists.txt`, its own driver executable, its own tests. It's called by OpenFAST's glue code via the standard NWTC Framework module interface (`AD_Init`, `AD_UpdateStates`, `AD_CalcOutput`, `AD_End`) and its state/input/output/parameter derived types.

**Size reality check:** AeroDyn is 42 Fortran files totaling **75,430 lines** — about **9× ROSCO** (ROSCO was ~8,600 lines across 11 modules). ROSCO took 38 calendar days start-to-finish; a linear scaling puts AeroDyn at roughly 10–12 months, and that's without factoring in the complexity differences flagged below. The user and I discussed this explicitly before kicking off — the commitment is understood.

**Three plausible scoping framings were discussed** before settling on AeroDyn translation as the target:
1. **Phase 12 generalization only** — translate 5–10 functions from one module to prove VIT is project-agnostic, ~2–4 weeks.
2. **Targeted module translation** — pick a specific module that's a user pain point and translate just that.
3. **Full OpenFAST port** — multi-year rewrite of the entire simulation framework.

The user chose the middle option: AeroDyn is a specific module with real value to Exawind integration (the same colleague who is currently integrating the ROSCO C++ build into Exawind will have a direct use for AeroDyn C++). It's not a toy-scale generality exercise, but it's also not "boil the ocean."

## Setup decisions

### Separate workspace directory, not symlinks

**Decision:** Create a new sibling workspace `vit_translation_openfast/` with its own independent local clones of VIT, KGen, and OpenFAST. Do not symlink to the existing ROSCO workspace's VIT/KGen.

**Why:** I initially proposed symlinks as a way to share one VIT install across both workspaces (improvements benefit both immediately). The user pushed back: separate local directories are safer for a ~1-year effort because the OpenFAST work will inevitably need experimental VIT changes that shouldn't touch the ROSCO workspace until they're proven. Separate clones also mean each workspace has its own `.git`, its own uncommitted changes, and its own working state — no cross-contamination risk if one workspace hits a weird state.

**Tradeoff accepted:** When VIT improvements made for OpenFAST should also land in the ROSCO workspace, the backport is a manual `git pull` in the ROSCO workspace (both clones push to the same remote). That's a small friction compared to the isolation benefits.

### VIT stays on `main` — no new branch

**Decision:** Use the same VIT `main` branch for both workspaces. Do not create an `openfast-dev` or similar experimental branch.

**Why:** The entire premise of Phase 12 in `PROJECT_PLAN.md` is "prove VIT is project-agnostic by running it on a second codebase without project-specific changes." Forking VIT for OpenFAST would implicitly concede that ROSCO-specific assumptions crept into the tool — defeating the generalization test. Better discipline: treat any VIT change OpenFAST seems to need as either (a) configuration in a new `openfast/vit.yaml`, or (b) a general-purpose improvement that also benefits ROSCO and lands on `main`. If we find something that genuinely *must* be a fork, that itself is a finding worth a dev note — it means VIT has a generality gap we didn't catch during ROSCO work.

### AeroDyn, not all of OpenFAST

**Decision:** Target is AeroDyn only, not the full OpenFAST simulation framework.

**Why:** A full OpenFAST port is a multi-year effort across dozens of tightly-coupled physics modules (ElastoDyn, HydroDyn, ServoDyn, BeamDyn, SubDyn, MoorDyn, InflowWind, SeaState, plus glue code). AeroDyn alone is a big enough swallow for one focused effort. And unlike ROSCO (which was a self-contained DLL with a Bladed interface), AeroDyn is a module in a larger framework — once AeroDyn is C++, the question of what to do with the rest of OpenFAST is a separate decision that can be made with a lot more information in hand.

### KGen is shared in concept, cloned independently in practice

Same KGen fork (`kevinmenear/KGen` with the PROCEDURE POINTER, BIND(C), and backslash-escape parser fixes) is needed for both workspaces. Cloned independently for isolation per the workspace decision, but both clones point at the same remote and should stay in sync.

## What was set up

Created `vit_translation_openfast/` with three independent git clones:

| Repo | Source | Branch | Tip commit | Size |
|------|--------|--------|-----------|------|
| `vit/` | `git@github.com:NatLabRockies/vit.git` | `main` | `6a43c2e` (today's PROJECT_PLAN.md update) | 3.9M |
| `KGen/` | `git@github.com:kevinmenear/KGen.git` | `master` | `4676161` (backslash-escape fix) | 30M |
| `openfast/` | `https://github.com/OpenFAST/openfast.git` | `main` | `2895884d2` (v5.0.1 release merge from `dev`) | 496M |

Total workspace size: ~530M, dominated by OpenFAST history.

The VIT clone carries the full project history through the most recent CLAUDE.md/PROJECT_PLAN.md updates from today — meaning all of the ROSCO dev notes, plan document, and tool code are immediately available in the new workspace without needing to pull separately.

The KGen clone has all three parser fixes committed on our fork (`31d653c` PROCEDURE POINTER, `b5fd702` BIND(C), `4676161` backslash-escape) — no further patching needed.

OpenFAST was cloned from upstream NREL with full history. Top of `main` is the v5.0.1 release point (April 2026 NREL release). No shallow clone used — we may want to track changes across OpenFAST history for reference.

## Initial AeroDyn reconnaissance

Read-only structural recon of `modules/aerodyn/` — no build attempted, no KGen invoked:

**Sub-modules discovered** (42 Fortran source files):
- `AeroDyn.f90` + `AeroDyn_Types.f90` + `AeroDyn_Registry.txt` + `AeroDyn_IO.f90` + `AeroDyn_IO_Params.f90` + `AeroDyn_AllBldNdOuts_IO.f90` — the top-level module
- `AeroDyn_Inflow*` — C-binding layer for external inflow coupling
- `AeroDyn_Driver*` — standalone driver executable (a test harness we can probably use similarly to how ROSCO used `vit_sim.py`)
- `AeroAcoustics*` — blade noise prediction (AeroAcoustics, AeroAcoustics_Driver, AeroAcoustics_IO, AeroAcoustics_TNO, AeroAcoustics_Types)
- `AirfoilInfo*` — airfoil table interpolation
- `BEMT*` + `BEMTUncoupled*` — Blade Element Momentum Theory (the core aero model)
- Additional sub-modules not yet enumerated: DBEMT (Dynamic BEMT), FVW (Free Vortex Wake), UnsteadyAero — these are known to exist from OpenFAST docs but weren't in the first 30-file listing.

**Size summary:**
- AeroDyn: 42 `.f90` files, 75,430 total lines
- NWTC Library (dependency): 53 `.f90` files, 52,688 total lines — 6× ROSCO just for the "utility" library

**Registry-generated types confirmed:** Multiple `_Registry.txt` files visible in the source tree (`AeroDyn_Registry.txt`, `AirfoilInfo_Registry.txt`, `BEMT_Registry.txt`, plus others for driver and C-binding layers). The OpenFAST Registry tool (`modules/openfast-registry/`) auto-generates the corresponding `_Types.f90` files with copy/destroy/pack/unpack routines for state serialization, restart, and MPI transport. These types include POINTER components and ALLOCATABLE-of-derived-type fields. `vit analyze-types` has never seen this pattern — we should expect new blockers on first run.

## Challenges anticipated (vs ROSCO)

Three specific things AeroDyn will do differently from ROSCO that may surface VIT generality gaps. Each needs investigation before the first translation attempt:

### 1. NWTC Framework interface semantics

ROSCO was a DLL with a flat Bladed entry point: `DISCON(avrSWAP, aviFAIL, accINFILE, avcOUTNAME, avcMSG)` — 5 C-compatible arguments, one exported symbol, internal state held in `SAVE` variables. VIT's integration pipeline was designed around this shape: generate a single Fortran wrapper, emit one `extern "C"` entry point, call via ctypes.

AeroDyn is not a DLL. It's an OpenFAST module with the full NWTC Framework interface: `AD_Init`, `AD_UpdateStates`, `AD_CalcOutput`, `AD_End`, plus `AD_CopyInput`/`AD_CopyOutput`/`AD_CopyParameter`/`AD_CopyContState`/`AD_CopyDiscState`/`AD_CopyConstrState`/`AD_CopyMisc`/`AD_CopyOtherState` for every state type. State is not in SAVE variables — it's in parameter-passed derived types (`ParameterType`, `ContinuousStateType`, `DiscreteStateType`, `ConstraintStateType`, `OtherStateType`, `MiscType`, `InputType`, `OutputType`). The glue code in `openfast-library` calls these in specific orders defined by the module framework contract.

**Implication for VIT:** The "function + view struct" pattern VIT uses for ROSCO's `ControlParameters` (pointer+size views, populator subroutines) may work for AeroDyn's state types in spirit, but the full-framework-contract translation strategy is an open design question. One big state type per module, or one per state-type category, or one per subroutine argument? How do Init/Update/CalcOutput share state across calls — do we need one master view or multiple? None of this has been thought through yet.

### 2. Registry-generated types

OpenFAST's Registry tool reads `.txt` spec files (like `AeroDyn_Registry.txt`) and generates Fortran types with boilerplate copy/destroy/pack/unpack routines. These types are not written by hand — they're machine-generated and rewriting them by hand would break future regenerations. They also contain features VIT's `analyze-types` has not been exposed to: nested POINTER components, `ALLOCATABLE`-of-derived-type (arrays of structs with their own ALLOCATABLE fields), explicit `TYPE(MeshType)` fields using NWTC's mesh mapping system, etc.

**Implication for VIT:** First `vit analyze-types` run against `AeroDyn_Types.f90` will probably surface several new blocker categories. We may need new `--fix` modes (or new view struct handling), or we may need to treat Registry-generated types as view structs from the start rather than trying to make them BIND(C)-compatible. The Registry is also a code-generation target — once we know what the C++ equivalent looks like, we might want to add a Registry-to-C++ generator to VIT itself, bypassing the Fortran `_Types.f90` step.

### 3. Heavier build + extraction chain

ROSCO builds in seconds: `cmake` + `cmake --build` produces `libdiscon.so` quickly. KGen instruments a call site, rebuilds, runs `vit_sim.py` (which calls the DLL via ctypes for a few thousand timesteps), captures state, and produces a kernel. End-to-end extraction for one ROSCO function takes ~1–2 minutes.

OpenFAST builds take minutes even on a fast machine, depending on which modules are enabled and whether optional external libraries (NetCDF, HDF5, yaml-cpp, MAP++, MoorDyn, etc.) are linked. Running an AeroDyn regression test requires either the AeroDyn Driver executable or a full OpenFAST executable with a turbine model configured. The call chain from OpenFAST executable entry → glue code → AeroDyn_Init → BEMT_Init → AirfoilInfo_Init → ... is many layers deeper than ROSCO's single-level DLL call. KGen needs to thread through all of that to capture state at one target call site.

**Implication for VIT:** Extraction cycles will be slower. We need to figure out the right "smallest reproducible runnable unit" — probably the AeroDyn Driver rather than the full OpenFAST executable. `vit extract`'s `run_cmd` configuration was built around ROSCO's simple Python test harness; OpenFAST's test harness is different and may need adaptation. We should also consider whether some functions are easier to extract from the `unit_tests/` directory (OpenFAST has a unit test framework under `unit_tests/`) rather than via full regression tests.

## What is deferred (intentionally not yet done)

Several setup tasks were identified and explicitly deferred until the user directs further:

1. **Docker container mount for the new workspace.** The `vit-dev` container currently mounts only `vit_translation/` → `/workspace/`. The new OpenFAST workspace at `vit_translation_openfast/` is not visible in the container. Options: (a) re-mount container at the workspace root → `/workspace/` so both sibling workspaces are visible as `/workspace/vit_translation/` and `/workspace/vit_translation_openfast/` — requires container recreation and Python 2.7 rebuild (Python tarball is at `/workspace/Python-2.7.18.tar.xz` and CLAUDE.md documents the rebuild commands); (b) create a second container `vit-dev-openfast`; (c) bind-mount the new workspace onto the existing container. This is a blocker before any VIT command can run against OpenFAST source, so it needs to be resolved before the first real work. Worth a separate conversation.

2. **Building vanilla OpenFAST baseline.** We need a working upstream OpenFAST build to establish a three-build chain (upstream ≡ modified ≡ C++) parallel to what we did for ROSCO. The build has dependencies (NetCDF, HDF5, CMake, gfortran) that need to be available in whatever container we use. Similar blocker to (1).

3. **Drafting `openfast/vit.yaml`.** The new workspace needs its own VIT configuration file with paths, kind aliases, assumed-size arrays, DLL persistence resets, etc. The ROSCO `vit.yaml` is a good template but everything will change: paths, target name, invocation semantics, compiler flags, maybe allocation helpers (AeroDyn likely uses `AllocAry` style allocations, not ROSCO's `ParseAry`). Deferred until we're closer to running `vit extract`.

4. **Inspecting `NWTC_Num.f90` for the first-extract candidate.** The "saturate equivalent" for AeroDyn is probably a scalar math helper in NWTC Library — something like an angle wrap, unit conversion, or simple interpolation utility with no state, no derived types, no I/O. A 30-minute read through `NWTC_Num.f90` should identify 2–3 candidates. Deferred until the user directs.

5. **Reading `AeroDyn_Registry.txt` and one generated `AeroDyn_Types.f90`.** This will tell us concretely what Registry-generated types look like, how complex they are, and what `vit analyze-types` will encounter on first run. Important pre-work before the Registry-generated types become a blocker, but deferrable to the first serious translation attempt.

## First concrete milestone (proposed, not committed)

**"Can VIT see OpenFAST at all?"** The first real milestone is a 1-week investigation that gates any actual translation work. Concrete sub-goals:

1. Resolve the Docker container mount situation so VIT commands can reach OpenFAST source.
2. Build vanilla upstream OpenFAST (at least enough to produce a working AeroDyn Driver executable).
3. Pick one pure-functional utility from `NWTC_Num.f90` — the AeroDyn equivalent of `saturate`. Run `vit analyze-types` on the relevant `_Types.f90` (probably `NWTC_Base.f90` or `NWTC_Library_Types.f90`) and see what blockers come up.
4. Try `vit extract` on the chosen function via the AeroDyn Driver. Record everything that breaks.

The goal is *not* a translation. The goal is to surface every VIT generality gap in the first week so we can make an informed plan for the 10-month translation effort. If extract works on day 3, we pick the second-simplest function and try again. If extract fails in ways that reveal a real architectural gap in VIT, we stop and fix the tool before writing any translations.

This mirrors how ROSCO started — `saturate` on February 25 was a three-line min/max clamp that took a full day to get through extract/verify/integrate the first time, and proved the pipeline worked before anything else was attempted.

## Related documentation

- `CLAUDE.md` (VIT repo root) — updated today to reflect ROSCO completion + Phase 11 integrated branch + post-completion verification push. Commit `ae63be2` on `main`.
- `PROJECT_PLAN.md` (VIT repo) — updated today with new Phase 11.5 subsection (post-completion verification push) and Phase 12 status lede. Commit `6a43c2e` on `main`. Phase 12 header now reads: "ROSCO-specific work is complete. Phase 12 is generalization work — validating VIT on a second codebase and production-grade packaging." The AeroDyn effort arguably pulls forward the "test on another codebase" Phase 12 item into a much larger scope than Phase 12 originally contemplated.
- Dev note `202604031714-project-retrospective.md` — the full 38-day arc from `saturate` (February 25) to the 11-mode Scenario 27 stress test (April 3). Essential reading for anyone picking up the project mid-stream because it captures the recurring lesson that the hardest problems are infrastructure, not translations.
- Dev note `202604031900-integrated-branch-cleanup.md` — how the `integrated` branch was produced (three cleanup commits). Useful context for the "what does 'done' look like" question that will come up repeatedly during AeroDyn work.
- Dev note `202604031638-adversarial-verification-audit.md` — the adversarial audit methodology. When AeroDyn verification stabilizes, we should run the same audit on the new pipeline to find the OpenFAST-specific blind spots.
