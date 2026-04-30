# CHARACTER(*) Robust Per-Variable Resolution
**Date:** 2026-04-30
**Status:** completed
**Scope:** VIT kernel_verify.py

## Problem with Initial Fix

The first fix (dev note `202604300138`) resolved CHARACTER(*) lengths by finding the first `CHARACTER(<constant>)` declaration in any file that calls the enclosing function. This was brittle — it would pick the wrong length if a file had multiple CHARACTER declarations with different lengths, or if different CHARACTER(*) variables in the kernel had different lengths.

## Robust Fix

`_resolve_character_star_lengths()` now returns a dict mapping each variable name to its individually resolved length. For each `CHARACTER(LEN=*) :: varname` in the kernel:

1. **Match to enclosing parameter by name** — strip `kgenref_` prefix, find the matching parameter in the enclosing function's signature (e.g., `errmsg` → `ErrMsg` parameter)

2. **Check parameter declaration** — if it has an explicit length (e.g., `CHARACTER(8196)`), resolve and use it

3. **If CHARACTER(*)** — the enclosing function also has assumed-length. Trace to the caller:
   a. Find files that CALL the enclosing function
   b. Parse the call's argument list to find the actual argument at the same position
   c. Find that actual argument's CHARACTER declaration in the same file
   d. Extract its length constant (e.g., `CHARACTER(ErrMsgLen) :: ErrMsg2`)
   e. Resolve the constant by walking up the directory tree (`ErrMsgLen = 8196` in `nwtc-library/src/NWTC_Base.f90`)

4. **Apply per-variable** — the regex replacement looks up each variable's resolved length from the dict, defaulting to 4096 if unresolved

## Why This Is Robust

- Handles multiple CHARACTER(*) variables with different lengths in the same kernel
- Traces each variable through the full parameter chain, not a heuristic scan
- The caller lookup uses argument POSITION (not name) to match actual arguments to formal parameters
- The constant resolution walks up the directory tree to find cross-module constants

## Verification

- ABM4: 20/20 IDENTICAL
- ComputeTau1: 20/20 IDENTICAL (regression clean)
- VIT tests: 644/644
- Resolution output: `{'errmsg': 8196}` (per-variable, not a single global value)
