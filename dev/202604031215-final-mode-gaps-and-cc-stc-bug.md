# Final Mode Coverage Gaps: Scenarios 24-25 and CC_Mode=2 Bug
**Date:** 2026-04-03
**Status:** in-progress
**Scope:** Mode coverage — CC_Mode=2, StC_Mode=2, PRC_Mode=2

## Summary

Added the last two testable scenarios to close all remaining mode coverage gaps:

- **Scenario 25** (PRC_Mode=2): Fully verified — upstream = modified = C++, 80,000 values IDENTICAL. Dynamic power rating with constant multipliers works correctly.
- **Scenario 24** (CC_Mode=2 + StC_Mode=2): Upstream = modified IDENTICAL, but C++ segfaults. A real bug in the C++ DISCON's allocation of `OL_CableControl` and `OL_StructControl` 2D arrays. Investigation partially complete — allocation added but crash persists.

## Scenario 25: PRC_Mode=2 (Complete)

PRC_Mode=2 with PRC_Comm=0 uses constant power rating multipliers from DISCON.IN. `PRC_R_Speed=0.9` reduces rated speed by 10%, visibly affecting torque and speed setpoints through the `LocalVar%PRC_R_Speed` multiplier in PowerControlSetpoints.

Simple scenario — no OL file needed, no conflicts. Verified across all three builds.

## Scenario 24: CC_Mode=2 + StC_Mode=2 (Bug Found)

### What It Tests

CC_Mode=2 and StC_Mode=2 read desired cable lengths and structural control forces from columns in an OL input file, interpolated by time breakpoints. Both require OL_Mode=1 with an extended input file.

Created `OL_Mode1_CC_StC_Input.dat` with 8 columns:
- Columns 1-6: same as OL_Mode1_Input.dat (Time, BldPitch1-3, GenTq, YawRate)
- Column 7: cable desired length (sinusoidal around 0.5m)
- Column 8: structural control force (sinusoidal around 1e6 N)

### The Bug

The C++ DISCON segfaults during the simulation. Root cause: `OL_CableControl` and `OL_StructControl` were not allocated in `discon.cpp`'s `allocate_cntrpar_arrays()` function.

**Why it was missed in Phase 11A:** The Fortran wrapper's comment said "OL_CableControl and OL_StructControl allocated inside C++ pass 2" — but `readcontrolparameterfilesub_pass2_c` only WRITES to these arrays if they're already allocated (guarded by `if (nOlCables > 0 && CntrPar->OL_CableControl)`). Nobody allocates them.

In the Fortran integration path (Stage B), the Fortran wrapper handled this allocation between pass1 and pass2. When Phase 11A absorbed the wrapper into `discon.cpp`, all 44 standard ALLOCATABLE fields were translated, but these two 2D arrays (whose sizes depend on counting non-zero `Ind_CableControl`/`Ind_StructControl` entries) were left with the "allocated in pass2" comment and skipped.

**Partial fix applied:** Added allocation logic to `allocate_cntrpar_arrays()`:
```cpp
int nOlCables = 0;
for (int i = 0; i < cp->n_Ind_CableControl; i++) {
    if (cp->Ind_CableControl[i] > 0) nOlCables++;
}
if (nOlCables > 0) {
    alloc.OL_CableControl = alloc_dbl(nOlCables * n_OL_rows);
    cp->OL_CableControl = alloc.OL_CableControl;
    cp->n_OL_CableControl_rows = nOlCables;
    cp->n_OL_CableControl_cols = n_OL_rows;
}
```

Same pattern for `OL_StructControl`.

**Crash persists:** The allocation is now done, but the segfault continues. The remaining issue is likely a dimension mismatch or access pattern problem — the 2D array layout (column-major, `nOlCables × n_OL_rows`) needs to match how `CableControl` and `StructuralControl` read from it. The C++ translations access it as `OL_CableControl[col * n_rows + I_GROUP]` with `n_rows = n_OL_CableControl_rows = nOlCables`. This looks correct but needs further debugging.

Possible causes:
- Stack overflow from VLA: `double row_slice[n_cols]` where `n_cols = n_OL_rows = 8000` (64KB on stack)
- Dimension swap: rows vs cols might be inverted between allocation and access
- `Ind_CableControl` or `Ind_StructControl` indexing mismatch (1-based vs 0-based)

### Impact

This is the only remaining C++ bug. It affects only CC_Mode=2 and StC_Mode=2 — open-loop cable and structural control from an OL file. These are rarely-used research modes. All other 24 scenarios (1,852,000 values) are fully verified.

## Updated Verification Totals

| Comparison | Values | Scenarios | Status |
|---|---|---|---|
| Upstream vs Modified | 1,872,000 | 24 (1-3, 5-25, excl. 4) | ALL IDENTICAL |
| Modified vs C++ | 1,852,000 | 24 (1-23, 25; excl. 24 segfault) | 1 subnormal artifact (Sc. 16) |

## What Remains

| Gap | Status |
|-----|--------|
| CC_Mode=2 C++ segfault | Bug found, partial fix applied, needs debugging |
| StC_Mode=2 C++ segfault | Same bug (combined with CC_Mode=2 in Scenario 24) |
| Ext_Mode=1 | Not testable (requires external DLL) |
| ZMQ_Mode=1 | Not testable (requires ZeroMQ server) |

## Commits

- ROSCO `bbccf1a`: Add Scenarios 24-25, fix OL_CableControl/OL_StructControl allocation (partial)
