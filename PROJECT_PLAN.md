# PROJECT PLAN: Full ROSCO Fortran → C++ Translation

## 1. Vision and Current State

### Vision

Fully translate the ROSCO wind turbine controller from Fortran to C++, producing a drop-in replacement `libdiscon.so` that is pure C++ and produces byte-identical simulation output to the Fortran original. VIT orchestrates the verification and integration. Claude (AI) writes the C++ translations, scaffolded by the `vit translate` command (see Section 4a).

### Current State (2026-03-18)

- **Verified translations**: 3 of 14 Functions.f90 functions (`saturate`, `wrap_180`, `wrap_360`) — all ErrVar-free
- **Integrated**: None yet (clean slate — integration happens in phase order)
- **Pipeline**: End-to-end extract → verify → integrate → rebuild → diff confirmed working
- **VIT codebase**: ~4,600 lines of core code, 231 unit tests, 11 modules
- **Translation infrastructure**: `vit translate` command, module-organized storage in `ROSCO/translations/`, metadata headers (Phase 0 complete)

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
| `interp1d` | 58 | 1D linear interpolation (2 arrays + scalar + ErrVar → scalar) | Blocked by ErrorVariables — requires BIND(C) struct (Phase 4). Validates inputs, writes `ErrVar%aviFAIL` and `ErrVar%ErrMsg` on error |
| `sigma` | 34 | Sigmoid/cubic ramp (5 scalars + ErrVar → scalar) | Blocked by ErrorVariables — requires BIND(C) struct (Phase 4). Only checks `ErrVar%aviFAIL` status |
| `unwrap` | 40 | Phase unwrapping (assumed-shape `x(:)` + ErrVar → array `DIMENSION(SIZE(x))`) | Blocked by ErrorVariables (Phase 4) + array-valued return where result size depends on input array |
| `interp2d` | 172 | 2D bilinear interpolation (3 arrays including `zData(:,:)` + 2 scalars + ErrVar → scalar) | Blocked by ErrorVariables (Phase 4) + 2D assumed-shape `DIMENSION(:,:)` (Phase 5). Calls `interp1d`, uses `ieee_arithmetic` |
| `AeroDynTorque` | 39 | Aerodynamic torque (2 scalars + 4 derived types → scalar) | Four derived type args: `LocalVariables`, `ControlParameters`, `PerformanceData` (all become C structs in Phase 4), `ErrorVariables` (BIND(C) struct in Phase 4). Calls `interp2d` |

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

**LocalVariables — 4 surgical fixes, then fully BIND(C) (161 fields):**

LocalVariables contains 139 REAL(DbKi) scalars, fixed-size arrays, and 5 nested BIND(C)-compatible types. Four fields block compatibility:

| Field | Type | Fix | Impact |
|-------|------|-----|--------|
| `ACC_INFILE` | `CHARACTER(:), ALLOCATABLE` | Move to separate `char*` parameter or file-scope storage. Only used at init (ReadSetParameters), never in control loop. | Trivial — init-only field. |
| `AWC_complexangle(3)` | `COMPLEX(DbKi)` | Replace with `AWC_complexangle_re(3)` + `AWC_complexangle_im(3)` as `REAL(DbKi)`. Only the REAL part is ever extracted downstream (`REAL(LocalVar%AWC_complexangle(K))`). | Low — used only in ActiveWakeControl. |
| `WriteThisStep` | `LOGICAL` | Change to `INTEGER(C_INT)` (0/1) or `LOGICAL(C_BOOL)`. Fortran `LOGICAL` size varies by compiler (1–8 bytes); C `bool` is 1 byte. | Low — rarely used. |
| `restart` | `LOGICAL` | Same fix as WriteThisStep. Change to `INTEGER(C_INT)` or `LOGICAL(C_BOOL)`. | Medium — passed to all filters and controllers, but fix is just a type change. |

After these fixes, the entire 161-field type (including nested WE, FilterParameters, piParams, resParams, rlParams) is a plain C struct. Total size ~305 KB, passed by pointer.

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

Deep investigation (2026-03-17) found that **every ROSCO type can become a C struct** — no opaque pointers or accessor APIs needed. This phase solves the entire type system in one shot.

**Design constraint:** These C structs become the permanent C++ types in the final all-C++ codebase. Design them as final types, not temporary interop scaffolding — use reasonable field names, logical grouping, and avoid Fortran-isms that would need unwinding later.

