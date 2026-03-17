# PROJECT PLAN: Full ROSCO Fortran → C++ Translation

## 1. Vision and Current State

### Vision

Fully translate the ROSCO wind turbine controller from Fortran to C++, producing a drop-in replacement `libdiscon.so` that is pure C++ and produces byte-identical simulation output to the Fortran original. VIT orchestrates the verification and integration; humans or LLMs write the C++ translations.

### Current State (2026-03-17)

- **Verified translations**: 4 of 14 Functions.f90 functions (`saturate`, `wrap_180`, `wrap_360`, `interp1d`)
- **Integrated**: 3 of those 4 (`wrap_360` verified but not yet integrated)
- **Pipeline**: End-to-end extract → verify → integrate → rebuild → diff confirmed working today
- **VIT codebase**: ~4,100 lines of core code, 207 unit tests, 10 modules

### Total Scope

ROSCO contains ~65 functions and subroutines across 11 modules (~8,600 lines of Fortran), plus type definitions (544 lines), constants, platform abstractions, and the DLL entry point. All of it must become C++.

---

## 2. Functions.f90 — Function-by-Function Assessment

Functions.f90 is the primary target module: 14 utility functions used throughout the controller. This is where VIT has proven itself and where the next wave of translations will come from.

### Already Done

| Function | Lines | Description | Status |
|----------|-------|-------------|--------|
| `saturate` | 11 | Min/max clamp (3 scalars → scalar) | Verified + integrated |
| `wrap_180` | 12 | Angle wrap to ±180° (scalar → scalar) | Verified + integrated |
| `wrap_360` | 12 | Angle wrap to 0–360° (scalar → scalar) | Verified, needs integration |
| `interp1d` | 58 | 1D linear interpolation (2 arrays + scalar + ErrVar passthrough → scalar) | Verified + integrated |

### Ready Now — No VIT Changes Needed

| Function | Lines | Description | Why It Works Today |
|----------|-------|-------------|-------------------|
| `sigma` | 34 | Sigmoid/cubic ramp (5 scalars + ErrVar passthrough → scalar) | Same pattern as interp1d minus arrays: all scalar args, ErrorVariables is passthrough |
| `CPfunction` | 13 | Parameterized Cp(λ) (fixed `CP(4)` array + scalar → scalar) | Fixed-size 1D array `CP(4)` has explicit dimensions — no assumed-shape complexity. Calls `saturate` internally but translation only implements the math |

### Minor VIT Work Required

| Function | Lines | Description | What VIT Needs |
|----------|-------|-------------|---------------|
| `matinv3` | 22 | 3×3 matrix inverse (explicit `A(3,3)` → explicit `B(3,3)`) | Fixed-size 2D array args and array-valued return. No derived types, pure math. VIT needs explicit-dimension array support in interface gen |
| `identity` | 16 | n×n identity matrix (scalar `n` → `A(n,n)`) | Runtime-dimensioned array return. VIT needs support for result arrays sized by input args |
| `ColemanTransform` | 18 | Coleman d-q transform (SUBROUTINE: fixed `rootMOOP(3)` + scalars → INTENT(OUT) scalars) | First SUBROUTINE target. VIT has subroutine codepath in interface_gen.py but it's untested in the full pipeline. Fixed-size array input is straightforward |
| `ColemanTransformInverse` | 19 | Inverse Coleman (SUBROUTINE: scalars → INTENT(OUT) fixed `PitComIPC(3)`) | SUBROUTINE with INTENT(OUT) fixed-size array. Same requirements as ColemanTransform plus output array handling |
| `unwrap` | 40 | Phase unwrapping (assumed-shape `x(:)` + ErrVar → array `DIMENSION(SIZE(x))`) | Array-valued return where result size depends on input array. VIT needs to support array return type in interface gen |

### Major VIT Work Required

