# Phase 4C: Make LocalVariables C-Compatible
**Date:** 2026-03-19
**Status:** completed
**Scope:** vit analyze-types — 4 new fix modes, derived_type_parser blocker detection, build_fixer pattern

## Background

LocalVariables is a 168-field Fortran derived type that every Controller and ControllerBlocks function takes as an argument. It was the single biggest bottleneck in the translation effort — ~23 functions were blocked until it could cross the Fortran/C boundary as a struct pointer. Four fields prevented BIND(C) compatibility:

1. `LOGICAL :: restart` — 58 references across Controllers, ControllerBlocks, Filters, ReadSetParameters, ROSCO_IO
2. `LOGICAL :: WriteThisStep` — 2 references (binary I/O only)
3. `COMPLEX(DbKi) :: AWC_complexangle(3)` — 8 references including Euler formula in Active Wake Control
4. `CHARACTER, DIMENSION(:), ALLOCATABLE :: ACC_INFILE` — 5 references for parameter input filename

Additionally, `INTEGER(IntKi) :: AlreadyInitialized = 0` had a default initializer incompatible with BIND(C), and five nested types (WE, FilterParameters, piParams, resParams, rlParams) needed BIND(C) attributes.

## What Was Implemented

### Four new `vit analyze-types --fix` modes

Each fix follows the established two-pass pattern from Phase 4A's `--fix character`:

