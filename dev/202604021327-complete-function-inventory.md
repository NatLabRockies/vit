# Complete ROSCO Function Inventory: What's Translated, What's Missing, What's Dead

**Date:** 2026-04-02
**Status:** completed
**Scope:** Exhaustive accounting of every Fortran function in ROSCO — translated vs. untranslated vs. dead code vs. absorbed — with corrections to the earlier survey

## Why This Matters

Before starting Stage C, we asked: "Are there functions missing from the roadmap?" An earlier survey (from the Stage C readiness assessment) identified 5 missing functions: PreFilterMeasuredSignals, CPfunction, matinv3, unwrap, and BladedDLL_Legacy_Procedure. But that survey missed an entire source file (`ROSCO_Helpers.f90` with ~20 functions) and didn't verify whether the "missing" functions were actually called.

This dev note corrects the record with a rigorous, exhaustive inventory.

## Methodology

1. Listed all `.f90` and `.F90` files in `rosco/controller/src/` (15 files total)
2. Grep'd every file for `SUBROUTINE` and `FUNCTION` declarations
3. Cross-referenced against all `.cpp` files in `translations/`
4. For each "missing" function, grep'd the entire codebase for call sites to determine if it's actually used
5. Classified each function into: translated, untranslated (in plan), untranslated (not in plan), dead code, or absorbed

## All Fortran Source Files (15 total)

