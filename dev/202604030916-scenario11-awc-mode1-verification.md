# Scenario 11: AWC_Mode=1 — Closing the Last Verification Gap
**Date:** 2026-04-03
**Status:** completed
**Scope:** Verification — COMPLEX→REAL type decomposition

## Summary

Added Scenario 11 (AWC_Mode=1, open-loop complex number active wake control) to exercise the only type modification without prior runtime verification: the COMPLEX→REAL decomposition of `AWC_complexangle`. 80,000 values are bit-identical across upstream Fortran (COMPLEX EXP), modified Fortran (COS/SIN), and C++ (COS/SIN).

This closes the last gap in the verification chain. Every type modification in ROSCO_Types.f90 is now proven behaviorally neutral by runtime comparison.

## The Gap

Phase 4C decomposed `COMPLEX(DbKi) :: AWC_complexangle(3)` into `REAL(DbKi) :: AWC_complexangle_re(3)` and `AWC_complexangle_im(3)`. The Euler's formula transformation:

```
Upstream:  AWC_complexangle(K) = AWC_complexangle(K) + amp * EXP(complexI * AWC_angle(K))
           PitCom(K) = PitCom(K) + REAL(AWC_complexangle(K))

Modified:  AWC_complexangle_re(K) = AWC_complexangle_re(K) + amp * COS(AWC_angle(K))
           AWC_complexangle_im(K) = AWC_complexangle_im(K) + amp * SIN(AWC_angle(K))
           PitCom(K) = PitCom(K) + AWC_complexangle_re(K)
```

This is mathematically exact (`exp(iθ) = cos θ + i sin θ`), and only the real part is used for the pitch command. But until now, no scenario exercised this code path — AWC_Mode=1 is the sole branch that uses `AWC_complexangle`, and no existing scenario set AWC_Mode=1. The DISCON.IN default is AWC_Mode=0 (disabled).

## Scenario 11 Configuration

```python
patches={
    'AWC_Mode': 1,
    'AWC_NumModes': 1,      # Single 1P forcing mode
    'AWC_n': '1',           # Azimuthal mode number
    'AWC_freq': '0.05',     # 0.05 Hz forcing frequency
    'AWC_amp': '2.0',       # 2-degree pitch amplitude
    'AWC_clockangle': '0.0', # Zero initial phase (must be 0-360 for Mode=1)
}
```

AWC_Mode=1 is a simple open-loop controller that applies sinusoidal pitch commands phase-locked to blade azimuth. Unlike AWC_Mode > 1, it does not require individual pitch control. The 2-degree amplitude produces clearly visible pitch oscillations in the simulation output, confirming the code path is actively exercised (pitch alternates between 0° and 2° at the forcing frequency).

Uses the same simulation parameters as Scenario 5 (dt=0.025, tlen=400, ws0=9 with step increases).

## Results

### Upstream vs Modified Fortran (COMPLEX EXP vs COS/SIN)
```
scenario_11: 5/5 IDENTICAL (80,000 values)
✓ ALL IDENTICAL — 80,000 total values compared
```

### Upstream vs C++
```
scenario_11: 5/5 IDENTICAL (80,000 values)
✓ ALL IDENTICAL — 80,000 total values compared
```

The COMPLEX→REAL decomposition produces bit-identical output. The mathematical equivalence holds at the floating-point level because `EXP(complexI * θ)` and `COS(θ) + i*SIN(θ)` use the same underlying libm functions — gfortran's COMPLEX EXP decomposes to COS/SIN internally.

## Updated Verification Chain

| Comparison | Values | Scenarios |
|---|---|---|
| Upstream Fortran vs Modified Fortran | 752,000 | 10 (1-3, 5-11, excl. 4) |
| Modified Fortran vs C++ | 772,000 | 11 (all) |
| **Upstream Fortran vs C++** | **752,000** | **10** |

All type modifications now have runtime proof:

| Modification | Verified by |
|---|---|
| `BIND(C)` on 7 types | Scenarios 1-3, 5-11 (672K → 752K values) |
| `LOGICAL→INTEGER(C_INT)` restart/WriteThisStep | Scenarios 1-3, 5-11 |
| `COMPLEX→REAL pair` AWC_complexangle | **Scenario 11** (80K values) |
| `CHARACTER(:) ALLOC→CHARACTER(1024)` | Scenarios 1-3, 5-11 |
| `AlreadyInitialized` default removed | Scenarios 1-3, 5-11 |

The only unverifiable scenario is Scenario 4 (Flp_Mode=2) — upstream crashes due to ROSCO bugs (uninitialized K and n). Modified Fortran vs C++ covers Scenario 4 (20K values identical), but the upstream→modified leg cannot be tested.

## Commits

- ROSCO `e55fa89`: Add Scenario 11: AWC_Mode=1 (complex number method)
