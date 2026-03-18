# Phase 4A: ErrorVariables + `analyze-types` + Build-Fix Loop
**Date:** 2026-03-18
**Status:** completed
**Scope:** Type system foundation — ErrorVariables C-compatibility, type analysis command, compiler-driven fix loop

## What Was Done

Phase 4A is the first sub-phase of the type system work (Phase 4). It accomplishes three things:

1. **Makes ErrorVariables C-compatible** — the sole blocker was `CHARACTER(:), ALLOCATABLE :: ErrMsg`. Converted to `CHARACTER(1024) :: ErrMsg`, which is the buffer size ROSCO already uses. This unblocks Phase 5's functions that take ErrorVariables (interp1d, sigma, unwrap, interp2d).

2. **Establishes `vit analyze-types`** — a new CLI command that scans all Fortran TYPE definitions in a project, classifies which fields block C compatibility, and provides automated fixes. Currently supports one fix type (`--fix character`) with an extensible architecture for future fix types.

3. **Introduces the build-fix loop** (`build_fixer.py`) — a general-purpose system that uses the compiler as an oracle. After any source modification, it builds the project, parses gfortran errors, applies fixes from a registry of known patterns, and retries. This replaces fragile pattern-matching of downstream consequences with compiler-verified correctness.

## Technical Details

### Type Map Changes (`type_map.py`)

- Added `character_length: Optional[int]` to `TypeMapping` dataclass — allows `generate_c_struct_header()` to emit `char field[N]` instead of `char field`
- Added CHARACTER(N) handling in `get_type_mapping()` — numeric-length CHARACTER types map to `char` with the length preserved. Deferred-length `CHARACTER(:)` raises a clear error directing the user to `vit analyze-types --fix character`
- Added ISO_C_BINDING kind names (`C_INT`, `C_FLOAT`, `C_DOUBLE`) to `DEFAULT_KIND_ALIASES` — needed because ROSCO's ErrorVariables uses `INTEGER(C_INT)` directly
- **Fixed kind alias merging**: Changed `aliases = kind_aliases or DEFAULT_KIND_ALIASES` to `aliases = {**DEFAULT_KIND_ALIASES, **(kind_aliases or {})}`. The old behavior silently dropped all defaults when project aliases were provided. The new behavior merges both, with project aliases overriding defaults. Applied in both `normalize_fortran_type()` and `get_type_mapping()`. All 280 existing tests still pass — no regressions.

### Derived Type Parser Changes (`derived_type_parser.py`)

- Added `source_file: Optional[str]` to `DerivedTypeInfo` — set by `find_and_parse_derived_type()` and the new `scan_all_derived_types()`
- `scan_all_derived_types(source_dir)` — globs all .f90/.F90 files, finds every TYPE definition via regex, parses each, deduplicates by name. Returns a complete inventory of all types in a project.
- `FieldBlocker` dataclass — captures why a field blocks C compatibility (reason + fix suggestion)
- `classify_field_blockers(type_info, kind_aliases)` — inspects each field and returns blockers. Reasons: `character_allocatable`, `allocatable`, `pointer`, `nested_type`, `logical`, `complex`
- Updated `generate_c_struct_header()` to check `mapping.character_length` and emit `char field[N]` for CHARACTER(N) fields

### Analyze Types Module (`analyze_types.py` — new)

- `TypeAnalysis` dataclass — wraps a `DerivedTypeInfo` with its blockers and compatibility status
- `analyze_all_types(source_dir, kind_aliases)` — scans and analyzes all types, returns list of `TypeAnalysis`
- `format_analysis_report(analyses, verbose)` — formatted report showing compatible/incompatible counts, per-type blockers with fix suggestions
- `fix_character_allocatable(source_dir, type_name, field_name, fixed_length, dry_run)` — two-pass fix:
  - Pass 1: Rewrites `CHARACTER(:), ALLOCATABLE :: field` to `CHARACTER(N) :: field` in the TYPE definition
  - Pass 2: Comments out `ALLOCATE(Var%field...)` / `DEALLOCATE(Var%field...)` across all source files

### Build Fixer Module (`build_fixer.py` — new)

This is the most architecturally significant addition. Rather than trying to predict all downstream consequences of a source change, VIT now uses the compiler itself as the oracle.

**Architecture:**
- `CompilerError` — structured representation of a gfortran error (file, line, column, severity, message, source line)
- `FixPattern` — a (matcher, fixer) pair. The matcher checks if a `CompilerError` matches the pattern. The fixer reads the file and returns an `ErrorFix` with the replacement line.
- `DEFAULT_FIX_REGISTRY` — list of `FixPattern` objects. New patterns are added by appending.
- `parse_gfortran_errors(output)` — regex parser for gfortran's multi-line error format (`file:line:col:` + source + `Error: message`)
- `build_and_fix(build_cmd, source_dir, max_iterations, fix_registry)` — the loop:
  1. Run build_cmd
  2. If success, return
  3. Parse errors
  4. For each error, check registry for matching pattern
  5. Apply fixes (grouped by file, reverse line order to preserve line numbers)
  6. Retry (up to max_iterations)

**First fix pattern: `transfer_shape_mismatch`**

