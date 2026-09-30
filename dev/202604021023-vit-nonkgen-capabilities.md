# VIT Non-KGen Integration Capabilities

**Date:** 2026-04-02
**Status:** completed
**Scope:** Add VIT tool support for non-KGen (simulation-verified) translations: `vit sim-compare`, reverse-copy auto-generation, `--reverse-copy` flag, verification tracking

## Why This Was Needed

Stage B translated three functions (CheckInputs, ReadCpFile, ReadControlParameterFileSub) without KGen — using idiomatic C++ verified via simulation-level comparison. All the wrapper generation, verification, and integration was done manually: handwritten Fortran wrappers, raw Python one-liner comparisons, regex-based integration in shell scripts. This worked, but it broke the pattern of making everything a VIT capability.

A post-Stage B review asked: "We broke our pattern of making everything a tool/vit capability — what do you think of this?" The investigation that followed identified:

1. **An oversight:** `vit integrate` already works without KGen verification — we could have used it for CheckInputs but didn't try.
2. **Three actual gaps** in VIT's capabilities for non-KGen functions.

The investigation then asked whether the Stage B wrapper patterns (two-pass protocol, pre-allocation, reverse-copy) contained a generalizable abstraction. The answer: **~80% of the wrapper is fully determined by the function signature + type information** (view declarations, INTERFACE blocks, populator calls, C_LOC passing). The remaining ~20% (allocation decisions) is inherently case-by-case domain knowledge. VIT should handle the 80% and scaffold the 20%.

## What Was Built

### 1. `vit sim-compare` — Simulation Array Comparison

**New file:** `vit/vit/sim_compare.py` (160 lines)

A new CLI command that compares `.npz` simulation output files between a baseline directory (pure Fortran build) and an integrated directory (Fortran + C++ build):

```
vit sim-compare baseline_arrays/ integrated_arrays/
```

**Output format:**
```
  scenario_1: 5/5 IDENTICAL (200,000 values)
  scenario_3: 5/5 IDENTICAL (80,000 values)
  ...
✓ ALL IDENTICAL — 692,000 total values compared
```

**Architecture:**
```python
@dataclass
class ArrayResult:
    name: str           # e.g., "gen_torque"
    n_values: int       # e.g., 16000
    identical: bool     # True if np.array_equal
    max_diff: float     # 0.0 if identical
    max_diff_index: int # index of max difference

@dataclass
class FileResult:
    filename: str       # e.g., "scenario_3.npz"
    arrays: list[ArrayResult]

@dataclass
class CompareResult:
    files: list[FileResult]
    # Properties: passed, total_values, total_arrays, identical_arrays
```

**Key design decisions:**
- Uses `numpy.array_equal()` for exact comparison (default `tolerance=0.0`)
- Supports `--tolerance` for non-exact comparison (e.g., FMA sanity checks)
- Enumerates `.npz` files by name in the baseline directory, matches against integrated
- Missing integrated files are reported as failures (not silently skipped)
- Missing array keys within a file are reported as failures
- Exit code: 0 if all identical, 1 if any differ

**Why this is general:** Any Fortran→C++ translation project with a simulation runner that produces numpy arrays can use this. The comparison doesn't know anything about ROSCO, wind turbines, or controller parameters. It's pure array comparison.

**Tests:** 8 unit tests in `tests/test_sim_compare.py`:
- `test_identical_comparison` — matching arrays → pass
- `test_mismatching_comparison` — differing array → fail with details
- `test_tolerance_comparison` — within tolerance → pass
- `test_missing_integrated_file` — missing file → fail
- `test_missing_baseline_dir` — nonexistent dir → FileNotFoundError
- `test_empty_baseline_dir` — no .npz files → FileNotFoundError
- `test_format_result_identical` — output formatting for pass
- `test_format_result_mismatch` — output formatting for fail

**Verified with real data:** `vit sim-compare baseline_arrays/ baseline_arrays/` on ROSCO produces "ALL IDENTICAL — 692,000 total values compared".

### 2. Auto-Generate Reverse-Copy in View Populator Modules

**Modified file:** `vit/vit/view_populator.py` (+89 lines)

The view populator generator (`generate_view_populator()`) now produces TWO subroutines instead of one:

1. `vit_populate_<type>(src, view)` — copies Fortran TYPE → view struct (existing)
2. `vit_copy_scalars_to_<type>(view, dest)` — copies view struct → Fortran TYPE (new)

The reverse-copy subroutine:
- Copies INTEGER scalars: `dest%field = INT(view%field, IntKi)`
- Copies REAL scalars: `dest%field = REAL(view%field, DbKi)`
- Copies CHARACTER fields char-by-char: `DO i = 1, N; dest%name(i:i) = view%name(i); END DO`
- Copies fixed-size arrays directly: `dest%field = view%field`
- Does NOT copy ALLOCATABLE pointer fields (C_PTR) — those only flow Fortran→view via C_LOC