**VIT `analyze-types` command — build the tool, then use it:**

Rather than applying type fixes manually, this phase implements a new `vit analyze-types` command that performs the analysis deterministically and generates the artifacts. The manual investigation we did for ROSCO becomes the spec and golden test cases.

The command takes a source directory and produces:
- [ ] **Per-type BIND(C) compatibility report** — field-by-field analysis with specific blockers (ALLOCATABLE, CHARACTER, COMPLEX, LOGICAL, nested non-BIND(C) types).
- [ ] **Fix prescriptions** — deterministic for known patterns: ALLOCATABLE → fixed-size with max bound, `CHARACTER(:), ALLOCATABLE` → `CHARACTER(N)`, `COMPLEX` → split real/imag `REAL` pair, `LOGICAL` → `INTEGER(C_INT)`.
- [ ] **Usage analysis** — scan source tree for `TypeName%FieldName` patterns, classify read vs. write, report which functions access which fields.
- [ ] **C++ struct header generation** — produce the `.h` file with C struct definitions matching the (fixed) Fortran layout.
- [ ] **Fortran BIND(C) type modifications** — generate patched TYPE definitions with `BIND(C)` attributes and incompatible fields replaced.

This makes the type system work reproducible and generalizable beyond ROSCO (Phase 12). The ROSCO analysis we already did serves as the validation target: the tool's output for ROSCO must match our manual findings.

**Already BIND(C)-compatible types (define C structs, no Fortran changes):**
- [ ] `FilterParameters` — 27 arrays of `double[1024]` (~221 KB). Used by all filters.
- [ ] `piParams` — 5 arrays of `double[1024]`. Used by PI/PID controllers.
- [ ] `resParams` — 4 arrays of `double[1024]`. Used by resonance controller.
- [ ] `rlParams` — 1 array of `double[1024]`. Already handled in Phase 3 for `ratelimit`.
- [ ] `WE` — 7 fields: 4 scalars + `double[3][3]` + 2× `double[3]`. Note: Fortran column-major layout for multi-dimensional arrays.
- [ ] `ObjectInstances` — 9 integers.
- [ ] `DebugVariables` — 22 `REAL(DbKi)` scalars.

**LocalVariables — 4 surgical Fortran fixes, then define C struct:**
- [ ] Replace `ACC_INFILE` (`CHARACTER(:), ALLOCATABLE`) — move to separate `char*` parameter or file-scope storage. Only used at init (ReadSetParameters), never in control loop.
- [ ] Replace `AWC_complexangle(3)` (`COMPLEX(DbKi)`) — split into `AWC_complexangle_re(3)` + `AWC_complexangle_im(3)` as `REAL(DbKi)`. Only the REAL part is ever extracted downstream.
- [ ] Replace `WriteThisStep` (`LOGICAL`) — change to `INTEGER(C_INT)` (0/1). Fortran `LOGICAL` size varies by compiler; `INTEGER(C_INT)` is guaranteed portable.
- [ ] Replace `restart` (`LOGICAL`) — same fix. Heavily used (passed to all filters/controllers) but fix is just a type change.
- [ ] Define C++ struct for `LocalVariables` with nested `WE`, `FilterParameters`, `piParams`, `resParams`, `rlParams`. 139 scalar fields + fixed-size arrays + 5 nested structs. ~305 KB total.
- [ ] Add `BIND(C)` attributes to all Fortran TYPE definitions.
- [ ] Update all Fortran code referencing changed fields (COMPLEX → split reals, LOGICAL → integer).

**ControlParameters — pointer+size view struct:**
- [ ] Create C++ view struct with scalar fields copied and ALLOCATABLE arrays as `double*` + `int` size pairs. No ROSCO source modifications.
- [ ] Write Fortran BIND(C) routine that populates the view struct once at init: copy scalars, take `C_LOC` of each ALLOCATABLE array, pass array sizes.
- [ ] Replace 4 CHARACTER fields with `char[1024]` in the view struct.
- [ ] Verify ControlParameters is truly read-only in algorithmic code (investigation confirmed: INTENT(INOUT) is legacy, all accesses are reads).

**PerformanceData — same pointer+size view pattern:**
- [ ] Create C++ view struct with ALLOCATABLE arrays as `double*` + size pairs.
- [ ] Write Fortran BIND(C) routine to populate view.

