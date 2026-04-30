# CHARACTER(*) Length Resolution in Kernel Verification
**Date:** 2026-04-30
**Status:** completed
**Scope:** VIT kernel_verify.py

## Problem

KGen generates `CHARACTER(LEN=*)` for local variables in kernel drivers (copied from enclosing function dummy arguments). VIT replaced `*` with a hardcoded `4096`. When the actual runtime length was different (e.g., `ErrMsgLen = 8196`), the kernel's STREAM I/O read the wrong number of bytes, misaligning all subsequent reads and causing segfaults.

This bug masked the ABM4 kernel verification — the kernel crashed during state data deserialization. Previous kernels (ComputeTau1, InitStates) survived by accident because nothing was read after the misaligned CHARACTER field.

## Root Cause Chain

1. KGen captures state data using the ACTUAL runtime CHARACTER length (e.g., 8196 bytes for ErrMsg)
2. KGen generates kernel_driver with `CHARACTER(LEN=*) :: errmsg` (illegal for a PROGRAM local)
3. VIT replaced `*` with `4096` (hardcoded)
4. Kernel reads 4096 bytes but state file has 8196 → 4100-byte misalignment
5. All subsequent reads consume wrong data → segfault in u_elem type reader

## Fix

`_resolve_character_star_length()` resolves the actual length from the source tree:

1. **Find enclosing function** — parse `CALL <name>(kgen_unit, kgen_measure, ...)` from the kernel driver
2. **Check enclosing function's CHARACTER parameters** — if they have explicit lengths (e.g., `CHARACTER(8196)`), use that
3. **If CHARACTER(**)** — the enclosing function also has assumed-length. Find callers of the enclosing function in the source tree
4. **Extract caller's CHARACTER length** — callers declare `CHARACTER(ErrMsgLen) :: ErrMsg2`. Extract the constant name
5. **Resolve the constant** — walk up the directory tree calling `_find_constant_in_source()` until found. `ErrMsgLen` is in `modules/nwtc-library/src/NWTC_Base.f90`, two levels above `modules/aerodyn/src/`

The directory walk-up is needed because constants are often defined in framework modules (nwtc-library) outside the target module's source directory (aerodyn).

## Why Previous Kernels Survived

All KGen kernels that extract from UpdateStates had the same 4096 vs 8196 mismatch. But:
- **ComputeTau1, InitStates:** The errmsg read is near the END of the read sequence. Nothing important is read after it. The misalignment affects only `nwtc_base_externs` (AbortErrLev), which is consumed harmlessly.
- **ABM4:** After errmsg, the kernel reads `u_elem` (local variable from the call site). The misalignment causes the type reader to interpret ErrMsg spaces as array bounds, leading to out-of-bounds access.

## Implementation

**File:** `vit/vit/kernel_verify.py`

- Added `source_dir` parameter to `_patch_kernel_fortran()` (propagated from `setup_kernel_cpp`)
- Added `_resolve_character_star_length(kernel_content, source_dir)` function
- Replaced `CHARACTER(LEN=4096)` hardcode with `CHARACTER(LEN={resolved})` using resolved value
- Fallback: 4096 if resolution fails at any step (no source_dir, no enclosing function found, no callers found, no constant resolved)

## Verification

- ABM4: 20/20 IDENTICAL (was segfaulting before)
- ComputeTau1: 20/20 IDENTICAL (regression clean)
- VIT tests: 644/644
- Kernel driver confirms: `CHARACTER(LEN=8196) :: errmsg`