| Function | Lines | Description | What VIT Needs |
|----------|-------|-------------|---------------|
| `ratelimit` | 37 | Rate limiter with state (8 args including `rlP` TYPE(rlParams) INOUT + OPTIONAL arg → scalar) | Load-bearing derived type: writes `rlP%LastSignal(inst)`. The derived type is not passthrough — the C++ code must access its fields. Also has OPTIONAL argument. Requires derived type struct mapping and OPTIONAL support |
| `interp2d` | 172 | 2D bilinear interpolation (3 arrays including `zData(:,:)` + 2 scalars + ErrVar → scalar) | 2D assumed-shape array `DIMENSION(:,:)` — VIT currently generates a single `n_` size param which is wrong for 2D. Also calls `interp1d` internally, uses `ieee_arithmetic` module |
| `AeroDynTorque` | 39 | Aerodynamic torque (2 scalars + 4 derived types → scalar) | Four derived type args: `LocalVariables` (reads `WE_Vw`), `ControlParameters` (reads `WE_BladeRadius`, `WE_RhoAir`), `PerformanceData` (reads `Beta_vec`, `TSR_vec`, `Cp_mat`), `ErrorVariables` (passthrough). The first three are NOT passthrough — C++ needs their field values. Calls `interp2d`. Requires derived type field extraction or struct mapping |

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

Every ROSCO module depends on ROSCO_Types. The types are the keystone of the entire translation effort. Deep investigation (2026-03-17) determined that **all 12 types can become C structs** — no opaque pointers, no accessor APIs, no deferred work. Phase 4.5 solves the entire type system in one shot.

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
| `WE` | 7 | 4 scalars + `DIMENSION(3,3)` + 2× `DIMENSION(3,1)`. Note: Fortran column-major layout. |
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

**ControlParameters — fixed-size arrays replacing ALLOCATABLE (213 fields):**

Investigation overturned the earlier "opaque pointer" assumption:

1. **Truly read-only**: Every access in Controllers, Filters, and ControllerBlocks is read-only. No algorithmic function writes to CntrPar. `INTENT(INOUT)` in signatures is legacy.
2. **All ALLOCATABLE arrays allocated once at init**: Sizes come from config file scalars (`PC_GS_n`, `VS_n`, `F_NumNotchFilts`, etc.) and never change.
3. **Sizes are bounded and small**: Gain schedules 20–50 entries, notch filters 0–10, performance tables 36×26, open-loop timeseries 100–1000.

**Strategy**: Replace each ALLOCATABLE with a fixed-size array using a compile-time max bound:
```c
#define MAX_GS_POINTS 100
#define MAX_NOTCH_FILTERS 15
#define MAX_OL_POINTS 2000
#define MAX_PERF_PITCH 60
#define MAX_PERF_TSR 60
// etc.
```

Each array keeps its existing size scalar (e.g., `PC_GS_n`) to indicate how many elements are valid. The 135 scalar fields and ~55 INTEGER fields are already C-compatible. The 4 CHARACTER fields become `char[1024]`.

Total struct size ~500 KB with generous bounds — large but allocated once and passed by pointer.

**PerformanceData — merge into ControlParameters or use fixed-size arrays (5 fields):**

All fields are ALLOCATABLE lookup tables allocated once from `Cp_Ct_Cq.NREL5MW.txt`:
- `TSR_vec(:)` — typically 26 entries
- `Beta_vec(:)` — typically 36 entries
- `Cp_mat(:,:)`, `Ct_mat(:,:)`, `Cq_mat(:,:)` — typically 26×36

Never modified after init. Strategy: fixed-size arrays with max bounds (`double Cp_mat[MAX_PERF_TSR][MAX_PERF_PITCH]`), same pattern as ControlParameters. Can be kept as a separate struct or merged into ControlParameters (which already holds `PerfTableSize`).

**ErrorVariables — passthrough + simple C error args (3 fields + 1 ALLOCATABLE):**

Critical finding: **ErrVar is purely for error reporting. It never affects computed control outputs.** The pattern everywhere:
1. Function sets `aviFAIL = -1` and writes `ErrMsg`
2. Caller checks `aviFAIL < 0` and propagates
3. Error data flows *out* to the simulator, never *into* the algorithm

Strategy for complete translation:
- `aviFAIL` and `ErrStat` are already `INTEGER(C_INT)` — directly C-compatible.
- `ErrMsg`: Replace `CHARACTER(:), ALLOCATABLE` with `CHARACTER(1024)`. The DLL interface already uses a 1024-char buffer. No observed error message exceeds ~500 chars.
- For C++ functions that need error reporting: pass `int* aviFAIL` and `char* ErrMsg` as separate C args. The Fortran wrapper maps these to/from the ErrVar struct.
- For functions that don't touch errors: continue passthrough pattern.

