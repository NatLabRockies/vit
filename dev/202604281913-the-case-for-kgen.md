# The Case for KGen
**Date:** 2026-04-28
**Status:** completed
**Scope:** Verification methodology, project strategy

## Context

ComputeTau2's integration failure exposed a real limitation of KGen: 20/20 IDENTICAL with a translation bug hiding in the sample gap. This prompted the question — is KGen worth the extraction overhead? The answer is yes, but the reasoning matters.

## What KGen Actually Gives You

### Per-field, per-case diagnostics

A baseline failure says: `ad_MHK_RM1_Fixed_UA: max_abs=5.73e+06 in RtFldMxh`. That's a simulation-level output channel, many call layers removed from the function you translated. You know something is wrong somewhere in the DBEMT→BEMT→AeroDyn→output chain. You don't know which function, which field, or which timestep introduced the error.

A KGen failure says: `case 7, field alphaBreakUpper: COMPUTED=0.283142857 REFERENCE=0.283148921, diff=6.064e-06`. That's the exact output field, the exact test case, the exact wrong value. You open the translation, find the line that computes `alphaBreakUpper`, and the bug is staring at you.

This scales with function complexity:

| Function | Output fields | Lines | KGen diagnostic value |
|----------|--------------|-------|----------------------|
| ComputeTau2 | 2 (tau2, k_tau) | 26 | Low — you already know which expression to check |
| ComputeTau1 | 3 (tau1, errStat, errMsg) | 66 | Low-medium — two code paths, but still simple |
| CalculateUACoeffs | 40+ (UA_BL struct fields) | 326 | **Essential** — 40 fields × 7 callee interactions × multiple code paths |
| AFI_Init | 20+ (spline coefficients, table params) | 200+ | **Essential** — two-pass architecture, multi-table validation, cubic spline init |
| DBEMT_UpdateStates | 10+ (vind, vind_1, tau1, tau2, states) | 67 | Medium — multiple code paths (Mode 1/2/3), feedback state |

For a 326-line orchestrator with 40 output fields, a baseline failure gives you one signal ("simulation diverged"). KGen gives you 40 signals per case × 20 cases = 800 data points. The first field that fails tells you exactly where the bug is.

### Structural validation before integration

KGen verification exercises the entire VIT integration machinery in a controlled environment:
- View struct population (Fortran → C struct)
- Type marshalling (ALLOCATABLE arrays via C_LOC, OPTIONAL parameters via has_X/X pairs)
- Callee bridge generation (C++ calling back into Fortran for untranslated dependencies)
- Array indexing conventions (1-based Fortran vs 0-based C++, column-major layout)
- Reverse-copy for INOUT view-type arguments

If any of these are wrong, KGen catches it on the first case with a clear field-level error. Without KGen, a type marshalling bug manifests as "simulation produces garbage" — and you're debugging the marshalling AND the translation AND the integration at the same time.

For ComputeTau2, the marshalling was simple (one view struct, one BIND(C) struct). For CalculateUACoeffs, the marshalling involved nested TYPE() view structs, ALLOCATABLE TYPE() array marshalling, 7 callee bridges with their own view stashes, LOGICAL type conversion, and array-returning function bridges. KGen validated all of that infrastructure in seconds. Building that confidence through baseline-only verification would have taken orders of magnitude longer to debug when something went wrong.

### Regression safety net for VIT changes

Golden kernel fixtures (`kernel/<FunctionName>/`) persist across VIT development. When we change VIT's kernel verification code, integration code, or view struct generation, we can re-verify all golden fixtures in seconds. This catches VIT regressions without running any simulations. The AirfoilInfo module has 8+ golden fixtures that serve as a fast regression suite for VIT tool changes.

### The extraction is already paid for

The extraction step (reset → rebuild clean → run simulation → capture state) takes ~15 minutes. But extraction also produces the kernel directory that `vit verify` uses — the standalone replay environment. This environment is reusable:
- Re-verify after translation changes (seconds, no re-extraction)
- Debug with modified C++ (edit, re-verify, iterate — seconds per cycle)
- Benchmark C++ vs Fortran performance
- Test callee bridge interactions in isolation

The 15-minute cost is paid once per function. Every subsequent verification is free.

## Where KGen falls short

### Sample coverage

20 invocations is 0.05% of a typical simulation's calls to a DBEMT function. Input-dependent bugs (like the `a*x**2` operator precedence issue) can hide in the 99.95% KGen doesn't see. This is a fundamental limitation of sample-based testing.

### False confidence

20/20 IDENTICAL creates a strong psychological signal: "the translation is correct." When baselines subsequently fail, the natural conclusion is "something else is wrong" — not "the translation has a bug KGen missed." This is exactly what happened with ComputeTau2. The false confidence cost hours of investigation down the wrong path.

**Mitigation:** Treat KGen as "the plumbing works," not "the translation is correct." The baseline gate is the correctness proof. This is now documented in CLAUDE.md and the workflow discipline section.

### Extraction overhead for simple functions

For a 26-line function with 2 output fields, the extraction pipeline (15 minutes) costs more than the debug cycle if baselines fail (add debug print, rebuild 2 minutes, run 1 test case 1 minute, find the bug). For complex functions, this ratio inverts dramatically.

## The verdict

KGen is worth it because **the functions that matter most are the ones where KGen helps most.** The simple leaf functions (ComputeTau1, ComputeTau2, DBEMT_InitStates) don't need KGen's per-field diagnostics — they're small enough to debug from a baseline failure. But these represent ~200 lines of the ~75,000 total.

The complex functions — CalculateUACoeffs, AFI_Init, DBEMT_UpdateStates, BEMT_CalcOutput, the UnsteadyAero orchestrators, the FVW solvers — have dozens of output fields, multiple code paths, nested type marshalling, and callee bridge interactions. For these, KGen's per-field diagnostics aren't a convenience. They're the difference between a 30-second fix and a multi-day investigation.

The dual-computation debug print (compute Fortran reference alongside C++ in the wrapper, compare per-call) is a valuable complement for catching input-dependent precision bugs that KGen's sample misses. But it only works after integration, and it only tells you "the value is wrong" — not which of 40 output fields diverged first. KGen tells you that.

**Keep KGen for every function.** The extraction overhead is real but bounded (15 minutes, paid once). The diagnostic value scales with complexity. And the golden fixtures provide ongoing regression safety for VIT development. The lesson from ComputeTau2 isn't "KGen is useless" — it's "KGen is necessary but not sufficient, and baselines are the ground truth."
