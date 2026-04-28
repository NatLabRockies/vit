# ComputeTau2 Integration Complete — Two Root Causes Found
**Date:** 2026-04-28
**Status:** completed
**Scope:** DBEMT Phase A, translation bug, compiler vectorization, workflow discipline

## Summary

ComputeTau2 is the first DBEMT function integrated into production. Integration initially caused catastrophic divergence in `ad_MHK_RM1_Fixed_UA` (max_abs=5.73e+06). An earlier investigation (dev note `202604281032`, now superseded) concluded that PRIVATE function bodies should not be replaced — this was wrong. Rigorous investigation found two independent root causes: a translation bug and a vectorization flag issue. Both fixed. **17/17 baselines now pass bit-identical.**

## Root Cause 1: Translation Bug — Fortran `**2` Operator Precedence

**The bug:** C++ `0.26 * spanRatio * spanRatio` evaluates left-to-right as `(0.26 * spanRatio) * spanRatio`. Fortran `0.26*spanRatio**2` evaluates as `0.26 * (spanRatio * spanRatio)` because exponentiation binds tighter than multiplication.

**The effect:** Different intermediate rounding produces 1-3 ULP differences in `k_tau` for certain `spanRatio` values. 7,833 mismatches across the simulation, affecting nodes 18, 24, 26, 27, 30 on both blades.

**Why KGen missed it:** The 20 captured invocations (from `ad_MHK_RM1_Fixed`) happened to use `spanRatio` values that don't produce a ULP difference at this particular expression, or the difference didn't propagate to the UpdateStates-level output fields that KGen verifies.

**The fix:** Add parentheses — `0.26 * (spanRatio * spanRatio)`.

**The lesson (added to TRANSLATION_TIPS.md):** Fortran `a*x**N` always means `a * (x**N)`. C++ `a * x * x` always means `(a * x) * x`. When translating coefficient-times-power expressions, always parenthesize the power: `a * (x * x)`.

### Diagnostic method

Added dual-computation debug code to the Fortran wrapper: compute the result using both the original Fortran expressions AND the C++ wrapper call, compare per-invocation. This immediately revealed the 7,833 mismatching values and pinpointed the exact expression.

```fortran
! Inline Fortran reference computation
REAL(8) :: sr_f, kt_f, t2_f
IF (p%DBEMT_Mod == DBEMT_tauConst .OR. p%DBEMT_Mod == DBEMT_cont_tauConst) THEN
    sr_f = p%spanRatio(i,j)
ELSE
    sr_f = u%spanRatio
END IF
kt_f = 0.39D0 - 0.26D0*sr_f**2
t2_f = kt_f * tau1
IF (tau2 /= t2_f) THEN
    WRITE(*,'(A,I3,A,I3,A,ES25.17,A,ES25.17)') &
        'DIFF i=',i,' j=',j,' tau2F=',t2_f,' tau2C=',tau2
    STOP 1
END IF
```

After the parentheses fix, this comparison produced zero mismatches — confirming the C++ now matches Fortran exactly.

## Root Cause 2: Vectorization Divergence (Scalar vs SIMD exp)

**The problem:** After fixing the translation bug, `ad_MHK_RM1_Fixed_UA` still failed with max_abs=5.51e+06. Assembly comparison showed:

- **Clean source (original ComputeTau2 body):** gfortran inlines ComputeTau2 → `do indx=1,2` loop body is large → gfortran decides NOT to vectorize → 2x scalar `bl exp`
- **Integrated source (wrapper):** ComputeTau2 is an opaque external call → loop body is just the exp expressions → gfortran vectorizes → 2x SIMD `bl _ZGVnN2v_exp`

glibc's libmvec SIMD `exp()` and scalar libm `exp()` differ by 1 ULP for certain inputs. The DBEMT exponential filter is a coupled ODE system:
```fortran
x%vind_1(indx) = C0*exp(-p%dt/tau1) + A + B*(p%dt-tau1)
x%vind(indx) = C0_2*exp(-p%dt/tau2) + A + B*(p%dt-tau1-tau2) + (C0/(1-k_tau))*exp(-p%dt/tau1)
```
These feed back into the next timestep via `x%element(i,j)%vind_1` and `x%element(i,j)%vind`. With UA enabled, the feedback loop amplifies the 1-ULP `exp()` difference into full divergence over 600 timesteps.

