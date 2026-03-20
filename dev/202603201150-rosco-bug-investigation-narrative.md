# Investigating Our ROSCO Source Changes: The Full Story
**Date:** 2026-03-20
**Status:** completed
**Scope:** Rigorous examination of three ROSCO source changes — from initial skepticism through investigation to final understanding

## The Challenge

After Phase 7A, we had modified three ROSCO source files and called all three changes "upstream ROSCO bugs." The push came: ROSCO is production code written by highly capable NREL engineers. Bugs that cause segfaults wouldn't go unnoticed for years. We needed to stress test our claims with rigor and intensity before carrying them forward.

The specific concern: we should not accept things because they're hard, and we should not accept not verifying things with KGen. If these are real bugs, prove it with airtight evidence. If they're our mistakes, own them.

## The Plan

Before diving into code, we stopped to think about what we actually needed to know:

1. For each change: is it fixing a genuine ROSCO defect, or correcting a problem we introduced?
2. Could something in OpenFAST (the production simulation framework) explain what we see? Maybe these aren't bugs in the OpenFAST context.
3. Did we look through all the Python simulations for answers?

The investigation would check git history (when was the code added, by whom, in what context), real turbine configurations (does the only Flp_Mode=2 config trigger the bugs), ROSCO's test suite (does anything exercise this code), and the Fortran standard (are the variables truly undefined).

We decided OpenFAST wasn't needed yet — the questions were about ROSCO's internal code and assumptions, answerable from the ROSCO repo itself.

## The Evidence

### Git History

Both the uninitialized K (Controllers.f90:669) and uninitialized n (Filters.f90:412) were introduced in commit `b994e4d` — **ROSCO v2.9.0** (January 19, 2024, author: dzalkind). This was a large squashed release commit. The Flp_Mode==2 init block and the Flp_Mode==2 notch filter branch were added in the same commit, alongside the steady-state PIIController block that correctly uses `DO K = 1,LocalVar%NumBl`.

### K Is Genuinely Undefined

`K` is declared `INTEGER(IntKi) :: K` as a local variable in FlapControl — no SAVE, no DATA, no default initialization. There is no outer scope that assigns K before line 669. Per the Fortran standard, its value is undefined. The steady-state block 13 lines below wraps the same call in `DO K = 1,LocalVar%NumBl`, establishing the intended pattern.

However, the practical impact is subtle. `instPI` is reset to 1 at the start of every DISCON call (line 183 of ReadSetParameters.f90). `piP%ITerm` is zero-initialized (SAVE semantics in DISCON). `I0` is passed as `0.0_DbKi`. So the PIIController reset branch writes 0.0 over 0.0, and the inst counter gets reset next call anyway. The init call is effectively a no-op — except for `Flp_Angle(K) = 0.0` where K is garbage, which writes 0.0 to a random array index. It's undefined behavior per the standard, but practically benign because it writes 0.0 to a likely-zero location.

### n Is Genuinely Undefined and Severe

`n` is declared `INTEGER(IntKi) :: n` as a local variable in PreFilterMeasuredSignals. It gets its value from `DO n = 1,CntrPar%F_GenSpdNotch_N` at line 355 — but only when `F_GenSpdNotch_N > 0`. When `F_GenSpdNotch_N = 0`, the loop never executes, and `n` is never set. The Flp_Mode==2 branch then uses `CntrPar%F_GenSpdNotch_Ind(n)` as an array index — accessing memory at a garbage offset.

The comment on the buggy line reads: "Use same as generator speed signal because that's how it was before." This is a copy-paste from the gen-speed notch loop, without the enclosing `DO n` iteration.

No validation in ReadSetParameters.f90 links `F_GenSpdNotch_N` to `Flp_Mode`. They are independent parameters.

### ACC_INFILE Is Our Responsibility

The original ROSCO_Types.f90 at `e8010f0` declares `CHARACTER, DIMENSION(:), ALLOCATABLE :: ACC_INFILE`. Our Phase 4A changed it to `CHARACTER(1024) :: ACC_INFILE` for BIND(C) compatibility. The original allocatable assignment worked fine. Our change broke it, and our element-wise copy loop restores the functionality we broke. This is not a ROSCO bug.

### BAR_10 Is the Only Flp_Mode=2 Config — and It Triggers the Bug

