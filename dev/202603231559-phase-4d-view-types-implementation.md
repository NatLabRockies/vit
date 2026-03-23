# Phase 4D: Pointer+Size View Pattern for ALLOCATABLE Types
**Date:** 2026-03-23
**Status:** in-progress (Steps 1-8 complete, Step 9 blocked by issues)
**Scope:** Type system — ALLOCATABLE derived types (ControlParameters, PerformanceData, ExtControlType)

## The Problem

Three ROSCO derived types have ALLOCATABLE arrays and can't use BIND(C) directly:
- **ControlParameters** — ~226 fields total, ~68 ALLOCATABLE (1D and 2D arrays), dynamically sized per turbine configuration
- **PerformanceData** — 5 ALLOCATABLE fields (2 × 1D vectors, 3 × 2D matrices)
- **ExtControlType** — 1 ALLOCATABLE field (avrSWAP array)

These types are used by every major controller subroutine (PitchControl, VariableSpeedControl, etc.). Phase 4D is the last remaining type system work — it unblocks Phase 5B (AeroDynTorque), Phase 7B (10 main controller subroutines), and Phase 8 (ControllerBlocks).

## Design: View Struct Pattern

The Fortran types are NOT modified. Instead, VIT generates a "view" into the Fortran-owned memory:

1. **View struct** (C header) — Mirrors the Fortran type. Scalars are copied directly. ALLOCATABLE 1D fields become `type* field; int n_field;` (pointer into Fortran memory + size). ALLOCATABLE 2D fields become `type* field; int n_field_rows; int n_field_cols;` (column-major). CHARACTER fields are `char field[N]`. Uses `_view_t` suffix to distinguish from BIND(C) `_t` structs.

2. **Populator** (Fortran module) — A subroutine that fills the view struct from the real Fortran type. Copies scalars with kind conversion. Uses `C_LOC()` for ALLOCATABLE arrays, `SIZE()` for dimensions. Wraps ALLOCATABLE access in `IF (ALLOCATED(...))`. The populator module also contains a BIND(C) mirror type that guarantees layout matches the C struct.

3. **Wrapper integration** — For view-type args, the Fortran wrapper declares a local view struct, calls the populator, then passes `C_LOC(view)` to the C function. This happens transparently — the C++ function just sees a `controlparameters_view_t*`.

Key design decision: the populator is a regular Fortran subroutine (not BIND(C)), because gfortran rejects non-C-interoperable types as arguments to BIND(C) procedures. The populator is called from the Fortran wrapper (which has access to the real type), so BIND(C) isn't needed.

## Size Source Mapping (Prior Feasibility Analysis)

Dev note `202603192051` classified every ALLOCATABLE field's size source into five types:

- **Type A (~56%):** Size is a `CntrPar%` field passed to `ParseAry` (e.g., `PC_GS_angles` sized by `PC_GS_n`)
- **Type B (~12%):** Literal integer (e.g., `ALLOCATE(IPC_KP(2))`)
- **Type C (2 fields):** Local variable from loop counting (needs EXIT_CONFIRM)
- **Type D (2 fields):** `SIZE()` of another field
- **Type E (5 fields):** Array index into size field (e.g., `PerfTableSize(1)`)

~90% are fully automatable. The scanner implemented in Step 1 automates this classification.

## Implementation: What Was Built (Steps 1-8)

### Step 1: Size Source Scanner (`analyze_types.py`)

New `AllocatableFieldMapping` dataclass and `scan_allocatable_sizes()` function. Scans all `.f90` files for:
- `ParseAry` calls: `CALL ParseAry(..., Var%field, size_expr, ...)` — extracts field→size mapping
- `ALLOCATE` calls: `ALLOCATE(Var%field(dim1, dim2))` — extracts per-dimension size expressions

Classifies each size expression by type (A-E) using `_classify_size_expr()`. Skips commented-out lines.

CLI: `vit analyze-types --scan-allocatable --type ControlParameters`

