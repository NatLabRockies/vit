# Verification Pipeline Integrity Validation
**Date:** 2026-03-27
**Status:** completed
**Scope:** KGen verification pipeline, two-run comparison, C++ execution confirmation

## Origin

After the integration test "PASSED" string diff bombshell (dev note `202603261048`) — where we discovered the integration test was only comparing print statements, not simulation output — the user raised the same concern about KGen verification: "The strange thing is, we were getting 40/40 passed before we did Fortran and C++ — we were getting that just for Fortran, and just for C++. This is why I'm suspicious."

This is a legitimate concern. If both Fortran-only and C++-only runs independently report "40/40 passed," the verification might be trivially passing — comparing each against the state file reference (which was captured from Fortran) rather than comparing them against each other.

## Investigation

### What "40/40 passed" means

KGen captures 20 invocations of a function during a real simulation. The kernel replays each invocation twice (Fortran reference run + C++ run) = 40 total. For each invocation, every output field is compared between the Fortran and C++ runs.

### How pass/fail is determined

Traced through `kernel_verify.py`:

1. **Step 1** (line 466-467): Run the kernel with the ORIGINAL Fortran function body restored from `.kgen` backup. Capture field-by-field output into `fortran_field_data`.

2. **Step 2** (line 470): Run the kernel with the C++ wrapper replacing the function body. Capture field output into `cpp_result`.

3. **Step 3** (lines 487-519): Compare `fortran_field_data` against `cpp_result.field_data` field-by-field:
   - If ALL fields match → `success=True`, report all cases passed
   - If ANY field differs → `success=False`, report differences

4. **Fallback** (lines 523-525): If `sig is None` (no signature provided), the Fortran reference run is skipped and the result falls back to KGen's state file comparison only. This path is NOT taken from the CLI (sig is always set).

### Why both runs independently "pass"

Each individual run compares against KGen's state file reference (captured during extraction from the original Fortran). Both Fortran and C++ match the state file because:
- Fortran matches by definition (it produced the state file)
- Correct C++ matches because it reproduces the same output

This is expected behavior, not a bug. The individual "passed" lines in the output are from KGen's built-in comparison. VIT's two-run comparison is a SEPARATE check that overrides the final result.

### The critical question: Is the C++ actually being executed?

If `_replace_function_body()` fails silently, the kernel runs the original Fortran for both "runs," they match trivially, and "40/40 passed" is meaningless.

## Definitive Test: Break the C++ and Confirm Failure

Created a deliberately broken `saturate.cpp` that returns `val * 2.0` instead of the correct clamping logic:

```cpp
double saturate(double val, double minval, double maxval) {
    return val * 2.0;  // WRONG
}
```

Ran verification:
```
vit verify saturate broken_saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/saturate
```

**Result: ✗ VERIFICATION FAILED: 0/40 passed**

Every single case failed. The broken C++ was detected immediately. The verification is genuinely executing the C++ code and comparing its output against the Fortran reference.

Restored the correct translation and re-verified:

**Result: ✓ VERIFICATION PASSED: 40/40 passed**

The kernel cleanly restored from `.kgen` backups and the correct translation passed as expected.

## Conclusion

The KGen verification pipeline IS testing what we think it's testing:
- The C++ code IS being compiled and executed
- The two-run comparison IS driving the final pass/fail result
- A broken translation DOES produce a failure (0/40, not 40/40)
- The `.kgen` backup mechanism correctly restores clean state between runs

This is NOT the same class of bug as the integration test "PASSED" string diff. The verification is substantive, not cosmetic.

## Why the User's Observation Was Still Valid

The user observed "40/40 passed for Fortran, and 40/40 for C++." This IS what happens — both runs independently pass KGen's state file comparison. But VIT's final result is determined by the THIRD step: comparing the two runs against each other. The individual "Verification PASSED with saturate.0.0.N" lines in the output are from KGen's per-case state file check; VIT's overall "40/40 passed" is from the field-level cross-comparison.

The architecture is correct but the output is potentially misleading — the per-case "PASSED" lines suggest each case passes on its own merits, when in fact the definitive comparison is between the two runs. This could be improved by making the output clearer about what's being compared.
