# Comprehensive Unit Test Suite
**Date:** 2026-02-27
**Status:** completed
**Scope:** tests

## Background

By late February, VIT had accumulated significant complexity across extraction, verification, NaN handling, integration planning, and configuration management. The existing test suite had 35 tests, concentrated in `test_core.py`, covering basic type mapping and interface generation. Large areas of the codebase — extraction pragma management, NaN comparison patching, verification output parsing, integration plan generation, CMake patching, and config serialization — had no test coverage.

This was a deliberate "stop and test everything" effort before adding more features. The NaN handling work (see `202602260809-nan-handling.md`) had demonstrated how subtle bugs can hide in string-transformation logic, and the upcoming derived-type support would add more complexity to the same code paths.

## What Changed

### Commit 8cf8882 (2026-02-27)

4 files changed, 1,284 insertions. Added 96 new tests across 4 test files, bringing the total from 35 to 131.

### Test File Breakdown

#### test_extract.py

Tests for the extraction pipeline's code-generation and cleanup logic:

- **KGen pragma insertion**: Verifying that `generate_kgen_command()` produces correct `--invocation` flags, that pragma markers are inserted at the right source lines, and that the call-site file (not the definition file) is correctly identified.
- **Pragma removal**: After extraction, VIT must remove any KGen pragmas it inserted. Tests verify that removal is idempotent (running it twice doesn't corrupt the file) and that it handles edge cases like pragmas adjacent to comments or continuation lines.
- **Artifact cleanup**: After a successful or failed extraction, VIT cleans up temporary files (state directories, model.ini, include.ini, kgen.log). Tests verify that cleanup removes the right files and doesn't touch unrelated files in the working directory.
- **Source restoration**: Extraction modifies the Fortran source file (to add KGen pragmas). Tests verify that the original source is restored after extraction completes, whether it succeeds or fails.

#### test_kernel_verify.py

Tests for the verification kernel patching and output parsing:

- **NaN comparison patching** (`_patch_nan_comparisons`): The core of NaN-safe verification. Tests use small synthetic Fortran snippets that mimic KGen's generated comparison code. Verifies that the `x /= x` NaN check is inserted correctly, that it handles REAL(4), REAL(8), and other kinds, and that it doesn't corrupt non-comparison code.
- **USE statement ordering**: KGen's generated code sometimes has USE statements in an order that causes compilation failures (e.g., a module that depends on another module is USE'd before its dependency). VIT patches the USE order. Tests verify correct reordering with various dependency graphs.
- **Makefile patching**: VIT modifies KGen's generated Makefile to add C++ compilation rules. Tests verify that the C++ source file, compiler flags, and link rules are inserted correctly.
- **Verification output parsing**: Tests for the logic that reads KGen's stdout and extracts pass/fail/skip counts. Uses representative output samples covering clean passes, NaN skips, failures, and mixed results. Verifies correct counting for "40/40 passed", "38/40 passed, 2 skipped", and failure cases.

#### test_integrate.py

Tests for integration plan generation and patch application:

- **Integration plan generation**: Given a function name and its C++ translation file, VIT generates a plan describing all the changes needed to replace the Fortran function with C++. Tests verify the plan structure: Fortran wrapper function, ISO_C_BINDING declarations, CMake modifications, and C++ file placement.
- **CMake patch generation**: Tests for `_generate_cmake_patches()` which produces the diff needed to add C++ compilation to the existing CMakeLists.txt. Verifies that `enable_language(CXX)` is added, the C++ source is added to the target's source list, and `stdc++` is added to the link libraries.
- **Idempotent patching**: When integrating a second function after a first has already been integrated, the CMake patches should not duplicate `enable_language(CXX)` or `stdc++`. Tests verify that already-present items are correctly detected and skipped.
- **Fortran source patching**: Tests for the modifications to the Fortran source file: adding `USE ISO_C_BINDING`, replacing the function body with a call to the C++ wrapper, and preserving the function signature and intent declarations.

#### test_config.py

Tests for configuration serialization:

- **Round-trip fidelity**: Write a `VitConfig` to YAML, read it back, verify all fields match. Covers all config sections: project, kgen, build_system, fortran, verification, translations.
- **Default handling**: Verify that missing optional fields in the YAML file are filled with correct defaults (e.g., `invocation` defaults to `"0:0:1-20"`, `tolerance` defaults to `1.0e-14`).
- **Kind alias loading**: The `fortran.kind_aliases` section maps project-specific type names to standard Fortran types. Tests verify correct loading and that aliases are available to the type mapping system.

## Design Principles

### Pure-logic tests only

Every test in this suite operates on strings, data structures, and file contents. None of them require Docker, KGen, ROSCO, gfortran, or any external tool. This makes them:

- **Fast**: The full suite runs in under 2 seconds.
- **Portable**: Runs on any machine with Python 3.10+ and pytest.
- **Deterministic**: No I/O, no network, no subprocess calls.

For integration-level testing (does the full pipeline produce correct results?), the golden fixtures in `/workspace/ROSCO/kernel/` serve that role. The unit tests verify that each component produces correct intermediate artifacts; the golden-fixture tests verify that the assembled pipeline produces correct end-to-end results.

### Synthetic Fortran snippets

Rather than testing against real ROSCO source files (which are large and could change), tests use small hand-crafted Fortran snippets that exercise specific code paths. For example, the NaN patching tests use a 10-line Fortran comparison block rather than the full 200-line KGen-generated verification file. This makes tests focused, readable, and resistant to upstream changes.

### Matching existing conventions

All new tests follow the patterns established in `test_core.py`: pytest functions (not classes), descriptive names prefixed with `test_`, inline assertions with clear messages, and `tmp_path` fixtures for any file I/O.

## Files Modified

- `vit/tests/test_extract.py` (new)
- `vit/tests/test_kernel_verify.py` (new)
- `vit/tests/test_integrate.py` (new)
- `vit/tests/test_config.py` (new)

## Impact

This test suite became the safety net for all subsequent development. The NaN handling, derived-type support, and multi-function integration features were all developed with confidence that existing behavior was preserved, because the unit tests would catch regressions immediately. The 131-test suite runs in under 2 seconds, making it practical to run after every change.
