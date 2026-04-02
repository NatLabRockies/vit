# PROJECT PLAN: Full ROSCO Fortran → C++ Translation

## 1. Vision and Current State

### Vision

Fully translate the ROSCO wind turbine controller from Fortran to C++, producing a drop-in replacement `libdiscon.so` that is pure C++ and produces byte-identical simulation output to the Fortran original. VIT orchestrates the verification and integration. Claude (AI) writes the C++ translations, scaffolded by the `vit translate` command (see Section 4a).

### Current State (2026-04-01)

- **Verified translations**: 45 functions across Functions.f90, Filters.f90, ControllerBlocks.f90, Controllers.f90, and ReadSetParameters.f90 — 42 KGen-verified (20-25 cases each, IDENTICAL) + 3 Stage B simulation-verified (ReadCpFile, ReadControlParameterFileSub, CheckInputs). Phase 10A complete: ReadAvrSWAP and PIDController. Phase 10B Stage B complete: DISCON.IN config loading pipeline. unwrap translated and verified (parser fix for nested DIMENSION parens).
- **Integrated (45 of 45)**: All 45 translations produce byte-identical simulation output across 10 scenarios (692,000 float64 values). Pure C++ — no Fortran bridges. All translations use Fortran source casing.
- **Scenario 3 FITPACK fix**: scipy's FITPACK `bispev` reads an uninitialized stack variable, causing non-deterministic Cp interpolation. Fixed with a stack scrubber + cached RectBivariateSpline in vit_sim.py (dev note `202603261539`).
- **Scenario 8 NotchFilterSlopes fix**: Fortran `**` precedence grouping mismatch in coefficient computation caused 1-ULP errors that accumulated over 14,791 timesteps. Fixed by parenthesizing C++ to match Fortran grouping (dev note `202603261747`).
- **Phase 7B complete**: All 10 functions extracted (20-23 cases each) and translated to C++. Required 10 VIT infrastructure fixes (bridge assumed-size arrays, DISCON.F90 kernel patches, avrSWAP kind mismatch, OPTIONAL bridge handling, view-type stash for callee bridges). See dev notes 202603241408, 202603241444.
- **Pipeline**: End-to-end extract → verify → integrate → rebuild → diff confirmed working. Field-level CSV logging, NaN-safe comparison, `-ffp-contract=off` for bit-identical verification. KGen BIND(C) parser fix and PROCEDURE POINTER parser fix enable extraction with Phase 4C types and DISCON.F90 call sites.
- **Type system**: LocalVariables (168 fields + 5 nested structs) is now BIND(C)-compatible. All 4 blocker fields fixed automatically by `vit analyze-types`. ErrorVariables, FilterParameters, piParams, resParams, rlParams, WE, ObjectInstances, DebugVariables all C-compatible. ControlParameters, PerformanceData, ExtControlType use view structs (Phase 4D).
- **VIT codebase**: ~11,000 lines of core code, 594 unit tests, 17 modules. New non-KGen capabilities: `vit sim-compare` (simulation-level array comparison), `--reverse-copy` and `--auto-allocate` flags on `vit integrate`, ALLOCATABLE field hints in `vit translate` prompts, auto-generated reverse-copy subroutines in view populator modules.
- **ROSCO fork**: Two bug fixes committed for untested Flp_Mode=2 research code (uninitialized K in Controllers.f90:669, uninitialized n in Filters.f90:412). Bug report prepared for NREL.
- **Extraction infrastructure** (Phase 7B): KGen PROCEDURE POINTER parser fix, assumed-size array patching (`kgen.assumed_size_arrays`), DLL SAVE persistence workaround (`kgen.dll_persistence`), `--run-args` CLI option, simplified extraction protocol (no manual sed).
- **Human-in-the-loop protocols**: EXIT_CONFIRM (10) for inferred values, EXIT_MANUAL (11) for code rewrites. Both documented in CLAUDE.md.
- **Generality audits** (2026-03-19, 2026-03-24): VIT confirmed project-agnostic. No ROSCO-specific logic in production code. All project-specific values configurable via vit.yaml, including DLL persistence workaround.

### Total Scope

ROSCO contains ~65 functions and subroutines across 11 modules (~8,600 lines of Fortran), plus type definitions (544 lines), constants, platform abstractions, and the DLL entry point. All of it must become C++.

---

## 2. Functions.f90 — Function-by-Function Assessment

Functions.f90 is the primary target module: 14 utility functions used throughout the controller. This is where VIT has proven itself and where the next wave of translations will come from.

### Already Verified (no ErrVar — clean translations)

| Function | Lines | Description | Status |
|----------|-------|-------------|--------|
| `saturate` | 11 | Min/max clamp (3 scalars → scalar) | Verified |
| `wrap_180` | 12 | Angle wrap to ±180° (scalar → scalar) | Verified |
| `wrap_360` | 12 | Angle wrap to 0–360° (scalar → scalar) | Verified |

All translations start from a clean ROSCO (nothing integrated). Integration happens in phase order.

### Dead Code (no call sites in ROSCO)

| Function | Lines | Description | Notes |
|----------|-------|-------------|-------|
| `CPfunction` | 13 | Parameterized Cp(λ) (fixed `CP(4)` array + scalar → scalar) | Defined in Functions.f90 but never called. Cannot be extracted or verified — no runtime state to capture. Skip until a call site exists. |

### Minor VIT Work Required

| Function | Lines | Description | What VIT Needs |
|----------|-------|-------------|---------------|
| `matinv3` | 22 | 3×3 matrix inverse (explicit `A(3,3)` → explicit `B(3,3)`) | Fixed-size 2D array args and array-valued return. No derived types, pure math. VIT needs explicit-dimension array support in interface gen |
| `identity` | 16 | n×n identity matrix (scalar `n` → `A(n,n)`) | Runtime-dimensioned array return. VIT needs support for result arrays sized by input args |
| `ColemanTransform` | 18 | Coleman d-q transform (SUBROUTINE: fixed `rootMOOP(3)` + scalars → INTENT(OUT) scalars) | First SUBROUTINE target. VIT has subroutine codepath in interface_gen.py but it's untested in the full pipeline. Fixed-size array input is straightforward |
| `ColemanTransformInverse` | 19 | Inverse Coleman (SUBROUTINE: scalars → INTENT(OUT) fixed `PitComIPC(3)`) | SUBROUTINE with INTENT(OUT) fixed-size array. Same requirements as ColemanTransform plus output array handling |

### Major VIT Work Required

| Function | Lines | Description | What VIT Needs |
|----------|-------|-------------|---------------|
| `ratelimit` | 37 | Rate limiter with state (8 args including `rlP` TYPE(rlParams) INOUT + OPTIONAL arg → scalar) | Load-bearing derived type: writes `rlP%LastSignal(inst)`. C++ must access fields. Also has OPTIONAL argument. Requires derived type struct mapping and OPTIONAL support |
| `interp1d` | 58 | 1D linear interpolation (2 arrays + scalar + ErrVar → scalar) | **Verified (Phase 5A)**: arrays + ErrorVariables struct pointer. Error checks + interpolation + error message chaining |
| `sigma` | 34 | Sigmoid/cubic ramp (5 scalars + ErrVar → scalar) | **Verified (Phase 5A)**: first ErrorVariables function. Scalars + struct pointer, error message chaining |
| `unwrap` | 40 | Phase unwrapping (assumed-shape `x(:)` + ErrVar → array `DIMENSION(SIZE(x))`) | **Verified (Phase 10)**: KGen-verified 25/25 (Scenario 10). Required parser fix for nested DIMENSION parens. Inline implementations in variablespeedcontrol.cpp and readcontrolparameterfilesub.cpp reverted to shared `unwrap_c()` calls |
| `interp2d` | 172 | 2D bilinear interpolation (3 arrays including `zData(:,:)` + 2 scalars + ErrVar → scalar) | **Verified (Phase 8C)**: 2D assumed-shape arrays, column-major Z macro, bilinear interpolation |
| `AeroDynTorque` | 39 | Aerodynamic torque (2 scalars + 4 derived types → scalar) | **Verified (Phase 8C)**: first PerformanceData view type function, calls `interp2d` |

---

## 3. Full ROSCO Module Map

### Module Inventory

| Module | File | Lines | Functions/Subs | Translation Approach |
|--------|------|-------|----------------|---------------------|
| Constants | `Constants.f90` | 78 | 0 (parameters only) | C++ header with `constexpr` and `typedef` |
| ROSCO_Types | `ROSCO_Types.f90` | 544 | 0 (types only) | C++ structs (the keystone — every module depends on it) |
| Functions | `Functions.f90` | 528 | 14 | VIT pipeline (detailed above) |
| Filters | `Filters.f90` | 438 | 7 | VIT pipeline (stateful filters with FilterParameters) |
| Controllers | `Controllers.f90` | 1,228 | 14 | VIT pipeline (control loops with LocalVariables, ControlParameters) |
| ControllerBlocks | `ControllerBlocks.f90` | 820 | 8 | VIT pipeline (state machines, Kalman filter) |
| ReadSetParameters | `ReadSetParameters.f90` | 1,804 | 5 | Direct C++ rewrite (file parsing) |
| ROSCO_Helpers | `ROSCO_Helpers.f90` | 1,724 | ~20 | Direct C++ rewrite (string/file utilities) |
| ROSCO_IO | `ROSCO_IO.f90` | 1,199 | 3 | Direct C++ rewrite (binary I/O, debug output) |
| ExtControl | `ExtControl.f90` | 137 | 2 | Direct C++ rewrite (DLL loading) |
| ZeroMQInterface | `ZeroMQInterface.f90` | 84 | 1 | Direct C++ rewrite (ZMQ bindings) |
| SysSubs | `SysFiles/Sys*.f90` | ~100 each | 3 per variant | Direct C++ rewrite (`dlopen`/`dlsym`) |
| DISCON | `DISCON.F90` | 162 | 1 | Last to translate (entry point orchestrator) |