**Scanner results:**

| Type | Mapped | Unmapped | Details |
|------|--------|----------|---------|
| ControlParameters | 56 | 12 | 41 Type A, 13 Type B, 2 Type C (2D). 11 OL_ file-read fields + 1 orphan (WE_CP) unmapped. |
| PerformanceData | 5 | 0 | All Type E (array-indexed PerfTableSize). 3 are 2D matrices. |
| ExtControlType | 1 | 0 | Type C (local var `max_avr_entries`). |

Two regex patterns handle different ParseAry call signatures:
- Standard: `ParseAry(FileLines, 'label', Var%field, size_expr, ...)`
- ReadCpFile variant: `ParseAry(Unit, CurLine, 'label', Var%field, size_expr, ...)`

ALLOCATE regex handles one level of nested parentheses (e.g., `PerfTableSize(2)` inside `ALLOCATE(...)`).

### Step 2: View Struct Header Generation (`derived_type_parser.py`)

New `generate_view_struct_header()` function. Consumes field mappings, produces C typedef:
- Scalars and fixed-size arrays → direct C fields (same as BIND(C) structs)
- ALLOCATABLE 1D → `type* field; int32_t n_field;`
- ALLOCATABLE 2D → `type* field; int32_t n_field_rows; int32_t n_field_cols;`
- POINTER fields → commented out (access via parent 2D array)
- Unmapped ALLOCATABLE fields → commented out with note
- Uses `_view_t` suffix and `#include <stdint.h>`

ControlParameters view struct: 292 lines.

### Step 3: Populator Generation (`view_populator.py` — new module)

New `generate_view_populator()` function. Produces a complete Fortran module containing:
- BIND(C) mirror type that matches the C view struct layout exactly
- Populator subroutine that fills the view from the real Fortran type

Populator pattern per field type:
- Scalars: `view%field = REAL(src%field, C_DOUBLE)` (with kind conversion)
- CHARACTER(N): `view%field = TRANSFER(src%field, view%field)` (string→array copy)
- Fixed-size arrays: `view%field = src%field` (direct copy)
- ALLOCATABLE 1D: `IF (ALLOCATED) THEN view%field = C_LOC(src%field(1)); view%n_field = SIZE(...)`
- ALLOCATABLE 2D: Same with `SIZE(src%field, 1)` and `SIZE(src%field, 2)` for rows/cols

CHARACTER fields use array form `CHARACTER(KIND=C_CHAR) :: field(N)` in the mirror type (BIND(C) requires LEN=1).

ControlParameters populator: 830 lines. Compiles cleanly with gfortran.

### Step 4: Type Registry Changes

- `DerivedTypeInfo` gains `view_strategy: Optional[str]` field
- New `apply_type_strategies()` function marks registry entries with strategies from config
- `TypeMapping` gains `is_view_type: bool` field
- `get_derived_type_mapping()` checks `view_strategy` — if "view", returns `is_c_compatible=True, is_view_type=True`

### Step 5: Interface Generation Changes (`interface_gen.py`)

- `build_c_params()`: view types → `controlparameters_view_t*` (not `_t*`)
- OPTIONAL view types: same `_view_t*` suffix
- `_build_wrapper_call_args()`: view types → `C_LOC(arg_view)` (local view struct, not original arg)
- New helpers: `_get_view_type_args()`, `_generate_view_preamble()`, `_get_view_use_statements()`
- Both `_generate_wrapper_function()` and `_generate_wrapper_subroutine()` updated to:
  - Add `USE vit_<type>_view` for view type modules
  - Declare local `TYPE(<type>_view_t), TARGET :: <arg>_view`
  - Call `vit_populate_<type>(<arg>, <arg>_view)` before the C function call

Validated against PitchControl: produces correct wrapper with USE, local view, populator call, and `C_LOC(CntrPar_view)`.

### Step 6: Integration Changes (`integrate.py`)