**The fix:** `set_source_files_properties(src/DBEMT.f90 PROPERTIES COMPILE_FLAGS "-fno-tree-loop-vectorize")` in `modules/aerodyn/CMakeLists.txt`. This ensures scalar `exp()` regardless of whether function bodies are present or replaced by wrappers.

**Relationship to kernel verification fix:** Dev note `202604260357` documented the identical scalar-vs-SIMD exp divergence in KGen kernel verification (CalculateUACoeffs). The kernel fix was the opposite direction: `-fvect-cost-model=unlimited` + `-lmvec -lm` to FORCE vectorization in kernels to match the production build. The production fix here is to PREVENT vectorization from changing. Both target the same root cause (inconsistent math library implementation) from different angles.

### Diagnostic method

Assembly generation and comparison:
```bash
# Generate assembly for clean vs integrated
gfortran -O3 -ffp-contract=off ... -S -o /tmp/dbemt_clean.s DBEMT_clean.f90
gfortran -O3 -ffp-contract=off ... -S -o /tmp/dbemt_integrated.s DBEMT.f90

# Extract UpdateStates function
awk '/^__dbemt_MOD_dbemt_updatestates:/,/\.cfi_endproc/' /tmp/dbemt_clean.s > /tmp/us_clean.s
awk '/^__dbemt_MOD_dbemt_updatestates:/,/\.cfi_endproc/' /tmp/dbemt_integrated.s > /tmp/us_int.s

# Compare exp calls
grep 'exp' /tmp/us_clean.s    # → 2x "bl exp" (scalar)
grep 'exp' /tmp/us_int.s      # → 2x "bl _ZGVnN2v_exp" (SIMD) + 6x "bl exp" (scalar, other paths)
```

### Hypotheses tested and eliminated

| Hypothesis | Test | Result |
|-----------|------|--------|
| Any change to module causes divergence | Added `CONTINUE` to ComputeTau2 body | 17/17 PASS — no effect |
| Adding a subroutine to the module causes divergence | Added `vit_noop()` subroutine | 17/17 PASS — no effect |
| Calling a subroutine from ComputeTau2 causes divergence | Added `call vit_noop()` | 17/17 PASS — no effect |
| `-fno-inline` prevents the issue | Added per-file flag | Still fails — inlining is not the only factor |
| `-O2` prevents the issue | Added per-file flag | Still fails — IPA/optimization level is not the only factor |
| `-fno-tree-loop-vectorize` alone prevents it | Added per-file flag | Still fails — translation bug remains |
| Translation bug alone causes it | Fixed parentheses, no vectorization flag | Still fails — vectorization still differs |
| Both fixes together | Parentheses + `-fno-tree-loop-vectorize` | **17/17 PASS** |

## Baselines Regenerated

All 17 baselines regenerated from clean Fortran source with the `-fno-tree-loop-vectorize` flag. The flag changes the baseline values for DBEMT-path test cases (scalar exp instead of potentially-vectorized exp). NREL regression passes 17/17 at 1e-2 tolerance with the new baselines.

## Process Reflection

The initial dev note (`202604281032`) concluded "Private leaf functions should NOT have their Fortran bodies replaced individually." This was wrong — it contradicted the ROSCO workflow where all 52 functions (including PRIVATE module-internal functions) were integrated successfully. The conclusion was premature because the investigation stopped at the symptom (divergence when body is replaced) without identifying the actual root cause (translation bug + vectorization).

**Lesson:** When integration fails, the workflow is probably right and the translation or environment is wrong. Investigate translation bugs, compiler flag issues, and stale state exhaustively before concluding that the workflow needs to change. The user caught this and redirected the investigation.

## Files Changed

| File | Change |
|------|--------|
| `openfast/translations/DBEMT/computetau2.cpp` | Parentheses fix: `0.26 * (spanRatio * spanRatio)` |
| `openfast/modules/aerodyn/CMakeLists.txt` | `-fno-tree-loop-vectorize` on DBEMT.f90 |
| `openfast/baselines/aerodyn/*` | All 17 regenerated from clean with new flag |
| `openfast/TRANSLATION_TIPS.md` | Added `**2` operator precedence lesson |
| `vit/CLAUDE.md` | Added vectorization fix note + workflow discipline section |
| `openfast/vit.yaml` | ComputeTau2 status → `integrated` |
