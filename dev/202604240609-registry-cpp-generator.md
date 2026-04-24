# Phase 0: Registry C++ Generator — Investigation and Implementation
**Date:** 2026-04-24
**Status:** completed
**Scope:** Phase 0 of PROJECT_PLAN_AERODYN.md — investigate OpenFAST Registry tool, answer OQ1 and OQ6, implement C++ code generator

## Summary

Extended the OpenFAST Registry tool with a `-cppcode` flag that generates C-compatible `ModuleName_Types.hpp` header files from the same `.txt` schema files used to generate Fortran `_Types.f90` files. All 10 AeroDyn headers (2,666 lines, 140 structs) compile cleanly with g++ C++17. Fortran output is unchanged (no regression).

## OQ1 Answer: Registry Generator Feasibility

**Verdict: Highly feasible.** The Registry is a clean C++ code generator (~3,200 lines total) with a modular architecture:

| File | Lines | Purpose |
|------|-------|---------|
| `registry.hpp` | 668 | Data model (Field, DataType, Module, DimSpec, Parameter) |
| `registry_gen_fortran.cpp` | 1,977 | Fortran output (types + Copy/Destroy/Pack/Unpack/ExtrapInterp/VarRoutines) |
| `registry_gen_c.cpp` | 132 | Rudimentary C header output (flat structs, TODO on derived types) |
| `registry_parse.cpp` | 310 | Parser for 10-field `.txt` schema format |
| `registry.cpp` | 40 | Dispatch (gen_module_files calls generators) |
| `main.cpp` | 197 | CLI entry point |

Adding a new generator is a natural extension — same pattern as the existing C generator. The data model already captures everything needed: POINTER vs ALLOCATABLE (`Field::is_pointer`, `Field::is_allocatable`), deferred dimensions, nested derived types, C type mappings (`DataType::c_type()`), interface types, parameters.

## OQ6 Answer: POINTER Fields in Registry Types

**Verdict: Manageable.** AeroDyn has only 5 POINTER field instances across all 10 Registry files:
- `FlowField` (FlowFieldType from InflowWind) — 3 instances
- `WaveField` (SeaSt_WaveFieldType from SeaState) — 1 instance  
- `FlowField` (FlowFieldType in AeroDyn_Inflow) — 1 instance

