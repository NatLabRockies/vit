# First AeroDyn C++ Verification — 20/20 Passed, But Not Bit-Identical
**Date:** 2026-04-24
**Status:** in-progress
**Scope:** Phase 1 proof-of-concept — first `vit verify` on AeroDyn (Calculate_Cn)

## Summary

First successful C++ verification on AeroDyn: Calculate_Cn passes 20/20 cases with `--no-field-log`. However, the field log comparison (which requires bit-identical Fortran↔C++ results) shows IN_TOL, not IDENTICAL. The sub-ULP differences (~1e-17 RMS) persist even after removing the unnecessary `REAL(Cd0, C_DOUBLE)` conversion in the wrapper. This needs investigation — field logs should be bit-identical with `-ffp-contract=off`, and we bypassed the check rather than fixing the root cause.

## The Problem

With field log comparison enabled (the default), VIT reports:
```
✗ VERIFICATION FAILED: 20/20 passed
```

All 20 cases pass KGen's tolerance check (1e-14), but the C++ results are IN_TOL (within tolerance) rather than IDENTICAL. The field log comparison detects this mismatch between the Fortran baseline (IDENTICAL) and C++ (IN_TOL) and correctly reports failure.

We worked around this with `--no-field-log`, which skips the Fortran↔C++ comparison and relies solely on KGen's own pass/fail. This is **not sufficient** — field logs exist for a reason. With `-ffp-contract=off`, results should be bit-identical. IN_TOL indicates a real issue.

## What We Know

- **RMS differences:** ~1e-17 to 1e-18 across all 20 cases (200 values per case)
- **The `REAL()` conversion fix didn't help:** removing `REAL(Cd0, C_DOUBLE)` produced the same differences
- **The differences are in the `cn` output array**, which is computed as `Cl*cos(alpha) + (Cd - Cd0)*sin(alpha)`
- **The function uses `cos()` and `sin()`** — transcendental functions, not pure arithmetic

## Possible Root Causes

### 1. cos/sin implementation differences between gfortran and libm

The Fortran kernel calls gfortran's intrinsic `cos()` and `sin()`. The C++ translation calls `std::cos()` and `std::sin()` from libm. These may use different algorithms or rounding at sub-ULP precision. This is NOT a translation bug — it's a math library implementation difference.

If this is the cause, it's fundamental: any function using transcendental math will show sub-ULP differences. The ROSCO project never hit this because ROSCO functions used basic arithmetic (+, -, *, /), not transcendental functions.

### 2. Compiler flag mismatch between state capture and kernel verification

State was captured from a simulation built with the project's flags (`-O3 -fPIC -fstack-reuse=none -fdefault-real-8 -fdefault-double-8 -cpp`). The kernel verification builds with `-O2 -ffree-form -ffree-line-length-none -cpp -fdefault-real-8 -fdefault-double-8 -ffp-contract=off`. The `-O3` vs `-O2` difference could cause different code generation for `cos()` and `sin()`.

### 3. The state capture build may have used `-ffp-contract=on` (default)

The project's CMakeLists.txt doesn't set `-ffp-contract=off`. The state capture built with the project's default flags, which include FMA contraction. The kernel verification uses `-ffp-contract=off`. If the captured state includes FMA-contracted results, the non-FMA kernel will differ.

This is likely the actual root cause — the same issue ROSCO solved by adding `-ffp-contract=off` to the project's CMakeLists.txt. For AeroDyn, we haven't done this yet.

## What Needs to Happen

1. **Investigate whether `-ffp-contract=off` in the project build eliminates the differences.** Rebuild OpenFAST with `-ffp-contract=off`, re-extract, and re-verify. If results become IDENTICAL, this confirms the FMA mismatch theory and the fix is to rebuild with `-ffp-contract=off` for all extraction/verification work.

2. **If differences persist after FMA fix, investigate cos/sin implementation.** Write a minimal test: compute `cos(x)` in both Fortran (gfortran intrinsic) and C++ (std::cos) for the same double-precision input, and compare. If they differ, this is a fundamental limitation that VIT's verification needs to accommodate for transcendental functions.

3. **Do not leave `--no-field-log` as the permanent workaround.** The field log comparison is the authoritative verification mechanism. Bypassing it defeats the purpose of bit-identical verification.

## VIT Fixes Implemented in This Session

### Fix 1: Parser balanced-paren matching (fortran_parser.py)

The inline array dimension parser used `[^)]*` which truncated nested parens like `Cn(size(alpha))` → `SIZE(ALPHA`. Fixed: balanced-paren matching (same pattern already used for DIMENSION attribute).

### Fix 2: Kind alias resolution in wrapper (interface_gen.py)

The wrapper emitted `REAL(ReKi)` — a project-specific kind undefined outside the module scope. Fixed: resolve kind aliases before emitting declarations. `REAL(ReKi)` → `REAL(8)` using `normalize_fortran_type()` with kind_aliases from vit.yaml. The wrapper is now self-contained.

### Fix 3: PUBLIC statement removal scoped to after CONTAINS (kernel_verify.py)

VIT removed ALL `PUBLIC` statements globally (including legitimate module-level ones), making module procedures inaccessible. Fixed: only remove PUBLIC statements after `CONTAINS` (where they're illegal Fortran inside procedure bodies).

### Fix 4: Unnecessary REAL() scalar conversion (interface_gen.py)

The wrapper generated `REAL(Cd0, C_DOUBLE)` for a `REAL(8)` argument — a no-op conversion that could introduce sub-ULP differences. Fixed: skip conversion when source and target have the same byte size. (This fix alone didn't eliminate the IN_TOL results, suggesting the root cause is elsewhere.)

## Related

- Dev note `202604241051` — catalog of VIT adaptation issues
- Dev note `202604241026` — kernel precision mismatch (ReKi = R4Ki vs R8Ki)
- Dev note `202604241004` — first extraction success
- CLAUDE.md: "If a translation shows IN_TOL (instead of IDENTICAL) in KGen field data with -ffp-contract=off, it indicates a real translation bug"
