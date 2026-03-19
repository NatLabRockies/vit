# SecLPFilter_Vel Extraction Fix: Two Root Causes, Zero KGen Bugs
**Date:** 2026-03-19
**Status:** completed
**Scope:** KGen extraction, kernel verification, vit_sim.py, VIT config

## Background

SecLPFilter_Vel was the only ROSCO function that could not be KGen-verified. The call site is inside a `DO I_GROUP = 1, CntrPar%CC_Group_N` loop in the `CableControl` subroutine (Controllers.f90:939). KGen generated the kernel directory but produced zero state files. The working hypothesis was that KGen could not handle DO loops with derived-type iteration bounds — this turned out to be wrong.

The function was originally translated by pattern from SecLPFilter (structurally identical) in Phase 6B and integrated without KGen verification. This investigation aimed to fix KGen so SecLPFilter_Vel could be properly verified.

## Investigation

### Step 1: Inspect KGen's instrumented source

Added a temporary debug hook in VIT's build wrapper (`extract.py`) to copy the instrumented Controllers.f90 before KGen restored it. This was the critical diagnostic step.

The instrumented source showed that **KGen's state capture code was correctly placed inside the DO loop**. The `ancestors()` method in KGen's `base_classes.py` does skip DO loops when building the parent block chain, but this only affects where declarations are placed (subroutine level) — not where the BEFORE_CALLSITE subpart is created. The BEFORE_CALLSITE uses `node.kgen_parent` (the actual parse tree parent, which IS the DO loop), not `Config.parentblock['stmt']`.

The instrumented code had:
- Initialization block (allocate kgen variables) — inside the DO loop, guarded by `.NOT. ALLOCATED(kgen_isstop)`
- `kgen_check_save` calls for invocations 1-20 — inside the DO loop
- State WRITE calls when `kgen_issave(0)` is true — inside the DO loop
- Invocation counter increment `kgen_invoke(0) = kgen_invoke(0) + 1` — inside the DO loop

All correct. KGen handles DO loop callsites properly.

### Step 2: Discover the real Issue 1 — CableControl never called

The `CALL CableControl` in DISCON.F90:131-133 is gated:
```fortran
IF (CntrPar%CC_Mode > 0) THEN
    CALL CableControl(avrSWAP, CntrPar, LocalVar, objInst, ErrVar)
END IF
```

Scenario 3 in vit_sim.py set `CC_Group_N=1` but left `CC_Mode=0`:
```python
# CC_Mode stays at 0 (safe — avoids hardcoded CC_DesiredL out-of-bounds)
'CC_Group_N': 1,
'CC_GroupIndex': '2601',
```

Setting `CC_Group_N` only controls the loop inside CableControl. CableControl itself is never called with `CC_Mode=0`. The comment about "hardcoded CC_DesiredL out-of-bounds" was a false concern — `CC_DesiredL` is a fixed-size(12) array initialized to 0, and the hardcoded values at `Time > 500` are never reached in the 400-second Scenario 3 simulation.

**Fix:** Changed `CC_Mode` to 1 in Scenario 3. Extraction immediately produced 20 state files.

### Step 3: Discover Issue 2 — Kernel literal precision loss

With 20 state files, verification showed 38/40 — only the first invocation (iStatus=0, where filter coefficients are computed) failed. The coefficient arrays differed by ~3.5e-9.

Extensive debugging ruled out:
- `X**2.0` vs `X*X` — gfortran at -O2 optimizes both identically (tested with exact values)
- Expression evaluation order — `(A*B)*(C*D)` vs `((A*B)*C)*D` — identical for the specific values
- Struct layout mismatch — invocations 2-20 passed, ruling out memory alignment issues
- Calling convention — `REAL(C_DOUBLE)` to `double` is a no-op for REAL(8)

Added `fprintf` debug output to the C++ function and discovered:
```
CornerFreq=3.14159274101257324e-01   (C++ received from kernel)
```
vs expected:
```
CornerFreq=3.14159265359000017e-01   (original simulation value)
```

`3.14159274101257324` is `float(π)` promoted to double — single-precision truncation!

