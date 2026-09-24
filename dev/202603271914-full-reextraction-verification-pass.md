# Full Re-extraction and Verification: 28/28 Passed
**Date:** 2026-03-27
**Status:** completed
**Scope:** Golden fixture regeneration, per-function scenario mapping, verification regression

## What Was Done

Complete re-extraction and verification of all 28 golden kernel fixtures with three improvements applied:

1. **Per-function scenario mapping**: Each function now extracts with the specific scenario that exercises its call site with non-zero inputs (see dev note `202603271853`)
2. **Statefile deduplication**: `kgen_statefile.lst` entries are deduplicated after extraction, eliminating the ElapsedTime append bug's doubling effect (see dev note `202603271740`)
3. **CMake cache cleanup**: `cleanup_kgen_utils_injection()` now reconfigures CMake after extraction to clear stale `DependInfo.cmake` references (see commit `ab47db6`)

## Extraction Results

All 28 functions extracted successfully using `scripts/extract_all.sh`. Case counts range from 20-23 (ElapsedTime adds a few invocations beyond the CLI 1-20 range). All `kgen_statefile.lst` files are deduplicated.

| Function | Scenario | Cases |
|----------|----------|-------|
| saturate | 3 | 20 |
| wrap_180 | 3 | 22 |
| wrap_360 | 2 | 22 |
| ratelimit | 3 | 21 |
| ColemanTransform | 8 | 20 |
| ColemanTransformInverse | 8 | 22 |
| identity | 3 | 20 |
| sigma | 8 | 20 |
| interp1d | 3 | 20 |
| LPFilter | 3 | 22 |
| HPFilter | 3 | 21 |
| SecLPFilter | 3 | 21 |
| NotchFilter | 3 | 21 |
| NotchFilterSlopes | 6 | 23 |
| SecLPFilter_Vel | 3 | 21 |
| PIController | 3 | 20 |
| PIIController | 4 | 20 |
| ResController | 8 | 21 |
| ForeAftDamping | 7 | 20 |
| IPC | 8 | 20 |
| FloatingFeedback | 7 | 20 |
| ActiveWakeControl | 8 | 23 |
| VariableSpeedControl | 3 | 22 |
| PitchControl | 3 | 20 |
| YawRateControl | 7 | 21 |
| FlapControl | 7 | 22 |
| CableControl | 7 | 21 |
| StructuralControl | 7 | 20 |

## Verification Results

All 28 functions verified successfully against the new golden fixtures:

```
=== Functions (9) ===
saturate: 20/20 passed
wrap_180: 22/22 passed
wrap_360: 22/22 passed
ratelimit: 21/21 passed
sigma: 20/20 passed
interp1d: 20/20 passed
ColemanTransform: 20/20 passed
ColemanTransformInverse: 22/22 passed
identity: 20/20 passed

=== Filters (6) ===
LPFilter: 22/22 passed
HPFilter: 21/21 passed
SecLPFilter: 21/21 passed
NotchFilter: 21/21 passed
NotchFilterSlopes: 23/23 passed
SecLPFilter_Vel: 21/21 passed

=== Controllers (13) ===
PIController: 20/20 passed
PIIController: 20/20 passed
ResController: 21/21 passed
ForeAftDamping: 20/20 passed
FloatingFeedback: 20/20 passed
StructuralControl: 20/20 passed
CableControl: 21/21 passed
FlapControl: 22/22 passed
YawRateControl: 21/21 passed
VariableSpeedControl: 22/22 passed
IPC: 20/20 passed
ActiveWakeControl: 23/23 passed
PitchControl: 20/20 passed
```

## What This Proves

The previous golden fixtures had two issues:
1. **Duplicated state file lists** (40 entries instead of 20) from the ElapsedTime append bug — harmless but wasteful and misleading
2. **Zero-input invocations** for 8 functions (YawRateControl, ForeAftDamping, FloatingFeedback, StructuralControl, CableControl, FlapControl, IPC, ActiveWakeControl) that only proved plumbing, not arithmetic

The new fixtures fix both issues. Every function is now verified with non-zero inputs from the correct scenario, and the case counts accurately reflect the number of unique invocations tested.

## Session Changes Summary

This session started with a Q&A discourse about the verification pipeline and evolved into a deep investigation of KGen's extraction internals:

**VIT commits:**
| Hash | Description |
|------|-------------|
| `19e3dbd` | Dev note: kgen_statefile.lst duplication root cause |
| `f49a789` | Update dev note: confirmed with instrumented extraction |
| `ab47db6` | Fix ElapsedTime build: reconfigure CMake, clean model/ |
| `3d196f8` | Deduplicate kgen_statefile.lst after extraction |
| `614ad7b` | Per-function scenario mapping, extract/verify scripts |

**ROSCO commits:**
| Hash | Description |
|------|-------------|
| `30605ee` | Add extract_all.sh and verify_all.sh scripts |
| `c3905ce` | Re-extract all 28 golden fixtures with correct scenarios |