**New functions added:**

```python
def _reverse_scalar_assignment(f: DerivedTypeField, kind_aliases) -> list[str]:
    """Generate reverse scalar copy: dest%field = convert(view%field).
    Returns a list of lines (CHARACTER fields need DO loops)."""
```

This mirrors `_scalar_assignment()` but reverses the direction and uses Fortran kind parameters (IntKi, DbKi) instead of C kind parameters (C_INT, C_DOUBLE).

```python
def _extract_kind(fortran_type: str, kind_aliases) -> str:
    """Extract Fortran kind from type declaration.
    E.g., 'REAL(DbKi)' → 'DbKi', 'INTEGER' → 'KIND(0)'."""
```

**Generated example:**
```fortran
    SUBROUTINE vit_copy_scalars_to_testtype(view, dest)
        TYPE(testtype_view_t), INTENT(IN) :: view
        TYPE(TestType), INTENT(INOUT) :: dest
        INTEGER :: i

        ! --- Scalars (reverse of populator) ---
        dest%count = INT(view%count, IntKi)
        dest%value = REAL(view%value, DbKi)
        DO i = 1, 256
            dest%name(i:i) = view%name(i)
        END DO

    END SUBROUTINE vit_copy_scalars_to_testtype
```

**PUBLIC list updated:** The module now exports three symbols:
```fortran
PUBLIC :: <type>_view_t, vit_populate_<type>, vit_copy_scalars_to_<type>, vit_original_<type>
```

**Why this is general:** Any Fortran project with ALLOCATABLE derived types using the view strategy needs this when C++ modifies scalar fields that Fortran code downstream accesses directly. The reverse-copy is the mechanical inverse of the populator — it's fully determined by the type definition and kind aliases.

**This replaces** the manually-written `vit_copy_scalars_to_cntrpar` subroutine that was added to `vit_controlparameters_view.f90` during Stage B. The next time `vit analyze-types --scan-allocatable` regenerates the populator modules, the reverse-copy will be auto-generated.

### 3. `--reverse-copy` Flag on `vit integrate`

**Modified files:** `vit/vit/cli.py`, `vit/vit/integrate.py`, `vit/vit/interface_gen.py`

A new `--reverse-copy` flag on the `vit integrate` command:

```
vit integrate CheckInputs checkinputs.cpp -f ReadSetParameters.f90 --apply --reverse-copy
```

When set, the generated Fortran wrapper adds reverse-copy calls after the C++ call:

```fortran
        CALL checkinputs_c(C_LOC(LocalVar), C_LOC(CntrPar_view), ...)
        ! Copy modified scalars back from view to Fortran type
        CALL vit_copy_scalars_to_controlparameters(CntrPar_view, CntrPar)
```

**Threading through the call chain:**

```
cli.py: cmd_integrate()
    → args.reverse_copy (from argparse)
    → integrate.py: generate_integration_plan(..., reverse_copy=True)
        → integrate.py: _generate_fortran_patches(..., reverse_copy=True)
            → interface_gen.py: generate_fortran_wrapper(..., reverse_copy=True)
                → interface_gen.py: _generate_wrapper_subroutine(..., reverse_copy=True)
                    → After CALL c_func(...): adds reverse-copy calls
                → interface_gen.py: _get_view_use_statements(..., reverse_copy=True)
                    → Adds vit_copy_scalars_to_<type> to USE import list
```