### Filters.f90 Detail

| Function | Lines | Description | Key Challenge |
|----------|-------|-------------|---------------|
| `LPFilter` | ~70 | First-order low-pass filter | FilterParameters state with instance indexing (`inst`) |
| `SecLPFilter` | ~50 | Second-order low-pass | Same state pattern + DampingRatio arg |
| `SecLPFilter_Vel` | ~40 | Second-order low-pass for velocity | Same state pattern |
| `HPFilter` | ~60 | High-pass filter | Same state pattern |
| `NotchFilterSlopes` | ~50 | Notch filter with slopes | Same state pattern + 2 extra params |
| `NotchFilter` | ~5 | Notch wrapper | Calls NotchFilterSlopes |
| `PreFilterMeasuredSignals` | ~110 | Cascade of filters on sensor inputs | Calls multiple filters; uses ControlParameters, LocalVariables, DebugVariables, ObjectInstances, ErrorVariables |

All filters use instance-based state: `FilterParameters` contains arrays of size 1024, and the `inst` argument selects which slot to use. This is the dominant Fortran pattern for reentrant state in ROSCO.

### Controllers.f90 Detail

| Function/Sub | Lines | Description | Key Challenge |
|-------------|-------|-------------|---------------|
| `PIController` | ~80 | Generic PI controller | piParams state, rate limiting, integrator saturation |
| `PIDController` | ~60 | Generic PID controller | Derivative filter, piParams state |
| `PIIController` | ~50 | Double-integrator PI | For torque ramp transitions |
| `ResController` | ~50 | Resonance controller | resParams state, pole-zero pairs |
| `PitchControl` | ~185 | Blade pitch control loop | Calls PIController, ratelimit; uses LocalVariables, ControlParameters |
| `VariableSpeedControl` | ~155 | Generator torque control | Calls PIIController, ResController; region-based state machine |
| `YawRateControl` | ~120 | Nacelle yaw control | Calls PIController |
| `IPC` | ~100 | Individual Pitch Control | Coleman transform, per-blade PI gains |
| `ForeAftDamping` | ~20 | Tower damping | HPFilter + PI |
| `FloatingFeedback` | ~30 | Platform feedback | HPFilter |
| `FlapControl` | ~70 | Blade flap actuator | PI control loop |
| `ActiveWakeControl` | ~170 | Wake steering | Coleman transform, complex math |
| `CableControl` | ~80 | Cable actuators | Ramp/rate limit |
| `StructuralControl` | ~60 | Structural damping | PI control loop |

### ControllerBlocks.f90 Detail

| Subroutine | Lines | Description | Key Challenge |
|-----------|-------|-------------|---------------|
| `WindSpeedEstimator` | ~225 | 3-state Kalman filter | Matrix operations (3×3), WE derived type state |
| `StateMachine` | ~70 | Region state machine | Multi-branch conditionals, mode selection |
| `ComputeVariablesSetpoints` | ~110 | Speed setpoints from lookup tables | interp1d calls, derived type access |
| `Startup` | ~90 | Startup procedure | Multi-stage state machine |
| `Shutdown` | ~125 | Shutdown procedure | Multi-stage state machine |
| `PowerControlSetpoints` | ~60 | Rotor speed setpoint selection | Conditional logic |
| `SetpointSmoother` | ~55 | Low-pass on setpoints | Filter call |
| `RefSpeedExclusion` | ~60 | Tower frequency avoidance | Zone exclusion logic |

### I/O Modules Detail

| Module | Key Functions | Lines | Translation Notes |
|--------|--------------|-------|-------------------|
| ReadSetParameters | `ReadControlParameterFileSub` | ~560 | Monolithic parameter file parser with ~100 parameter cases. C++ equivalent: structured parser or key-value config library |
| ReadSetParameters | `ReadAvrSWAP` | ~115 | Extract values from avrSWAP array (Bladed DLL convention). Direct index-based access — trivial in C++ |
| ReadSetParameters | `ReadCpFile` | ~85 | CSV reader for aerodynamic tables |
| ROSCO_Helpers | Various | ~1,724 total | String parsing (`GetWords`, `Conv2UC`), file I/O (`ParseDbAry`, `FindLine`), path handling (`GetPath`, `PathIsRelative`). Standard C++ equivalents exist for all |
| ROSCO_IO | `WriteRestartFile` | ~340 | Binary serialization of all state. Must be byte-compatible during transition |
| ROSCO_IO | `ReadRestartFile` | ~350 | Binary deserialization |
| ROSCO_IO | `Debug` | ~500 | Channel-based text output for debugging |

### Dependency Graph

```
Layer 0 (Foundation):    Constants, SysSubs
Layer 1 (Types):         ROSCO_Types
Layer 2 (Utilities):     Functions, ROSCO_Helpers
Layer 3 (Filters):       Filters
Layer 4 (Blocks):        ControllerBlocks, ReadSetParameters
Layer 5 (Controllers):   Controllers, ExtControl, ROSCO_IO
Layer 6 (Entry):         DISCON
```

Translation must proceed bottom-up through this graph. A function can only be translated after its dependencies are either already translated or have stable Fortran↔C++ interop shims.

---

## 4. Derived Type Translation Strategy

Every ROSCO module depends on ROSCO_Types. The types are the keystone of the entire translation effort. Deep investigation (2026-03-17) determined that **all 12 types can become C structs** — no opaque pointers, no accessor APIs, no deferred work. Phase 4 solves the entire type system in one shot.

### Key Finding: Field Extraction Does Not Scale

Field extraction — passing individual derived type fields as separate C arguments — does not scale beyond Functions.f90. Controllers access 30–70 fields per function. This would produce unmaintainable signatures. Instead, all types become C structs passed by pointer.

### Type-by-Type Strategy

**Already BIND(C) compatible — no changes needed (7 types):**

| Type | Fields | Notes |
|------|--------|-------|
| `FilterParameters` | 27 | All `REAL(DbKi), DIMENSION(1024)`. ~221 KB. |
| `piParams` | 5 | All `REAL(DbKi), DIMENSION(1024)`. |
| `resParams` | 4 | All `REAL(DbKi), DIMENSION(1024)`. |
| `rlParams` | 1 | Single `REAL(DbKi), DIMENSION(1024)`. |
| `WE` | 7 | 4 scalars + `DIMENSION(3,3)` + 2× `DIMENSION(3,1)`. VIT auto-generates transposition at BIND(C) boundary so C++ always uses row-major indexing. |
| `ObjectInstances` | 9 | All `INTEGER(IntKi)`. |
| `DebugVariables` | 22 | All `REAL(DbKi)` scalars. |

These become C structs with identical memory layout. Zero-copy pass-by-pointer.

**LocalVariables — 4 surgical fixes applied, now fully BIND(C) (168 fields):** ✓

LocalVariables contains ~140 REAL(DbKi) scalars, fixed-size arrays, and 5 nested BIND(C)-compatible types. Four fields blocked compatibility — all fixed in Phase 4C:

| Field | Type | Fix Applied | Impact |
|-------|------|-------------|--------|
| `restart` | `LOGICAL` | `--fix logical` → INTEGER(C_INT). 58 references rewritten: .True.→1, .False.→0, IF→/=0, function args→(/=0 wrap), binary I/O unchanged. | 5 files modified |
| `WriteThisStep` | `LOGICAL` | `--fix logical` → INTEGER(C_INT). 2 references (binary I/O only, unchanged). | 1 file modified |
| `AWC_complexangle(3)` | `COMPLEX(DbKi)` | `--fix complex` → split to `_re(3)` + `_im(3)` REAL(DbKi). Euler formula EXP→COS/SIN provided via `--rewrite` (EXIT_MANUAL protocol). | 3 files modified |
| `ACC_INFILE` | `CHARACTER, DIMENSION(:), ALLOCATABLE` | `--fix character` (array form) → CHARACTER :: ACC_INFILE(1024). Size confirmed via EXIT_CONFIRM protocol. | 3 files modified |

Additionally: `AlreadyInitialized = 0` default initializer removed by `--fix bind_c`, and BIND(C) attribute added to LocalVariables + all 5 nested types (WE, FilterParameters, piParams, resParams, rlParams).

The entire 168-field type (including nested structs) is now a plain C struct, ~305 KB, passed by pointer. Simulation output byte-identical after all fixes.

**ControlParameters — pointer+size view during transition, C++ ownership in final state (213 fields):**

Investigation confirmed:
1. **Truly read-only**: Every access in Controllers, Filters, and ControllerBlocks is read-only. `INTENT(INOUT)` in signatures is legacy.
2. **All ALLOCATABLE arrays allocated once at init**: Sizes from config file scalars, never reallocated.

**Strategy — two-phase ownership model:**
- **During transition** (mixed Fortran/C++): C++ receives a pointer+size view struct. A thin Fortran BIND(C) routine populates it once at init by copying scalar values and taking `C_LOC` of each ALLOCATABLE array. C++ reads through pointers into Fortran-owned memory. No ROSCO source modifications, no arbitrary max bounds.
- **Final state** (pure C++): C++ owns the data with `std::vector<double>`. The translated ReadSetParameters allocates naturally from the config file.
- **VIT tracks ownership state** of each type — "view" (Fortran-owned, C++ reads via pointers) vs. "owned" (C++ allocates and manages). When all Fortran consumers of a type are translated, VIT flags that ownership can transfer.

