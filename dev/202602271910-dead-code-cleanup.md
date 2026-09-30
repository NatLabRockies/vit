# Dead Code Removal and Cleanup
**Date:** 2026-02-27
**Status:** completed
**Scope:** cli, extract, type_map, interface_gen, tests

## Background

After the comprehensive unit test suite was in place (see `202602271826-unit-test-suite.md`), there was both motivation and safety to clean up dead code. The test suite served as a regression safety net — if removing a function broke something that was actually being used, a test would fail. Without the tests, removing code would have been risky because it was not always obvious from static inspection whether a function was called indirectly, used by a test, or truly unused.

This cleanup was also motivated by confusion during development: several functions had similar names and overlapping purposes (e.g., `generate_cmake_patch()` in interface_gen.py vs. `_generate_cmake_patches()` in integrate.py), making it unclear which was the canonical implementation. Removing the dead versions eliminated that ambiguity.

## What Changed

### Commit 0a2cb2b (2026-02-27)

5 files changed, 1 insertion, 110 deletions.

### Per-File Breakdown

#### cli.py

- **Removed unused `import json`**: The json module had been imported during early development when config was JSON-based. After switching to YAML, the import was never removed.
- **Fixed cmd_run() docstring**: The docstring incorrectly stated that `cmd_run` executed an "interface generation" step as part of the pipeline. The `vit run` command runs extract + verify, not interface generation. The interface step is implicit within verify. Updated the docstring to accurately describe the command's behavior.

#### extract.py

- **Removed unused `import textwrap`**: Was imported but never used. Likely left over from an early version that used `textwrap.dedent()` for generating multi-line command strings.
- **Removed `build_kgen_build_cmd()`**: This function constructed a KGen build command string from config parameters. It was defined but never called from any production code path. The actual KGen command construction happens in `generate_kgen_command()`, which takes a different approach (reading directly from config rather than building up arguments incrementally). `build_kgen_build_cmd()` was an earlier attempt that was superseded but never deleted.

#### type_map.py

- **Removed `c_to_fortran_cast()`**: Generated a Fortran expression to cast a C type to a Fortran type (e.g., `REAL(c_var, 8)` from a C `double`). This was part of an early design where type casts were generated centrally in type_map.py. The actual implementation generates casts inline within interface_gen.py, where the full context of the variable (intent, array dimensions, derived type membership) is available. The centralized approach was abandoned because it couldn't handle the contextual variations.
- **Removed `fortran_to_project_cast()`**: The inverse of `c_to_fortran_cast()` — generated a cast from standard Fortran types to project-specific kind aliases (e.g., from `REAL(8)` to `ReKi`). Same reason for removal: casts are done inline in interface_gen.py with full context.

#### interface_gen.py

- **Removed `generate_kernel_makefile_patch()`**: Generated a patch for KGen's kernel Makefile to add C++ compilation rules. This was superseded by `_patch_kernel_makefile()` in kernel_verify.py, which applies the same changes but does so directly (modifying the file in place) rather than generating a patch string. The direct approach was adopted because the Makefile format varies slightly between KGen versions, and in-place modification can adapt to what's actually present in the file.
- **Removed `generate_cmake_patch()`**: Generated a patch for the project's CMakeLists.txt to add C++ support. Superseded by `_generate_cmake_patches()` in integrate.py, which produces a structured list of patch operations rather than a monolithic diff string. The structured approach enables idempotent application (checking whether each change is already present before applying it), which is essential for integrating multiple functions sequentially.

#### test_extract.py

- **Removed tests for `build_kgen_build_cmd`**: Two test functions that exercised the now-removed `build_kgen_build_cmd()`. These were the only callers of that function, which is why it appeared to be "used" in a naive grep. Removing the function required removing its tests, and removing the tests confirmed that nothing else depended on the function.

## Impact

Net effect: 110 lines removed, 1 line added (the docstring fix). The codebase became smaller and less ambiguous:

- **No more duplicate implementations**: Before cleanup, there were two ways to generate CMake patches and two ways to generate Makefile patches. Now there is one of each, in the module where it belongs (integrate.py for project-level CMake, kernel_verify.py for kernel-level Makefile).
- **No more unused type-cast functions**: The type_map module now contains only the type mapping logic that is actually called. The unused cast functions had been a source of confusion because developers would see them, assume they were the right way to do casts, and try to use them — only to discover they didn't handle all the edge cases that the inline approach in interface_gen.py does.
- **Cleaner imports**: Removing unused imports makes it clearer what each module actually depends on.

## Design Decisions

- **Cleanup after tests, not before**: The test suite provided confidence that removed code was truly dead. Without tests, this cleanup would have been risky.
- **Remove tests for removed functions**: Tests that exercise dead code are themselves dead code. They add maintenance burden and can give a false sense of coverage.
- **One commit for all cleanup**: Rather than one commit per file, all removals were grouped into a single commit. This made it clear that the changes were a coordinated cleanup effort, not independent fixes.

## Files Modified

- `vit/cli.py`
- `vit/extract.py`
- `vit/type_map.py`
- `vit/interface_gen.py`
- `vit/tests/test_extract.py`