**Ownership tracking:**
- [ ] Implement VIT ownership tracking for types — each type is tagged as "view" (Fortran-owned, C++ reads via pointers) or "owned" (C++ allocates and manages).
- [ ] When all Fortran consumers of a type are translated, VIT flags that ownership can transfer to C++.

**Multi-dimensional array transposition:**
- [ ] VIT auto-detects multi-dimensional arrays in BIND(C) interfaces (e.g., `WE%P(3,3)`, `Cp_mat(:,:)`).
- [ ] Generates transposition code in the Fortran wrapper at the boundary, so C++ always receives row-major data.
- [ ] When Fortran is removed and C++ owns the data natively, transposition disappears.

**ErrorVariables — BIND(C) struct (one fix):**
- [ ] Replace `CHARACTER(:), ALLOCATABLE :: ErrMsg` with `CHARACTER(1024)`. The DLL interface already uses a 1024-char buffer. No observed error message exceeds ~500 chars.
- [ ] `aviFAIL`, `ErrStat`, and `size_avcMSG` are already C-compatible — no changes needed.
- [ ] After this fix, ErrorVariables is a BIND(C) struct passed by pointer like all other types. C++ accesses fields directly: `errVar->aviFAIL`, `errVar->ErrMsg`, etc.

**Validation:**
- [ ] Write memory layout test: compare field offsets between Fortran `BIND(C)` type and C++ struct to verify binary compatibility.
- [ ] Verify mixed Fortran/C++ build produces byte-identical simulation output after type modifications.
- [ ] Run full verification regression on all previously verified functions.

**Why this must come before Phase 5:** All ErrVar functions are blocked until ErrorVariables is a BIND(C) struct. Filters access ~30 fields on FilterParameters, Controllers access ~70 fields on LocalVariables. With C structs, C++ accesses fields naturally (`state->lpf1_a1[inst]`) and the Fortran wrapper just passes the struct address.

### Phase 5: ErrorVariables Functions + 2D Assumed-Shape Arrays
**Target: 13/14 Functions.f90 (CPfunction skipped — dead code)**

With Phase 4 complete, ErrorVariables is a BIND(C) struct. All 5 ErrVar-blocked functions can now be translated. This phase also adds 2D assumed-shape array support needed by `interp2d`.

- [ ] Multi-dimensional assumed-shape — `DIMENSION(:,:)` must generate two size params (`n_rows`, `n_cols`), not one. Current code silently generates broken interfaces for 2D. Add rank detection and per-dimension size params.
- [ ] Guard against unsupported ranks — add explicit error for `DIMENSION(:,:,:)` and higher until supported.
- [ ] Array-valued return where result size depends on input array — needed by `unwrap`
- [ ] Translate + verify + integrate `interp1d`
- [ ] Translate + verify + integrate `sigma`
- [ ] Translate + verify + integrate `unwrap`
- [ ] Translate + verify + integrate `interp2d`
- [ ] Translate + verify + integrate `AeroDynTorque` — first function using the complete type system: ControlParameters (view struct), PerformanceData (view struct), LocalVariables (BIND(C) struct), ErrorVariables (BIND(C) struct)
- [ ] Run full verification regression (all 13 translated functions pass)

**New functions unlocked**: `interp1d`, `sigma`, `unwrap`, `interp2d`, `AeroDynTorque`

### Phase 6: Filters Module
**Target: All of Filters.f90**

With Phase 4 complete, `FilterParameters` is a C struct passed by pointer. All filters receive it directly. ErrorVariables is also a BIND(C) struct — no special handling needed.

- [ ] Instance-based state access in C++ — filters use `FP%fieldname(inst)` pattern, which becomes `fp->fieldname[inst]` in C++. Validate this pattern works correctly across the BIND(C) boundary.
- [ ] Multi-state-type functions — `PreFilterMeasuredSignals` takes 5 derived type args (CntrPar, LocalVar, DebugVar, objInst, ErrVar). All are C structs passed by pointer.
- [ ] Translate + verify + integrate `LPFilter`
- [ ] Translate + verify + integrate `SecLPFilter`
- [ ] Translate + verify + integrate `SecLPFilter_Vel`
- [ ] Translate + verify + integrate `HPFilter`
- [ ] Translate + verify + integrate `NotchFilterSlopes`
- [ ] Translate + verify + integrate `NotchFilter`
- [ ] Translate + verify + integrate `PreFilterMeasuredSignals`