The 135 scalar fields and ~55 INTEGER fields are copied directly into the view struct. The 76 ALLOCATABLE arrays become `double*` + `int` size pairs. The 4 CHARACTER fields become `char[1024]`.

**PerformanceData — same pointer+size pattern (5 fields):**

All fields are ALLOCATABLE lookup tables allocated once from `Cp_Ct_Cq.NREL5MW.txt`:
- `TSR_vec(:)` — typically 26 entries
- `Beta_vec(:)` — typically 36 entries
- `Cp_mat(:,:)`, `Ct_mat(:,:)`, `Cq_mat(:,:)` — typically 26×36

Never modified after init. Same pointer+size view during transition, `std::vector` in final state.

**ErrorVariables — BIND(C) struct passed by pointer (same as all other types):**

`ErrVar` is used for error reporting: functions set `aviFAIL = -1` and write `ErrMsg` on validation failures, callers check `aviFAIL < 0` and propagate. It never affects computed control outputs.

Strategy: make ErrorVariables a BIND(C) struct, same treatment as every other type. One fix needed:
- `ErrMsg`: Replace `CHARACTER(:), ALLOCATABLE` with `CHARACTER(1024)`. The DLL interface already uses a 1024-char buffer. No observed error message exceeds ~500 chars.
- `aviFAIL` and `ErrStat` are already `INTEGER(C_INT)` — no changes needed.
- `size_avcMSG` is `INTEGER(IntKi)` — no changes needed.

After this fix, the entire type is BIND(C)-compatible. C++ receives `ErrorVariables*` and reads/writes fields directly — `errVar->aviFAIL`, `errVar->ErrMsg`, etc. No passthrough, no decomposed args, no special cases.

**Remaining types (handled in Phase 10):**

| Type | Fields | Strategy |
|------|--------|----------|
| `ExtDLL_Type` | 4 | Already uses `C_PTR`, `C_FUNPTR`, `CHARACTER(1024)` — excellent BIND(C) candidate. |
| `ExtControlType` | 1 | Single ALLOCATABLE `avrSWAP` array. Replace with fixed-size or pointer+size. |

### KGen Strategy

KGen workarounds are handled case-by-case. Every new Fortran pattern (subroutines, 2D arrays, stateful functions) may surface new KGen issues. Strategy: favor workarounds over KGen patches until the system is stable with rigorous regression tests. Once the full pipeline is running, selectively replace brittle workarounds with targeted KGen patches. KGen has been forked and is open to modification when justified.

### Timeline

- **Phase 4**: All type changes applied (including ErrorVariables). C++ struct headers generated. BIND(C) attributes added to Fortran types. Memory layout validated. Pointer+size view structs for ALLOCATABLE types.
- **Phases 5–8**: All functions receive full structs by pointer. C++ accesses fields naturally.
- **Phase 9**: Ownership transfer — types move from Fortran-owned (view) to C++ owned (`std::vector`). C++ becomes source of truth for type definitions.

---

## 4a. Translation Workflow

### `vit translate` Command

VIT provides a `vit translate` command that scaffolds a C++ translation file from a parsed Fortran function signature. The command produces two outputs:

1. **C++ scaffold file** (written to disk): metadata header comment block + correct C++ function signature + placeholder body. This is an **implementation-only** file — the `extern "C"` wrapper is added at integration time by `_generate_standalone_cpp()` in `integrate.py`. This matches the format of existing hand-written translations.
2. **Translation prompt** (printed to stdout): the original Fortran source (verbatim), the generated scaffold, and the contents of `TRANSLATION_TIPS.md` if it exists.

The scaffold signature is generated by `build_c_params()` in `interface_gen.py`, which produces the correct C++ parameter types (size params for assumed-shape arrays, pointer params for structs, derived type handling per the type registry).

Claude fills in the algorithm body. VIT then verifies the translation against runtime state (extract → verify) and integrates it (integrate → rebuild → diff).

### Translation Storage

Translations live in the target project repo (`ROSCO/translations/`), organized by module. File extension distinguishes type (`.cpp` for function translations, `.h` for type/constant definitions).

```
ROSCO/translations/
  Functions/
    saturate.cpp
    wrap_180.cpp
    interp1d.cpp
    sigma.cpp
    ...
  Filters/
    LPFilter.cpp
    SecLPFilter.cpp
    ...
  Controllers/
    PIController.cpp
    PitchControl.cpp
    ...
  ControllerBlocks/
    WindSpeedEstimator.cpp
    ...
  ROSCO_Types/
    rosco_types.h
  Constants/
    rosco_constants.h
```

Translations are ROSCO-specific artifacts — they belong with the target codebase, not with VIT (which is a general-purpose tool).

### Translation Metadata

Each translation file carries metadata (in a header comment block):
- Source Fortran file and function name
- Enclosing module name (if any)
- Fortran function signature at time of translation
- MD5 hash of original Fortran source (to detect upstream changes)
- VIT version used for scaffolding
- Verification status (unverified/verified)
- Generation timestamp (UTC)

During verification and integration, VIT reads translations from `translations/<Module>/` and copies them into the build area as needed.

---

## 5. VIT Enhancement Roadmap

Each phase unlocks new translation targets. Phases are ordered by dependency — each builds on the capabilities added by previous phases.

### Phase 0: Translation Infrastructure ✓
**Target: `vit translate` command and module-organized translation storage**

Build the translation workflow tooling described in Section 4a before any new translations are written. This ensures every translation from Phase 1 onward follows the correct convention from day one.

- [x] Create module-organized `ROSCO/translations/` directory structure
- [x] Move existing translations into `ROSCO/translations/Functions/`
- [x] Implement `vit translate` command (scaffold + prompt generation)
- [x] Add `vit translate` to CLI (`cli.py`)
- [x] Update existing translations with metadata headers (retroactive)
- [x] Unit tests for `vit translate` scaffolding
- [x] Verify existing pipeline still works with translations in new locations

### Phase 1: Low-Hanging Fruit ✓
**Target: 3/14 Functions.f90**

Starting from a clean ROSCO fork with no integrations and no golden fixtures.

- [x] Build clean Fortran ROSCO and capture baseline simulation output
- [x] Extract kernels for all 3 functions (saturate, wrap_180, wrap_360)
- [x] Verify `saturate`, `wrap_180`, `wrap_360` against extracted kernels (40/40 each)
- [x] Integrate `saturate` → rebuild → diff against baseline
- [x] Integrate `wrap_180` → rebuild → diff against baseline
- [x] Integrate `wrap_360` → rebuild → diff against baseline
- [x] Run full verification regression (all 3 functions pass post-integration)
- [x] Update `vit.yaml` translations section

`CPfunction` is dead code (no call sites in ROSCO) — skipped.

**Completed 2026-03-18.** Created `Examples/vit_sim.py` — a VIT-specific multi-scenario simulation that exercises all controller code paths including Y_ControlMode=2 (for wrap_360, which is never called by the standard `04_simple_sim.py`). The `vit.yaml` run_cmd now uses `vit_sim.py`. Discovered that `translations/Functions/*.cpp` had been overwritten by scaffolds from `vit translate`; restored from canonical copies in `vit_translation/translations/`.

### Phase 2: Fixed-Size Arrays + Subroutines ✓
**Target: 6/14 Functions.f90** (matinv3 deferred — dead code)

- [x] Explicit-dimension array support — fixed-size arrays (`rootMOOP(3)`, `PitComIPC(3)`) pass as pointers without spurious size params. Already worked correctly.
- [x] Subroutine pipeline validation — fixed scalar INTENT(OUT) pointer bug in `build_c_params()`, fixed INTEGER kind conversion (`INT()` not `INTEGER()`). ColemanTransform was the first real SUBROUTINE through the full pipeline.
- [x] Array-valued function results — parser extended with `result_is_array`/`result_dims`; interface gen transforms array-returning FUNCTION to void C SUBROUTINE with output pointer; Fortran wrapper remains FUNCTION but CALLs the C subroutine internally.
- [x] Translate + verify + integrate `ColemanTransform` (40/40)
- [x] Translate + verify + integrate `ColemanTransformInverse` (40/40)
- [x] Translate + verify + integrate `identity` (40/40)
- [x] Run full verification regression (all 6 functions pass 40/40)

`matinv3` is dead code (zero call sites in ROSCO) — deferred like `CPfunction`.

**Completed 2026-03-18.** Used `vit translate` to generate scaffolds with correct signatures. Fixed two bugs discovered during execution: (1) `build_c_params()` passed scalar INTENT(OUT) subroutine args by value instead of pointer, (2) Fortran wrapper generated `INTEGER(x, kind)` instead of `INT(x, kind)` for integer kind conversion. Both Coleman functions extracted from IPC subroutine (scenario 2), identity extracted from EKF (scenario 1).

**New functions integrated**: `ColemanTransform`, `ColemanTransformInverse`, `identity`

### Phase 3: Load-Bearing Derived Types ✓
**Target: 8/14 Functions.f90**

