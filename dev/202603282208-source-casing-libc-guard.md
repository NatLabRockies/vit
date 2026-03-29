# Preserve Fortran Source Casing + Dynamic libc Collision Guard

**Date:** 2026-03-28
**Status:** completed
**Scope:** VIT codegen — function naming in translate.py, integrate.py, fortran_parser.py

## Background

The Phase 8C investigation (dev note `202603282007`) uncovered that the C++ Shutdown translation was silently replaced by POSIX's `shutdown(2)` system call via PLT interposition in the shared library. The root cause: VIT's codegen lowercased all Fortran function names for C++ — `Shutdown` became `shutdown()`, which collides with the POSIX socket shutdown.

VIT already preserved source casing for types (fortran_parser.py:292), result names (:183), and module names (:406), but the function name was the one place it didn't — it took the CLI argument verbatim and lowercased it for C++.

## What Changed

### 1. Source-cased function names (`fortran_parser.py`)

**Before:** `FortranSignature.name` was set to `function_name` (the CLI argument). The parser searched case-insensitively but stored whatever the user typed.

**After:** The parser extracts the actual function name from the Fortran source line, just like it already does for types and result names:

```python
# After finding the FUNCTION/SUBROUTINE line
source_name_match = re.search(
    r'(?:FUNCTION|SUBROUTINE)\s+(\w+)\s*\(', sig_line, re.IGNORECASE
)
source_name = source_name_match.group(1) if source_name_match else function_name

# ...
return FortranSignature(name=source_name, ...)  # was: function_name
```

Now `SUBROUTINE Shutdown(...)` yields `sig.name == "Shutdown"` regardless of whether the user typed `shutdown`, `Shutdown`, or `SHUTDOWN` on the CLI.

### 2. Dynamic libc collision detection (`fortran_parser.py`)

Instead of a hardcoded list of POSIX names, VIT queries the actual dynamic linker at runtime:

```python
import ctypes

def _is_libc_symbol(name: str) -> bool:
    """Check if name is an exported symbol in the C library."""
    try:
        libc = ctypes.CDLL(None)  # default handle = all loaded libs
        getattr(libc, name)
        return True
    except (OSError, AttributeError):
        return False
```

`ctypes.CDLL(None)` opens the default shared object handle, which includes all symbols from libc and other loaded libraries — the exact same symbols the dynamic linker would resolve via PLT. This catches precisely the names that would collide, with no maintenance burden.

### 3. `cpp_impl_name` property (`fortran_parser.py`)

New property on `FortranSignature` that combines casing preservation with the libc safety net:

```python
@property
def cpp_impl_name(self) -> str:
    if _is_libc_symbol(self.name):
        return f"{self.name}_impl"
    return self.name
```

Results for ROSCO functions:
- `Shutdown` (PascalCase from source) → `Shutdown` (not a libc symbol — libc has lowercase `shutdown`)
- `shutdown` (if source were lowercase) → `shutdown_impl` (IS a libc symbol)
- `WindSpeedEstimator` → `WindSpeedEstimator` (not a libc symbol)
- `saturate` → `saturate` (not a libc symbol)

### 4. Integration wrapper auto-detection (`integrate.py`)

The wrapper generator now scans the implementation content to find the actual function name, handling both old lowercase translations and new source-cased ones:

```python
impl_name_match = re.search(
    rf'\b(?:void|double|int|float)\s+({re.escape(sig.name)}(?:_impl)?)\s*\(',
    impl_content, re.IGNORECASE
)
impl_func_name = impl_name_match.group(1) if impl_name_match else sig.cpp_impl_name
```

This means:
- Old translation `void colemantransform(...)` → wrapper calls `colemantransform(...)`
- New translation `void ColemanTransform(...)` → wrapper calls `ColemanTransform(...)`
- Renamed translation `void Shutdown(...)` → wrapper calls `Shutdown(...)`

No manual fixup needed after `integrate_all.sh`.

### 5. Scaffold generation (`translate.py`)

New scaffolds use `sig.cpp_impl_name`:

```python
func_name = sig.cpp_impl_name  # was: sig.name.lower()
```

### 6. `_c` wrapper name unchanged

`sig.c_func_name` stays lowercase (`shutdown_c`, `colemantransform_c`) for backward compatibility with existing Fortran BIND(C) interfaces.

## Files Modified

| File | Change |
|------|--------|
| `vit/vit/fortran_parser.py` | Extract source-cased name; add `_is_libc_symbol()` + `cpp_impl_name` property |
| `vit/vit/integrate.py` | Auto-detect impl function name from content; use it in wrapper |
| `vit/vit/translate.py` | Use `sig.cpp_impl_name` for scaffold generation |
| `vit/tests/test_fortran_parser.py` | 9 new tests for casing preservation and libc collision |
| `vit/tests/test_core.py` | Updated `test_case_insensitive` to expect source casing |
| `ROSCO/translations/ControllerBlocks/shutdown.cpp` | Renamed `shutdown_impl` → `Shutdown` (source casing) |

## Test Results

- 566 unit tests pass
- Full integration test: 39 functions, 9 scenarios, 672,000 values ALL IDENTICAL
- No manual fixup needed after `integrate_all.sh` — the `shutdown_c` wrapper correctly calls `Shutdown()`

## Migration Note

Existing translations use lowercase names (`saturate`, `lpfilter`, `windspeedestimator`). New translations generated by `vit translate` will use source casing (`Saturate`, `LPFilter`, `WindSpeedEstimator`). Both work correctly — the integration wrapper auto-detects the actual function name from the implementation content. The `_c` wrapper name stays lowercase for all, ensuring backward compatibility with existing Fortran BIND(C) interfaces.

## Design Decisions

**Why `ctypes.CDLL(None)` instead of a hardcoded list?** A static list requires maintenance and inevitably misses symbols. The dynamic approach queries the same symbol table the linker uses, so it's always correct for the current platform. It works on both Linux (container) and macOS (development), since most POSIX symbols exist on both.

**Why not just preserve casing and skip the libc check?** Preserving casing solves the `Shutdown` case (PascalCase doesn't collide with lowercase `shutdown`). But if a Fortran developer writes `FUNCTION close(x)` (Fortran is case-insensitive, so this is legal and would be stored as `close` in source), the lowercase C++ name `close` would collide with libc. The `_impl` suffix is the safety net for this edge case.

**Why auto-detect the function name from implementation content?** This handles the migration gracefully. Old translations with lowercase names work without modification. New translations with source casing work too. The wrapper always calls the right function.
