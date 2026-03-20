# Phase 7A Follow-Up: PIIController KGen Verification and Bug Fixes
**Date:** 2026-03-20
**Status:** completed
**Scope:** Fixing KGen BIND(C) parser bug, ROSCO Flp_Mode=2 bugs, achieving KGen verification for PIIController

## Context

Phase 7A translated PIController, PIIController, and ResController. PIController and ResController were KGen-verified 40/40. PIIController could not be KGen-extracted — the extraction produced 0 state files despite Scenario 4 (Flp_Mode=2) appearing to work. PIIController was initially verified via simulation diff only.

This was not acceptable. VIT is a rigorous verification tool, and every translation should be KGen-verified unless there is a truly insurmountable technical barrier. We investigated the extraction failure to root cause and fixed everything we found.

## The Investigation

The PIIController extraction failure had multiple causes layered on top of each other. Fixing one revealed the next. The investigation proceeded through 7 layers before reaching full KGen verification.

### Layer 1: KGen BIND(C) parser crash

Phase 4C added `BIND(C)` to 6 TYPE definitions in ROSCO_Types.f90. KGen's Python 2.7 Fortran parser crashed on `TYPE, BIND(C), PUBLIC :: WE` with `UnboundLocalError: local variable 'newline' referenced before assignment`.

**Root cause:** `KGen/kgen/parser/utils.py:142` — the `parse_bind()` function only initializes `newline` when `item is not None`, but uses it unconditionally on line 142.

**Fix:** Add `newline = line` in the `else` branch. One line.

**Impact:** Without this fix, extraction required resetting ROSCO_Types.f90 to upstream (removing BIND(C)), which created a cascade of type incompatibilities.

### Layer 2: Source state incompatibility

With the BIND(C) bug forcing a full upstream reset, the extraction environment had upstream ROSCO_Types (`LOGICAL :: restart`) but Phase 4C call sites expected `INTEGER(C_INT) :: restart`. The workaround was resetting ALL source files to upstream and applying `sed` for the restart call-site fix. This was fragile and error-prone.

**Resolved by:** Layer 1 fix. With KGen able to parse BIND(C), we could use Phase 4C ROSCO_Types directly.

### Layer 3: F_FlpCornerFreq validation

Scenario 4 set `Flp_Mode=2` but did not patch `F_FlpCornerFreq`, which defaulted to `0.0000  1.0000`. ROSCO's `CheckInputs` validates `F_FlpCornerFreq(1) > 0` when `Flp_Mode > 0`. With corner frequency = 0, validation set `aviFAIL = -1`, causing DISCON to return immediately without calling any controllers. FlapControl was never entered.

This was invisible — Scenario 4 reported "PASSED" because the Python sim completed without exception. The controller error was swallowed silently.

**Fix:** Patch `F_FlpCornerFreq` to `0.5000  0.7000` in Scenario 4.

### Layer 4: write_discon regex

The `write_discon` function used regex `^(\S+)` to match parameter values, but `AWC_CntrGains` has two space-separated values (`0.0000 0.0000`). The regex matched only the first token, and the patch silently failed (count=0).

**Fix:** Changed to `^(.+?)` (non-greedy match up to the `!` comment).

### Layer 5: Uninitialized K in FlapControl init

Controllers.f90 line 669: `LocalVar%Flp_Angle(K) = PIIController(RootMyb_VelErr(K), ...)`. The variable `K` is a loop variable that was never assigned in the initialization block — there is no enclosing `DO K` loop. Using uninitialized `K` as an array index produced out-of-bounds access.

**Fix:** Wrap in `DO K = 1,LocalVar%NumBl ... END DO`. This is an upstream ROSCO bug.

### Layer 6: Uninitialized n in Flp_Mode==2 notch filter

Filters.f90 line 412: the `Flp_Mode==2` branch of `PreFilterMeasuredSignals` calls `NotchFilter` using `CntrPar%F_GenSpdNotch_Ind(n)` as an array index. The variable `n` is a loop counter from an earlier `DO n = 1,CntrPar%F_GenSpdNotch_N` loop at line 355, but in the `Flp_Mode==2` branch it is used outside any loop. When `F_GenSpdNotch_N=0` (no notch filters configured), the earlier loop never executes, and `n` is never set.

This was the actual segfault — using garbage `n` as an index into `F_NotchFreqs`, `F_NotchBetaNum`, `F_NotchBetaDen` arrays caused an out-of-bounds memory access. This is an upstream ROSCO bug.

**Diagnosis:** Added debug WRITE statements with FLUSH to DISCON.F90 between each subroutine call. The crash occurred between `SetParameters` (completed) and `PreFilterMeasuredSignals` (never returned). The segfault was inside the filter setup, not in FlapControl or PIIController.

**Fix:** Wrap the NotchFilter call in `DO n = 1,CntrPar%F_GenSpdNotch_N ... END DO`. When no notch filters are configured, the loop body is skipped entirely.

### Layer 7: ACC_INFILE character assignment

ReadSetParameters.f90 line 216: `LocalVar%ACC_INFILE = accINFILE`. Phase 4A changed `ACC_INFILE` from `CHARACTER(:), ALLOCATABLE` to `CHARACTER(1024)`. The `accINFILE` parameter is a `CHARACTER(C_CHAR)` array of length `avrSWAP(50)` (typically 13, the DISCON filename length). Assigning a `CHARACTER(1)` array of size 13 to a `CHARACTER(1024)` scalar is a type/shape mismatch that gfortran's `-fcheck=bounds` catches as `Array bound mismatch for dimension 1 of array 'localvar' (1024/13)`.

**Fix:** Element-wise copy: `DO i = 1, MIN(size, 1024); ACC_INFILE(i:i) = accINFILE(i); END DO`.

### Layer 8 (false lead): kgen_array_sumcheck STOP

KGen's state integrity check crashes the kernel when denormalized (subnormal) values from uninitialized struct fields in state files don't match zero-initialized memory. This affected the FlapControl kernel because it receives `ControlParameters` (large struct with many array fields).

**Fix:** VIT patches `kgen_array_sumcheck` to remove the `STOP` from the finish branch. The function output verification catches real issues.

This was fixed during Phase 7A but was a red herring for the extraction failure — the extraction problem was in the simulation, not the kernel verification.

## Outcome

After fixing all 7 layers, PIIController extracts with 20 state files and verifies 40/40 via KGen. All 18 functions are now KGen-verified.

## Key Lesson

The initial conclusion was that PIIController extraction was "blocked by an upstream ROSCO bug" that was "outside VIT's scope." This was wrong. The ROSCO bugs (uninitialized variables) were straightforward to diagnose and fix once we committed to finding the actual crash location instead of accepting "it segfaults" as a final answer.

The debugging approach that worked: adding `WRITE(0,*) "DBG..."` statements between each subroutine call in DISCON.F90 to binary-search for the crash location, then reading the source at the crash point to identify the uninitialized variable. Total time from "let me dig deeper" to "40/40 passed": under an hour.