- [x] Scalar INOUT in functions — removed `sig.is_subroutine` guard; scalar INTENT(OUT/INOUT) args are always pointers regardless of function vs subroutine. Required for `ratelimit`'s `inst` parameter.
- [x] Derived type auto-discovery and registry — `build_derived_type_registry()` scans function args for TYPE(...), searches source directory for definitions via `find_and_parse_derived_type()`. Registry threaded through entire pipeline (cli → interface_gen → kernel_verify → integrate → translate).
- [x] BIND(C) struct generation — `generate_c_struct_header()` emits C typedef from parsed TYPE block. C-compatible derived types become struct pointers (`rlparams_t*` in C, `TYPE(C_PTR) VALUE` in interface, `C_LOC()` in wrapper with `TARGET` attribute).
- [x] OPTIONAL argument support — OPTIONAL args split into flag+value pair (`int has_ResetValue`, `double ResetValue`). Wrapper generates `PRESENT()` preamble with local variables.
- [x] LOGICAL type conversion — explicit `LOGICAL(x, C_BOOL)` in wrapper calls for safe kind conversion.
- [x] Translate + verify + integrate `ratelimit` (40/40)
- [x] Run full clean build test — all 7 functions extracted from clean source, verified 40/40, integrated, rebuilt, byte-identical simulation output.

**Completed 2026-03-18.** Three new VIT capabilities: derived type struct passing, OPTIONAL argument handling, scalar INOUT in functions. Extraction requires clean (pre-integration) source — golden kernel fixtures persist for fast regression. `ratelimit` is the first function with a derived type (`TYPE(rlParams)`), OPTIONAL parameter, LOGICAL input, and scalar INOUT in a FUNCTION.

**New function integrated**: `ratelimit`

### Phase 4: Complete Type System C Structs
**Target: C++ struct definitions for ALL types (including ErrorVariables), unblocking Phases 5–8**

Deep investigation (2026-03-17) found that **every ROSCO type can become a C struct** — no opaque pointers or accessor APIs needed. Split into 4 sub-phases (4A–4D) that each deliver concrete value, build VIT's `analyze-types` tooling incrementally, and respect downstream dependencies.

**Design constraint:** These C structs become the permanent C++ types in the final all-C++ codebase. Design them as final types, not temporary interop scaffolding.

**Key principle:** All Fortran source modifications are automated by VIT (`vit analyze-types --fix`), not done manually. The fix patterns (LOGICAL→INTEGER, COMPLEX→split, CHARACTER ALLOCATABLE→fixed) are general — they recur across scientific Fortran codebases and must be reproducible.

#### Phase 4A: ErrorVariables + `analyze-types` Foundation ✓

- [x] `vit analyze-types` CLI command — scans source dir, parses all TYPE definitions, reports per-type BIND(C) compatibility with field-level blockers (ALLOCATABLE, POINTER, COMPLEX, LOGICAL, nested non-compatible TYPE). ROSCO: 13 types found, 7 compatible, 6 incompatible.
- [x] Extend `generate_c_struct_header()` to handle `CHARACTER(N)` fields → `char field[N]` in C struct
- [x] `vit analyze-types --fix character` — automated rewrite: `CHARACTER(:), ALLOCATABLE` → `CHARACTER(N)` in type definitions, removes associated ALLOCATE/DEALLOCATE calls across all source files. Configurable N (default 1024).
- [x] Apply to ErrorVariables: `CHARACTER(:), ALLOCATABLE :: ErrMsg` → `CHARACTER(1024) :: ErrMsg`. ALLOCATE already commented out in ReadSetParameters.f90.
- [x] Generate ErrorVariables C struct header — `char ErrMsg[1024]` confirmed
- [x] Unit tests: 30 tests in test_analyze_types.py + 23 tests in test_build_fixer.py (333 total, zero regressions)
- [x] Verify simulation still produces identical output after ErrorVariables fix
- [x] **Build-fix loop** (`build_fixer.py`) — not in original plan, added after discovering that `TRANSFER(C_NULL_CHAR, avcMSG)` in ExtControl.f90 fails when `LEN(ErrVar%ErrMsg)` becomes compile-time constant. Uses compiler as oracle: build → parse gfortran errors → apply fixes from extensible registry → retry. First pattern: `transfer_shape_mismatch` (replaces `TRANSFER(C_NULL_CHAR, var)` with `var = C_NULL_CHAR` scalar broadcast).
- [x] **Kind alias merging fix** — `kind_aliases or DEFAULT` → `{**DEFAULT, **kind_aliases}` so project aliases override but defaults (including C_INT, C_DOUBLE) remain as fallback.
- [x] **Assumption confirmation protocol** (`confirm.py`) — general mechanism for VIT to signal inferred values that need human confirmation. Exit code 10 + `[VIT:CONFIRM]` blocks. Claude must never auto-confirm. CHARACTER length inference from ALLOCATE calls is the first use case. Also fixed silent type fallbacks in translate.py and interface_gen.py (now raise instead of defaulting to `double`/`REAL(8)`).
- [x] **Prevent status downgrade** — `record_translation()` refuses to overwrite "integrated" with "verified".

**Types usable after:** ErrorVariables
**Unblocks:** Phase 5 first batch (interp1d, sigma, unwrap, interp2d — these need only ErrorVariables + primitives/arrays)

#### Phase 4B: Already-Compatible Structs + Nested Type Recursion

- [x] Extend `is_c_compatible` with recursive registry-based checking — `is_c_compatible_recursive(registry)` method on DerivedTypeInfo; `TYPE(WE)` field is compatible if WE itself is compatible in the registry
- [x] Extend `generate_c_struct_header()` for nested struct fields (`TYPE(WE) :: WE` → `we_t WE;` with `#include` dependency). Supports `registry` param for nested type lookup, `emit_includes` for separate-file vs inline mode.
- [x] Extend `generate_c_struct_header()` for multi-dimensional fixed arrays (`DIMENSION(3,3)` → `double P[3][3]`, not `double P[3,3]`). Added `_fortran_dims_to_c()` helper. Column-major note emitted for multi-dim arrays.
- [x] Header dependency ordering — `order_types_by_dependency()` topological sort; threaded through integrate.py and translate.py
- [x] All 7 types verified structurally C-compatible by parser auto-discovery (WE, FilterParameters, ObjectInstances, ControlParameters, PerformanceData, PitManVar, DebugVariables). No ROSCO source changes needed.
- [x] WE `DIMENSION(3,1)` kept as `double[3][1]` — exact layout match, documents 3×1 column vector semantics
- [x] Registry threaded through all callers: `classify_field_blockers()`, `analyze_all_types()`, `kernel_verify.py`, `integrate.py`, `translate.py`
- [x] 35 new tests (399 total): 2D arrays, recursive compat, nested struct headers, dependency ordering, all 7 type headers

**Types usable after:** FilterParameters, piParams, resParams, WE, ObjectInstances, DebugVariables
**Unblocks:** Phase 6 Filters (FilterParameters + ObjectInstances + ErrorVariables all ready)

#### Phase 4C: LocalVariables Automated Fixes ✓

