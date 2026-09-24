# Verification Integrity Audit: FMA Fix, Field Logging Gap, and KGen Limitation
**Date:** 2026-03-19
**Status:** completed (FMA + field logging); in-progress (SecLPFilter_Vel KGen workaround)
**Scope:** kernel_verify.py, integrate.py, ROSCO CMakeLists.txt, test_kernel_verify.py

## Origin

After Phase 6B completed (all 6 Filters.f90 functions translated), the clean build test showed a small diff in Scenario 3 output — Power values differing by 0.1–0.3 kW. Two issues were flagged during review:

1. **The drift is not acceptable.** Either the output matches exactly, or it doesn't. "Sub-ULP drift" was hand-waved as expected FMA behavior, but the standard must be bit-identical output. If there's non-determinism, we need to find it and eliminate it.

2. **A KGen extraction failure cannot be accepted.** SecLPFilter_Vel was translated "by pattern" from SecLPFilter because KGen couldn't capture state from its call site (inside a DO loop with derived-type iteration bound). This is not verification — it's assumption. We need to find a way to make KGen work for this function.

Both issues required investigation and resolution before moving forward.

## Issue 1: The Drift

### Initial Diagnosis

The Scenario 3 diff showed 3 lines with Power differences of 0.1–0.3 kW. Scenarios 1+2 were byte-identical. Systematic bisection:

1. **Pure Fortran baseline is deterministic** — two identical runs produced identical output.
2. **9 Functions.f90 translations only (no Filters)** — Scenario 3 byte-identical. Functions are clean.
3. **3 Phase 6A Filters only (no Functions)** — Scenario 3 shows the drift. Narrowed to Filters.
4. **LPFilter alone** — Scenario 3 shows the drift. Isolated to one function.

The LPFilter C++ translation was verified 40/40 by KGen. But examining the field data revealed the smoking gun:

```
LPFilter.0.0.7,genspeedf,scalar,IN_TOL,24.042039462813207,24.042039462813211,3.55e-15
LPFilter.0.0.10,genspeedf,scalar,IN_TOL,30.472872602753299,30.472872602753306,7.11e-15
```

12 fields across 20 cases showed `IN_TOL` instead of `IDENTICAL`. The differences were exactly 1–2 ULP — the signature of different floating-point rounding paths.

### Root Cause: FMA Contraction Scheduling

**FMA (Fused Multiply-Add)** is a hardware instruction that computes `a*b + c` with a single rounding instead of two. Both gfortran and g++ default to `-ffp-contract=fast` at `-O2`, which allows the compiler to fuse multiply-add pairs. But gfortran and g++ are independent compilers with independent optimizers — they choose different fusions for the same expression.

For the LPFilter expression:
```fortran
LPFilter = 1.0/FP%lpf1_a1(inst) * (-FP%lpf1_a0(inst)*FP%lpf1_OutputSignalLast(inst) + FP%lpf1_b1(inst)*InputSignal + FP%lpf1_b0(inst)*FP%lpf1_InputSignalLast(inst))
```

The inner expression `(-a0*O + b1*I + b0*IL)` has multiple possible FMA schedules:
- `fma(b1, I, -a0*O)` then `fma(b0, IL, temp)` — one rounding per fma
- `-a0*O` then `fma(b1, I, temp)` then `fma(b0, IL, temp2)` — different rounding path
- No FMA: 3 multiplies + 2 adds, each rounded independently

Different schedules produce different bit patterns. The difference is at most 1 ULP per operation.

### Why It Was Invisible Before Scenario 3

In Scenarios 1+2, LPFilter's 1-ULP output difference didn't propagate visibly because:
- GenSpeedF (the filtered value) goes to the torque controller, but GenSpeed comes from the *next timestep's* rotor dynamics (in Python). The 1-ULP difference doesn't feed back into LPFilter within the same DLL call.
- Power output format (`%.1f kW`) has enough display resolution headroom that accumulated error stays below the rounding threshold.

