# avrSWAP Assumed-Size Array Heap Overflow Fix
**Date:** 2026-03-25
**Status:** completed
**Scope:** KGen extraction, assumed-size arrays, CableControl/StructuralControl kernels

## The Problem

Three ControlParameters ALLOCATABLE fields showed entry ≠ exit in KGen state files despite no code modifying them:

| Function | Field | Element | Entry | Exit (correct) | Error |
|----------|-------|---------|-------|-----------------|-------|
| CableControl | `we_fopoles` | 3 | 0.0 | -0.01965 | zero |
| StructuralControl | `vs_kp` | 1 | -697.771 (correct) | -697.77099609375 | float32 truncation |
| StructuralControl | `pc_gs_ki` | 29 | -0.00197 (correct) | -0.001969999 | small diff |

The two-run Fortran-vs-C++ comparison masked these (both runs saw the same corruption), so verification passed. But the underlying corruption was real.

## Root Cause

**`assumed_size_arrays.avrSWAP: 2000` in vit.yaml was too small.**

ROSCO's CableControl writes to `avrSWAP(CntrPar%CC_GroupIndex(I_GROUP))` and `+1` at indices starting at 2601. StructuralControl writes to `avrSWAP(CntrPar%StC_GroupIndex(I_GROUP))` at indices starting at 2801. Both exceed the 2000-element allocation.

During extraction, the real simulation has a much larger avrSWAP (allocated by the simulation framework). KGen only captures the first 2000 elements (per VIT's sed patch: `avrswap(1:2000)`). In the kernel, avrSWAP is allocated from the state file with exactly 2000 elements. When the function writes to indices > 2000, it overflows into adjacent heap memory, corrupting ALLOCATABLE array data in ControlParameters.

## Why 2000 was wrong

The value was a guess based on the standard Bladed DLL interface, which uses indices up to ~1018 (platform motion states). ROSCO's cable and structural control features are OpenFAST extensions that use indices at 2601+ and 2801+ — well beyond the standard range. These minimum indices are explicitly validated in ReadSetParameters.f90:

```fortran
IF (CntrPar%CC_GroupIndex(I) < 2601) THEN   ! line 1731
IF (CntrPar%StC_GroupIndex(I) < 2801) THEN  ! line 1748
```

## Why this wasn't caught earlier

1. **No bounds checking**: avrSWAP is declared as `avrSWAP(*)` (assumed-size). Fortran's `-fcheck=all` cannot bounds-check assumed-size arrays.
2. **Silent heap corruption**: The overflow writes into ALLOCATABLE heap data, producing corrupted values (zeros, float32 truncation) rather than crashes.
3. **Two-run comparison masked it**: Both Fortran and C++ kernels overflow identically, so corrupted fields match between runs.
4. **Different error signatures per kernel**: CableControl corrupted WE_FOPoles (entry=0.0), StructuralControl corrupted VS_KP (float32) and PC_GS_KI (small diff). This made it look like multiple unrelated KGen bugs.

## Investigation Path

1. Confirmed the state file had correct data (Python binary parse showed -0.01965 at both entry and exit positions)
2. Added diagnostics: kr_ read gets correct value → CableControl changes it to 0.0
3. Narrowed within CableControl: correct before avrSWAP write at index 2601, zero after
4. Identified CC_GroupIndex(1) = 2601 exceeds 2000-element allocation
5. Confirmed fix: padded avrSWAP to 4000 → WE_FOPoles IDENTICAL
6. Proper fix: increased `assumed_size_arrays.avrSWAP` to 3000 and re-extracted

## Fix

### ROSCO changes
- `vit.yaml`: `avrSWAP: 2000` → `avrSWAP: 3000` (covers CC_GroupIndex ≥2601 and StC_GroupIndex ≥2801 with margin)
- Re-extracted CableControl and StructuralControl golden fixtures with correct size
- Both now pass 40/40 with zero OUT_TOL fields

### VIT changes
- `extract.py`: Added `infer_assumed_size_arrays()` that scans source for array index accesses and bounds checks
- `cli.py`: Added `--assumed-size NAME=SIZE` CLI override for `vit extract`
- When configured size < inferred minimum, VIT exits with code 10 (`[VIT:CONFIRM]`) showing the evidence
- `CLAUDE.md`: Documented assumed-size overflow risk and the confirm protocol

## CableControl Heap Crash Resolution

The CableControl "heap crash" (malloc tcache corruption on case 3+) that was previously attributed to VIT's field logging was ALSO caused by this avrSWAP overflow. With avrSWAP=3000, CableControl now passes 40/40 with field logging enabled — no crash.

## Related Dev Notes
- `202603250848-cablecontrol-heap-crash-investigation.md` — heap crash attributed to field logging (superseded: actual cause was avrSWAP overflow)
- `202603251133-we-fopoles-state-file-data-error.md` — WE_FOPoles investigation (resolved by this fix)
- `202603251131-pi-constant-and-fma-fix.md` — PI/FMA fixes (separate issue, also resolved)