- [x] `vit analyze-types --fix logical` — automated rewrite of LOGICAL fields to INTEGER(C_INT): changes type definition AND updates all `%fieldname` references across source files. Handles all Fortran boolean idioms: `IF (x)` → `IF (x /= 0)`, `.NOT. x` → `(x == 0)`, `.TRUE.` → `1`, `.FALSE.` → `0`. Applied to `restart` (58 refs across 5 files) and `WriteThisStep` (2 refs, I/O only). LOGICAL type mismatch fix pattern added to build_fixer as safety net (not needed in practice — regex pass handles all cases).
- [x] `vit analyze-types --fix complex` — scan-first architecture: classifies all references as auto or manual, applies nothing until all manual lines have `--rewrite` entries. Auto-rewrites: REAL()→_re, AIMAG()→_im, zero init→both, I/O→split, fallback→_re. Lines with complex intrinsics (EXP, CMPLX, CONJG, etc.) exit 11 (EXIT_MANUAL) with `[VIT:MANUAL]` block including `cli_override`. Applied to `AWC_complexangle` with Euler COS/SIN decomposition provided via `--rewrite`.
- [x] `--fix character` extended to handle CHARACTER array form — `CHARACTER, DIMENSION(:), ALLOCATABLE` detected by requiring `DIMENSION` keyword, rewritten to `CHARACTER :: field(N)`. Removed separate `--fix character_array` (was redundant). Applied to `ACC_INFILE` with size 1024 confirmed via EXIT_CONFIRM.
- [x] `vit analyze-types --fix bind_c` — adds `BIND(C)` attribute to type definitions, removes default initializers (BIND(C) forbids them; SAVE'd module variables zero-initialize at runtime). Also parses `= value` in field declarations and reports as `default_initializer` blocker.
- [x] Applied all fixes to LocalVariables + 5 nested types (WE, FilterParameters, piParams, resParams, rlParams). `AlreadyInitialized = 0` default removed.
- [x] Verify simulation still produces identical output after all LocalVariables fixes — confirmed byte-identical across 3 scenarios.
- [x] **EXIT_MANUAL protocol** (not in original plan): Added exit code 11 with `[VIT:MANUAL]` blocks for lines VIT cannot auto-rewrite. Parallels EXIT_CONFIRM (10). `ManualEdit` dataclass with `cli_override` field. CLAUDE.md instructions added. Designed during post-implementation review when generalizing `--fix complex` revealed that ROSCO-specific Euler regex was not acceptable for a general-purpose tool.
- [x] **Generality audit** (not in original plan): Systematic review of all VIT source confirmed no ROSCO-specific logic in production code. `--fix complex` was the only instance; fixed by generalization + EXIT_MANUAL.

**Completed 2026-03-19.** 446 unit tests (30 new). Memory layout test deferred — not blocking since the Fortran BIND(C) attribute guarantees C-compatible layout and the simulation diff proves correctness end-to-end.

**Types usable after:** LocalVariables (168 fields + 5 nested structs)
**Unblocks:** Phase 7 Controllers (helper functions immediately; main subroutines after Phase 4D), Phase 8 ControllerBlocks (same split)

#### Phase 4D: Pointer+Size View Pattern for ALLOCATABLE Types

**Key finding (2026-03-19):** Feasibility analysis of all ALLOCATABLE fields in ControlParameters (61 fields) and PerformanceData (5 fields) confirmed that ~90% of size sources are fully automatable via source scanning — the same pattern VIT uses for CHARACTER length inference. See dev note `202603192051-allocatable-size-mapping-feasibility.md` for the complete field-by-field classification (Types A–E) and automation strategy.

**Size source patterns discovered:**
- **Type A (~56%):** Size is a `CntrPar%` field, passed to `ParseAry` → fully automatable
- **Type B (~12%):** Literal constant → trivial
- **Type C (2 fields):** Local variable from loop counting → EXIT_CONFIRM (present expression, ask human)
- **Type D (2 fields):** `SIZE()` of another field → automatable
- **Type E (5 fields, PerformanceData):** Array index into size field (`PerfTableSize(1)`) → automatable
- **OL_ slices (~11 fields):** POINTER-associated, not ALLOCATABLE — need special handling

**Recommended approach:** Implement `vit analyze-types --scan-allocatable` (or integrate into existing `analyze-types`) to automate the mapping. Scan ParseAry calls and explicit ALLOCATE statements, classify size expressions, generate the mapping table consumed by `generate_view_struct_header()`. Use EXIT_CONFIRM for Type C (unresolvable runtime expressions). This eliminates the manual "read ReadSetParameters deeply" step — VIT does the analysis automatically.

- [x] `vit analyze-types --scan-allocatable` — configurable allocation helper scanner (ParseAry defined in `vit.yaml`, not hardcoded). Scans ParseAry/ALLOCATE calls, classifies by type (A–E). All 68 ALLOCATABLE fields in ControlParameters included in view struct (scanner is informational, not gating).
- [x] New type classification: `types:` section in `vit.yaml` maps type names to strategies ("bind_c" or "view"). `apply_type_strategies()` marks registry entries. Pre-computed field mappings stored on DerivedTypeInfo.
- [x] `generate_view_struct_header()` — all ALLOCATABLE fields emitted as pointer+size pairs (1D: `type* field; int n_field;`, 2D: `type* field; int n_field_rows; int n_field_cols;`). Scalars and CHARACTER copied directly.
- [x] `generate_view_populator()` — Fortran module with BIND(C) mirror type + populator subroutine. Uses C_LOC + SIZE at runtime. ALLOCATED checks for safety. 941 lines for ControlParameters, compiles with gfortran.
- [x] Apply to ControlParameters (259 view struct fields: ~150 scalars, 68 ALLOCATABLE pointer+size pairs, CHARACTER fields)
- [x] Apply to PerformanceData (5 ALLOCATABLE fields: 2×1D, 3×2D — all Type E)
- [x] Apply to ExtControlType (1 ALLOCATABLE field — avrSWAP, Type C)
- [x] Integration generates populator .f90 + adds to CMake SOURCES. Wrapper USEs populator module, declares local view struct, calls populator before C function.
- [x] Kernel verification: view struct headers + populator in kernel. Makefile dependencies for compilation ordering.
- [x] Recursive nested type registry — `build_derived_type_registry()` discovers all transitive TYPE(X) field dependencies.
- [x] Kernel callee bridges — auto-generated BIND(C) bridges for translated callees (e.g., PIController) so C++ can call them in kernel context.
- [x] ForeAftDamping verified 40/40 — first ControlParameters view type function. Byte-identical simulation output.
- [x] 501 tests (50 view type tests covering scanner, struct gen, populator, registry, interface gen, callee detection).

**Types usable after:** ControlParameters, PerformanceData, ExtControlType
**Unblocks:** Phase 5 AeroDynTorque (all 4 type args ready), Phases 7-8 full ControlParameters field access. **Now unblocked.**

#### Phase 4 Dependency Graph and Execution Order

```
4A (ErrorVariables) ✅ ──→ Phase 5A (sigma, interp1d) ✅ ──→ Phase 5B (interp2d, AeroDynTorque) ✅
4B (Simple structs)  ✅ ──→ Phase 6A (LPFilter, HPFilter, SecLPFilter) ✅
                        ──→ Phase 6B (NotchFilter, NotchFilterSlopes, SecLPFilter_Vel) ✅
4C (LocalVariables)  ✅ ──→ Phase 7A Controllers (PI/PID/PII/Res) ✅
4D (View structs)    ✅ ──→ Phase 7B Controllers (main subroutines) ✅
                        ──→ Phase 8 ControllerBlocks ✅
                        ──→ PreFilterMeasuredSignals (Phase 10)
```

4A, 4B, 4C all complete. 4D (pointer+size views for ALLOCATABLE types) is the remaining type system work.

**Key insight from Phase 4C:** Controllers.f90 splits into two tiers by type dependency:
- **Tier 1 (4 helper functions, ready NOW):** PIController, PIDController, PIIController, ResController — take only LocalVariables (piParams/resParams nested), ObjectInstances. No ControlParameters.
- **Tier 2 (10 main subroutines, need Phase 4D):** PitchControl, VariableSpeedControl, YawRateControl, IPC, ForeAftDamping, FloatingFeedback, FlapControl, ActiveWakeControl, CableControl, StructuralControl — all take ControlParameters.

Recommended execution: **7A** (4 helper controllers as proof-of-concept) → **4D** → **7B + 8 + 5B** (everything else).

### Phase 5A: ErrorVariables Validation (sigma + interp1d) ✅
**Status: Complete**

Validates the ErrorVariables struct pointer pipeline end-to-end with the two simplest ErrVar functions. No VIT code changes needed — the pipeline already handles C-compatible derived types as struct pointers.

- [x] Translate + verify + integrate `sigma` — 40/40 verified, scalars + ErrorVariables struct pointer
- [x] Translate + verify + integrate `interp1d` — 40/40 verified, arrays + ErrorVariables struct pointer
- [x] Full integration test: 9 C++ translations produce byte-identical simulation output

### Phase 5B: Remaining Functions.f90 (unwrap, interp2d, AeroDynTorque)
**Status: Complete (interp2d + AeroDynTorque done in Phase 8C; unwrap done in Phase 10)**

- [x] Multi-dimensional assumed-shape — `DIMENSION(:,:)` generates two size params (`n_rows`, `n_cols`). Rank detection and per-dimension size params implemented in Phase 5.
- [x] Guard against unsupported ranks — explicit error for `DIMENSION(:,:,:)` and higher.
- [x] Translate + verify + integrate `interp2d` — 21/21 passed. 2D bilinear interpolation with column-major Z macro. Done in Phase 8C.
- [x] Translate + verify + integrate `AeroDynTorque` — 20/20 passed. First function using complete type system (ControlParameters + PerformanceData view structs + LocalVariables + ErrorVariables). Done in Phase 8C.
- [x] Translate + verify + integrate `unwrap` — 25/25 passed (Scenario 10). Array-returning FUNCTION with `DIMENSION(SIZE(x))` result. VIT already supported array-valued returns (identity proved it); the original deferral was incorrect. Required a parser fix: `DIMENSION` extraction used `[^)]*` regex which broke on nested parens like `SIZE(x)`. Fixed with balanced-paren counting + case preservation. Reverted inline unwrap implementations in variablespeedcontrol.cpp and readcontrolparameterfilesub.cpp to shared `unwrap_c()` calls. 10-scenario sim-compare verified (692,000 values identical).

### Phase 6A: Filters Module — Active Filters ✅
**Status: Complete**

Translated the 3 filter functions that are active in the standard NREL-5MW test simulation. FilterParameters is a C struct (46 fields, all `REAL(DbKi) DIMENSION(1024)`). No VIT code changes needed — all patterns (struct pointer, OPTIONAL, LOGICAL, inst INOUT) proven in Phase 3 ratelimit.

- [x] Instance-based state access in C++ — `FP%fieldname(inst)` → `FP->fieldname[idx]` where `idx = *inst - 1` (1-based to 0-based). Validated across all 3 filters.
- [x] Translate + verify + integrate `LPFilter` — 40/40 passed. Extracted from Filters.f90 line 347.
- [x] Translate + verify + integrate `HPFilter` — 40/40 passed. Extracted from Filters.f90 line 375.
- [x] Translate + verify + integrate `SecLPFilter` — 40/40 passed. Extracted from Filters.f90 line 371.
- [x] Full clean build test: 12 C++ translations (9 Functions.f90 + 3 Filters.f90) produce byte-identical simulation output.

**Extraction lesson:** Many Controllers.f90 call sites (lines 167, 169, 350) are in conditional branches (`PA_Mode`, `F_LPFType`) not active in the test simulation. Successful extraction used unconditional call sites inside PreFilterMeasuredSignals (Filters.f90 lines 347, 371, 375).

#### Phase 6B: Filters Module — Remaining Filters + Simulation Coverage

Three filter functions could not be extracted because they are never called in the standard NREL-5MW test simulation. This phase adds a simulation scenario that exercises them, then translates them.

- [x] Added `vit_sim.py` Scenario 3: enables IPC (IPC_ControlMode=1), notch filters (F_GenSpdNotch_N=1 at 1.0 rad/s), and cable control loop (CC_Mode=1, CC_Group_N=1, CC_GroupIndex=2601). Scenario 3 runs first so KGen captures early invocations. Scenarios 1+2 untouched.
- [x] Translate + verify + integrate `NotchFilter` — 40/40 passed. Different scalar args (omega, betaNum, betaDen). Extracted from Filters.f90 line 356.
- [x] Translate + verify + integrate `NotchFilterSlopes` — 40/40 passed. First function with two OPTIONAL args (Moving LOGICAL + InitialValue REAL). Extracted from Filters.f90 line 405.
- [x] Translate + verify + integrate `SecLPFilter_Vel` — initially pattern-translated from SecLPFilter (structurally identical, lpfV_ prefix). Later KGen-verified 40/40 IDENTICAL after fixing two issues: (1) CC_Mode=1 required so DISCON calls CableControl (gated by `CC_Mode > 0`), (2) kernel Makefile needed `-fdefault-real-8` to prevent PI literal truncation to single precision. See dev note `202603191607-seclpfilter-vel-extraction-fix.md`.
- [x] Full clean build test: 15 C++ translations, all 3 scenarios, **byte-identical output** (after FMA fix).
- [x] **FMA contraction fix**: Discovered that gfortran and g++ schedule fused multiply-add differently for equivalent expressions, causing 1-ULP differences that accumulate in closed-loop feedback (NotchFilter on GenSpeed). Fixed by adding `-ffp-contract=off` to both Fortran and C++ in ROSCO's CMakeLists.txt. This is a verification-phase tool — once all of ROSCO is C++, FMA can be re-enabled. Clean build test now includes an FMA sanity check (default flags) to confirm diffs are small and bounded. See dev note `202603191041-fma-contraction-fix.md`.
- [x] **`fortran.flags` config**: Added to VIT so project-specific Fortran compiler flags propagate to the kernel Makefile. Without this, literals like `PI = 3.14159265359` lose precision when compiled without `-fdefault-real-8`.

**KGen DO loop handling note:** KGen correctly instruments call sites inside DO loops with derived-type iteration bounds. The original SecLPFilter_Vel "zero state files" issue was not a KGen bug — it was caused by CableControl being gated by `CC_Mode > 0` (simulation never entered the code path) and the kernel Makefile lacking `-fdefault-real-8` (literal constant precision loss).

**Unblocks:** Full Filters module coverage (6/6 individual filters translated). PreFilterMeasuredSignals remains blocked on Phase 4C (LocalVariables) + Phase 4D (ControlParameters).

### Phase 7A: Controllers Module — Helper Functions (No ControlParameters)
**Target: 4 helper controller functions that only need LocalVariables + ObjectInstances**

These functions take piParams/resParams (nested in LocalVariables) and ObjectInstances — all BIND(C)-compatible after Phase 4C. No ControlParameters needed. This is the proof-of-concept for Controllers translation: first functions with read+write access to LocalVariables struct fields, and the first functions that call already-translated Filters and Functions from C++.

- [x] Read+write derived type access — these controllers read AND write fields on piParams/resParams nested in LocalVariables. C++ code modifies the struct in place; changes are visible to Fortran since the struct is passed by pointer.
- [x] Cross-module call chains — PIController is called by many Controllers and ControllerBlocks functions. By this phase, its dependencies (Filters, Functions) are already C++.
- [x] **Shared C header for translated functions** — `vit integrate --apply` emits/updates a shared header file with `extern "C"` declarations for each integrated function's `_c` entry point. C++ translations that call other translated functions `#include` this header and call `_c` directly — no round-trip through Fortran. Fortran callers still use the wrapper (unchanged). This infrastructure was already in place from Phase 6B.
- [x] `vit translate` scaffold includes `_c` declarations for already-translated callees (detected from `vit.yaml` translations registry)
- [x] Translate + verify + integrate `PIController` — KGen-verified 40/40, piParams struct read/write with instance indexing
- [ ] ~~Translate + verify + integrate `PIDController`~~ — **moved to Phase 10**. PIDController is only called when `OL_Mode==2` (open-loop azimuth tracking). Extraction requires an OL CSV file. It doesn't block PitchControl or any Phase 8 function. Address alongside OL_Mode infrastructure in Phase 10.
- [x] Translate + verify + integrate `PIIController` — KGen-verified 40/40. Required fixing two upstream ROSCO bugs: uninitialized K in FlapControl init (Controllers.f90:669) and uninitialized n in Flp_Mode==2 notch filter (Filters.f90:412), plus ACC_INFILE assignment fix (ReadSetParameters.f90:216).
- [x] Translate + verify + integrate `ResController` — KGen-verified 40/40, Tustin-discretized resonant controller with resParams state

**Actual work done:**
- Added Scenarios 4 (Flp_Mode=2) and 5 (AWC_Mode=4) to `vit_sim.py` for PIIController/ResController code path coverage
- Fixed VIT `kgen_array_sumcheck` STOP to be non-fatal (subnormal value false positives in state integrity checks)
- Fixed `write_discon` regex to handle multi-value parameter lines (AWC_CntrGains)
- 3 new C++ translations in `translations/Controllers/` — first Controllers module functions
- All 18 integrated functions produce byte-identical output across 5 scenarios

**Type dependencies:** LocalVariables (✓ Phase 4C), ObjectInstances (✓ Phase 4B), piParams/resParams (nested in LocalVariables, ✓)
**New functions unlocked**: 3 helper controller functions (PIController, PIIController, ResController)

### Phase 7B: Controllers Module — Main Subroutines (Need ControlParameters)
**Target: 10 main controller subroutines — requires Phase 4D**

All main control subroutines take ControlParameters (ALLOCATABLE arrays, needs pointer+size view from Phase 4D). These are 100-200 line subroutines with complex control flow, state machines, and multi-derived-type argument lists. C++ implementations call Phase 7A helpers (PIController, etc.) directly via the shared C header — no Fortran round-trip.

**Extraction complete (2026-03-24).** All 10 functions extracted with 20 state files each. Required KGen parser fix (PROCEDURE POINTER), assumed-size array patching, DLL SAVE persistence workaround, and ROSCO validation fixes. See dev notes 202603240026, 202603240139, 202603240952, 202603241004, 202603241047.

- [x] Extraction infrastructure — KGen parser fix, assumed-size array patching, DLL persistence workaround, `--run-args` CLI, simplified extraction protocol
- [x] Extract all 10 functions (20 cases each): golden fixtures committed
- [x] Translate + verify `PitchControl` — 40/40 PASSED. Master pitch orchestrator (185 lines), 11+ callees. Required view-type stash fix for callee bridges.
- [x] Translate + verify `VariableSpeedControl` — 40/40 PASSED. Generator torque state machine, K*Omega^2 law.
- [x] Translate + verify `YawRateControl` — 40/40 PASSED. Yaw state machine, SAVE variables, trig filtering.
- [x] Translate + verify `IPC` — 40/40 PASSED. 1P/2P Individual Pitch Control, Coleman transforms.
- [x] Translate + verify `ForeAftDamping` — 40/40 PASSED (Phase 4D). First view type validation. Callee bridge to PIController.
- [x] Translate + verify `FloatingFeedback` — 40/40 PASSED. FUNCTION (returns double), gain-scheduled nacelle velocity feedback.
- [x] Translate + verify `FlapControl` — 40/40 PASSED. 3 Flp_Mode branches, Coleman transforms.
- [x] Translate + verify `ActiveWakeControl` — 40/40 PASSED. 5 AWC modes, complex decomposition.
- [x] Translate + verify `CableControl` — 40/40 PASSED. Cable length control, SecLPFilter_Vel + PIController.
- [x] Translate + verify `StructuralControl` — 40/40 PASSED. Structural control inputs, 2 modes.
- [x] Integration test — 27 of 28 functions integrated (PitchControl deferred: untranslated callee `pitchsaturation_c` in ControllerBlocks.f90). Byte-identical simulation output across 6 scenarios. Each function validated individually: integrate → compile → simulate → diff. Clean rebuild confirms no stale artifacts.

**Type dependencies:** All Phase 7A types + ControlParameters (Phase 4D) + DebugVariables (✓ Phase 4B) + ErrorVariables (✓ Phase 4A)
**New functions unlocked**: 10 main controller subroutines

### Phase 8: ControllerBlocks Module
**Target: All of ControllerBlocks.f90 + Functions.f90 dependencies (interp2d, AeroDynTorque)**

With Phase 4 complete, `WE` nested struct is accessible through `LocalVariables`. WindSpeedEstimator can read/write `LocalVar.WE.P[3][3]`, `LocalVar.WE.xh[3]`, etc.

**Note:** matinv3 is NOT needed despite original plan — WindSpeedEstimator's Kalman filter uses 1×1 innovation covariance S, so inversion is scalar division. matinv3 is dead code (defined in Functions.f90 but never called).

#### Phase 8A: PitchSaturation + PitchControl Unblock
- [x] Translate + verify + integrate `PitchSaturation` — 21/21 PASSED. Peak shaving min pitch via interp1d lookup.
- [x] Integrate `PitchControl` — was verified in Phase 7B, blocked on PitchSaturation. Now unblocked.
- [x] Integration test — 29 functions integrated, 612,000 values ALL IDENTICAL across 8 scenarios (later expanded to 39 functions / 9 scenarios in Phase 8C).
- [x] Parallel verification (`verify_all.sh`) — all 29 functions verified (later expanded to 39).

#### Phase 8B: Seven Independent ControllerBlocks Functions
- [x] Scenario 9 setup in vit_sim.py — SU_Mode=1, SD_Mode=1, TRA_Mode=1. Added `run_scenario_9()`.
- [x] Translate + verify + integrate `StateMachine` — pure conditional logic, 2 args, no callees
- [x] Translate + verify + integrate `SetpointSmoother` — LPFilter callee
- [x] Translate + verify + integrate `PowerControlSetpoints` — interp1d callee
- [x] Translate + verify + integrate `RefSpeedExclusion` — ratelimit callee
- [x] Translate + verify + integrate `ComputeVariablesSetpoints` — LPFilter + interp1d + saturate + RefSpeedExclusion callees
- [x] Translate + verify + integrate `Shutdown` — LPFilter + wrap_180. Required renaming C++ function to avoid POSIX `shutdown(2)` PLT collision.
- [x] Translate + verify + integrate `Startup` — LPFilter + sigma

#### Phase 8C: WindSpeedEstimator + Dependencies
- [x] VIT 2D assumed-shape array support — interface_gen.py, fortran_parser.py, kernel_verify.py
- [x] Translate + verify + integrate `interp2d` (Functions.f90) — 2D bilinear interpolation, first rank-2 array function
- [x] Translate + verify + integrate `AeroDynTorque` (Functions.f90) — first PerformanceData view type function
- [x] Translate + verify + integrate `WindSpeedEstimator` — EKF with 3×3 matrices, 6 type args. Required `std::pow(v_m, 3.0)` fix and Fortran MATMUL bridge (`vit_ekf_update`).
- [x] Integration test — 39 functions integrated, 672,000 values ALL IDENTICAL across 9 scenarios.

**Phase 8 complete.** All 39 ROSCO functions translated to C++ and verified bit-identical. See dev notes `202603282007` for full investigation details.

### Phase 9: Type Ownership Transfer
**Target: C++ becomes source of truth for all types**

Phase 4 created BIND(C) structs and pointer+size views. Phase 9 progressively transfers ownership to C++ as the remaining Fortran infrastructure is translated. The transfer has a dependency chain: C++ can't own allocations until the allocating code (ReadSetParameters) is translated (Phase 10), and struct definitions can't move to C++ headers until DISCON.F90 is translated (Phase 11).

**Actionable now (no Phase 10 dependency):**
- [x] C++ constants header — created `translations/rosco_constants.h` with all Constants.f90 values. 17 translations updated to use it. Documented in TRANSLATION_TIPS.md (included in translation prompts automatically). Translations are self-contained — each includes only what it uses.
- [x] Extract struct typedefs into consolidated `vit_types.h` — all 11 C struct definitions consolidated into a single `translations/vit_types.h`, auto-generated incrementally by `generate_consolidated_types_header()` in `derived_type_parser.py`. `vit translate` creates/updates the file as new types are discovered. `vit integrate --apply` copies it to `src/`. Every translation uses `#include "vit_types.h"` (one include). Kernel verification generates the same consolidated `vit_types.h` via `generate_consolidated_types_header()`. 39 translation files use the consolidated header (16,009 → 3,399 lines from inline embedding, then further simplified to one include per file).

**After Phase 10 (I/O translated to idiomatic C++):**
- [ ] Transfer ALLOCATABLE types to C++ ownership — C++ config reader allocates ControlParameters and PerformanceData arrays. View struct transitions from "view into Fortran data" to "primary data owner." During the mixed phase, DISCON.F90 reads scalar mode flags from the view struct via a thin Fortran bridge; ALLOCATABLE arrays are only accessed by C++ algorithm functions through the view struct (no bridge needed). Memory allocated with `malloc()` for C_PTR compatibility.

**After Phase 11 (DISCON.F90 translated to C++):**
- [ ] Move struct definitions from Fortran to C++ headers — C++ structs become the canonical definitions. ROSCO_Types.f90 becomes dead code.

### Phase 10: I/O and Platform Modules
**Target: ReadSetParameters, ROSCO_Helpers, ROSCO_IO, SysSubs, ExtControl, ZeroMQInterface**

**Strategic shift (2026-04-01):** Consultation with the ROSCO developer and three software engineers established a new approach for Phase 10. KGen cannot verify file I/O, platform wrappers, or stateful diagnostics. Rather than building elaborate new VIT verification infrastructure (file pre-staging, `--io` mode, `--diagnostic` mode) to mirror-translate I/O code line-by-line, Phase 10 writes **idiomatic C++** for non-KGen functions and verifies at the simulation level (10 scenarios, 692,000 values). KGen is still used for pure transformer functions.

**Two tracks:**
- **Track A (KGen, mirror Fortran):** Pure transformers with no file I/O — standard VIT extract → verify → integrate pipeline
- **Track B (Idiomatic C++):** Config loading, restart I/O, debug logging, platform modules — verified via 9-scenario simulation comparison

**Design decisions:**
- **Config format:** DISCON.IN parser complete. YAML format deferred to Phase 11 (doesn't advance migration, requires Python toolbox changes).
- **Restart format:** New self-describing C++ binary format. No Fortran UNFORMATTED compatibility needed (controller will be fully C++ after Phase 10+11).
- **VIT changes:** Minimal — `vit.yaml` adds `strategy: idiomatic` for Track B translations. No new VIT verification modes.
- **C++ standard:** C++17 (for `<filesystem>` path operations)

**Fortran text-parsing helpers — dead code, not ported:**
`ParseDbAry`, `ParseInAry`, `FindLine`, `GetWords`, `ChkParseData`, `ParseInput` variants, `Read_OL_Input` — these all exist solely to parse DISCON.IN's positional text format. The idiomatic C++ config reader replaces all of them. Utility functions (`Conv2UC`, `Int2LStr`, `GetPath`, `GetRoot`, `PathIsRelative`) are trivially replaced with `<algorithm>`, `std::to_string`, and `<filesystem>`. `GetNewUnit` is not needed in C++ (uses `std::ofstream`).

**Stage A — KGen functions (standard pipeline):**
- [x] `ReadAvrSWAP` — unpacks avrSWAP array into LocalVar fields (140 lines of pure assignment). Extracted (21 cases, Scenario 3), verified 21/21, integrated. Required ROSCO_Helpers.f90 backslash patch for KGen parser.
- [x] `PIDController` — PI + derivative term. Extracted (22 cases, Scenario 10 / OL_Mode=2), verified 22/22, integrated. Added Scenario 10 to vit_sim.py. Updated VariableSpeedControl translation with OL_Mode=2 code path (inline UNWRAP + PIDController call).

**Stage B — Configuration loading (idiomatic C++):**
- [x] `ReadCpFile` → `translations/ReadSetParameters/readcpfile.cpp` — Clean C++ CSV reader for Cp/Ct/Cq performance tables. Simplest idiomatic file reader, proved the pattern. Simulation-verified (10 scenarios, 692,000 values identical).
- [x] Config reader → `translations/ReadSetParameters/readcontrolparameterfilesub.cpp` — Replaces `ReadControlParameterFileSub` (560 lines). DISCON.IN parser only (YAML deferred to Phase 11 — doesn't advance migration, requires Python toolbox changes). Two-pass protocol for ALLOCATABLE allocation: first pass parses scalars, second pass allocates and fills arrays. Populates `controlparameters_view_t` with all 225+ fields. Handles array allocation, computed constants, path resolution, open-loop CSV, echo file output. **Largest piece of work in Phase 10.** Simulation-verified (10 scenarios, 692,000 values identical).
- [x] `CheckInputs` → `translations/ReadSetParameters/checkinputs.cpp` — Parameter validation (range checks, cross-parameter consistency). Pure logic. Simulation-verified (10 scenarios, 692,000 values identical).

**Stage C — Restart I/O + Debug (idiomatic C++):**

- [ ] `WriteRestartFile` + `ReadRestartFile` → `translations/IO/restart.cpp` (combined — exact mirror pair)
  - ROSCO_IO.f90:14-355 (342 lines) + ROSCO_IO.f90:358-707 (350 lines)
  - 310 binary WRITE/READ statements each: ~130 LocalVar scalars, ~84 manually-unrolled array elements (rootMOOP(1..3), BlPitch(1..3), etc.), 9 objInst fields, nested WE/FP/piP/resP/rlP fields (including 46 DIMENSION(1024) FilterParameters arrays at 8 KB each)
  - C++ uses `std::ofstream`/`std::ifstream` with `std::ios::binary` — direct `write()`/`read()` of raw bytes matches Fortran UNFORMATTED/STREAM exactly
  - New binary format with `uint8_t version` header (version 1). No backward compatibility with old Fortran checkpoint files needed — controller will be fully C++ after Phase 11
  - ReadRestartFile calls `readcontrolparameterfilesub_pass1_c`/`_pass2_c` and `readcpfile_c` after reading checkpoint (all Stage B, already translated)
  - No SAVE variables, no ALLOCATE
  - **Risk:** Field ordering — if a single field is written out of order relative to the read, the checkpoint is silently corrupted. Mitigate by generating both Write/Read from the same field list or careful parallel editing
  - **Verification:** Restart I/O is only triggered by iStatus==-8/-9 (not exercised in standard 10-scenario sim-compare). Verified indirectly: if ReadRestartFile produces incorrect state, downstream controller outputs would diverge. Optional: add a restart scenario to vit_sim.py for direct verification

- [ ] `Debug` → `translations/IO/debug_logger.cpp`
  - ROSCO_IO.f90:710-1203 (494 lines) — largest remaining function
  - 3 SAVE variables (file handles → `static std::ofstream`), 1 SAVE ALLOCATABLE (avrIndices → `static std::vector<int>`)
  - 3 output files controlled by `CntrPar%LoggingLevel`:
    - `.RO.dbg`: 26 DebugVar fields (always)
    - `.RO.dbg2`: 159 LocalVar fields (LoggingLevel > 1)
    - `.RO.dbg3`: avrSWAP indices — base 85 + CC_GroupIndex + StC_GroupIndex (LoggingLevel > 2)
  - Lifecycle: opens files + writes headers on first call (iStatus==0 or -9), writes data every `n_DT_Out` steps with value clamping [1E-99, 1E+99], console output every 10 seconds, closes files on iStatus < 0
  - Replaces ROSCO_Helpers consumed: GetNewUnit → `std::ofstream`, CurDate/CurTime → `<chrono>/<ctime>`, AddToList → `std::vector::push_back`, Int2LStr → `std::to_string`
  - **Verification:** Debug does NOT affect simulation output arrays. 10-scenario sim-compare proves controller logic is correct. Debug output can be spot-checked by diffing `.RO.dbg` files between baseline and integrated builds. Minor formatting differences acceptable

**Stage D — Platform modules (idiomatic C++):**

- [ ] `UpdateZeroMQ` → `translations/IO/zmq_interface.cpp`
  - ZeroMQInterface.f90:7-83 (77 lines) — simplest remaining function
  - Sends 17 turbine measurements to ZeroMQ server, receives 8 setpoints
  - Conditional on `ZMQ_CLIENT` compile flag (C++ uses `#ifdef ZMQ_CLIENT` identically)
  - Calls `zmq_client()` — already a C function with BIND(C), trivially callable from C++
  - No SAVE, no ALLOCATE, no file I/O (other than ZMQ socket)
  - **Verification:** ZMQ_Mode=0 in all test scenarios — function never entered. sim-compare verifies no regression from wrapper plumbing

- [ ] `ExtController` → `translations/IO/ext_control.cpp`
  - ExtControl.f90:52-129 (78 lines)
  - Loads and calls an external controller DLL via dlopen/dlsym
  - SAVE variable: `DLL_Ext` (library handle + proc address) → `static void*` + `static` function pointer
  - Absorbs LoadDynamicLib/LoadDynamicLibProc/FreeDynamicLib from SysSubs — replaced by direct `<dlfcn.h>` calls (`dlopen`, `dlsym`, `dlclose`)
  - BladedDLL_Legacy_Procedure (abstract interface) → C++ function pointer typedef
  - Must link with `-ldl` on Linux
  - **Verification:** Ext_Mode=0 in all test scenarios — function never entered. sim-compare verifies no regression

- `SysSubs` — fully absorbed after ExtController translation. LoadDynamicLib/LoadDynamicLibProc/FreeDynamicLib → inlined as dlopen/dlsym/dlclose. GetNewUnit → std::ofstream. CurDate/CurTime → `<chrono>`. Path utilities → `<filesystem>`. No separate translation file.
- `ROSCO_Helpers.f90` (~20 functions) — No individual translation needed. All functionality absorbed into idiomatic C++ equivalents: DisconParser replaces all ParseInput/ParseAry/FindLine/GetWords/ChkParseData variants, `<filesystem>` replaces GetPath/GetRoot/PathIsRelative, `std::fstream` replaces GetNewUnit, `std::vector::push_back` replaces AddToList, `std::toupper` replaces Conv2UC, `std::to_string` replaces Int2LStr, `<chrono>`/`<ctime>` replaces CurTime/CurDate, NonDecreasing inlined in checkinputs.cpp, Read_OL_Input inlined in readcontrolparameterfilesub.cpp. Fully accounted for via Stage B translations.
- `BladedDLL_Legacy_Procedure` (ExtControl.f90) — Abstract interface definition, not a function body. Becomes a C++ function pointer typedef in Stage D's ExtControl translation.

**Stage E — Orchestrators (idiomatic C++):**

- [ ] `PreFilterMeasuredSignals` → `translations/Filters/prefiltermeasuredsignals.cpp`
  - Filters.f90:325-439 (115 lines) — pure filter orchestrator
  - Calls LPFilter (5x), SecLPFilter (6x), HPFilter (4x), NotchFilter (in loops), NotchFilterSlopes (1x), wrap_180 (1x) — all already translated, called via `_c` entry points
  - No file I/O, no ALLOCATE, no SAVE
  - Control flow: conditional filtering based on F_LPFType, IPC_ControlMode, Flp_Mode, TD_Mode; loops over F_GenSpdNotch_N and F_TwrTopNotch_N notch filters; blade loop (K=1..NumBl)
  - CntrPar ALLOCATABLE array access requires 1-based → 0-based index adjustment (F_NotchFreqs, F_GenSpdNotch_Ind, etc.)
  - **Verification:** Called every timestep, directly affects all downstream controllers. 10-scenario sim-compare provides strong coverage

- [ ] `SetParameters` → `translations/IO/set_parameters.cpp`
  - ReadSetParameters.f90:143-298 (156 lines) — initialization + per-timestep orchestrator
  - **Per-timestep (every call):** initializes objInst counters, sets avrSWAP defaults (indices 35-81), initializes ErrVar, computes OL_Index (OL_BP_Mode breakpoint)
  - **First call (iStatus==0):** prints ROSCO banner, saves ACC_INFILE string, calls readcontrolparameterfilesub (two-pass protocol), conditionally calls readcpfile_c, initializes ~25 LocalVar fields (PitCom, WE_Vw, WE_VwI, VS_LastGenTrq, VS_MaxTq, CC_DesiredL, ZMQ fields), calls checkinputs_c
  - Callees: readcontrolparameterfilesub_pass1_c/pass2_c (Stage B), readcpfile_c (Stage B), checkinputs_c (Stage B), interp1d_c (Phase 5A), lpfilter_c (Phase 6) — all translated
  - **Risk:** Two-pass ReadControlParameterFileSub protocol must be replicated correctly (pass1 → Fortran allocates → pass2). The Fortran wrapper handles the ALLOCATE sequence between passes
  - **Verification:** Called every timestep, initialization affects all downstream behavior. 10-scenario sim-compare is comprehensive

**Dead code (not translated):**
- `matinv3` (Functions.f90) — 3x3 matrix inverse. Zero call sites in entire codebase. Dropped.
- `CPfunction` (Functions.f90) — Parameterized Cp(lambda) function. Zero call sites; superseded by interp2d table-lookup approach. Dropped.

**Interface: idiomatic I/O ↔ existing C structs:**
All new C++ I/O code populates the same types from `vit_types.h` that the 45 algorithm functions use. All functions exposed to Fortran (during the mixed phase) use `extern "C"` with C struct pointers. The `controlparameters_view_t` transitions from "view into Fortran data" to "primary data owner" — see derived type ownership notes above.

**Verification (three tiers):**
1. **KGen (Stages A + unwrap):** Bit-identical function-level verification for ReadAvrSWAP, PIDController, unwrap (42 functions total)
2. **Unit tests (Track B):** Each idiomatic module gets standalone tests where applicable (roundtrip restart, format debug output)
3. **Simulation gate (all stages):** After each stage, 10-scenario sim-compare (692,000 values, must be identical with `-ffp-contract=off`). Stage D and C functions are mostly not exercised by test scenarios — gate verifies wrapper plumbing. Stage E functions ARE exercised every timestep — gate provides full coverage

**Build system changes:**
- [x] Add `target_include_directories` for `translations/` — C++ translations can `#include` headers from the translations tree
- [ ] Add `-ldl` to link flags — needed for ExtController's dlopen/dlsym (Stage D)
- [ ] Set C++17 standard flag — needed for `<filesystem>` if used in path handling (optional, `<cstring>` path ops work without it)
- [x] Add new C++ source files to SOURCES list
- [ ] Full clean build test after all stages complete

### Phase 11: Entry Point and Final Assembly
**Target: DISCON.F90 → pure C++ libdiscon.so**

- [ ] Translate `DISCON` — 162-line orchestrator: initialization, parameter reading, controller dispatch. By this phase, everything it calls is already C++.
- [ ] Remove Fortran from build — CMakeLists.txt becomes C++-only. No more `gfortran`.
- [ ] Full simulation regression — run the complete wind turbine simulation and confirm byte-identical output to the original pure-Fortran baseline.
- [ ] Binary compatibility — verify the C++ `libdiscon.so` works as a drop-in replacement: same symbol names, same calling convention (Bladed DLL interface).

### Phase 12: Generalization Beyond ROSCO
**Target: VIT as a general-purpose tool**

A generality audit (2026-03-19) confirmed VIT has no ROSCO-specific logic in production code. Kind aliases are already fully configurable via vit.yaml. The remaining items are packaging and validation on a second codebase.

- [x] Abstract kind aliases — `kind_aliases` in vit.yaml supports arbitrary project kinds. NWTC/OpenFAST kinds (ReKi, DbKi, IntKi) are defaults, overridable.
- [x] General `--fix complex` — scan-first architecture with EXIT_MANUAL protocol. No project-specific regex. Complex intrinsics flagged for human review via `--rewrite`.
- [ ] Build system support — add support for Make, Autotools, and other build systems beyond CMake
- [ ] Documentation — user guide, API reference, tutorial for new codebases
- [ ] Packaging — PyPI distribution, Docker image, CI/CD
- [ ] Test on another codebase — validate VIT on a second Fortran project to prove generality