BAR_10_DISCON.IN is the only file in the ROSCO repo with `Flp_Mode=2`. It has `F_GenSpdNotch_N=0` — zero generator speed notch filters. This is the exact combination that makes `n` undefined.

### Nobody Tests Flp_Mode=2

We checked every Python script and test file in the repository:

- `09_distributed_aero.py` uses BAR.yaml (Flp_Mode=2) to TUNE controller gains but never runs a simulation. It calls `controller.tune_controller(turbine)` and prints Kp/Ki. The Fortran code is never executed.
- `test_examples.py` runs example 09 as part of CI — but since 09 only tunes, it passes without loading the Fortran library.
- `04_simple_sim.py` runs the 1-DOF sim with NREL-5MW (Flp_Mode=0).
- `ROSCO_testing.py` runs OpenFAST with NREL-5MW (Flp_Mode=0).
- Zero mentions of `Flp`, `flap`, or `BAR` in any test file.
- Our `vit_sim.py` Scenario 4 is the first script that ever runs Flp_Mode=2 in simulation.

### Could OpenFAST Fix These?

No. Both `K` and `n` are local variables. Nothing in the calling chain — not OpenFAST, not DISCON, not SetParameters — can assign them before FlapControl and PreFilterMeasuredSignals use them. The Fortran standard is clear: local variables without explicit initialization are undefined.

## The Real Question: Why Haven't These Bugs Affected Anyone?

This was the hardest question. If these are genuine segfault-causing defects, surely someone would have hit them.

The answer is that **Flp_Mode=2 is research code for a research turbine that doesn't physically exist.**

Flp_Mode=2 is trailing edge flap control — physical flap actuators on the blade (like ailerons on airplane wings) that actively adjust the aerodynamic profile to reduce fatigue loads. This is for the BAR (Big Adaptive Rotor), an NREL research turbine concept. Trailing edge flaps on utility-scale wind turbines are still experimental technology. The BAR is a design study, not a deployed turbine.

The code was implemented in ROSCO v2.9.0 to support the BAR research program. Example 09 demonstrates tuning the gains. But nobody has run the full closed-loop simulation — not in the 1-DOF sim, not in OpenFAST, not in CI. The feature was partially implemented and never end-to-end tested.

If a researcher at NREL did run Flp_Mode=2 in OpenFAST, the n bug would only crash if `F_GenSpdNotch_N=0`. If they configured notch filters (common in turbine analysis), `n` would be set by the prior loop and the bug would be invisible. The BAR reference config has `F_GenSpdNotch_N=0`, but a researcher might add notch filters for their specific study, inadvertently avoiding the bug.

## Verdicts

| Change | Verdict | Practical Impact |
|--------|---------|-----------------|
| Uninitialized K (Controllers.f90:669) | Genuine ROSCO defect per Fortran standard | Practically benign — writes 0.0 over 0.0, inst counter reset each call. Technically undefined behavior. |
| Uninitialized n (Filters.f90:412) | Genuine ROSCO defect, severe | Garbage array index → segfault or silent corruption. Copy-paste error from gen-speed notch loop. Triggered by the only real Flp_Mode=2 config (BAR_10 with F_GenSpdNotch_N=0). |
| ACC_INFILE (ReadSetParameters.f90:216) | Our responsibility | Phase 4A changed ALLOCATABLE to CHARACTER(1024). Original code worked. Our fix restores what we broke. |

## What We Learned

The initial instinct — "these are NREL production bugs" — was technically correct but missed important context. These are defects in research code for an experimental feature that has never been simulation-tested by anyone. The bugs exist per the Fortran standard, but they've never affected anyone's results because the code path has never been exercised.

The deeper lesson: when investigating code from capable engineers, understanding WHY something looks wrong matters as much as proving THAT it's wrong. The answer wasn't "NREL missed obvious bugs" — it was "this is partially-implemented research code that was never end-to-end tested, and we're the first to run it."

Our fixes are correct. The K loop matches the steady-state pattern and properly initializes all blade instances. The n loop correctly handles `F_GenSpdNotch_N=0` by skipping the notch filter entirely. The ACC_INFILE fix is our own cleanup. We should carry all three forward, but document them accurately: two are fixes for untested research code, one is our own Phase 4A consequence.