In Scenario 3, the NotchFilter (still Fortran, not translated) sits between LPFilter and the torque controller: `GenSpeed → LPFilter(C++) → GenSpeedF → NotchFilter(Fortran) → GenSpeedF → torque controller`. The NotchFilter's state-dependent coefficients amplify the 1-ULP difference into a diverging trajectory over hundreds of timesteps.

### Fix Attempts

**Attempt 1: `-ffp-contract=off` on C++ only.** This made things *worse*. The baseline Fortran used FMA while C++ didn't, creating *larger* differences for all translated functions.

**Attempt 2: `-ffp-contract=off` on both Fortran and C++ (via VIT integration CMake patch).** This broke the clean build test because the baseline (compiled without the patch) and the integrated build (compiled with the patch) used different Fortran flags. The Fortran code for untranslated functions produced different results.

**Attempt 3 (final): `-ffp-contract=off` in ROSCO's CMakeLists.txt permanently.** This ensures both baseline and integrated builds use the same flags. Both gfortran and g++ compute with standard IEEE 754 rounding at each individual operation — no FMA fusion, no scheduling differences, bit-identical results.

### Resolution

- Added `-ffp-contract=off` to the GNU Fortran flags in `ROSCO/rosco/controller/CMakeLists.txt` line 20
- Added `set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -ffp-contract=off")` for C++
- Updated VIT's kernel Makefile CXXFLAGS to include `-ffp-contract=off`
- **This is a verification-phase tool, not permanent.** Once all of ROSCO is translated to C++ and there's no mixed Fortran/C++ binary, FMA can be re-enabled.

### Post-Fix Verification

After the fix, the clean build test procedure includes two checks:

1. **Verification check** (`-ffp-contract=off` on both, the default): diff must be empty. All 15 C++ translations, all 3 scenarios, byte-identical.
2. **FMA sanity check** (default flags, FMA on both): diff should be small and bounded. Confirms no structural translation bugs hiding behind the FMA mask.

## Issue 1b: Re-extraction of LPFilter Golden Kernel

After adding `-ffp-contract=off` to ROSCO's CMakeLists.txt, the LPFilter golden kernel (extracted before the flag was added) still showed 12 `IN_TOL` fields. The reference data was captured with FMA-enabled Fortran, but the verification kernel now compiled C++ without FMA.

Fix: re-extracted the LPFilter kernel with the new flags. After re-extraction: 9200 IDENTICAL, 0 IN_TOL. The previous extraction failure during re-extraction was caused by leftover integration artifacts in CMakeLists.txt (enable_language(CXX) with no C++ source files) — resolved by fully resetting CMakeLists.txt to upstream before adding the FMA flag.

## Issue 2: Field Logging Gap

### Discovery

While auditing all 14 golden fixtures for IN_TOL counts after the FMA fix, two kernels had **no field CSV at all**:

- `wrap_360`: 40/40 passed, but no `verify_fields.csv`
- `ColemanTransformInverse`: 40/40 passed, but no `verify_fields.csv`

All other 12 kernels had complete field CSVs with 0 IN_TOL.

### Root Cause

KGen generates two different verification patterns:

1. **Struct field comparisons** (most functions): KGen compares the full `LocalVar` struct using `var%fieldname == kgenref_var%fieldname`. VIT's `_patch_nan_comparisons()` and `_patch_field_value_logging()` detect the `var%` pattern and inject NaN guards + `[VIT_FIELD]`/`[VIT_ARRAY]` logging tags.

2. **Bare variable comparisons** (wrap_360, ColemanTransformInverse): For functions whose verified output is a scalar return value or array output parameter (not struct modification), KGen generates inline `kv_ipc_real__dbki` / `kv_ipc_real__dbki_dim1` subroutines. These compare `var == kgenref_var` directly (no `%` field access). Neither `_patch_nan_comparisons()` nor `_patch_field_value_logging()` detected these patterns.

These subroutines live in the kernel's `Controllers.f90` (not `ROSCO_Types.f90`). The variable name is passed as a string parameter `varname`.