- `_generate_standalone_cpp()`: embeds view struct headers (via `generate_view_struct_header()`) for view-type args, using the scanner to get field mappings on the fly
- Compatible types filter: includes types where `view_strategy == 'view'` in addition to `is_c_compatible`

### Step 7: Translation Scaffold Changes (`translate.py`)

- `generate_translation_scaffold()`: embeds view struct definitions for view-type args
- `generate_translation_prompt()`: embeds view struct definitions in the struct section

Validated: PitchControl scaffold (557 lines) includes full `controlparameters_view_t` definition and correct `void pitchcontrol(double* avrSWAP, controlparameters_view_t* CntrPar, ...)` signature.

### Step 8: Apply to ROSCO

Added `types:` section to `ROSCO/vit.yaml`:
```yaml
types:
  ControlParameters:
    strategy: view
  PerformanceData:
    strategy: view
  ExtControlType:
    strategy: view
```

### Verification

- All 451 existing unit tests pass
- Existing 18-function verification regression unaffected (saturate, PIController, NotchFilterSlopes, interp1d all 40/40)
- PitchControl interface generation produces correct output (validated via Python script)
- PerformanceData and ControlParameters populators compile with gfortran

## Issues Found During Reflection

### CRITICAL — Must Fix Before Step 9

**1. CLI `_build_registry()` never calls `apply_type_strategies()`**

`_build_registry()` (cli.py:893) builds the derived type registry but never applies the view strategy from config. Every CLI command — `vit verify`, `vit integrate`, `vit translate`, `vit run` — will treat ControlParameters as a passthrough (non-C-compatible) type, silently excluding it from the interface.

The validation test worked because it was run from a Python script that called `apply_type_strategies()` manually. The actual CLI pipeline will produce wrong output.

**Fix:** Add `apply_type_strategies(registry, config.type_strategies)` to `_build_registry()`.

**2. `kernel_verify.py` doesn't handle view types**

`setup_kernel_cpp()` generates struct headers by checking `type_info.is_c_compatible` (line 114). For view types, `is_c_compatible` is False on the `DerivedTypeInfo` — the override only happens through `get_derived_type_mapping()`. The kernel setup will either skip ControlParameters (passthrough) or fail.

The kernel needs:
- The view struct header (not the BIND(C) struct header)
- The populator Fortran module (for the wrapper to USE)
- The wrapper to use the view pattern (USE, local view, populator call)

This is the same scope of changes as Steps 5-6 but applied to the kernel verification path.

**3. `integrate.py` doesn't generate the populator `.f90` file or update CMake**

The plan specified that `generate_integration_plan()` should create the populator Fortran file in `src/` and add it to CMakeLists.txt SOURCES. This was not implemented. Without it, the Fortran wrapper's `USE vit_controlparameters_view` will fail at compile time because the module doesn't exist.

**Fix:** Add populator file creation to `generate_integration_plan()` and add the file to CMake SOURCES.

**4. Dimension splitting bug for nested parentheses in ALLOCATE**

`OL_CableControl` has `ALLOCATE(%OL_CableControl(N_OL_Cables,SIZE(CntrPar%OL_Channels,DIM=1)))`. The dimension splitter does `dims_str.split(",")` which splits at the comma inside `SIZE(...)`, producing 3 dimensions instead of 2. The scanner reports these as "3D" when they are 2D arrays.

**Fix:** Use parenthesis-aware comma splitting (skip commas inside nested parentheses).

### IMPORTANT — Must Address Before Phase 7B

**5. INTENT(INOUT) scalar write-back**

The view pattern copies scalars INTO the view struct. If C++ modifies a scalar field, the change exists only in the local view struct and is not written back to the Fortran type. Array modifications ARE visible because C++ writes through the pointer into Fortran memory.

ControlParameters is INTENT(INOUT) in most controller subroutines. If any subroutine modifies CntrPar scalar fields (not just reads them), a "depopulate" step is needed after the C call to copy modified scalars back.