**Remaining types (handled in Phase 9):**

| Type | Fields | Strategy |
|------|--------|----------|
| `ExtDLL_Type` | 4 | Already uses `C_PTR`, `C_FUNPTR`, `CHARACTER(1024)` — excellent BIND(C) candidate. |
| `ExtControlType` | 1 | Single ALLOCATABLE `avrSWAP` array. Replace with fixed-size or pointer+size. |

### Timeline

- **Phase 4.5**: All type changes applied. C++ struct headers generated. BIND(C) attributes added to Fortran types. Memory layout validated.
- **Phases 5–7**: All functions receive full structs by pointer. C++ accesses fields naturally.
- **Phase 8**: C++ becomes source of truth for type definitions. Fortran types become thin BIND(C) wrappers.

---

## 5. VIT Enhancement Roadmap

Each phase unlocks new translation targets. Phases are ordered by dependency — each builds on the capabilities added by previous phases.

### Phase 1: Low-Hanging Fruit — No VIT Changes
**Target: 7/14 Functions.f90**

- [ ] Integrate `wrap_360` — already verified, run `vit integrate wrap_360 ...`
- [ ] Translate + verify + integrate `sigma` — all scalar + ErrorVariables passthrough, same pattern as existing verified functions
- [ ] Translate + verify + integrate `CPfunction` — fixed `CP(4)` array + scalar, calls `saturate` internally but translation just implements the exponential-polynomial formula
- [ ] Run full verification regression (all 7 functions pass)
- [ ] Update `vit.yaml` translations section

**VIT changes**: None.
**Effort estimate**: Small — writing 3 C++ translations and running the pipeline.

### Phase 2: Fixed-Size Arrays + Subroutines
**Target: 11/14 Functions.f90**

- [ ] Explicit-dimension array support — `A(3,3)` in Fortran → `double* A` with known size in C. Interface gen must recognize `DIMENSION(3,3)` vs `DIMENSION(:)` and handle accordingly. No synthetic size param needed — the size is compile-time known.
- [ ] Subroutine pipeline validation — VIT's interface_gen.py has subroutine support (BIND(C) SUBROUTINE, INTENT(OUT) args) but it's never been tested end-to-end. Validate with ColemanTransform as the first real SUBROUTINE target.
- [ ] Array-valued function results — `matinv3` returns `B(3,3)`, `identity` returns `A(n,n)`. In C, array returns become pointer args. VIT needs to transform the interface: Fortran FUNCTION returning array → C function with output pointer parameter.
- [ ] Fix INTENT(OUT) array handling in subroutine interface generation
- [ ] Translate + verify + integrate `matinv3`
- [ ] Translate + verify + integrate `identity`
- [ ] Translate + verify + integrate `ColemanTransform`
- [ ] Translate + verify + integrate `ColemanTransformInverse`
- [ ] Translate + verify + integrate `unwrap`
- [ ] Run full verification regression (all 11 functions pass)

**New functions unlocked**: `matinv3`, `identity`, `ColemanTransform`, `ColemanTransformInverse`, `unwrap`

### Phase 3: 2D Assumed-Shape Arrays
**Target: 12/14 Functions.f90**

- [ ] Multi-dimensional assumed-shape — `DIMENSION(:,:)` must generate two size params (`n_rows`, `n_cols`), not one. Current code silently generates broken interfaces for 2D. Add rank detection and per-dimension size params.
- [ ] Guard against unsupported ranks — add explicit error for `DIMENSION(:,:,:)` and higher until supported.
- [ ] Translate + verify + integrate `interp2d`
- [ ] Run full verification regression (all 12 functions pass)

**New function unlocked**: `interp2d`

### Phase 4: Load-Bearing Derived Types
**Target: 14/14 Functions.f90 (module complete)**