### Fix

Extended both functions in `kernel_verify.py`:

**`_patch_nan_comparisons()`**: Added a second `re.sub` for bare `IF (var == kgenref_var) THEN` pattern. The NaN guard uses the same IEEE 754 `x /= x` trick. The diagnostic WRITE uses `trim(adjustl(varname))` instead of a hardcoded field name.

**`_patch_field_value_logging()`**: Added two new regex patterns (`bare_scalar_re` and `bare_array_re`) alongside the existing struct patterns. The injected WRITE statements use `var`/`kgenref_var`/`diff`/`rmsdiff`/`n`/`varname` — all local variables in the `kv_ipc_*` subroutines, guaranteed to be in scope.

Added 12 new tests in `test_kernel_verify.py` covering bare scalar NaN patching, bare scalar/array field logging, mixed struct+bare content, and idempotency.

### Post-Fix State

All 14 golden fixtures now produce field CSVs:

| Kernel | States | IDENTICAL | IN_TOL |
|--------|--------|-----------|--------|
| saturate | 20 | 9200 | 0 |
| wrap_180 | 20 | 9200 | 0 |
| wrap_360 | 20 | 40 | 0 |
| ratelimit | 20 | 9200 | 0 |
| ColemanTransform | 20 | 9200 | 0 |
| ColemanTransformInverse | 20 | 40 | 0 |
| identity | 20 | 9200 | 0 |
| sigma | 20 | 9360 | 0 |
| interp1d | 20 | 9360 | 0 |
| LPFilter | 20 | 9200 | 0 |
| HPFilter | 20 | 9200 | 0 |
| SecLPFilter | 20 | 9200 | 0 |
| NotchFilter | 20 | 9200 | 0 |
| NotchFilterSlopes | 20 | 9200 | 0 |

wrap_360 (40 entries) and ColemanTransformInverse (40 entries) have fewer entries because they only verify 1 output variable per case (scalar return / array output) rather than a full 460-field struct.

## Issue 3: SecLPFilter_Vel KGen Extraction (Outstanding)

### Problem

KGen cannot capture state for SecLPFilter_Vel. The call site is inside `DO I_GROUP = 1, CntrPar%CC_Group_N` in CableControl (Controllers.f90 line 939). KGen generates the kernel but writes zero state files. The issue appears to be that KGen doesn't instrument calls inside DO loops where the iteration bound comes from a derived type field.

### Current State

SecLPFilter_Vel was translated by pattern from SecLPFilter — structurally identical (same args, same FilterParameters struct), just different coefficient formulas (`lpfV_` prefix, velocity output variant `b2 = 2.0*DT*CornerFreq**2.0`, `b1 = 0.0`, `b0 = -2.0*DT*CornerFreq**2.0`). It passes the full simulation diff (byte-identical output with `-ffp-contract=off`). But it has no KGen verification.

### Proposed Workaround

Add a temporary standalone call site for SecLPFilter_Vel outside the DO loop — a direct call in Fortran (not inside any loop) that exercises the function with realistic parameters. Extract from that call site, verify the translation, then remove the temporary call site.

This has not been implemented yet. It is the next item to address.

## Commits

| Commit | Repo | Description |
|--------|------|-------------|
| `fe613bf` | VIT | Fix FMA contraction in kernel CXXFLAGS |
| `c326bc5` | ROSCO | Add `-ffp-contract=off` to CMakeLists.txt |
| `9698358` | VIT | Document FMA fix and KGen limitations |
| `1191461` | VIT | Revise FMA as verification-phase tool, add FMA sanity check |
| `b3cec2c` | ROSCO | Re-extract LPFilter kernel with no-FMA baseline |
| `ee807ef` | VIT | Extend field logging to bare variable comparisons |

## Test Results

- 410 unit tests pass (12 new for bare variable patterns)
- 14/14 golden fixtures: 40/40 passed, field CSVs present, 0 IN_TOL
- Full clean build test: 15 C++ translations, all 3 scenarios, byte-identical output