**New functions unlocked**: `LPFilter`, `SecLPFilter`, `SecLPFilter_Vel`, `HPFilter`, `NotchFilter`, `NotchFilterSlopes`, `PreFilterMeasuredSignals`

### Phase 7: Controllers Module
**Target: All of Controllers.f90**

With Phase 4 complete, all types are C structs passed by pointer — `LocalVariables`, `ControlParameters`, `FilterParameters`, `ErrorVariables`, and all nested state types. Controllers read and write struct fields directly.

- [ ] Large subroutine support — controller subroutines are 100-200 lines with complex control flow. VIT's extraction and verification should handle these mechanically, but testing at scale is needed.
- [ ] Read+write derived type access — controllers read AND write fields on LocalVariables. C++ code modifies the struct in place; changes are visible to Fortran since the struct is passed by pointer.
- [ ] Cross-module call chains — controllers call Filters and Functions. By this phase, those modules are already translated, so the C++ controllers call C++ filters and functions directly.
- [ ] Translate + verify + integrate `PIController`
- [ ] Translate + verify + integrate `PIDController`
- [ ] Translate + verify + integrate `PIIController`
- [ ] Translate + verify + integrate `ResController`
- [ ] Translate + verify + integrate `PitchControl`
- [ ] Translate + verify + integrate `VariableSpeedControl`
- [ ] Translate + verify + integrate `YawRateControl`
- [ ] Translate + verify + integrate `IPC`
- [ ] Translate + verify + integrate `ForeAftDamping`
- [ ] Translate + verify + integrate `FloatingFeedback`
- [ ] Translate + verify + integrate `FlapControl`
- [ ] Translate + verify + integrate `ActiveWakeControl`
- [ ] Translate + verify + integrate `CableControl`
- [ ] Translate + verify + integrate `StructuralControl`

**New functions unlocked**: All 14 Controllers.f90 subroutines/functions

### Phase 8: ControllerBlocks Module
**Target: All of ControllerBlocks.f90**

With Phase 4 complete, `WE` nested struct is accessible through `LocalVariables`. WindSpeedEstimator can read/write `LocalVar.WE.P[3][3]`, `LocalVar.WE.xh[3]`, etc.

- [ ] Kalman filter support — `WindSpeedEstimator` uses 3×3 matrix operations via the WE derived type. By this phase, matinv3 and identity are already translated. C++ accesses `WE` fields through the LocalVariables struct pointer.
- [ ] State machine support — `StateMachine`, `Startup`, `Shutdown` have multi-stage sequential logic with persistent state stored in LocalVariables fields.
- [ ] Translate + verify + integrate `WindSpeedEstimator`
- [ ] Translate + verify + integrate `StateMachine`
- [ ] Translate + verify + integrate `ComputeVariablesSetpoints`
- [ ] Translate + verify + integrate `Startup`
- [ ] Translate + verify + integrate `Shutdown`
- [ ] Translate + verify + integrate `PowerControlSetpoints`
- [ ] Translate + verify + integrate `SetpointSmoother`
- [ ] Translate + verify + integrate `RefSpeedExclusion`

**New functions unlocked**: All 8 ControllerBlocks.f90 subroutines

### Phase 9: Type Ownership Transfer
**Target: C++ becomes source of truth for all types**

Phase 4 created BIND(C) structs and pointer+size views. Phase 9 transfers ownership: C++ allocates and manages all data, Fortran wrappers become thin shims.

- [ ] Transfer ALLOCATABLE types to C++ ownership — ControlParameters and PerformanceData pointer+size views become C++ structs with `std::vector<double>`. C++ ReadSetParameters (translated in Phase 10) allocates directly.
- [ ] Move struct definitions from Fortran to C++ headers — C++ structs become the canonical definitions. Fortran types become thin `BIND(C)` wrappers that mirror the C++ layout.
- [ ] C++ constants header — `constexpr double PI = ...`, `using DbKi = double`, etc. Replace Constants.f90 as the source of truth.
- [ ] VIT ownership tracker confirms all types are now "owned" by C++.
- [ ] Verify mixed Fortran/C++ build still produces byte-identical simulation output.

### Phase 10: I/O and Platform Modules
**Target: ReadSetParameters, ROSCO_Helpers, ROSCO_IO, SysSubs, ExtControl, ZeroMQInterface**

Investigation (2026-03-17) classified every I/O function into four categories, each with a distinct VIT verification strategy. The verification capabilities are built at the start of this phase, then used to verify each translation.

