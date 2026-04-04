# FMA Benchmark and Colleague Integration Test
**Date:** 2026-04-04
**Status:** completed
**Scope:** Performance — FMA fast vs off comparison, external user validation

## Colleague Validation

A colleague ran `04_simple_sim.py` against both the upstream Fortran ROSCO and our C++ integrated branch on their own machine. Initial results showed "slightly different results towards the end of the file."

### Root cause

The colleague's build did not have `-ffp-contract=off`. Without this flag, gfortran and g++ independently decide which multiply-adds to fuse into FMA instructions. They choose differently, producing 1-ULP differences that accumulate through the closed-loop feedback over hundreds of timesteps. By the end of a 1000s simulation, the drift is visible in printed output.

### Resolution

Adding `-ffp-contract=off` to both the Fortran and C++ compiler flags produced matching output on the colleague's machine.

### Warning message difference

The colleague also noticed that the Fortran build prints "Did not find correct size F_NotchFreqs" warnings that the C++ build does not. This is a cosmetic difference in the DISCON.IN parser:

- The ROSCO toolbox's `write_DISCON` generates `F_NotchFreqs = 0.0000` (1 element) even when `F_NumNotchFilts = 0`
- The Fortran `ParseAry` expects 0 elements, finds 1, and prints a warning before using defaults
- The C++ `parseDblAry` treats the array as optional when `F_NumNotchFilts == 0` and skips it silently
- Both end up with the same parameter values — the warning doesn't affect controller behavior

### On our platform

Running `04_simple_sim.py` on our platform (ARM64 Linux, gfortran/g++ 13.3.0) with `-ffp-contract=off` produced **numerically identical** output between Fortran and C++. The only differences were warning messages and stdout interleaving (C++ printf racing with Python print).

## FMA Performance Benchmark

Benchmarked all 27 scenarios × 3 runs for each of three builds, comparing `-ffp-contract=off` (verification mode) against `-ffp-contract=fast` (production mode).

### C++ vs Fortran speedup

| FMA Setting | Upstream Fortran | Modified Fortran | C++ | C++ vs Modified |
|-------------|-----------------|-----------------|-----|----------------|
| `-ffp-contract=off` | 23.20s | 23.17s | 17.48s | **+25% faster** |
| `-ffp-contract=fast` | 21.77s | 22.34s | 17.17s | **+23% faster** |

The C++ speedup is consistent regardless of FMA setting — ~24% faster in both cases.

### FMA benefit per language

| Build | off (s) | fast (s) | FMA benefit |
|-------|---------|----------|-------------|
| Upstream Fortran | 23.20 | 21.77 | **6.2% faster** |
| Modified Fortran | 23.17 | 22.34 | **3.6% faster** |
| C++ | 17.48 | 17.17 | **1.8% faster** |

Fortran benefits more from FMA than C++. gfortran's code generation leaves more unfused multiply-add opportunities that `-ffp-contract=fast` can exploit. g++ already generates tighter code with less room for FMA improvement.

### Production configuration

The fastest configuration is **C++ with `-ffp-contract=fast`**: 17.17s total across 27 scenarios — 26% faster than upstream Fortran with fast FMA, and the recommended setting for production deployment where cross-language verification is no longer needed.

### `-ffp-contract` flag values

| Value | Behavior | Use case |
|-------|----------|----------|
| `off` | Never fuse. Every multiply and add rounds separately. | Verification (Fortran == C++) |
| `on` | Fuse within a single source expression only. | C standard default |
| `fast` | Fuse across statements. Compiler can combine multiply from one line with add from another. | Production (gcc/gfortran default at -O2+) |
