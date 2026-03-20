# KGen BIND(C) Parser Fix and PIIController Extraction Root Cause
**Date:** 2026-03-20
**Status:** completed
**Scope:** KGen parser bug fix, PIIController extraction failure root cause analysis

## KGen BIND(C) Parser Fix

### The Bug

KGen's Python 2.7 Fortran parser crashes when parsing `TYPE, BIND(C), PUBLIC :: WE` declarations. The crash occurs in `KGen/kgen/parser/utils.py:142`:

```python
def parse_bind(line, item = None):
    if not line.lower().startswith('bind'):
        return None, line
    if item is not None:
        newitem = item.copy(line, apply_map=True)
        newline = newitem.get_line()
    else:
        newitem = None
    newline = newline[4:].lstrip()  # ← CRASH: newline undefined when item is None
```

When `item is None` (which happens for TYPE declarations), `newline` is never assigned in the `else` branch, but line 142 uses it unconditionally. This causes `UnboundLocalError: local variable 'newline' referenced before assignment`.

### The Fix

Add `newline = line` to the `else` branch:

```python
    else:
        newitem = None
        newline = line  # ← FIX
```

### Verification

After the fix, PIController was successfully extracted with Phase 4C ROSCO_Types.f90 (containing `TYPE, BIND(C), PUBLIC` on WE, FilterParameters, rlParams, piParams, resParams, LocalVariables). KGen parsed all types without crashing and produced 20 state files.

### Impact

This fix eliminates the need to reset ROSCO_Types.f90 to upstream for extraction. With the fix in place, extraction can use Phase 4C types directly, which simplifies the source state management for extraction.

## PIIController Extraction: Full Root Cause Analysis

### Investigation Chain

The PIIController extraction failure was investigated through 5 layers:

**Layer 1: KGen BIND(C) crash (RESOLVED)**
- Phase 4C ROSCO_Types has `BIND(C)` on types → KGen crashes
- Workaround: full upstream reset (removes BIND(C))
- Root fix: `utils.py:142` one-line fix (see above)

**Layer 2: Source state incompatibility (RESOLVED BY LAYER 1)**
- Upstream ROSCO_Types has `LOGICAL :: restart`
- Phase 4C has `INTEGER(C_INT) :: restart`
- Upstream source files pass `LocalVar%restart` to LOGICAL params → type mismatch
- With BIND(C) fix, we can use Phase 4C ROSCO_Types + apply restart call-site fixes to upstream sources

**Layer 3: F_FlpCornerFreq validation (RESOLVED)**
- When `Flp_Mode > 0`, ROSCO validates `F_FlpCornerFreq(1) > 0`
- Default DISCON.IN has `F_FlpCornerFreq = 0.0000  1.0000` → validation fails → `aviFAIL = -1`
- With `aviFAIL < 0`, DISCON returns without calling any controllers → FlapControl never entered
- Fix: Scenario 4 patches `F_FlpCornerFreq` to `0.5000  0.7000`

**Layer 4: Uninitialized K in FlapControl init (PARTIALLY RESOLVED)**
- Line 669: `LocalVar%Flp_Angle(K) = PIIController(RootMyb_VelErr(K), ...)`
- `K` is a loop variable never assigned in the init block (no DO loop)
- Using uninitialized `K` for array indexing → potential out-of-bounds
- Fix: wrap in `DO K = 1,LocalVar%NumBl`
- This alone did not resolve the crash

**Layer 5: ROSCO Flp_Mode=2 segfault (UNRESOLVABLE — upstream bug)**
- Even with layers 1-4 fixed, Flp_Mode=2 crashes during `init_discon` (first DISCON call)
- Flp_Mode=1 works correctly (no crash)
- The crash is inside SetParameters or controller initialization — after F_FlpCornerFreq validation passes but before FlapControl is called
- `faulthandler` shows the crash in `call_discon` → DISCON (Fortran) → segfault
- `-fcheck=bounds` reveals: `Array bound mismatch for dimension 1 of array 'localvar' (1024/13)`
- This is an **upstream ROSCO bug** in the Flp_Mode=2 initialization code, not related to VIT or Phase 4C changes
- Confirmed: same crash with full upstream ROSCO (no Phase 4C changes at all)

### Why This Bug Exists in ROSCO

The Flp_Mode=2 code path was likely never tested with ROSCO's Python 1-DOF simulation. ROSCO is designed to run with OpenFAST (full aeroelastic simulation), and Flp_Mode=2 probably works in that context where all avrSWAP values are properly initialized and the Fortran types are fully populated. The 1-DOF sim is a simplified model that doesn't set all the fields the controller expects.

### Conclusion

PIIController **cannot be KGen-extracted** because the ROSCO 1-DOF simulation segfaults with Flp_Mode=2. This is an upstream ROSCO bug, not a VIT or KGen issue.

The sim-diff verification performed in Phase 7A (byte-identical output with 18 integrated functions across 5 scenarios) is the strongest verification available without fixing ROSCO's Flp_Mode=2 code. The translation is structurally identical to the KGen-verified PIController (same piParams access pattern, same inst indexing, same saturate inlining) with only an additional integral term.

### What About Fixing the ROSCO Bug?

Fixing the Flp_Mode=2 segfault would require:
1. Running the 1-DOF sim under GDB (not installed in container)
2. Tracing the exact crash location in SetParameters or initialization
3. Understanding what array/field is causing the bounds mismatch
4. Patching ROSCO's initialization code for Flp_Mode=2

This is outside VIT's scope — it's a ROSCO simulation bug, not a translation verification issue. The correct long-term solution is to run extraction against OpenFAST (full simulation) rather than the 1-DOF sim. That's a larger infrastructure change for a future phase.

## Updated Recommendations

1. **KGen BIND(C) fix is committed** — extraction can now use Phase 4C ROSCO_Types directly
2. **PIIController remains sim-diff verified** — this is the best we can do without fixing ROSCO's Flp_Mode=2 bug
3. **KGen verification is required for all translations where extraction is feasible** — sim-diff is only acceptable when extraction is blocked by upstream bugs
4. **CLAUDE.md extraction instructions updated** — reflect that ROSCO_Types no longer needs upstream reset (BIND(C) fix makes Phase 4C compatible)
5. **TRANSLATION_TIPS.md updated** — Fortran `**` operator precedence lesson added