When `CHARACTER(:), ALLOCATABLE` becomes `CHARACTER(N)`, downstream code using `TRANSFER(C_NULL_CHAR, array_var)` to initialize a character array breaks. The TRANSFER result has shape (1) but the array has shape (N+1). gfortran emits "Different shape for array assignment."

The fix: replace `VAR = TRANSFER(C_NULL_CHAR, VAR)` with `VAR = C_NULL_CHAR`. Fortran's scalar broadcast assignment fills the entire array correctly. The regex matches the variable name on both sides of the TRANSFER and preserves indentation, equals spacing, and trailing comments.

**CLI integration:** When `vit analyze-types --fix character --apply` succeeds and `build_cmd` is configured in `vit.yaml`, the build-fix loop runs automatically. No extra flags needed. The user sees:
```
✓ Fixed 1 file(s)
Verifying build...
  Auto-fixed: Replace TRANSFER(C_NULL_CHAR, avcMSG) with avcMSG = C_NULL_CHAR (scalar broadcast)
    /workspace/ROSCO/rosco/controller/src/ExtControl.f90:83
✓ Build succeeded after 1 auto-fix(es)
```

### The ErrorVariables Fix in ROSCO

Running `vit analyze-types` against ROSCO shows 13 types: 7 compatible, 6 incompatible. ErrorVariables has exactly 1 blocker: `ErrMsg: character_allocatable`.

After `--fix character --apply`:
1. ROSCO_Types.f90: `CHARACTER(:), ALLOCATABLE :: ErrMsg` → `CHARACTER(1024) :: ErrMsg`
2. No ALLOCATE/DEALLOCATE to comment out (already commented in ROSCO)
3. Build-fix loop catches ExtControl.f90:83 TRANSFER error, auto-fixes it
4. Rebuild succeeds
5. Simulation produces byte-identical output to baseline

The generated C struct:
```c
typedef struct {
    int size_avcMSG;
    int aviFAIL;
    int ErrStat;
    char ErrMsg[1024];
} errorvariables_t;
```

## Discovery: TRANSFER Shape Mismatch

The original plan assumed "All string assignment/TRIM/LEN usage works unchanged with fixed-length CHARACTER." This was mostly true, but missed one pattern: `TRANSFER(C_NULL_CHAR, avcMSG)` in ExtControl.f90.

When `ErrMsg` was `CHARACTER(:), ALLOCATABLE`, the expression `LEN(ErrVar%ErrMsg)` was runtime-evaluated, so gfortran couldn't check array shapes at compile time. After fixing to `CHARACTER(1024)`, `LEN()` becomes a compile-time constant (1024), making `avcMSG` a 1025-element array. `TRANSFER(C_NULL_CHAR, avcMSG)` then produces a shape-(1) result assigned to a shape-(1025) variable — compile error.

This discovery motivated the build-fix loop design. Rather than trying to enumerate all possible downstream patterns (which we got wrong on the first try), we now let the compiler tell us what's broken and fix known patterns automatically. The fix registry grows as we encounter new patterns in future phases.

## Test Coverage

| Test File | Tests | What |
|-----------|-------|------|
| `test_analyze_types.py` | 30 | CHARACTER(N) mapping, scan_all_derived_types, classify_field_blockers, C struct generation, fix_character_allocatable |
| `test_build_fixer.py` | 23 | gfortran error parsing, TRANSFER fix pattern matching/fixing, build-and-fix loop with mocked subprocess |

Total: 333 tests (280 existing + 53 new), zero regressions.

Verification regression: saturate, wrap_180, wrap_360, ratelimit — all 40/40 passed.

Simulation diff: empty (byte-identical).

## Files Changed

**VIT repo:**
- `vit/vit/type_map.py` — character_length, CHARACTER(N) handling, C_INT alias, kind alias merging
- `vit/vit/derived_type_parser.py` — source_file, scan_all_derived_types, classify_field_blockers, CHARACTER(N) in struct gen
- `vit/vit/analyze_types.py` — new module: type analysis, fix_character_allocatable
- `vit/vit/build_fixer.py` — new module: compiler error parser, fix registry, build loop
- `vit/vit/cli.py` — analyze-types subparser, cmd_analyze_types with build-verify
- `vit/tests/test_analyze_types.py` — 30 tests
- `vit/tests/test_build_fixer.py` — 23 tests

**ROSCO repo:**
- `rosco/controller/src/ROSCO_Types.f90` — ErrorVariables ErrMsg: CHARACTER(1024) (applied by tool)
- `rosco/controller/src/ExtControl.f90` — TRANSFER → scalar broadcast (applied by build-fix loop)

## What This Unblocks

- **Phase 5**: Functions with ErrorVariables args (interp1d, sigma, unwrap, interp2d) can now be translated. ErrorVariables is a C-compatible struct passed by pointer.
- **Phase 4B-4D**: The `analyze-types` command and build-fix infrastructure extend naturally for LOGICAL→INTEGER fixes, nested type flattening, and ALLOCATABLE array handling.
- **General robustness**: Any future VIT command that modifies Fortran source can use `build_and_fix()` to verify the result compiles and auto-fix known patterns.