All are non-owning references to external module types (marked with `*` prefix in the Registry schema, setting `is_pointer=true`, `is_target=false`). In the Fortran generator:
- **Copy:** Non-target pointers use shallow association (`dst => src`), not deep copy
- **Destroy:** Nullified without deallocating (the pointer doesn't own the target)
- **Pack:** Uses `RegPackPointer` with `c_loc()` for global pointer tracking
- **Unpack:** Uses `RegUnpackPointer` with `c_f_pointer()` for resolution

In the C++ generator, all POINTER fields map to `void*` (opaque non-owning pointer). No target pointers (`&` prefix) exist in AeroDyn's Registry types.

## Implementation Details

### Files Modified

| File | Change |
|------|--------|
| `openfast/modules/openfast-registry/src/registry_gen_cpp.cpp` | **Created** — 230 lines, the C++ code generator |
| `openfast/modules/openfast-registry/src/registry.hpp` | Added `bool gen_cpp_code` flag and `gen_cpp_module()` declaration |
| `openfast/modules/openfast-registry/src/registry.cpp` | Added dispatch: `if (gen_cpp_code) gen_cpp_module()` |
| `openfast/modules/openfast-registry/src/main.cpp` | Added `-cppcode` CLI flag handling |
| `openfast/modules/openfast-registry/CMakeLists.txt` | Added new source file, bumped to C++17 |

### C++ Type Mapping

| Fortran Pattern | C++ Representation |
|----------------|-------------------|
| `INTEGER(IntKi)` scalar | `int32_t` |
| `REAL(ReKi)` scalar | `float` |
| `REAL(R8Ki/DbKi)` scalar | `double` |
| `LOGICAL` scalar | `int32_t` (4 bytes for BIND(C) compat) |
| `CHARACTER(N)` scalar | `char[N]` |
| Fixed array `DIMENSION(3)` | `float[3]` |
| Fixed multi-D `DIMENSION(3,3)` | `double[9]` (flattened, column-major) |
| Allocatable `DIMENSION(:)` | `float* x; int32_t x_Len;` |
| Nested derived type (AeroDyn family) | `TypeName x;` (by value) |
| Allocatable array of structs | `TypeName* x; int32_t x_Len;` |
| POINTER field | `void* x;` (non-owning) |
| External module type (MeshType, etc.) | `void* x;` (opaque) |
| Allocatable CHARACTER array | `char* x; int32_t x_Len; int32_t x_StrLen;` |

### Design Decisions

1. **`LOGICAL` uses `int32_t`, not `bool`** — Fortran `LOGICAL` is 4 bytes with gfortran. Using C++ `bool` (1 byte) would break BIND(C) struct layout alignment.

2. **Multi-D fixed arrays flatten to 1D** — Fortran column-major layout preserved. A comment documents the original dimensions. C++ code accesses using `arr[col * nrows + row]`.

3. **External types are opaque** — Types from NWTC Library, InflowWind, SeaState, GridInterp stay Fortran. The C++ struct holds `void*` so data passes through without C++ needing internal layout. A hardcoded set of external module names (`external_modules`) determines opacity.

4. **Include detection scans type dependencies, not `use_modules`** — The Registry's `use_modules` list misses transitive dependencies (e.g., UnsteadyAero parsed at `recurse_level=2` via BEMT's usefrom isn't in `use_modules`). The generator scans all fields in the root module to find which external module types are actually referenced and includes only those headers.

5. **Named character lengths resolved from known defaults** — `CHARACTER(ChanLen)` resolves to `char[20]` via a lookup table of known NWTC Library parameters (ChanLen=20, LinChanLen=200). Unknown names fall back to 256.

### Generated Output Statistics

| File | Lines | Structs |
|------|-------|---------|
| AeroDyn_Types.hpp | 670 | 31 |
| FVW_Types.hpp | 465 | 25 |
| AeroAcoustics_Types.hpp | 285 | 10 |
| UnsteadyAero_Types.hpp | 284 | 12 |
| BEMT_Types.hpp | 256 | 11 |
| AeroDyn_Driver_Types.hpp | 189 | 10 |
| AirfoilInfo_Types.hpp | 185 | 8 |
| AeroDyn_Inflow_Types.hpp | 179 | 16 |
| DBEMT_Types.hpp | 95 | 12 |
| AeroDyn_Inflow_C_Binding_Types.hpp | 58 | 5 |
| **Total** | **2,666** | **140** |

### Verification

1. **Compilation:** All 10 C++ headers compile with `g++ -std=c++17 -c -fsyntax-only`
2. **Fortran regression:** All 10 `_Types.f90` files are byte-identical to committed versions (when using correct `-noextrap` flags)
3. **Cross-module includes:** Dependency scanning produces correct `#include` directives even for transitively-parsed modules

### Issue Found and Fixed

**Transitive usefrom ordering:** When AeroDyn_Registry.txt includes BEMT_Registry.txt (at recurse_level 1), and BEMT includes UnsteadyAero_Registry.txt (at recurse_level 2), UnsteadyAero is already in `include_files` when AeroDyn tries to include it directly. The `use_modules` list only tracks recurse_level==1 includes, so UnsteadyAero was missing. Fixed by scanning actual type field dependencies instead of relying on `use_modules`.

## What's NOT in Phase 0

- **No Fortran populators** — view-struct populators that fill C++ structs from Fortran types are a Phase 1 concern (needed when translating physics functions).
- **No C++ Copy/Destroy/Pack/Unpack** — these Fortran routines are called by the OpenFAST framework, which stays Fortran. C++ equivalents are a Phase 8 (cleanup) concern.
- **No ExtrapInterp in C++** — same rationale.
- **No AeroDyn CMakeLists.txt changes** — the C++ headers are generated during development, not integrated into the OpenFAST build system yet.

## What's Next

Phase 1: AirfoilInfo + DBEMT — translate 37 procedures (~3,415 lines). This is the "prove it works" gate:
- Validate KGen extraction on AeroDyn build chain (OQ2)
- Validate VIT on AeroDyn (OQ8)
- Design mesh interop pattern (OQ7)
- First use of generated C++ type headers

## Related

- `PROJECT_PLAN_AERODYN.md` — Phase 0 section (updated with completion status)
- `CLAUDE.md` — OpenFAST/AeroDyn status section (updated)
- Dev note `202604240016` — open questions (OQ1/OQ6 answered)
- `openfast/modules/openfast-registry/src/registry_gen_cpp.cpp` — the implementation