**Category 1 — Pure transformers (standard VIT pipeline):**
Functions with no file I/O — pure input→output transformations. Verified using the existing extract → verify pipeline.

- `ReadAvrSWAP` — unpacks avrSWAP array into LocalVar fields
- Helper functions: `ParseDbAry`, `ParseInAry`, `FindLine`, `GetWords`, `Conv2UC`, `GetPath`, `GetRoot`, `PathIsRelative`, `Int2LStr`

**Category 2 — File readers (VIT pipeline + file pre-staging):**
Functions that read files and populate structs. Deterministic: same input file → same output struct. VIT enhancement: `vit extract` captures file dependencies (which files the function opens) and copies them into the kernel directory. Verification compares the populated struct between Fortran and C++ given the same input file.

- `ReadControlParameterFileSub` — reads DISCON.in → populates ControlParameters
- `ReadCpFile` — reads Cp_Ct_Cq CSV → populates PerformanceData
- `ReadRestartFile` — reads binary checkpoint → populates LocalVar

**Category 3 — File writers (VIT `--io` mode with output file comparison):**
Functions whose output is a file on disk, not a Fortran argument. KGen cannot capture file system side effects. VIT enhancement: `vit verify --io` captures input struct state, runs both Fortran and C++ with those inputs, byte-compares the output files.

- `WriteRestartFile` — serializes LocalVar → binary checkpoint file

**Category 4 — Diagnostic/logging (VIT `--diagnostic` mode with structured diff):**
Functions with side effects only, stateful (SAVE variables), non-deterministic elements (timestamps). Correctness means "equivalent information," not "identical bytes." This pattern recurs across Fortran codebases. VIT enhancement: `vit verify --diagnostic` uses a configurable comparison spec (which columns to compare, which to ignore, tolerance for numeric fields, format-agnostic whitespace handling).

- `Debug` — formatted text output to debug files

**Platform modules (direct translation + simulation regression):**

| Module | Lines | C++ Approach |
|--------|-------|-------------|
| SysSubs | ~100 | `dlopen`/`dlsym` on Linux, `LoadLibrary`/`GetProcAddress` on Windows |
| ExtControl | 137 | Thin wrapper around dlopen |
| ZeroMQInterface | 84 | Direct C++ ZMQ bindings (libzmq or cppzmq) |

**VIT I/O verification capabilities (build first, then use):**
- [ ] File pre-staging in `vit extract` — capture file dependencies alongside function state
- [ ] `vit verify --io` mode — output file comparison for file writers
- [ ] `vit verify --diagnostic` mode — structured diff with configurable comparison spec (columns to compare/ignore, numeric tolerance, whitespace handling)
- [ ] `vit.yaml` configuration for I/O modes (`io_mode: file_reader | file_writer | diagnostic`, `file_dependencies`, `output_files`, `comparison_spec`)

**Translations:**
- [ ] Translate `ReadAvrSWAP` (Category 1 — standard pipeline)
- [ ] Translate helper functions: `ParseDbAry`, `ParseInAry`, `FindLine`, `GetWords`, etc. (Category 1)
- [ ] Translate `ReadControlParameterFileSub` (Category 2 — file pre-staging)
- [ ] Translate `ReadCpFile` (Category 2)
- [ ] Translate `ReadRestartFile` (Category 2)
- [ ] Translate `WriteRestartFile` (Category 3 — output file comparison)
- [ ] Translate `Debug` (Category 4 — structured diff)
- [ ] Translate `SysSubs` (platform — simulation regression)
- [ ] Translate `ExtControl` (platform — simulation regression)
- [ ] Translate `ZeroMQInterface` (platform — simulation regression)
- [ ] Full simulation regression after each module

### Phase 11: Entry Point and Final Assembly
**Target: DISCON.F90 → pure C++ libdiscon.so**

- [ ] Translate `DISCON` — 162-line orchestrator: initialization, parameter reading, controller dispatch. By this phase, everything it calls is already C++.
- [ ] Remove Fortran from build — CMakeLists.txt becomes C++-only. No more `gfortran`.
- [ ] Full simulation regression — run the complete wind turbine simulation and confirm byte-identical output to the original pure-Fortran baseline.
- [ ] Binary compatibility — verify the C++ `libdiscon.so` works as a drop-in replacement: same symbol names, same calling convention (Bladed DLL interface).

### Phase 12: Generalization Beyond ROSCO
**Target: VIT as a general-purpose tool**

