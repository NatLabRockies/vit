# VIT File Naming Convention Cleanup

**Date:** 2026-03-29
**Status:** completed
**Scope:** Rename VIT-generated C++ files from `{name}_impl.hpp` / `{name}_cpp.cpp` to `{name}.hpp` / `{name}.cpp`

## Background

VIT generates two C++ files during kernel verification and integration:

1. A copy of the user's translation, included via `#include`
2. A wrapper file with the `extern "C"` entry point that Fortran calls

These had awkward names:
- `{name}_impl.hpp` — the `_impl` suffix was confusing because VIT also uses `_impl` as a suffix on *function names* for libc collision avoidance (e.g., `shutdown_impl()`). A file named `shutdown_impl.hpp` looked like it contained a function named `shutdown_impl`, but it was just the implementation file for a function named `Shutdown`.
- `{name}_cpp.cpp` — redundant. A `.cpp` file is already C++; `_cpp` adds nothing.

## Changes

### New naming convention

| Purpose | Old name | New name |
|---------|----------|----------|
| User's translation (included) | `{name}_impl.hpp` | `{name}.hpp` |
| Generated wrapper | `{name}_cpp.cpp` | `{name}.cpp` |

### VIT source changes (3 files)

**`kernel_verify.py`** (3 lines):
- Line 151: `f"{sig.name.lower()}_impl.hpp"` → `f"{sig.name.lower()}.hpp"`
- Line 210: `f"{sig.name.lower()}_cpp.cpp"` → `f"{sig.name.lower()}.cpp"`
- Line 909: `f"{sig.name.lower()}_cpp.cpp"` → `f"{sig.name.lower()}.cpp"`

Also fixed a path resolution bug on line 211: changed `impl_filename` (short name) to `str(impl_dest)` (full path) so that `generate_cpp_wrapper()` can read the file to auto-detect the function name. This bug was introduced by the source casing change — the wrapper generator fell back to `sig.cpp_impl_name` (source-cased) when it couldn't find the file, producing a mismatched wrapper call.

**`integrate.py`** (2 lines):
- Line 114: `f"{sig.name.lower()}_cpp.cpp"` → `f"{sig.name.lower()}.cpp"`
- Line 116: `f"{sig.name.lower()}_impl.hpp"` → `f"{sig.name.lower()}.hpp"`

**`interface_gen.py`**: No change needed — the `(?:_impl)?` regex pattern matches both function names with and without the libc collision suffix, which is a function naming concern, not a file naming concern.

### VIT test changes (4 files)

- `test_core.py`: `*_impl.hpp` → `*.hpp` (5 occurrences)
- `test_derived_types.py`: `*_impl.hpp` → `*.hpp` (2 occurrences)
- `test_integrate.py`: `*_cpp.cpp` → `*.cpp` (40+ occurrences)
- `test_kernel_verify.py`: `test_cpp.cpp` → `test.cpp` (4 occurrences)

### ROSCO file renames

- 39 kernel fixture files: `kernel/{name}/{name}_impl.hpp` → `kernel/{name}/{name}.hpp`
- 39 kernel fixture files: `kernel/{name}/{name}_cpp.cpp` → `kernel/{name}/{name}.cpp`
- 39 integrated source files: `rosco/controller/src/{name}_cpp.cpp` → `rosco/controller/src/{name}.cpp`
- `CMakeLists.txt`: all 39 `src/{name}_cpp.cpp` → `src/{name}.cpp` entries
- `scripts/reset_to_clean.sh`: glob `*_cpp.cpp` → `*.cpp`

## The path resolution bug

During the source casing preservation work (dev note `202603282208`), a bug was introduced in kernel verification. The `generate_cpp_wrapper()` function in `interface_gen.py` was updated to read the implementation file and auto-detect the actual function name (handling old lowercase translations alongside new source-cased ones). But `kernel_verify.py` passed only the *short filename* (`lpfilter_impl.hpp`) instead of the full path (`kernel/LPFilter/lpfilter_impl.hpp`). The file read failed silently (caught by try/except), and the function fell back to `sig.cpp_impl_name` — which returned `LPFilter` (source casing), not `lpfilter` (what the old translation actually defines). The wrapper called `LPFilter(...)` but the translation defined `lpfilter(...)` → linker error.

Fixed by passing `str(impl_dest)` (full path) instead of `impl_filename` (short name).

## Test Results

- Unit tests: 566/566 passed
- Kernel verification: 39/39 passed (including Shutdown and WindSpeedEstimator — previously broken by bridge dependencies, now fully working with pure C++ translations)
- Integration: 39/39 passed
- 9-scenario comparison: ALL IDENTICAL — 672,000 values