**Action:** Audit all INTENT(INOUT) uses of ControlParameters in Phase 7B target functions. Determine whether a depopulator is needed.

**6. OL_ unmapped fields (11 fields + WE_CP)**

11 OL_ fields (OL_Breakpoints, OL_BldPitch1-3, OL_GenTq, OL_YawRate, OL_Azimuth, OL_R_Speed, OL_R_Torque, OL_R_Pitch, OL_Channels) and WE_CP have no size source detected. They're commented out of the view struct.

These are open-loop control timeseries read from a CSV file via custom I/O (not ParseAry/ALLOCATE with detectable patterns). If any Phase 7B function accesses these fields, the C++ translation won't have access.

**Action:** Check which Phase 7B target functions access OL_ fields. If needed, add manual size mappings or extend the scanner for the OL file reader pattern.

**7. No unit tests for new code**

All 451 existing tests pass, but zero new tests were written. Missing coverage:
- `scan_allocatable_sizes()` — synthetic Fortran snippets with ParseAry/ALLOCATE patterns
- `generate_view_struct_header()` — verify struct output for 1D, 2D, scalar, CHARACTER fields
- `generate_view_populator()` — verify Fortran output compiles and has correct assignments
- `apply_type_strategies()` — verify registry modification
- `is_view_type` flow through `build_c_params()` — verify `_view_t*` naming
- Wrapper generation with view types — verify USE/populator/C_LOC pattern

### MINOR — Track But Don't Block

**8. Nested TYPE() fields skipped in view struct** — ControlParameters doesn't have them, so not blocking. Would need handling if a future view type does.

**9. Stack size for view struct locals** — ~2KB for ControlParameters. Fine for Fortran stack conventions.

**10. Populator performance** — ~150 scalar copies + ~56 C_LOC calls per invocation. Negligible at ROSCO's ~1000 timestep scale.

## Fix Priority for Step 9

| # | Issue | Fix | Effort |
|---|-------|-----|--------|
| 1 | CLI `_build_registry` missing `apply_type_strategies` | One-line fix | 5 min |
| 4 | Dimension split bug for nested parens | Parenthesis-aware split | 15 min |
| 3 | `integrate.py` populator file + CMake | Generate .f90, add to SOURCES | ~1 hour |
| 2 | `kernel_verify.py` view type support | View header, populator in kernel, wrapper changes | ~1 hour |
| 7 | Unit tests | Synthetic test cases for all new functions | ~2 hours |
| 5 | INTENT(INOUT) audit | Grep for CntrPar field assignments | 30 min |
| 6 | OL_ fields assessment | Check Phase 7B function access patterns | 30 min |

Items 1-4 are blockers for Step 9 (end-to-end validation with a real ControlParameters function). Items 5-7 should be completed before Phase 7B translation begins.

## Files Created/Modified

| File | Action | Lines changed |
|------|--------|--------------|
| `vit/vit/analyze_types.py` | Modified — scanner + classifier + report formatter | +262 |
| `vit/vit/cli.py` | Modified — `--scan-allocatable` flag + handler | +51 |
| `vit/vit/derived_type_parser.py` | Modified — `view_strategy` field, `apply_type_strategies()`, `generate_view_struct_header()` | +137 |
| `vit/vit/view_populator.py` | Created — populator generation module | +170 |
| `vit/vit/type_map.py` | Modified — `is_view_type` on TypeMapping, strategy check in `get_derived_type_mapping()` | +11 |
| `vit/vit/config.py` | Modified — `type_strategies` on VitConfig, `_parse_type_strategies()`, save support | +33 |
| `vit/vit/interface_gen.py` | Modified — view type params, USE statements, local views, populator calls in wrappers | +105 |
| `vit/vit/integrate.py` | Modified — view struct header embedding | +26 |
| `vit/vit/translate.py` | Modified — view struct in scaffolds + prompts | +48 |
| `ROSCO/vit.yaml` | Modified — `types:` section with strategies | +7 |

Total: ~850 lines added across 10 files, 1 new module.