**`--fix logical`** — Converts `LOGICAL :: field` to `INTEGER(C_INT) :: field`.
- Pass 1: Rewrite type definition
- Pass 2: Context-sensitive regex rewrite of all `%field` references:
  - `.True.`/`.False.` assignments → `1`/`0`
  - Bare IF conditions (`IF (x%field)`) → `IF (x%field /= 0)`
  - `.NOT. x%field` → `(x%field == 0)`
  - Function arguments → `(x%field /= 0)` wrapping (produces LOGICAL from integer comparison)
  - Binary I/O (WRITE/READ) → unchanged (raw bytes, type doesn't matter)

The function argument wrapping is the most common pattern (~55 occurrences for `restart`). Each filter and controller function takes `reset` as a LOGICAL parameter, so every call site passing `LocalVar%restart` needs the `/= 0` wrapper to convert the now-INTEGER field back to a LOGICAL expression.

**`--fix complex`** — Splits `COMPLEX :: field(dims)` into `REAL :: field_re(dims)` + `REAL :: field_im(dims)`.
- Pass 1: Replace single COMPLEX field with two REAL fields in type definition
- Pass 2: Pattern-specific rewrites:
  - Zero initialization → set both `_re` and `_im` (inserts new line)
  - Euler formula `EXP(complexI * (expr))` → `COS(expr)` for real, `SIN(expr)` for imaginary (inserts new line)
  - `REAL(field)` extraction → `field_re` (removes REAL() wrapper)
  - Binary I/O per element → split into `_re` write + `_im` write (inserts new line)
  - Output data → use `_re` only
- Also comments out the `COMPLEX(DbKi) :: complexI = (0.0, 1.0)` local variable declaration that was only needed for the EXP form

The Euler formula rewrite required a regex that handles nested parentheses in the angle expression (`AWC_angle(K)`). Initial implementation used `[^)]+` which broke on nested parens — fixed with `[^()]*(?:\([^()]*\))[^()]*` to handle one level of nesting.

**`--fix character_array`** — Converts `CHARACTER, DIMENSION(:), ALLOCATABLE :: field` to `CHARACTER :: field(N)`.
- Uses the confirmation protocol (EXIT_CONFIRM = 10) for size inference, same as `--fix character`
- For ACC_INFILE, the ALLOCATE expression is `LocalVar%ACC_INFILE_SIZE` (runtime variable, not evaluable), so VIT defaults to 1024 and asks for confirmation
- Comments out ALLOCATE/DEALLOCATE statements

**`--fix bind_c`** — Adds `BIND(C)` attribute and removes default initializers.
- Inserts `, BIND(C)` into the TYPE declaration line
- Finds and removes `= value` from field declarations (BIND(C) types cannot have default initializers in Fortran)
- Does NOT require `--field` argument (operates on the whole type)

### Supporting infrastructure

**`derived_type_parser.py` changes:**
- `DerivedTypeField` now has `has_default_init` and `default_init_value` fields
- `_parse_field_declaration()` detects `= value` in both scalar and array field declarations
- `classify_field_blockers()` now reports LOGICAL as a blocker (was removed in Phase 4A as advisory-only), COMPLEX has an actionable fix suggestion, and `default_initializer` is a new blocker reason

**`build_fixer.py` addition:**
- New `logical_type_mismatch` fix pattern in DEFAULT_FIX_REGISTRY
- Matches gfortran "Type mismatch" errors mentioning LOGICAL
- Fixer finds `word%word` expression near the error column and wraps with `(/= 0)`
- Acts as safety net for any references the main regex pass misses (none were needed in practice)

### Application to ROSCO

Fixes were applied in dependency order:
1. `--fix logical` for `restart` (58 refs across 5 files) — build succeeded, sim diff empty
2. `--fix logical` for `WriteThisStep` (2 refs, I/O only) — build succeeded, sim diff empty
3. `--fix complex` for `AWC_complexangle` (8 refs + complexI removal) — build succeeded, sim diff empty
4. `--fix character_array` for `ACC_INFILE` (5 refs, size=1024 confirmed) — build succeeded, sim diff empty
5. `--fix bind_c` for `LocalVariables` — triggered cascade: nested types WE, FilterParameters, piParams, resParams, rlParams also needed BIND(C)
6. `--fix bind_c` for all 5 nested types — build succeeded, sim diff empty

The BIND(C) cascade was expected — gfortran requires all nested types in a BIND(C) type to also be BIND(C). These types were already C-compatible (no blockers) from Phase 4B, they just needed the explicit attribute.

## Key Technical Decisions

**LOGICAL → INTEGER(C_INT) rather than INTEGER(4):** Using `C_INT` makes the C interop intent explicit and matches the ISO_C_BINDING convention. The runtime representation is identical (`INTEGER(4)` on all platforms), but `C_INT` documents that this field participates in Fortran/C interfacing.

**Function argument wrapping with `(/= 0)` rather than changing function signatures:** The functions (LPFilter, PIController, ratelimit, etc.) still have LOGICAL dummy arguments. Wrapping the call-site expression with `/= 0` is minimally invasive — it produces a LOGICAL temp from the integer comparison. The alternative (changing all function signatures to accept INTEGER) would require modifying every filter/controller function definition, which is far more disruptive and unnecessary since those functions haven't been translated yet.

**Binary I/O left unchanged:** WRITE/READ of LOGICAL and INTEGER(C_INT) both transfer raw bytes. The binary checkpoint format remains compatible because the storage size is identical. No migration or version bump needed.

**COMPLEX split into `_re`/`_im` rather than C99 `_Complex`:** Fortran's `COMPLEX` and C99's `_Complex double` have compatible memory layout, but using two REAL fields is simpler for the C struct, avoids C99 complex math dependencies, and makes the Euler decomposition explicit in the Fortran code. The AWC feature only uses the real part for pitch commands, so the split is semantically clean.

**ACC_INFILE size = 1024:** The runtime size comes from `avrSWAP(50)` (simulator-provided filename length). 1024 is a standard PATH_MAX-like upper bound. The actual filenames in ROSCO simulations are ~50-100 characters, so 1024 is generous.

## Bugs Found and Fixed During Implementation

1. **Nested paren regex for Euler formula:** Initial `[^)]+` pattern consumed the closing paren of `AWC_angle(K)`, producing `COS(AWC_angle(K)` (unbalanced). Fixed with `[^()]*(?:\([^()]*\))[^()]*` to handle one level of nesting.

2. **`re.MULTILINE` missing on complexI removal:** The `_remove_complexi_decl` function used `^` anchor without `re.MULTILINE`, so `re.search(content)` on the full file content only matched if the COMPLEX line was the first line. Fixed by adding `re.MULTILINE` flag.

## Verification Results

- **440 unit tests** pass (27 new tests added)
- **12/12 golden fixture verifications** — all 40/40 passed
- **Simulation diff** — byte-identical after all fixes
- **`vit analyze-types`** — LocalVariables: 168 fields, 0 blockers, C-compatible

## Files Modified

### VIT repo (7 files)
| File | Changes |
|------|---------|
| `vit/derived_type_parser.py` | LOGICAL/default_initializer blockers, `= value` parsing in fields |
| `vit/analyze_types.py` | `fix_logical_field()`, `fix_complex_field()`, `fix_character_array_allocatable()`, `fix_bind_c()` + helpers (~350 lines) |
| `vit/build_fixer.py` | LOGICAL type mismatch fix pattern |
| `vit/cli.py` | Extended `--fix` choices and dispatch |
| `tests/test_analyze_types.py` | 27 new tests (TestFixLogical, TestFixComplex, TestFixCharacterArray, TestFixBindC) |
| `tests/test_build_fixer.py` | 3 new tests (TestLogicalTypeMismatchFix) |

### ROSCO repo (6 files, modified by automated fixes)
| File | Changes |
|------|---------|
| `ROSCO_Types.f90` | LOGICAL→INTEGER(C_INT) (2 fields), COMPLEX→REAL split (1 field, +1 line), CHARACTER array→fixed (1 field), BIND(C) added (6 types), default init removed (1 field) |
| `Controllers.f90` | 30 restart arg wraps, AWC Euler→COS/SIN (+3 lines), REAL()→_re, zero init split (+1 line), complexI commented |
| `ControllerBlocks.f90` | 11 restart arg wraps, 1 IF condition rewrite |
| `Filters.f90` | 18 restart arg wraps |
| `ReadSetParameters.f90` | 2 assignment rewrites (.True.→1, .False.→0), 1 arg wrap, 1 ALLOCATE commented |
| `ROSCO_IO.f90` | 6 AWC I/O splits (+6 lines), 1 output data→_re, 1 ALLOCATE commented |

## What This Unlocks

With LocalVariables now BIND(C)-compatible, the following phases are unblocked:
- **Phase 7:** Controllers.f90 functions (~15 functions that take LocalVariables)
- **Phase 8:** ControllerBlocks.f90 functions (~8 functions that take LocalVariables)

These represent the bulk of ROSCO's control logic. Combined with the already-translated Functions.f90 and Filters.f90, this will bring ROSCO close to full C++ translation.

## Related Notes
- [202603101739-project-inception.md](202603101739-project-inception.md) — Project inception and Phase 1
- Phase 4A (ErrorVariables fix) established the `--fix character` pattern that Phase 4C extends
- Phase 4B (nested type compatibility) made WE/FilterParameters/piParams/resParams/rlParams C-compatible, which Phase 4C leverages for BIND(C)