- [ ] Derived type registry in CLI — wire the `derived_types` parameter from `get_type_mapping()` through the CLI pipeline. Currently hardcoded to None, defaulting all TYPE(...) to passthrough.
- [ ] BIND(C) struct generation for `rlParams` — auto-generate C struct from parsed TYPE block. `rlParams` is the simplest load-bearing type (1 fixed-size array), used by `ratelimit`.
- [ ] OPTIONAL argument support — `ratelimit` has `OPTIONAL :: ResetValue`. Interface gen must handle PRESENT() checks in the Fortran wrapper and default values in C.
- [ ] ControlParameters field extraction for `AeroDynTorque` — this function reads only 3 fields (`WE_BladeRadius`, `WE_RhoAir` from CntrPar, `WE_Vw` from LocalVar). Small enough for field extraction as a bridge until Phase 4.5. PerformanceData arrays (`Beta_vec`, `TSR_vec`, `Cp_mat`) passed as separate pointer+size args.
- [ ] Translate + verify + integrate `ratelimit`
- [ ] Translate + verify + integrate `AeroDynTorque`
- [ ] Run full verification regression (all 14 functions pass — Functions.f90 complete)

**New functions unlocked**: `ratelimit`, `AeroDynTorque`

### Phase 4.5: Complete Type System C Structs
**Target: C++ struct definitions for ALL types, unblocking Phases 5–7**

Deep investigation (2026-03-17) found that **every ROSCO type can become a C struct** — no opaque pointers or accessor APIs needed. This phase solves the entire type system in one shot.

**Already BIND(C)-compatible types (define C structs, no Fortran changes):**
- [ ] `FilterParameters` — 27 arrays of `double[1024]` (~221 KB). Used by all filters.
- [ ] `piParams` — 5 arrays of `double[1024]`. Used by PI/PID controllers.
- [ ] `resParams` — 4 arrays of `double[1024]`. Used by resonance controller.
- [ ] `rlParams` — 1 array of `double[1024]`. Already handled in Phase 4 for `ratelimit`.
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

**ControlParameters — replace ALLOCATABLE with fixed-size arrays, then define C struct:**
- [ ] Define compile-time max bounds for all 76 ALLOCATABLE arrays. Investigation confirmed all arrays are allocated once at init, never reallocated, with bounded sizes (gain schedules 20–50, notch filters 0–10, performance tables 36×26, open-loop timeseries 100–1000).
- [ ] Replace each `DIMENSION(:), ALLOCATABLE` with `DIMENSION(MAX_*)`. Size scalars (PC_GS_n, VS_n, etc.) remain to indicate valid element count.
- [ ] Replace 4 CHARACTER fields with `CHARACTER(1024)` (fixed-length).
- [ ] Verify ControlParameters is truly read-only in algorithmic code (investigation confirmed: INTENT(INOUT) is legacy, all accesses are reads).
- [ ] Define C++ struct. Total ~500 KB with generous bounds. Allocated once, passed by pointer.

**PerformanceData — same fixed-size pattern:**
- [ ] Replace ALLOCATABLE arrays with fixed-size: `TSR_vec(MAX_PERF_TSR)`, `Beta_vec(MAX_PERF_PITCH)`, `Cp_mat(MAX_PERF_TSR, MAX_PERF_PITCH)`, etc. Typical sizes: 26×36.
- [ ] Define C++ struct. Can remain separate or merge into ControlParameters.

**ErrorVariables — replace ALLOCATABLE ErrMsg, keep passthrough pattern:**
- [ ] Replace `CHARACTER(:), ALLOCATABLE :: ErrMsg` with `CHARACTER(1024)`. The DLL interface already uses a 1024-char buffer. No observed error message exceeds ~500 chars.
- [ ] `aviFAIL` and `ErrStat` are already `INTEGER(C_INT)` — no changes needed.
- [ ] For C++ functions that need error reporting: pass `int* aviFAIL` and `char* ErrMsg` as separate C args. Fortran wrapper maps to/from ErrVar struct.
- [ ] For functions that don't touch errors: continue passthrough.

**Validation:**
- [ ] Write memory layout test: compare field offsets between Fortran `BIND(C)` type and C++ struct to verify binary compatibility.
- [ ] Verify mixed Fortran/C++ build produces byte-identical simulation output after type modifications.
- [ ] Run full verification regression on all previously verified functions.