**Smart filtering:** Only adds reverse-copy calls for view-type arguments with `INTENT(INOUT)` or `INTENT(OUT)`. `INTENT(IN)` arguments are not reverse-copied (C++ shouldn't be modifying them, and the Fortran type is read-only).

### 4. ALLOCATABLE Field Hints in Translation Prompt

**Modified file:** `vit/vit/translate.py` (+24 lines)

Initially planned as comments in the generated wrapper (`interface_gen.py`), which would have required threading `DerivedTypeInfo` through four function calls. A review of alternatives identified a cleaner approach: emit the hints in the **translation prompt** (`vit translate`), where the type registry is already available and the translator sees the information when they're writing the C++ implementation — which is when the allocation decision is made.

In `generate_translation_prompt()`, after the "C Struct Definitions" section, a new "ALLOCATABLE Fields" section lists all ALLOCATABLE fields from view-type arguments:

```markdown
## ALLOCATABLE Fields (may need pre-allocation)

If this function allocates or populates arrays in these types,
the Fortran wrapper will need ALLOCATE statements before calling C++.
The view struct exposes these as pointer+size pairs.

**ControlParameters** (view type — ALLOCATABLE fields):
- `F_NotchFreqs` — `REAL(DbKi), DIMENSION(:)`
- `PC_GS_angles` — `REAL(DbKi), DIMENSION(:)`
- `VS_KP` — `REAL(DbKi), DIMENSION(:)`
...

**PerformanceData** (view type — ALLOCATABLE fields):
- `TSR_vec` — `REAL(DbKi), DIMENSION(:)`
- `Cp_mat` — `REAL(DbKi), DIMENSION(:,:)`
...
```

The implementation iterates over `derived_types.values()`, filters for `view_strategy == 'view'`, then lists fields where `f.is_allocatable == True`. No changes to the integration pipeline needed — the information is already where the translator needs it.

**Verified with real data:** `vit translate ReadCpFile -f ReadSetParameters.f90 --module ReadSetParameters` now shows all ALLOCATABLE fields from ControlParameters and PerformanceData in the prompt output.

### 5. vit.yaml Verification Method Tracking

**Modified file:** `vit/vit/cli.py`

When `--reverse-copy` is used with `vit integrate --apply`, the translation entry in vit.yaml gets an additional field:

```yaml
translations:
  CheckInputs:
    status: integrated
    cpp_file: translations/ReadSetParameters/checkinputs.cpp
    verification: simulation
```

Default (when `--reverse-copy` is not used) remains `kgen` implicitly (no field added, for backward compatibility with existing entries).

This allows `vit status` to distinguish between KGen-verified and simulation-verified functions. It's also useful for documentation and auditing — knowing how each translation was proven correct.

## The Abstraction That Emerged

The investigation revealed that ALL Fortran wrappers (Track A and Stage B) follow the same skeleton:

| Step | Track A (consumer) | Stage B (producer) | Determined by |
|------|-------------------|-------------------|---------------|
| 1. USE view modules | Yes | Yes | vit.yaml type strategies |
| 2. Declare view locals | Yes | Yes | Function signature + type info |
| 3. INTERFACE with BIND(C) | Yes | Yes | C++ extern "C" signature |
| 4. ALLOCATE arrays | No | Sometimes | **Domain knowledge** (case-by-case) |
| 5. Populate views | Yes | Yes | Type field list |
| 6. Call C++ via C_LOC | Yes | Yes | Function signature |
| 7. Reverse-copy scalars | No | Sometimes | `--reverse-copy` flag |

Steps 1-3 and 5-6 were already handled by `vit integrate`. Steps 4 and 7 were the gaps. Step 7 is now handled. Step 4 remains case-by-case but could be scaffolded with hints (deferred).

**The key insight:** `vit integrate` was never KGen-specific. It generates wrappers from function signatures and type information, not from kernels or verification results. We could have used it for CheckInputs from the start — we just assumed it was KGen-only and wrote everything manually.

## What This Means for Future Stages

Stage C (restart I/O, debug logger) and Stage D (platform modules) can now use the VIT tool properly:

1. `vit translate` to generate scaffolds (should have used for Stage B)
2. Write the C++ implementation
3. `vit integrate --apply --reverse-copy` to generate wrappers (if the function modifies view-type scalars)
4. Manually add ALLOCATE statements to the wrapper (if the function allocates arrays)
5. `vit sim-compare baseline_arrays/ integrated_arrays/` to verify

The manual work is reduced to: writing the C++ implementation and adding any ALLOCATE statements. The wrapper skeleton, view handling, INTERFACE block, C_LOC passing, reverse-copy, and verification comparison are all tooled.

## Files Modified

| File | Lines Changed | Description |
|------|--------------|-------------|
| `vit/vit/sim_compare.py` | +160 (new) | Array comparison module with dataclasses and formatter |
| `vit/vit/cli.py` | +25 | sim-compare command + --reverse-copy flag + verification tracking |
| `vit/vit/view_populator.py` | +89 | Reverse-copy subroutine generation + _reverse_scalar_assignment + _extract_kind |
| `vit/vit/interface_gen.py` | +21 | Reverse-copy calls in wrapper + USE import updates |
| `vit/vit/integrate.py` | +4 | Thread reverse_copy parameter through |
| `vit/vit/translate.py` | +24 | ALLOCATABLE field hints in translation prompt |
| `vit/tests/test_sim_compare.py` | +115 (new) | 8 unit tests for sim-compare |

## Test Results

581/581 tests pass (up from 501 documented earlier — test count has grown organically through development). The 8 new sim-compare tests are included in this count.

## Commits

| Commit | Description |
|--------|-------------|
| `563368a` | `vit sim-compare` command (new file + CLI + 8 tests) |
| `c9eb31c` | Auto-generate reverse-copy in view populator modules |
| `a108a83` | `--reverse-copy` flag threaded through cli → integrate → interface_gen |
| `c2f97b1` | vit.yaml `verification: simulation` tracking |
| `621125b` | ALLOCATABLE field hints in `vit translate` prompt |
