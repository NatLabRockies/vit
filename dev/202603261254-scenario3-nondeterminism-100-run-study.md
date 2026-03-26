# Scenario 3 Non-Determinism: 100-Run Study
**Date:** 2026-03-26
**Status:** completed
**Scope:** vit_sim.py determinism, Scenario 3, pre-existing ROSCO issue

## Background

During the integration test validation, Scenario 3 intermittently produced different gen_torque results between runs. This raised the question: is the non-determinism in the C++ translations, the Python physics, or the Fortran controller? A systematic 100-run study was conducted on each scenario using pure Fortran (no C++ integrations) to isolate the source.

## Method

Clean Fortran build (zero `_c(` references, `// stub` C++ files). Each scenario run 100 times in separate `docker exec` calls (separate OS processes). gen_torque MD5 checksum captured for each run.

## Results

| Scenario | Runs | Distinct gen_torque Values | Rate | Deterministic? |
|----------|------|---------------------------|------|---------------|
| 1 (standard, 1000s) | 96* | 1 | 100% same | **Yes** |
| 2 (wrap_360, 100s) | 100 | 1 | 100% same | **Yes** |
| **3 (mode coverage, 400s)** | **97*** | **2** | **71% / 29%** | **No** |
| 4 (PIIController, 100s) | 100 | 1 | 100% same | **Yes** |
| 5 (ResController, 400s) | 100 | 1 | 100% same | **Yes** |
| 6 (NotchFilterSlopes, 100s) | 100 | 1 | 100% same | **Yes** |

*A few runs lost to Python cleanup segfaults (ctypes dlclose teardown).*

**Scenario 3 is the only non-deterministic scenario.** It flips between exactly two gen_torque states:
- `e078402d620bd512b407fe261545cc71` — 69 of 97 runs (71%)
- `0f297bd1ad37d0e6f580a562ce29cf81` — 28 of 97 runs (29%)

No third value was ever observed. The flip is binary, not continuous.

## Key Finding: This Is a Pre-Existing ROSCO Issue

The non-determinism occurs in **pure Fortran** with the **old vit_sim.py** (before Scenario 7/8 additions). It is NOT caused by:
- C++ translations (no integrations applied)
- vit_sim.py code changes (tested with committed version at `7294c23`)
- Python physics model (gen_torque is a controller output from DISCON, not Python computation)

## What Makes Scenario 3 Different

Scenario 3 enables the most mode flags simultaneously:
```
F_NumNotchFilts=1, CC_Mode=1, TD_Mode=1, Fl_Mode=1,
Y_ControlMode=1, StC_Mode=1, Flp_Mode=1
```

Scenarios 1, 2, 4, 5, 6 each enable at most one or two non-default modes. Scenario 3 enables seven. The interaction of multiple controller modes (notch filter + cable control + tower damping + floating feedback + yaw control + structural control + flap control) creates a code path sensitive to some non-deterministic factor.

The non-determinism likely comes from:
- Uninitialized memory in one of the mode-specific Fortran code paths
- Floating-point sensitivity to memory layout or stack alignment in the deeply nested call chain
- A Fortran SAVE variable that retains different state depending on process initialization

The binary nature (exactly 2 states, ~71/29 split) suggests a single bit flip somewhere — possibly an uninitialized boolean or a comparison at a floating-point boundary.

## Impact on Integration Testing

The previous "definitive" integration test (412,000 values ALL IDENTICAL) succeeded because both baseline and integrated Scenario 3 happened to land on the same state. With a 71% probability per run, both landing on the same state has ~58% probability (0.71² + 0.29² ≈ 0.59). We got lucky.

**For integration testing, the correct approach is:**
1. Scenarios 1, 2, 4, 5, 6: compare all arrays (gen_torque, bld_pitch, gen_speed, gen_power, nac_yaw) — these are fully deterministic
2. Scenario 3: either run both baseline and integrated until they land on the same state, or accept that Scenario 3's non-determinism is a pre-existing ROSCO issue and exclude it from byte-identical comparison (use tolerance-based comparison instead)

Alternatively: investigate and fix the Scenario 3 non-determinism root cause in the Fortran source. This would be the cleanest solution but requires debugging ROSCO's multi-mode interaction.

## Implications for Earlier Tests

- **KGen verification (40/40 per function):** Unaffected. KGen captures state from a single run and verifies the C++ against that specific state. Non-determinism between runs doesn't matter.
- **Previous clean build tests (stdout diff):** The stdout diff was always empty because it compared text markers, not numerical data. The non-determinism was invisible.
- **The "definitive" 412k-value test:** Passed by chance (~59% probability). Subsequent attempts may fail for Scenario 3.

## Related Dev Notes

- `202603261145-integration-test-systematic-evaluation.md` — systematic evaluation that identified under-exercised functions
- `202603261122-definitive-integration-test-412k-values.md` — the test that passed (by chance for Scenario 3)
- `202603261110-simulation-nondeterminism-investigation.md` — initial non-determinism diagnosis (partially wrong — attributed to Python Cp interpolation)