**Why this must come before Phase 5:** Filters access ~30 fields on FilterParameters, Controllers access ~70 fields on LocalVariables. With C structs, C++ accesses fields naturally (`state->lpf1_a1[inst]`) and the Fortran wrapper just passes the struct address.

### Phase 5: Filters Module
**Target: All of Filters.f90**

With Phase 4.5 complete, `FilterParameters` is a C struct passed by pointer. All filters receive it directly.

- [ ] Instance-based state access in C++ — filters use `FP%fieldname(inst)` pattern, which becomes `fp->fieldname[inst]` in C++. Validate this pattern works correctly across the BIND(C) boundary.
- [ ] Multi-state-type functions — `PreFilterMeasuredSignals` takes 5 derived type args (CntrPar, LocalVar, DebugVar, objInst, ErrVar). All are C structs (Phase 4.5) except ErrVar which continues as passthrough with `int* aviFAIL` + `char* ErrMsg` when C++ needs error reporting.
- [ ] Translate + verify + integrate `LPFilter`
- [ ] Translate + verify + integrate `SecLPFilter`
- [ ] Translate + verify + integrate `SecLPFilter_Vel`
- [ ] Translate + verify + integrate `HPFilter`
- [ ] Translate + verify + integrate `NotchFilterSlopes`
- [ ] Translate + verify + integrate `NotchFilter`
- [ ] Translate + verify + integrate `PreFilterMeasuredSignals`

**New functions unlocked**: `LPFilter`, `SecLPFilter`, `SecLPFilter_Vel`, `HPFilter`, `NotchFilter`, `NotchFilterSlopes`, `PreFilterMeasuredSignals`

### Phase 6: Controllers Module
**Target: All of Controllers.f90**

With Phase 4.5 complete, all types are C structs passed by pointer — `LocalVariables`, `ControlParameters`, `FilterParameters`, and all nested state types. Controllers read and write struct fields directly.

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

### Phase 7: ControllerBlocks Module
**Target: All of ControllerBlocks.f90**

With Phase 4.5 complete, `WE` nested struct is accessible through `LocalVariables`. WindSpeedEstimator can read/write `LocalVar.WE.P[3][3]`, `LocalVar.WE.xh[3]`, etc.

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

### Phase 8: Type System Finalization
**Target: C++ becomes source of truth for all types**

Phase 4.5 did the heavy lifting: all types are already C structs with BIND(C) attributes. Phase 8 completes the transition by making C++ the canonical owner of the type definitions.

- [ ] Move struct definitions from Fortran to C++ headers — C++ structs become the canonical definitions. Fortran types become thin `BIND(C)` wrappers that mirror the C++ layout.
- [ ] C++ constants header — `constexpr double PI = ...`, `using DbKi = double`, etc. Replace Constants.f90 as the source of truth.
- [ ] Remove fixed-size array workarounds if desired — ControlParameters and PerformanceData can optionally transition from fixed-max arrays to `std::vector<double>` now that C++ owns the types. (Only worthwhile if the fixed bounds cause practical issues.)
- [ ] Verify mixed Fortran/C++ build still produces byte-identical simulation output.

### Phase 9: I/O and Platform Modules
**Target: ReadSetParameters, ROSCO_Helpers, ROSCO_IO, SysSubs, ExtControl, ZeroMQInterface**

These modules are better translated directly (human or LLM rewrite) than through VIT's extract/verify pipeline. I/O and string-parsing logic doesn't have meaningful "runtime state" to capture — the value is in the logic, not in numerical correctness.

| Module | Lines | C++ Approach |
|--------|-------|-------------|
| ReadSetParameters | 1,804 | Structured config parser (key-value or YAML), avrSWAP array accessor |
| ROSCO_Helpers | 1,724 | Standard C++ string/file utilities, mostly `<fstream>`, `<string>`, `<sstream>` |
| ROSCO_IO | 1,199 | Binary I/O with `fread`/`fwrite`, text output with `<fstream>` |
| SysSubs | ~100 | `dlopen`/`dlsym` on Linux, `LoadLibrary`/`GetProcAddress` on Windows |
| ExtControl | 137 | Thin wrapper around dlopen — trivial |
| ZeroMQInterface | 84 | Direct C++ ZMQ bindings (libzmq or cppzmq) |