**Root cause:** The kernel Makefile used:
```
FC_FLAGS_SET_0 ?= -O2 -ffree-form -ffree-line-length-none
```
Missing `-fdefault-real-8 -fdefault-double-8` from ROSCO's CMakeLists.txt. Without these flags, the literal `3.14159265359` in `REAL(DbKi), PARAMETER :: PI = 3.14159265359` is first evaluated as `REAL(4)` (default), then promoted to `REAL(8)` — losing ~7 digits of precision. The original ROSCO build includes `-fdefault-real-8`, so `3.14159265359` is evaluated directly as `REAL(8)`.

This affected `CornerFreq = 2*PI/CntrPar%CC_ActTau` computed in the kernel's CableControl subroutine. The C++ function received the wrong CornerFreq and computed different coefficients.

**Why SecLPFilter passed but SecLPFilter_Vel failed:** SecLPFilter's CornerFreq comes from parameter file values (already stored as doubles in CntrPar), not from an expression involving the PI constant. Only expressions using Fortran literal constants in the kernel code are affected.

**Fix:** Added `fortran.flags` config field to vit.yaml. VIT propagates these flags to the kernel Makefile's `FC_FLAGS_SET_0`. For ROSCO: `flags: -fdefault-real-8 -fdefault-double-8`.

## Changes

### ROSCO repo
| File | Change |
|------|--------|
| `Examples/vit_sim.py` | Set `CC_Mode=1` in Scenario 3 patches |
| `vit.yaml` | Added `fortran.flags: -fdefault-real-8 -fdefault-double-8` |
| `translations/Filters/seclpfilter_vel.cpp` | Status changed from "unverified" to "verified" |
| `kernel/SecLPFilter_Vel/` | New golden kernel fixture (20 state files) |

### VIT repo
| File | Change |
|------|--------|
| `vit/config.py` | Added `fortran_flags` field to `VitConfig`, DEFAULT_CONFIG, load/save |
| `vit/kernel_verify.py` | `setup_kernel_cpp` and `_patch_kernel_makefile` accept `fortran_flags`; Makefile patching always updates `FC_FLAGS_SET_0` |
| `vit/cli.py` | Both `setup_kernel_cpp` call sites pass `config.fortran_flags` |
| `CLAUDE.md` | Updated golden fixtures list, extraction table, verification regression, clean build procedure, known limitations |

## Verification

- **SecLPFilter_Vel:** 40/40 IDENTICAL (0 IN_TOL)
- **All 15 golden fixtures:** 40/40 passed each
- **Unit tests:** 410/410 passed
- **Clean build test:** byte-identical simulation output across all 3 scenarios with 15 integrated C++ translations

## Key Lessons

1. **Inspect the instrumented source before blaming the tool.** The DO loop hypothesis was plausible but wrong. Adding a debug hook to preserve the instrumented .f90 file immediately showed KGen's code was correctly placed. Without this, we would have spent time modifying KGen's parser for a non-existent bug.

2. **Zero state files means "code never ran", not "code generation failed."** KGen generates the kernel regardless of runtime behavior. The distinction between "kernel exists but empty" and "kernel wasn't generated" is critical. The former is a runtime/config issue; the latter is a parse-time issue.

3. **Compiler flags are part of the correctness contract.** `-fdefault-real-8` changes the semantics of Fortran literal constants. A kernel compiled without it produces numerically different code even though the source is identical. This is not a rounding issue or FMA issue — it's a type width issue that causes ~1e-8 errors, well beyond any tolerance.

4. **Debug output in the translated C++ is the fastest diagnostic.** When the coefficient mismatch appeared, adding `fprintf(stderr, ...)` to the C++ function immediately revealed the CornerFreq discrepancy. Hours of theoretical analysis about expression ordering and pow() semantics were less productive than one line of debug output.

## Related

- Phase 6B dev note: `202603190316-phase6b-notch-seclpfilter-vel.md` — original pattern-based translation
- Phase 6A dev note: `202603182220-phase6a-seclpfilter.md` — SecLPFilter verification (same coefficient expressions, different call context)
