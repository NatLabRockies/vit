# ReKi Type Mapping Fix: REAL(4) not REAL(8)
**Date:** 2026-03-24
**Status:** in-progress (4 of 6 avrSWAP functions verified, 2 under investigation)
**Scope:** type_map.py, interface_gen.py, kernel_verify.py, integrate.py, 6 C++ translations

## The Discovery

During the integration test, the BLOCK conversion for avrSWAP (converting `REAL(4)` to `REAL(8)` and back) was flagged as potentially corrupting data. Investigation revealed the root cause: the kind_alias `ReKi: REAL(8)` in vit.yaml was wrong.

**ROSCO defines `ReKi = C_FLOAT = REAL(4)`** in Constants.f90. The `-fdefault-real-8` compiler flag does NOT change explicitly-kinded types — it only promotes unqualified `REAL` (no kind specifier) to 8 bytes. `REAL(ReKi)` is genuinely 4-byte float in the actual ROSCO binary.

This means `avrSWAP(*)` — which is `REAL(ReKi)` — is a `float*`, not a `double*`. The Bladed DLL interface passes 4-byte floats. All internal ROSCO computation uses `REAL(DbKi)` = `REAL(8)` (double), with implicit promotion on read from avrSWAP and demotion on write.

## The Wrong Assumption

VIT's `DEFAULT_KIND_ALIASES` in type_map.py had `"ReKi": "REAL(8)"` with the comment "typically double precision". This was inherited from OpenFAST/NWTC conventions where `-fdefault-real-8` is assumed to make all reals 8-byte. But `SELECTED_REAL_KIND(P=6, R=37)` returns 4 even with that flag — it's a KIND PARAMETER, not a default declaration.

Verified empirically:
```
$ gfortran -fdefault-real-8 test.f90 && ./a.out
 ReKi = 4
 size of REAL(ReKi) = 4 bytes
 size of default REAL = 8 bytes   ← only unqualified REAL is promoted
```

## What Was Fixed

1. **vit.yaml**: `ReKi: REAL(8)` → `ReKi: REAL(4)`
2. **type_map.py**: `DEFAULT_KIND_ALIASES["ReKi"]` → `"REAL(4)"`
3. **interface_gen.py**: Removed `_find_kind_mismatch_arrays()` and the BLOCK conversion in `_generate_wrapper_subroutine()`. With correct mapping, `REAL(ReKi)` matches `REAL(C_FLOAT)` — no conversion needed. Also removed `assumed_size_arrays` parameter threading.
4. **kernel_verify.py**: Removed `_patch_assumed_size_kind_mismatch()` — no longer needed.
5. **integrate.py / cli.py**: Removed `assumed_size_arrays` parameter threading — no longer needed for wrapper generation.
6. **6 C++ translations**: Changed `double* avrSWAP` to `float* avrSWAP` in function signatures.

Net result: 178 lines of workaround code removed, 11 lines of correct mappings added.

## Verification Results

| Function | Result | Notes |
|----------|--------|-------|
| FlapControl | 40/40 PASSED | |
| YawRateControl | 40/40 PASSED | |
| VariableSpeedControl | 40/40 PASSED | |
| PitchControl | 40/40 PASSED | |
| StructuralControl | 38/40 FAILED | 2 cases fail |
| CableControl | 1/2 FAILED | 1 case fails (only 2 cases total for CableControl) |

The 4 non-avrSWAP functions (IPC, ForeAftDamping, FloatingFeedback, ActiveWakeControl) are unaffected — they were already correct.

## StructuralControl and CableControl Failures

These two functions have a specific pattern: they write **hardcoded float literal constants** to avrSWAP. For example, StructuralControl:

```fortran
! Fortran: REAL(ReKi) = REAL(4) values
LocalVar%StC_Input(1) = -1.234e+06
```

```cpp
// C++: assigns double literal to float*
LocalVar->StC_Input[0] = -1.234e+06;  // double literal → stored in double field
// ...
avrSWAP[idx] = LocalVar->StC_Input[I_GROUP];  // double → float truncation
```

The issue is subtle: `LocalVar->StC_Input` is `double` (it's a `REAL(DbKi)` field in the struct), but `avrSWAP` is now `float*`. The Fortran code assigns a `REAL(ReKi)` literal (4-byte) to `StC_Input` (which is `REAL(DbKi)` = 8-byte), then copies `StC_Input` to `avrSWAP` (4-byte). The C++ assigns a double literal to a double field, then truncates to float on write to avrSWAP.

The question is whether the Fortran `-1.234e+06` literal is evaluated as `REAL(4)` or `REAL(8)` before being stored in the `REAL(DbKi)` StC_Input field. If the literal is `REAL(4)` (because it appears in a context where the variable was originally `REAL(ReKi)`), the bit pattern differs from a double `-1.234e+06` truncated to float. But actually, in the Fortran source, `LocalVar%StC_Input(1) = -1.234e+06` — the literal is a default REAL (which with `-fdefault-real-8` is 8-byte), assigned to a `REAL(DbKi)` field. So both Fortran and C++ should produce the same double value in StC_Input. The truncation to float should be identical.

The failure may instead be from the kernel's state file — which captured data with the OLD mapping (BLOCK conversion, double→float→double). The captured avrSWAP reference values in the state files may have been through a double-precision round-trip that the new direct float path doesn't reproduce.

**Investigation needed:** Check the kernel verification field log to see which fields fail and by how much. If the difference is in avrSWAP output values at the float-precision level, the failure is from the state file capture, not the translation. In that case, re-extraction would fix it.

## How to Proceed

### Option A: Re-extract StructuralControl and CableControl
The golden fixtures were captured with the old (wrong) ReKi mapping. The BLOCK conversion double→float→double introduced a precision filter that the new direct float path doesn't match. Re-extracting with the correct source state would produce fixtures that match the direct float path.

### Option B: Investigate the specific failures
Read the field log CSV for the failing cases. If the differences are in avrSWAP output fields at float precision (1-ULP), the cause is confirmed as state file precision artifacts from the old BLOCK conversion.

### Option C: Check if the CC_Mode/StC_Mode paths are even exercised
CableControl only has 2 total cases (not 40) — this suggests most cases are NaN-skipped (mode not active). The 1 failing case may be the only one where avrSWAP is actually written to.

**Recommended:** Start with Option B (quick — read the CSV), then Option A if confirmed.

### Broader next steps
1. Fix the 2 remaining verification failures (likely re-extraction or precision analysis)
2. Integration test (27 functions excluding PitchControl, which needs PitchSaturation)
3. Phase 8: Translate PitchSaturation to unblock PitchControl integration
4. Full 28-function integration test

## Commits

### VIT repo
| Commit | Description |
|--------|-------------|
| `698904b` | Fix ReKi type mapping: REAL(4) not REAL(8). Remove BLOCK conversion and assumed_size_arrays threading. |

### ROSCO repo
| Commit | Description |
|--------|-------------|
| `4d33a67` | Fix ReKi mapping in vit.yaml. Update 6 translations: double* → float* for avrSWAP. |

## Lesson

Kind aliases must match the actual PARAMETER definition in the source code, not the assumed runtime behavior of compiler flags. `-fdefault-real-8` is a flag that affects *default* types — it does not retroactively change the value of `SELECTED_REAL_KIND()` or `C_FLOAT`. When in doubt, test with a small Fortran program that prints `sizeof()`.

The original `ReKi: REAL(8)` assumption cascaded through the entire type mapping, interface generation, wrapper generation, and kernel verification — requiring 178 lines of workaround code (BLOCK conversion, kind patching) to mask the mismatch. Fixing the root cause removed all of it.
