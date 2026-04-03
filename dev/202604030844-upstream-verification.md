# Upstream Fortran Verification: Closing the Chain
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification — upstream Fortran vs modified Fortran vs C++

## Summary

Built upstream ROSCO (`e8010f0`, unmodified) and compared its simulation output against our modified Fortran fork. 672,000 values across 9 scenarios are **bit-identical**. This closes the verification chain:

```
Upstream Fortran = Modified Fortran = C++
```

The ROSCO_Types.f90 modifications (Phase 4A/4C) are proven behaviorally neutral — zero impact on simulation output.

## Why This Matters

Throughout the translation, our "Fortran baseline" used modified types (`BIND(C)` on 7 derived types, `LOGICAL→INTEGER(C_INT)` for restart/WriteThisStep, `COMPLEX→REAL pair` for AWC_complexangle, `CHARACTER(:) ALLOCATABLE→CHARACTER(1024)` for ACC_INFILE/ErrMsg). We proved **modified Fortran = C++** (692,000 values identical), but never **modified Fortran = upstream Fortran**. This left open the possibility that the type modifications introduced subtle behavioral differences that both sides shared — invisible to our comparison.

This verification eliminates that concern.

## Methodology

### Step 1: Build upstream ROSCO

Restored all source files to `e8010f0` (upstream release), including the unmodified ROSCO_Types.f90 with `LOGICAL restart`, `COMPLEX(DbKi) AWC_complexangle`, `CHARACTER(:), ALLOCATABLE :: ErrMsg`, and no `BIND(C)` on any derived type.

Added `-ffp-contract=off` to the gfortran flags (not in upstream, but required for bit-identical comparison — without it, FMA scheduling differences would introduce noise unrelated to the type modifications).

Built in Docker container. Pure Fortran build, no C++.

### Step 2: Build modified Fortran

Used `reset_to_clean.sh` to restore our modified fork: Phase 4A/4C ROSCO_Types.f90, bug-fixed source files (46e8d4f Controllers/ControllerBlocks, dba7738 Functions/Filters), upstream CMakeLists.txt with `-ffp-contract=off`.

Built in Docker container. Pure Fortran build, no C++.

### Step 3: Compare

Ran `vit sim-compare` on matching scenarios. Skipped Scenario 4 (Flp_Mode=2) because upstream ROSCO segfaults on it — the uninitialized K bug in FlapControl init causes a crash before any output is produced.

## Results

```
scenario_1:  5/5 IDENTICAL (200,000 values)
scenario_2:  3/3 IDENTICAL (12,000 values)
scenario_3:  5/5 IDENTICAL (80,000 values)
scenario_5:  5/5 IDENTICAL (80,000 values)
scenario_6:  5/5 IDENTICAL (20,000 values)
scenario_7:  5/5 IDENTICAL (120,000 values)
scenario_8:  5/5 IDENTICAL (80,000 values)
scenario_9:  5/5 IDENTICAL (60,000 values)
scenario_10: 5/5 IDENTICAL (20,000 values)

ALL IDENTICAL — 672,000 total values compared
```

## Type Modification Analysis

| Modification | Exercised | Impact |
|---|---|---|
| `BIND(C)` on WE, FilterParameters, rlParams, piParams, resParams, LocalVariables, ErrorVariables | Every scenario — these types are used by all controller functions | **None.** gfortran already uses C-compatible layout for these types (simple scalar fields, fixed-size arrays). `BIND(C)` adds a constraint that matches the existing layout. |
| `LOGICAL→INTEGER(C_INT)` for `restart` | Every scenario — passed to every filter and controller call | **None.** All call sites adapted from `LocalVar%restart` to `(LocalVar%restart /= 0)`. The boolean semantics are preserved: gfortran's `LOGICAL .TRUE.` evaluates to non-zero, matching `INTEGER /= 0`. |
| `LOGICAL→INTEGER(C_INT)` for `WriteThisStep` | Every scenario — debug logging gate | **None.** Same boolean gate semantics. |
| `COMPLEX(DbKi)→REAL(DbKi) pair` for `AWC_complexangle` | **Never exercised.** Only active when `AWC_Mode==1`, which no scenario uses (default is 0). | N/A — dead code in test suite. Euler's formula decomposition (`exp(iθ) → cos θ + i sin θ`) is mathematically exact but unverified at runtime. |
| `CHARACTER(:), ALLOCATABLE→CHARACTER(1024)` for `ACC_INFILE` | Every scenario — SetParameters saves the input filename | **None.** All test filenames are well under 1024 characters. |
| `CHARACTER(:), ALLOCATABLE→CHARACTER(1024)` for `ErrMsg` | Every scenario — error handling path | **None.** Error messages are well under 1024 characters. |
| `AlreadyInitialized = 0` default removed | Every scenario — DISCON initialization | **None.** Fortran SAVE semantics zero-initialize the variable regardless of the default initializer. |

## Scenario 4: Upstream ROSCO Bug

Scenario 4 (Flp_Mode=2) was skipped because upstream ROSCO crashes with a segfault. The root cause is two bugs in ROSCO v2.9.0's untested Flp_Mode=2 research code:

1. **Uninitialized K** in FlapControl init (Controllers.f90:669): The `Flp_Mode==2` initialization block doesn't wrap the PIIController call in a `DO K = 1,NumBl` loop. K is uninitialized, causing array out-of-bounds access.

2. **Uninitialized n** in the Flp_Mode==2 notch filter branch (Filters.f90:412): A missing `DO n = 1,F_GenSpdNotch_N` loop leaves n uninitialized.

These are defects in ROSCO, not VIT modifications. Our fork fixes them. The fixes are included in commits `46e8d4f` (Controllers.f90) and `dba7738` (Filters.f90). See dev note `202603201150` for details.

## Bug Found: reset_to_clean.sh and ROSCO_Types.f90

During this exercise, we discovered that `reset_to_clean.sh` didn't restore ROSCO_Types.f90. The script's design assumed ROSCO_Types.f90 would always be at HEAD (the modified version), but a prior `git checkout e8010f0 -- rosco/controller/src/` had reset it to upstream. The bug-fixed source files (which use `restart /= 0`) then failed to compile against the upstream types (where `restart` is `LOGICAL`):

```
Error: Operands of comparison operator '/=' at (1) are LOGICAL(4)/INTEGER(4)
```

Fixed by adding `git checkout -- rosco/controller/src/ROSCO_Types.f90` to `reset_to_clean.sh`. This ensures the modified types are always restored, regardless of prior operations on the `src/` directory.

## The Complete Verification Chain

| Comparison | Values | Scenarios | Result |
|---|---|---|---|
| Upstream Fortran vs Modified Fortran | 672,000 | 9 (excl. Scenario 4 — upstream bug) | IDENTICAL |
| Modified Fortran vs C++ | 692,000 | 10 (all) | IDENTICAL |
| **Upstream Fortran vs C++** | **672,000** | **9** | **IDENTICAL** |

The one gap: Scenario 4 (Flp_Mode=2) cannot be compared against upstream because upstream crashes. Our modified Fortran vs C++ comparison covers Scenario 4 (20,000 values identical), but the upstream→modified leg is unverifiable for this scenario due to the ROSCO bug.

The AWC_complexangle COMPLEX→REAL decomposition is also unverified at runtime (AWC_Mode=1 is never exercised). The mathematical equivalence (`exp(iθ) = cos θ + i sin θ`) is exact, but a test scenario with AWC_Mode=1 would provide runtime proof.

## Commits

- ROSCO `b06d9cb`: Add ROSCO_Types.f90 restoration to reset_to_clean.sh
