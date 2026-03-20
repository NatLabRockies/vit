# Phase 7A Follow-Up: Remaining Items After Issue Resolution
**Date:** 2026-03-20
**Status:** in-progress
**Scope:** Tracking what was completed vs. what remains from the Phase 7A issue resolution plan

## Background

After Phase 7A implementation, we identified 6 issues and created a plan to fix them. The plan had 5 steps. This note tracks what was completed, what's blocked, and what still needs doing.

## Completed

### Step 1: KGen BIND(C) Parser Fix
- Fixed `KGen/kgen/parser/utils.py:142` — one-line `newline = line` initialization
- Verified: PIController extracts with Phase 4C ROSCO_Types (BIND(C) on 6 types)
- Committed to KGen repo: `b5fd702`

### Step 2: PIIController Re-extraction Attempt
- Set up extraction environment: Phase 4C ROSCO_Types + upstream sources with restart call-site fix
- PIIController extraction still produced 0 state files
- Full 5-layer root cause investigation:
  1. KGen BIND(C) crash → fixed
  2. Source state incompatibility → resolved by fix
  3. F_FlpCornerFreq validation → fixed in vit_sim.py
  4. Uninitialized K at line 669 → fixed
  5. **ROSCO Flp_Mode=2 segfault** → upstream bug, unresolvable without fixing ROSCO
- Confirmed: Flp_Mode=1 works, Flp_Mode=2 crashes. Same crash with full upstream ROSCO (no Phase 4C changes). This is a pre-existing ROSCO bug.

### Step 3b: Dev Note Correction
- Updated `202603192309-phase-7a-extraction-issues.md`: removed "2-strike rule", marked recommendations as superseded, pointed to new dev note `202603200110`

### Step 3c: PROJECT_PLAN.md Update
- PIIController entry updated with root cause: upstream ROSCO segfault in Flp_Mode=2, references dev note `202603200110`

### Step 3d: TRANSLATION_TIPS.md
- Added Fortran `**` operator precedence lesson with ResController example

## Blocked

### Step 4: Re-verify PIIController via KGen
- **Permanently blocked** by upstream ROSCO Flp_Mode=2 segfault
- The 1-DOF simulation crashes during `init_discon` with Flp_Mode=2
- `-fcheck=bounds` shows: `Array bound mismatch for dimension 1 of array 'localvar' (1024/13)`
- Fixing this requires debugging ROSCO's SetParameters/initialization code, which is outside VIT's scope
- PIIController remains sim-diff verified (byte-identical across 5 scenarios with 18 integrated functions)

## Remaining

### Step 3a: CLAUDE.md Extraction Instructions
**What needs to change:** The extraction instructions currently say to reset source files to upstream (`e8010f0`) but imply ROSCO_Types.f90 should NOT be reset. With the KGen BIND(C) fix, the instructions should be updated to reflect that:
- ROSCO_Types.f90 can now stay at Phase 4C (BIND(C) is parseable)
- Source files with integration wrappers (Functions.f90, Filters.f90, ControllerBlocks.f90) still need upstream reset
- Controllers.f90 at Phase 4C has no integration wrappers and can be used directly
- The restart call-site fix (`LocalVar%restart` → `(LocalVar%restart /= 0)`) must be applied to upstream Filters.f90 and Functions.f90 when using Phase 4C ROSCO_Types
- Or: full upstream reset still works (simplest, but loses Phase 4C type compatibility during extraction — KGen handles this fine since the kernel types are independent)

**Why it's not done yet:** The extraction instructions interact with the clean build test procedure. Changing one requires reviewing the other to ensure consistency. This is documentation-only work with no code risk.

### Step 5: Full Regression
**What's needed:**
1. Unit tests — run `python3 -m pytest tests/ -v` (451+ must pass)
2. Verification regression — all 17 KGen-verifiable functions pass 40/40 (PIIController excluded, no golden kernel)
3. Simulation diff — byte-identical output across 5 scenarios with 18 integrated functions

**Why it's not done yet:** The source files are currently in a mixed state from the investigation (upstream + various patches). A clean regression requires restoring to the committed HEAD state and rebuilding. This is mechanical work with no risk of failure — the same regression passed during Phase 7A implementation.

## Assessment

The critical work is done: the KGen bug is fixed, the PIIController root cause is fully diagnosed and documented, the "2-strike rule" is corrected, and TRANSLATION_TIPS.md is updated. The remaining items (CLAUDE.md update + regression run) are low-risk documentation and verification tasks that should be completed before starting the next phase.