However, VIT's simulation regression test (baseline vs. integrated diff) is still essential for verifying that the translated I/O code produces identical behavior.

- [ ] Translate `ReadSetParameters` (5 subroutines, ~1,804 lines)
- [ ] Translate `ROSCO_Helpers` (~20 functions, ~1,724 lines)
- [ ] Translate `ROSCO_IO` (3 subroutines, ~1,199 lines)
- [ ] Translate `SysSubs` (3 functions, ~100 lines)
- [ ] Translate `ExtControl` (2 subroutines, 137 lines)
- [ ] Translate `ZeroMQInterface` (1 subroutine, 84 lines)
- [ ] Full simulation regression after each module

### Phase 10: Entry Point and Final Assembly
**Target: DISCON.F90 → pure C++ libdiscon.so**

- [ ] Translate `DISCON` — 162-line orchestrator: initialization, parameter reading, controller dispatch. By this phase, everything it calls is already C++.
- [ ] Remove Fortran from build — CMakeLists.txt becomes C++-only. No more `gfortran`.
- [ ] Full simulation regression — run the complete wind turbine simulation and confirm byte-identical output to the original pure-Fortran baseline.
- [ ] Binary compatibility — verify the C++ `libdiscon.so` works as a drop-in replacement: same symbol names, same calling convention (Bladed DLL interface).

### Phase 11: Generalization Beyond ROSCO
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
| 2D assumed-shape arrays generate single `n_` size param | Wrong C interface for DIMENSION(:,:) | Phase 3 |
| ALLOCATABLE args parsed as regular arrays | Wrong size semantics | Phase 4.5 |
| CHARACTER function results assume numeric kind conversion | Would generate broken wrapper | Phase 4.5 |
| ELEMENTAL/PURE/RECURSIVE prefixes not recognized | Function declaration not matched by parser | Phase 6 |
| Fortran/C struct memory layout mismatch | Fields at wrong offsets if compiler pads differently | Phase 4.5 |

### Correctness Issues

| Issue | Impact | Fix Phase |
|-------|--------|-----------|
| INTENT(OUT) arrays in subroutines may get wrong intent in interface | Could generate read-only when write is needed | Phase 2 |
| Derived type registry not wired into CLI | All TYPE(...) defaults to passthrough — wrong for BIND(C) types | Phase 4/4.5 |
| Subroutine codepath untested end-to-end | Latent bugs in subroutine interface/verify/integrate | Phase 2 |

### Missing Guards

| Issue | Impact | Fix Phase |
|-------|--------|-----------|
| No error on unsupported array ranks | Silent wrong code generation | Phase 3 |
| No warning for ALLOCATABLE/POINTER args | User doesn't know VIT is generating wrong code | Phase 4 |
| No detection of inter-function dependencies | Translated function calling untranslated function | Phase 6 |

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
- **Current coverage**: 4/14 Functions.f90 functions verified. 3 more achievable immediately with no tool changes.
- **Clear phased roadmap**: 7 → 11 → 12 → 14 Functions.f90 functions, then Filters, Controllers, ControllerBlocks, I/O, and finally the entry point.
- **Verification against reality**: Not synthetic test cases — actual runtime data from the NREL 5MW reference turbine simulation.
- **Incremental and reversible**: Each function is verified and integrated independently. The mixed Fortran/C++ codebase works at every step.
- **Full ROSCO is the goal**: Every module, every function, culminating in a pure C++ `libdiscon.so`.

### Questions to Expect

| Question | Answer |
|----------|--------|
| "How long to translate all of ROSCO?" | Functions.f90 (14 functions) could be complete in weeks. Full ROSCO depends on type system work — months, not years. |
| "What about numerical precision?" | VIT verifies to 1e-14 tolerance (effectively bit-identical for double precision). Every translated function is proven against real simulation data. |
| "Can it handle our other Fortran codes?" | VIT is designed to generalize. ROSCO is the first target; Phase 11 addresses other codebases. The core pipeline (extract → verify → integrate) is codebase-agnostic. |
| "Why not just use an AI to auto-translate?" | LLMs can write the C++ (and often do). VIT's value is *proving* the translation is correct — which no LLM can guarantee — and automating the integration into the build. |