- [ ] Abstract kind aliases — support arbitrary `kind_aliases` configuration, not just ROSCO's ReKi/DbKi/IntKi
- [ ] Build system support — add support for Make, Autotools, and other build systems beyond CMake
- [ ] Documentation — user guide, API reference, tutorial for new codebases
- [ ] Packaging — PyPI distribution, Docker image, CI/CD
- [ ] Test on another codebase — validate VIT on a second Fortran project to prove generality

---

## 6. VIT Technical Debt

Issues that should be addressed during the enhancement roadmap. These are not blocking today's verified functions but will cause problems as scope expands.

### Silent Breakage Risks

| Issue | Impact | Fix Phase |
|-------|--------|-----------|
| 2D assumed-shape arrays generate single `n_` size param | Wrong C interface for DIMENSION(:,:) | Phase 5 |
| ALLOCATABLE args parsed as regular arrays | Wrong size semantics | Phase 4 |
| CHARACTER function results assume numeric kind conversion | Would generate broken wrapper | Phase 4 |
| ELEMENTAL/PURE/RECURSIVE prefixes not recognized | Function declaration not matched by parser | Phase 7 |
| Fortran/C struct memory layout mismatch | Fields at wrong offsets if compiler pads differently | Phase 4 |
| Column-major arrays passed to C++ without transposition | C++ computes wrong results with Fortran-layout matrices | Phase 4 |
| No I/O verification capability | File readers/writers/diagnostics cannot be verified through existing pipeline | Phase 10 |

### Correctness Issues

| Issue | Impact | Fix Phase |
|-------|--------|-----------|
| INTENT(OUT) arrays in subroutines may get wrong intent in interface | Could generate read-only when write is needed | Phase 2 |
| Derived type registry not wired into CLI | All TYPE(...) defaults to passthrough — wrong for BIND(C) types. Passthrough is not the intended pattern; all types become BIND(C) structs | Phase 3/4 |
| Subroutine codepath untested end-to-end | Latent bugs in subroutine interface/verify/integrate | Phase 2 |

### Missing Guards

| Issue | Impact | Fix Phase |
|-------|--------|-----------|
| No error on unsupported array ranks | Silent wrong code generation | Phase 5 |
| No warning for ALLOCATABLE/POINTER args | User doesn't know VIT is generating wrong code | Phase 3 |
| No detection of inter-function dependencies | Translated function calling untranslated function | Phase 7 |

---

## 7. Meeting Preparation

### What to Demo

The existing interp1d demo (validated 2026-03-17) shows the complete VIT pipeline:
1. Read Fortran source → understand the function
2. Write C++ translation
3. Extract runtime state from a real wind turbine simulation (20 calls captured)
4. Verify bit-identical output (40/40 cases passed)
5. Integrate into the codebase (patch Fortran + CMake)
6. Rebuild and confirm byte-identical simulation output

### Key Talking Points

- **VIT verifies, it doesn't translate.** Humans or LLMs write the C++; VIT proves it's correct against real runtime data and integrates it into the build.
- **Current coverage**: 3/14 Functions.f90 functions verified (ErrVar-free). These 3 are ready to integrate in Phase 1.
- **Clear phased roadmap**: 3 → 7 → 8 → 13 Functions.f90 functions (1 dead code), then Filters, Controllers, ControllerBlocks, I/O, and finally the entry point.
- **Verification against reality**: Not synthetic test cases — actual runtime data from the NREL 5MW reference turbine simulation.
- **Incremental and reversible**: Each function is verified and integrated independently. The mixed Fortran/C++ codebase works at every step.
- **Full ROSCO is the goal**: Every module, every function, culminating in a pure C++ `libdiscon.so`.

### Questions to Expect

| Question | Answer |
|----------|--------|
| "How long to translate all of ROSCO?" | Functions.f90 (14 functions) could be complete in weeks. Full ROSCO depends on type system work — months, not years. |
| "What about numerical precision?" | VIT verifies to 1e-14 tolerance (effectively bit-identical for double precision). Every translated function is proven against real simulation data. |
| "Can it handle our other Fortran codes?" | VIT is designed to generalize. ROSCO is the first target; Phase 12 addresses other codebases. The core pipeline (extract → verify → integrate) is codebase-agnostic. |
| "Why not just use an AI to auto-translate?" | LLMs can write the C++ (and often do). VIT's value is *proving* the translation is correct — which no LLM can guarantee — and automating the integration into the build. |