| File | Contains | Functions |
|------|----------|-----------|
| Controllers.f90 | Controller subroutines | 14 (all translated) |
| ControllerBlocks.f90 | Controller block subroutines | 9 (all translated) |
| Functions.f90 | Math/utility functions | 13 total (10 translated, 2 dead, 1 inlined) |
| Filters.f90 | Filter functions + orchestrator | 7 total (6 translated, 1 not in plan) |
| ReadSetParameters.f90 | Parameter reading/validation | 5 total (4 translated, 1 in Stage E) |
| ROSCO_IO.f90 | Restart I/O + debug logging | 3 (none translated, all in Stage C) |
| DISCON.F90 | Main entry point | 1 (not translated, Phase 11) |
| ExtControl.f90 | External controller interface | 2 (1 abstract interface, 1 in Stage D) |
| ZeroMQInterface.f90 | ZeroMQ communication | 2 (1 Fortran subroutine in Stage D, 1 C interface binding) |
| ROSCO_Helpers.f90 | I/O parsing helpers | ~20 (none individually translated — absorbed into idiomatic C++) |
| ROSCO_Types.f90 | Type definitions only | 0 functions |
| Constants.f90 | Constants only | 0 functions |
| SysFiles/*.f90 | Platform-specific DLL loading | ~3 per platform × 7 platforms (absorbed into Stage D) |
| vit_*_view.f90 | VIT-generated view populators | Not ROSCO functions |
| vit_math_bridge.f90 | VIT-generated math bridges | Not ROSCO functions |

## Complete Function-by-Function Status

### Controllers.f90 — 14 functions, ALL TRANSLATED

| Function | Translation | Status |
|----------|------------|--------|
| PitchControl | translations/Controllers/pitchcontrol.cpp | KGen-verified |
| VariableSpeedControl | translations/Controllers/variablespeedcontrol.cpp | KGen-verified |
| YawRateControl | translations/Controllers/yawratecontrol.cpp | KGen-verified |
| IPC | translations/Controllers/ipc.cpp | KGen-verified |
| ForeAftDamping | translations/Controllers/foreaftdamping.cpp | KGen-verified |
| FloatingFeedback | translations/Controllers/floatingfeedback.cpp | KGen-verified |
| FlapControl | translations/Controllers/flapcontrol.cpp | KGen-verified |
| ActiveWakeControl | translations/Controllers/activewakecontrol.cpp | KGen-verified |
| CableControl | translations/Controllers/cablecontrol.cpp | KGen-verified |
| StructuralControl | translations/Controllers/structuralcontrol.cpp | KGen-verified |
| PIController | translations/Controllers/picontroller.cpp | KGen-verified |
| PIDController | translations/Controllers/pidcontroller.cpp | KGen-verified |
| PIIController | translations/Controllers/piicontroller.cpp | KGen-verified |
| ResController | translations/Controllers/rescontroller.cpp | KGen-verified |

### ControllerBlocks.f90 — 9 functions, ALL TRANSLATED

| Function | Translation | Status |
|----------|------------|--------|
| PowerControlSetpoints | translations/ControllerBlocks/powercontrolsetpoints.cpp | KGen-verified |
| ComputeVariablesSetpoints | translations/ControllerBlocks/computevariablessetpoints.cpp | KGen-verified |
| StateMachine | translations/ControllerBlocks/statemachine.cpp | KGen-verified |
| WindSpeedEstimator | translations/ControllerBlocks/windspeedestimator.cpp | KGen-verified |
| SetpointSmoother | translations/ControllerBlocks/setpointsmoother.cpp | KGen-verified |
| PitchSaturation | translations/ControllerBlocks/pitchsaturation.cpp | KGen-verified |
| Startup | translations/ControllerBlocks/startup.cpp | KGen-verified |
| Shutdown | translations/ControllerBlocks/shutdown.cpp | KGen-verified |
| RefSpeedExclusion | translations/ControllerBlocks/refspeedexclusion.cpp | KGen-verified |

### Functions.f90 — 13 functions, 10 TRANSLATED + 2 DEAD + 1 INLINED

| Function | Translation | Status |
|----------|------------|--------|
| saturate | translations/Functions/saturate.cpp | KGen-verified |
| ratelimit | translations/Functions/ratelimit.cpp | KGen-verified |
| interp1d | translations/Functions/interp1d.cpp | KGen-verified |
| interp2d | translations/Functions/interp2d.cpp | KGen-verified |
| identity | translations/Functions/identity.cpp | KGen-verified |
| ColemanTransform | translations/Functions/colemantransform.cpp | KGen-verified |
| ColemanTransformInverse | translations/Functions/colemantransforminverse.cpp | KGen-verified |
| AeroDynTorque | translations/Functions/aerodyntorque.cpp | KGen-verified |
| wrap_180 | translations/Functions/wrap_180.cpp | KGen-verified |
| wrap_360 | translations/Functions/wrap_360.cpp | KGen-verified |
| sigma | translations/Functions/sigma.cpp | KGen-verified |
| **matinv3** | — | **DEAD CODE** (see below) |
| **CPfunction** | — | **DEAD CODE** (see below) |
| **unwrap** | — | **INLINED** (see below) |

### Filters.f90 — 7 functions, 6 TRANSLATED + 1 NOT IN PLAN

| Function | Translation | Status |
|----------|------------|--------|
| LPFilter | translations/Filters/lpfilter.cpp | KGen-verified |
| SecLPFilter | translations/Filters/seclpfilter.cpp | KGen-verified |
| SecLPFilter_Vel | translations/Filters/seclpfilter_vel.cpp | KGen-verified |
| HPFilter | translations/Filters/hpfilter.cpp | KGen-verified |
| NotchFilterSlopes | translations/Filters/notchfilterslopes.cpp | KGen-verified |
| NotchFilter | translations/Filters/notchfilter.cpp | KGen-verified |
| **PreFilterMeasuredSignals** | — | **NOT IN PLAN** (see below) |

### ReadSetParameters.f90 — 5 functions, 4 TRANSLATED + 1 IN PLAN

| Function | Translation | Status |
|----------|------------|--------|
| ReadAvrSWAP | translations/ReadSetParameters/readavrswap.cpp | KGen-verified |
| ReadControlParameterFileSub | translations/ReadSetParameters/readcontrolparameterfilesub.cpp | Simulation-verified |
| ReadCpFile | translations/ReadSetParameters/readcpfile.cpp | Simulation-verified |
| CheckInputs | translations/ReadSetParameters/checkinputs.cpp | Simulation-verified |
| **SetParameters** | — | Stage E (in plan) |

### ROSCO_IO.f90 — 3 functions, NONE TRANSLATED (Stage C)

| Function | Status |
|----------|--------|
| WriteRestartFile | Stage C (in plan) |
| ReadRestartFile | Stage C (in plan) |
| Debug | Stage C (in plan) |

### DISCON.F90 — 1 function, NOT TRANSLATED (Phase 11)

| Function | Status |
|----------|--------|
| DISCON | Phase 11 — main entry point, last to translate |

### ExtControl.f90 — 2 items

| Function | Status |
|----------|--------|
| BladedDLL_Legacy_Procedure | Abstract INTERFACE definition — not a function body, doesn't need translation |
| ExtController | Stage D (in plan) |

### ZeroMQInterface.f90 — 2 items

| Function | Status |
|----------|--------|
| UpdateZeroMQ | Stage D (in plan) |
| zmq_client | C interface binding (BIND(C)) — declaration only, wraps existing C code |

### ROSCO_Helpers.f90 — ~20 functions, ALL ABSORBED

These are Fortran I/O/parsing helper functions. None need individual translation — their functionality is replaced by idiomatic C++ equivalents:

| Fortran Helper(s) | C++ Replacement | Used By |
|-------------------|-----------------|---------|
| ParseInput_Int, ParseInput_Dbl, ParseInput_Str (+ _Opt variants) | DisconParser class (readcontrolparameterfilesub.cpp) | ReadControlParameterFileSub |
| ParseDbAry, ParseInAry (+ _Opt variants) | DisconParser class | ReadControlParameterFileSub |
| ChkParseData, FindLine, ReadEmptyLine, GetWords | DisconParser internals | ReadControlParameterFileSub |
| GetPath, GetRoot, PathIsRelative | `<filesystem>` | ReadControlParameterFileSub, SetParameters |
| Read_OL_Input | Inline CSV reader in readcontrolparameterfilesub.cpp | ReadControlParameterFileSub |
| GetNewUnit | `std::fstream` (no unit number needed) | ROSCO_IO functions |
| CurTime, CurDate | `<chrono>` / `<ctime>` | Debug |
| NonDecreasing | Inlined in checkinputs.cpp | CheckInputs |
| Conv2UC | `std::toupper` | ReadControlParameterFileSub |
| Int2LStr | `std::to_string` | Error messages |
| AddToList | `std::vector::push_back` | CheckInputs |
| Cleanup (internal) | RAII / destructors | Various |

**The plan mentions SysSubs being "absorbed into other modules" (Stage D) but does not mention ROSCO_Helpers.f90.** This is the biggest gap in the plan — it's an entire module with ~20 functions that needs explicit acknowledgment as "absorbed, no individual translation needed."

### SysFiles/ — Platform-specific DLL loading, ABSORBED

| Functions | Status |
|-----------|--------|
| LoadDynamicLib, LoadDynamicLibProc, FreeDynamicLib | 7 platform variants (GnuLinux, LLVMLinux, FlangLinux, GnuWin, IVF, LLVMWin, IFL). All absorbed into Stage D ext_control.cpp via C++ `dlopen`/`dlsym` |

## Deep Dive: The "Missing" Functions

### matinv3 — DEAD CODE

**Definition:** Functions.f90:304 — 3x3 matrix inverse using cofactor expansion.

**Call sites:** Zero. Grep'd the entire `rosco/controller/src/` directory — only hits are the definition itself and a comment in the module header (line 24). Not called from WindSpeedEstimator (the EKF uses `vit_ekf_update` which handles the matrix math in C++ via the math bridge), not called from anywhere else.

**Verdict:** Dead code. Can be dropped entirely in the C++ version. No translation needed.

### CPfunction — DEAD CODE

**Definition:** Functions.f90:396 — Parameterized Cp(lambda) curve: `exp(-CP(1)/lambda)*(CP(2)/lambda-CP(3))+CP(4)*lambda`, clamped via `saturate()`.

**Call sites:** Zero. Grep'd the entire source directory — only hits are the definition itself. ROSCO uses `interp2d` with pre-computed Cp tables (from ReadCpFile) instead of this parametric function. The `WE_CP` field in ROSCO_Types.f90 (which holds the CP parameters) is defined but never populated by ReadControlParameterFileSub.

**Verdict:** Dead code — a legacy parametric model superseded by the table-lookup approach. Can be dropped. No translation needed.

### unwrap — ALREADY INLINED IN TRANSLATIONS

**Definition:** Functions.f90:518 — Unwraps an array of angles by adding/subtracting 2*PI to keep consecutive differences in [-PI, PI].

**Call sites:**
1. Controllers.f90:339 — inside PIDController's OL_Mode=2 azimuth tracking branch: `LocalVar%AzBuffer = UNWRAP(LocalVar%AzBuffer, ErrVar)`
2. ReadSetParameters.f90:811 — inside ReadControlParameterFileSub: `CntrPar%OL_Azimuth = Unwrap(CntrPar%OL_Channels(:,CntrPar%Ind_Azimuth),ErrVar)`

**How it's handled in C++:**
- `variablespeedcontrol.cpp:150` — Comment says "Inline 2-element unwrap (equivalent to Fortran UNWRAP function)". The unwrap logic is inlined directly.
- `readcontrolparameterfilesub.cpp:66` — `static void unwrap(double* arr, int n)` — local static function implementing the same algorithm.

**Verdict:** Both call sites already have working C++ implementations. No standalone translation needed. In the pure C++ phase (Phase 11), this could be extracted into a shared utility header if desired, but it's not blocking anything.

### PreFilterMeasuredSignals — NOT IN ANY STAGE

**Definition:** Filters.f90:325-439 — Orchestrator subroutine that applies all signal pre-filtering (LPFilter, SecLPFilter, NotchFilter, NotchFilterSlopes) to measured signals (rotor speed, generator speed, pitch angles, etc.).

**Call site:** DISCON.F90:96 — `CALL PreFilterMeasuredSignals(CntrPar, LocalVar, DebugVar, objInst, ErrVar)`

**Why it's not in the plan:** It's an orchestrator like SetParameters — it doesn't implement any algorithm itself, just calls the already-translated filter functions in sequence. The plan has SetParameters in Stage E but doesn't mention PreFilterMeasuredSignals.

**Arguments:** `CntrPar` (ControlParameters, view type), `LocalVar` (LocalVariables, bind_c), `DebugVar` (DebugVariables, bind_c), `objInst` (ObjectInstances, bind_c), `ErrVar` (ErrorVariables, bind_c).

**Complexity:** Medium. It's ~115 lines of orchestration — reading mode flags from CntrPar, calling filters with the right parameters, storing results in LocalVar. No file I/O, no ALLOCATE, no complex data structures. All callees are already translated.

**Options for where to slot it:**
1. **Stage E** (alongside SetParameters) — both are orchestrators
2. **Stage C** (as an addition) — it's in Filters.f90, conceptually adjacent
3. **Phase 11** (DISCON translation) — fold it into the DISCON rewrite

## Summary: What the Plan Needs

### Functions genuinely needing translation (not yet in plan):

| Function | Recommended Stage | Rationale |
|----------|-------------------|-----------|
| PreFilterMeasuredSignals | Stage E (with SetParameters) | Both are orchestrators that sequence already-translated functions |

### Modules needing explicit plan acknowledgment (absorbed, not translated):

| Module | Acknowledgment Needed |
|--------|----------------------|
| ROSCO_Helpers.f90 (~20 functions) | "Absorbed into idiomatic C++ equivalents — no individual translation" |
| SysFiles/ (platform DLL loading) | Already partially mentioned in Stage D SysSubs note |

### Functions confirmed as dead code (can be dropped):

| Function | Evidence |
|----------|----------|
| matinv3 | Zero call sites in entire codebase |
| CPfunction | Zero call sites; superseded by interp2d table-lookup |

### Functions confirmed as already handled (no action needed):

| Function | How Handled |
|----------|-------------|
| unwrap | Inlined in variablespeedcontrol.cpp and readcontrolparameterfilesub.cpp |
| BladedDLL_Legacy_Procedure | Abstract interface, not a function body |
| zmq_client | C interface binding declaration, wraps existing C code |

## Corrected Totals

- **Total ROSCO functions (excluding VIT infrastructure, interface declarations, and Cleanup internals):** ~67
- **Translated:** 44
- **In plan (Stages C-E + Phase 11):** 7 (WriteRestartFile, ReadRestartFile, Debug, ExtController, UpdateZeroMQ, SetParameters, DISCON)
- **Not in plan but needs a stage:** 1 (PreFilterMeasuredSignals)
- **Absorbed (no individual translation):** ~23 (ROSCO_Helpers ~20 + SysFiles ~3)
- **Dead code (drop):** 2 (matinv3, CPfunction)
- **Already handled (inlined):** 1 (unwrap)
- **Not a function (interface/binding):** 2 (BladedDLL_Legacy_Procedure, zmq_client)

**44 translated + 8 to translate + ~23 absorbed + 2 dead + 1 inlined + 2 not-functions = ~80 items accounted for.** This matches the grep output (accounting for SysFiles having multiple platform copies of the same 3 functions).
