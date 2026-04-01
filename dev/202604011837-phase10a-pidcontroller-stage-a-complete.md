# Phase 10A Complete: Stage A — ReadAvrSWAP + PIDController

**Date:** 2026-04-01
**Status:** completed
**Scope:** Extract, translate, verify, and integrate ReadAvrSWAP (#40) and PIDController (#41), completing Phase 10 Stage A

## Summary

Stage A of Phase 10 added two KGen-verified functions to the ROSCO C++ translation, bringing the total from 39 to 41. Both use the standard VIT pipeline (extract → translate → verify → integrate). The project now verifies 692,000 values across 10 scenarios (up from 672,000 across 9).

## ReadAvrSWAP (#40)

Pure array unpacker — 110 lines of `LocalVar%field = avrSWAP(N)` assignments from the Bladed interface swap array. First function from the ReadSetParameters.f90 module.

- **Extraction:** 21 cases, Scenario 3
- **Kernel verification:** 21/21 IDENTICAL
- **Translation notes:** Fortran 1-based → C++ 0-based indexing, float→double promotion (avrSWAP is REAL(4)), R2D/D2R constants, space-padded error message for Fortran CHARACTER compatibility
- **KGen parser issue:** ReadSetParameters.f90 USEs ROSCO_Helpers, which contains backslash characters in PathIsRelative/GetPath that crash KGen's Python 2.7 parser. Fixed by temporarily patching the backslashes before extraction. Codified in `extract_all.sh`.

## PIDController (#41)

PID controller with derivative filtering — 57 lines, structurally similar to PIController (Phase 7A) with added derivative term. Calls LPFilter (to filter error before differentiation) and saturate.

- **Extraction:** 22 cases, Scenario 10 (new — OL_Mode=2 azimuth tracking)
- **Kernel verification:** 22/22 IDENTICAL
- **Translation notes:** Inline `extern "C"` callee declarations (not `#include "vit_translated.h"` — see TRANSLATION_TIPS.md). Uses objInst->instPI for PI state indexing and objInst->instLPF for filter instance.
- **Scenario 10 setup:** Added `run_scenario_10()` to vit_sim.py with OL_Mode=2, RP_Gains=[1000, 100, 500, 0.1], and a custom OL input file (`Examples/example_inputs/OL_Mode2_Input.dat`) with azimuth column (linear ramp at 1 rad/s).

## VariableSpeedControl OL_Mode=2 Code Path

The VariableSpeedControl C++ translation (Phase 7B) had left the OL_Mode=2 code path as a stub — PIDController and UNWRAP weren't translated at the time. With PIDController now available, the stub was replaced with the full implementation:

- **UNWRAP:** Implemented inline as a 2-element unwrap (while loops adjusting by ±2π). The Fortran UNWRAP function takes a variable-length array, but VariableSpeedControl only uses a 2-element buffer. The inline version is exact-equivalent for this case.
- **Azimuth tracking:** interp1d lookup of desired azimuth from OL file → error computation → PIDController → torque correction added to base GenTq.

Initial simulation comparison showed Scenario 10 mismatches (integrated output had constant torque — PIDController wasn't modifying it). Root cause: VariableSpeedControl's OL_Mode=2 stub was skipping the computation. After completing the code path, all 10 scenarios match.

## Translation Pattern Discovery: Callee Declarations

PIDController's kernel build initially failed because the translation included `#include "vit_translated.h"`, which doesn't exist in the kernel directory. Investigation revealed that ALL existing translations with callees use inline `extern "C"` declarations instead — making each translation self-contained and compilable in both kernel and integration contexts.

This pattern was not previously documented. Added to TRANSLATION_TIPS.md:
> "Declare callee signatures inline — do not `#include "vit_translated.h"`"

## Script Updates

All four batch scripts updated: 39→41 functions, 9→10 scenarios.

| Script | Key changes |
|--------|-------------|
| `reset_to_clean.sh` | Resets ReadSetParameters.f90 to HEAD. Creates stubs for .cpp files listed in CMakeLists.txt (not just existing files). |
| `extract_all.sh` | Adds PIDController (Scenario 10) and ReadAvrSWAP (Scenario 3 + ROSCO_Helpers backslash patch). |
| `verify_all.sh` | Adds ReadAvrSWAP (Batch 1) and PIDController (Batch 2). |
| `integrate_all.sh` | Adds PIDController (Controllers section) and ReadAvrSWAP (ReadSetParameters section). |

## Documentation Updates

- **CLAUDE.md:** 41 functions, 10 scenarios, 692,000 values. Added extraction parameters, golden fixture list entries, C++ translations table rows. Updated reset_to_clean.sh docs. Added notes on ReadAvrSWAP backslash patch and Scenario 10.
- **PROJECT_PLAN.md:** Stage A checkboxes marked [x]. Current state updated to 41/41 functions. Phase 10 verification counts updated.
- **TRANSLATION_TIPS.md:** Added callee declaration pattern (inline extern "C", not vit_translated.h include).

## Verification

- KGen: ReadAvrSWAP 21/21, PIDController 22/22
- Simulation: ALL SCENARIOS IDENTICAL — 692,000 values across 10 scenarios
- Integration: 41/41 passed

## Related Dev Notes

- `202604011740-phase10a-readavrswap.md` — ReadAvrSWAP details and KGen backslash issue
