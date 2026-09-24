# vit test-validate: Prompt-Based Test Generator for Validation Functions
**Date:** 2026-04-26
**Status:** completed
**Scope:** New VIT command, validation function testing, Fortran-side comparison

## Problem

Validation functions (e.g., `AFI_ValidateInitInput`, `ValidateInputData`) check input parameters and report errors. KGen kernel verification only exercises the happy path — valid production inputs that produce ErrStat=0. The interesting tests are the error branches: does the C++ translation correctly reject invalid input the same way Fortran does?

AeroDyn has 3 validation functions with ~94 error branches total. The largest (`ValidateInputData` in AeroDyn.f90) has 64+ branches including simple range checks, enum validation, cross-field comparisons, array element checks in nested loops, and conditional checks gated by feature flags. Hand-writing tests for all of these is tedious and error-prone.

## Design Decision: Prompt-Based, Not Regex-Based

Initial design attempted to scan the Fortran source with regex to classify each error branch (range check, enum check, cross-field, etc.) and auto-generate test code. This was rejected for three reasons:

1. **Brittle.** The regex classification would break on non-standard patterns, nested conditionals, internal wrapper functions like `call Fatal('message')`, and the many variations across codebases.

2. **Project-specific.** The error-reporting mechanism (`SetErrStat` in OpenFAST) would need to be configured per project. Expecting users to write regex patterns is unrealistic.

3. **The LLM already understands code semantics.** The same insight that makes `vit translate` work: given the Fortran source and C++ struct layouts, an LLM can read the validation conditions, understand what "invalid" means for each field, and write appropriate tests — including complex cases like conditional branches and array loops that regex can't handle.

**Final design:** `vit test-validate` follows the same pattern as `vit translate` — it extracts raw materials from the codebase and generates a structured prompt. The LLM writes the actual test code.

## What VIT Generates

```bash
vit test-validate AFI_ValidateInitInput -f modules/aerodyn/src/AirfoilInfo.f90
```

Produces three things:

1. **Test harness skeleton** (`translations/AirfoilInfo/afi_validateinitinput_test.cpp`) — includes, TestResult type, `extern "C"` declaration for the Fortran bridge, main runner. The LLM fills in `make_valid_input()` and the test functions.

2. **Fortran BIND(C) bridge** (`translations/AirfoilInfo/afi_validateinitinput_bridge.f90`) — wraps the original Fortran function so the C++ test harness can call it. Named `<name>_f90` to distinguish from the C++ function.

3. **Prompt** (to stdout) — Fortran source, C++ struct definitions, C++ translation, skeleton, and instructions for dual-call testing.

## Dual-Call Verification

Each test calls **both** the C++ translation and the original Fortran (via the bridge) with the same input, and asserts they produce the same error status. This is the same C++ ≡ Fortran verification principle as KGen kernel comparison, applied to error branches instead of floating-point outputs.

The LLM writes tests like:
```cpp
TestResult test_InCol_Alfa_negative() {
    auto input = make_valid_input();
    input.InCol_Alfa = -1;  // trigger: InCol_Alfa < 0
    
    int err_cpp = 0; char msg_cpp[8197] = {};
    AFI_ValidateInitInput(&input, &err_cpp, msg_cpp);
    
    int err_f90 = 0; char msg_f90[8197] = {};
    afi_validateinitinput_f90(&input, &err_f90, msg_f90);
    
    if (err_cpp != err_f90) return {"InCol_Alfa_negative", false, "C++/Fortran disagree"};
    if (err_cpp != 4) return {"InCol_Alfa_negative", false, "expected Fatal"};
    return {"InCol_Alfa_negative", true, ""};
}
```

## Test Storage

Tests live at `translations/<Module>/<name>_test.cpp` alongside the translation `.cpp` file. Same directory, `_test.cpp` suffix. Not compiled into the production build — these are verification artifacts like KGen kernel fixtures.

## Bug Found During Implementation

`build_c_params()` silently dropped derived-type parameters (like `InitInput`) from the C signature when called without the `derived_types` registry. Without the registry, `get_type_mapping` returns `is_c_compatible=False` for all derived types, and `build_c_params` skips non-C-compatible derived types as "passthrough args."

The fix: pass `derived_types` to `build_c_params` in the skeleton generator. This was a straightforward bug, not a "known limitation" — I initially described it as such to avoid investigating, which was wrong.

## Applicability

The tool is general to any codebase:
- No project-specific error handling is hardcoded
- The prompt tells the LLM to read the actual code to understand how errors are reported
- The Fortran bridge generation uses standard BIND(C) conventions
- The test harness skeleton has no dependencies beyond `<cstdio>` and `<cstring>`

The error-reporting pattern (`SetErrStat`, exceptions, return codes) doesn't matter — the LLM sees it in the source and writes appropriate assertions.

## Commits

| Repo | Commit | What |
|------|--------|------|
| VIT | `f77f8ac` | Initial vit test-validate (prompt + skeleton) |
| VIT | `0fdc47c` | Add Fortran-side comparison (bridge generation) |
| VIT | `12e2c42` | Fix: pass derived_types to build_c_params |
