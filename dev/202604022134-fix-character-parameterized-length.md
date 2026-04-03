# Fix VIT: Support CHARACTER(variable) Function Arguments

**Date:** 2026-04-02
**Status:** completed
**Scope:** VIT type mapper and interface generator — handle parameterized-length CHARACTER arguments

## Problem

`vit translate` and `vit integrate` failed on any function with a `CHARACTER(len_param)` argument — where the CHARACTER length comes from another function parameter rather than a compile-time literal. All three ROSCO_IO.f90 functions (WriteRestartFile, ReadRestartFile, Debug) have this pattern:

```fortran
INTEGER(IntKi), INTENT(IN)             :: size_avcOUTNAME
CHARACTER(size_avcOUTNAME), INTENT(IN) :: RootName
```

Running `vit translate WriteRestartFile` produced:
```
ERROR: Cannot map Fortran type 'CHARACTER(size_avcOUTNAME)'
(normalized: 'CHARACTER(SIZE_AVCOUTNAME)').
Deferred-length CHARACTER(:) is not C-compatible.
```

This forced Stage C to use manual integration in `integrate_all.sh`, and would have blocked Stage E's `SetParameters` (same pattern).

## Root Cause

`type_map.py:146` — the `isdigit()` gate:

```python
if normalized.startswith("CHARACTER("):
    inner = normalized[10:-1].strip()  # "SIZE_AVCOUTNAME"
    if inner.isdigit():                # False — not a number
        # ... handle fixed-length ...
    raise ValueError(...)              # Falls through to error
```

The type mapper only accepted numeric CHARACTER lengths (e.g., `CHARACTER(1024)`). Variable names like `SIZE_AVCOUTNAME` failed `isdigit()` and were incorrectly classified as deferred-length `CHARACTER(:)`.

## Fix

Three files changed:

### 1. `vit/type_map.py` — Accept parameterized CHARACTER

Added `character_len_param: Optional[str]` field to `TypeMapping` dataclass. After the `isdigit()` check, added a new branch that recognizes variable names (alphanumeric + underscores, not `:` or `*`):

```python
if inner not in (':', '*') and not inner.startswith('LEN=') and inner.replace('_', '').isalnum():
    return TypeMapping(
        fortran_type=fortran_type.strip(),
        fortran_kind=inner,
        c_kind="C_CHAR",
        c_type="char",
        iso_c_type="CHARACTER(KIND=C_CHAR)",
        byte_size=1,
        character_len_param=inner.lower(),
    )
```

This produces a TypeMapping where `character_length=None` (no compile-time length) and `character_len_param="size_avcoutname"` (the parameter that carries the length). In C, this maps to `char*` with the length passed as a separate integer argument (which is already in the function signature).

### 2. `vit/interface_gen.py` — CHARACTER args as pointers

Two functions updated to check `mapping.character_len_param` (or `character_length > 1`):

**`build_c_params`:** CHARACTER args with parameterized or fixed length > 1 → `char*` pointer (not `char` value).

**`_build_interface_args`:** Same args → `CHARACTER(KIND=C_CHAR), INTENT(IN) :: name(*)` in the BIND(C) INTERFACE block.

This also fixes a latent bug: `CHARACTER(1024)` function arguments would have been mapped to a single `char` value (never triggered because no previously translated functions had CHARACTER arguments in their signatures).

### 3. `vit/tests/test_analyze_types.py` — New test cases

Added 4 tests:
- `test_character_parameterized_mapping` — `CHARACTER(size_avcOUTNAME)` → char with len_param
- `test_character_parameterized_simple` — `CHARACTER(N)` → char with len_param
- `test_character_parameterized_with_underscore` — `CHARACTER(my_len)` → char with len_param
- `test_character_assumed_raises` — `CHARACTER(*)` still raises ValueError

## Verification

- **598 unit tests pass** (594 existing + 4 new)
- **`vit translate WriteRestartFile`** now succeeds — generates correct scaffold with `char* RootName, int size_avcOUTNAME` C signature
- No changes to verification or extraction code — `verify_all.sh` regression not needed

## Generated C Signature (confirmed correct)

```cpp
void WriteRestartFile(localvariables_t* LocalVar, controlparameters_view_t* CntrPar,
                      errorvariables_t* ErrVar, objectinstances_t* objInst,
                      char* RootName, int size_avcOUTNAME) {
```

## Impact

- Stage E's `SetParameters` (which has the same `CHARACTER(size_avcOUTNAME)` pattern) can now use `vit translate` and `vit integrate` instead of manual integration
- Any future Fortran function with parameterized CHARACTER arguments is now supported
- Stage C's existing manual integration still works — this fix enables but doesn't require changing the integration approach

## Cleanup Note

Running `verify_all.sh` during testing contaminated 4 kernel fixture files (`kernel/sigma/Functions.f90`, `kernel/wrap_180/Functions.f90`, `kernel/ratelimit/Functions.f90`, `kernel/wrap_360/Functions.f90`) because the interrupted verification left patched source files unrestore. These were cleaned up with `git checkout --`. Lesson: don't run `verify_all.sh` when it's not needed — it patches kernel source files during execution and only restores them on clean completion.

## Files Changed

| File | Change |
|------|--------|
| `vit/vit/type_map.py` | Added `character_len_param` field, handle `CHARACTER(variable)` |
| `vit/vit/interface_gen.py` | CHARACTER args → `char*` pointer + `CHARACTER(KIND=C_CHAR) :: name(*)` |
| `vit/tests/test_analyze_types.py` | 4 new parameterized CHARACTER test cases |
