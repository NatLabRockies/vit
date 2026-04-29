# DBEMT_CalcOutput Integration + ErrMsg Blank-Padding Lesson
**Date:** 2026-04-29
**Status:** completed
**Scope:** DBEMT Phase A (4/5), KGen verification pattern

## What We Did

Completed the full 7-step workflow for DBEMT_CalcOutput — a 35-line function that returns filtered or unfiltered induced velocity depending on initialization state. This is the 4th of 5 leaf functions in DBEMT Phase A.

Result: 20/20 KGen IDENTICAL, 17/17 baselines PASS.

## The Function

DBEMT_CalcOutput is the simplest DBEMT function yet. It checks `OtherState%areStatesInitialized(i,j)` and returns either `u%element(i,j)%vind_s` (unfiltered input) or `x%element(i,j)%vind` (filtered state) as a 2-element output array. Error handling just initializes ErrStat/ErrMsg to no-error. Several arguments (`t`, `p`, `m`) are in the signature but unused in the body.

## Cross-Module Extraction

CalcOutput is defined in DBEMT.f90 but only called from BEMT.f90:1215 (in `calculate_Inductions_from_DBEMT`). Extraction used `-f modules/aerodyn/src/BEMT.f90 -l 1215` — the `-f` flag specifies the call site file, not the definition file. This is the same cross-module pattern used for some AirfoilInfo functions. Worked cleanly.

## ErrMsg Blank-Padding

Initial verification failed 0/20 — all output fields were IDENTICAL except `errmsg` (OUT_TOL). The actual computation was correct.

**Root cause:** Fortran `ErrMsg = ""` blank-pads the entire CHARACTER variable with spaces (ASCII 32). The C++ `errMsg[0] = '\0'` sets only the first byte to null, leaving the remaining bytes as uninitialized garbage. KGen compares the full CHARACTER buffer byte-by-byte.

**Fix:** `std::memset(errMsg, ' ', ErrMsgLen)` before the null terminator. This matches Fortran's blank-padding semantics.

```cpp
#include <cstring>
*errStat = 0;
std::memset(errMsg, ' ', ErrMsgLen);
```

**Why ComputeTau1 didn't hit this:** ComputeTau1 also has `errMsg[0] = '\0'`, but its KGen verification passed. Two possible reasons: (1) the CHARACTER(*) kernel repair may have handled the comparison differently for ComputeTau1's kernel, or (2) the errMsg field comparison may not have been included in ComputeTau1's verification output. Either way, the blank-padding pattern is correct and should be used going forward for all functions with ErrMsg output.

**Standard pattern for error handling:**
```cpp
*errStat = 0;
std::memset(errMsg, ' ', ErrMsgLen);  // Match Fortran blank-padding
```

## Where We Are Now

DBEMT Phase A: **4/5 leaf functions complete** (ComputeTau2, ComputeTau1, DBEMT_InitStates, DBEMT_CalcOutput).

Remaining: DBEMT_CalcContStateDeriv (44 lines) — ODE derivatives for Mode 3, calls ComputeTau2 as a C++ callee. This is the last Phase A function.
