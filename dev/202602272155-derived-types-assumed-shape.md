# Derived Type and Assumed-Shape Array Support
**Date:** 2026-02-27
**Status:** completed
**Scope:** derived_type_parser.py, fortran_parser.py, type_map.py, interface_gen.py, integrate.py, tests

## Background

The first two VIT target functions — saturate and wrap_180 — have trivial
signatures: scalar REAL(DbKi) arguments in, scalar REAL(DbKi) result out. The
third target, `interp1d`, is the first function that exercises non-trivial
Fortran types:

```fortran
FUNCTION interp1d(xData, yData, xq, ErrVar) RESULT(yq)
    REAL(DbKi), DIMENSION(:), INTENT(IN)       :: xData
    REAL(DbKi), DIMENSION(:), INTENT(IN)       :: yData
    REAL(DbKi),               INTENT(IN)       :: xq
    TYPE(ErrorVariables),     INTENT(INOUT)     :: ErrVar
    REAL(DbKi)                                  :: yq
```

Two new type categories that VIT had no support for:

1. **Assumed-shape arrays** (`DIMENSION(:)`): These have no compile-time size.
   Fortran passes them with hidden descriptors that carry shape information.
   There is no direct C equivalent — C interop requires explicit sizes.

2. **Derived types** (`TYPE(ErrorVariables)`): A user-defined composite type.
   Whether it can cross the Fortran/C boundary depends on whether it has
   `BIND(C)` and whether all its fields are C-compatible.

Without support for these, interp1d could not be translated through VIT.

## Implementation

### Derived Type Parser (derived_type_parser.py)

New module (253 lines) that parses Fortran TYPE definitions from source files.
Given a type name and source file path, it:

1. Finds the `TYPE :: TypeName` ... `END TYPE` block.
2. Extracts each field's name, type, and attributes (ALLOCATABLE, POINTER,
   DIMENSION, etc.).
3. Determines BIND(C) compatibility: a type is compatible if and only if it has
   `BIND(C)` in its definition AND all fields are C-interoperable types (no
   ALLOCATABLE, no POINTER, no CHARACTER of variable length, no nested
   non-BIND(C) types).

ErrorVariables in ROSCO contains ALLOCATABLE arrays, CHARACTER strings, and
INTEGER fields — it is definitively not BIND(C) compatible.

### Fortran Parser Updates (fortran_parser.py)

The argument parser now recognizes `TYPE(...)` in declarations. When a
`TYPE(ErrorVariables)` argument is found, the parser stores the derived type
name (`ErrorVariables`) on the FortranArg object. This metadata flows through
to the type mapper and interface generator.

### Type Mapping Updates (type_map.py)

Previously, encountering `TYPE(...)` in get_type_mapping() raised a ValueError,
halting the pipeline. Now:

- A new `get_derived_type_mapping()` function returns a TypeMapping with an
  `is_c_compatible` flag.
- Non-compatible types (like ErrorVariables) get a "passthrough" mapping:
  they are recognized but explicitly excluded from the C interface.
- Compatible types (hypothetical future case with BIND(C) types) would get
  a direct mapping to a C struct.

### Interface Generator Updates (interface_gen.py)

The interface generator now handles three argument categories:

1. **Scalar C-compatible args**: Passed through BIND(C) as before (e.g., `xq`).
2. **Assumed-shape array args**: Converted to assumed-size (`*`) in the BIND(C)
   interface, with a synthetic size parameter.
3. **Passthrough args**: Accepted by the Fortran wrapper but excluded from the
   BIND(C) interface and C function signature entirely.

For assumed-shape arrays, VIT generates a synthetic `n_<array_name>` INTEGER
parameter for each array. The Fortran wrapper passes `SIZE(array)` for each.
The generated code for interp1d looks conceptually like:

```fortran
SUBROUTINE interp1d_c_wrapper(xData, n_xData, yData, n_yData, xq, ErrVar, result) BIND(C)
    REAL(C_DOUBLE), INTENT(IN) :: xData(*)
    INTEGER(C_INT), VALUE, INTENT(IN) :: n_xData
    REAL(C_DOUBLE), INTENT(IN) :: yData(*)
    INTEGER(C_INT), VALUE, INTENT(IN) :: n_yData
    REAL(C_DOUBLE), VALUE, INTENT(IN) :: xq
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar  ! passthrough, not in C interface
    REAL(C_DOUBLE), INTENT(OUT) :: result
    ! calls C++ function with (xData, n_xData, yData, n_yData, xq)
    ! ErrVar passed to original Fortran for error handling
END SUBROUTINE
```

The C++ extern "C" function and user-facing C++ function both receive the
per-array size parameters.

### Passthrough Design Decision

The key architectural decision was how to handle ErrorVariables. Three options
were considered:

1. **Full marshalling**: Convert ErrorVariables to a C struct, pass across the
   boundary, convert back. This would require deep introspection of the type
   (ALLOCATABLE arrays need descriptor conversion, CHARACTER fields need
   length handling) and would be fragile across compiler versions.

2. **Opaque pointer**: Pass a void* to C++, let the C++ code cast it back.
   This requires the C++ code to know the Fortran memory layout, which is
   compiler-specific and defeats the purpose of a portable translation.

3. **Passthrough**: The Fortran wrapper accepts ErrorVariables but does not
   pass it to C++. The C++ function implements only the core algorithm.
   Error handling remains in Fortran.

Option 3 was chosen because it is correct for interp1d's actual semantics:
the interpolation algorithm does not depend on ErrorVariables. The function
uses ErrVar only to report errors (out-of-range queries, mismatched array
sizes), and those checks can remain in Fortran or be re-implemented in C++
independently. This avoids the complexity of cross-language derived type
marshalling entirely.

The passthrough approach would NOT be correct for a function whose core
algorithm reads or modifies derived type fields. That case would require
option 1 or 2, which VIT does not yet support (documented as a known
limitation).

### Per-Array Size Parameters: Why Not Coalesce?

For interp1d, xData and yData are semantically the same length (paired x/y
data points for interpolation). It would be natural to generate a single `n`
parameter rather than separate `n_xData` and `n_yData`. This was considered
and deliberately rejected:

- VIT would need to infer that two arrays share the same size, which requires
  either semantic analysis of the algorithm or explicit user annotation.
- VIT's design philosophy is mechanical correctness over cleverness — generate
  code that is provably correct from the type signatures alone, without
  requiring understanding of the algorithm.
- The per-array approach is always correct (each array knows its own size),
  while coalescing could be wrong if arrays legitimately have different sizes.
- The C++ translation simply accepts both size parameters. The slight API
  ugliness is an acceptable trade-off for guaranteed correctness.

## Testing

49 new tests in test_derived_types.py (480 lines) covering:

- Derived type parsing: field extraction, attribute detection, BIND(C)
  compatibility determination.
- Passthrough mapping: TYPE(ErrorVariables) correctly identified as
  non-C-compatible and excluded from C interface.
- Assumed-shape handling: synthetic size parameters generated, DIMENSION(:)
  converted to assumed-size (*).
- Integration: interp1d-shaped functions produce correct Fortran wrapper,
  BIND(C) interface, and C++ extern "C" signature.
- Edge cases: types with only ALLOCATABLE fields, types with only scalar
  fields, mixed types.

Updates to test_integrate.py for interp1d integration plan generation.

## Follow-Up Commit: API Rename (12287a5)

Six minutes after the main commit, `_build_c_params` was renamed to
`build_c_params` (removing the leading underscore). The function was being
imported by integrate.py and test_derived_types.py across module boundaries,
making the "private" naming convention misleading. This was a pure rename
with no logic changes (3 files, 11 lines changed in each direction).

## Verification

- 207/207 unit tests passing.
- saturate: 40/40 passed.
- wrap_180: 40/40 passed.
- interp1d: 40/40 passed.
- Full end-to-end: all three functions integrated, ROSCO rebuilt, simulation
  output byte-identical to pure-Fortran baseline.

## Known Limitations Documented

This commit also established the "Known Limitations" section in CLAUDE.md:

- Per-array size params (as discussed above).
- Derived type registry not wired into CLI pipeline.
- No multi-dimensional assumed-shape handling (DIMENSION(:,:) would silently
  generate broken code).
- USE statement matching is string-based (works for ONLY imports, would miss
  bare USE).

## Files Changed

- `vit/vit/derived_type_parser.py`: New module, 253 lines.
- `vit/vit/fortran_parser.py`: TYPE(...) recognition in argument declarations.
- `vit/vit/type_map.py`: Derived type mapping with C-compatibility flag.
- `vit/vit/interface_gen.py`: Passthrough args, assumed-shape conversion,
  synthetic size parameters.
- `vit/vit/integrate.py`: interp1d integration plan generation.
- `vit/tests/test_derived_types.py`: New test file, 480 lines, 49 tests.

## Commits

- f85e4fa — Derived type and assumed-shape array support (1,022 insertions,
  95 deletions)
- 12287a5 — Rename _build_c_params to build_c_params (11 lines)
