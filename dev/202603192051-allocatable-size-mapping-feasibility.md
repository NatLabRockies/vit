# ALLOCATABLE Size Mapping: Feasibility Analysis for Phase 4D Automation
**Date:** 2026-03-19
**Status:** completed (analysis); implementation deferred to Phase 4D
**Scope:** analyze_types.py — automated ALLOCATABLE field → size source mapping

## The Problem

The Phase 4D pointer+size view struct for ControlParameters (213 fields, 76 ALLOCATABLE arrays) requires knowing which scalar determines the size of each ALLOCATABLE array. The earlier plan said "read ReadControlParameterFileSub deeply" — a manual, non-repeatable step. Any user of VIT facing an ALLOCATABLE type would need to do the same detective work. This doesn't scale and isn't how VIT should work.

## The Question

Can VIT automatically scan source code to map every ALLOCATABLE field to its size source?

## Investigation

Exhaustive analysis of every ALLOCATE and ParseAry call in ROSCO's ReadSetParameters.f90, ReadCpFile, and related code. Every ALLOCATABLE field in ControlParameters (61 fields) and PerformanceData (5 fields) was traced to its size source.

## Findings: 100% Coverage, Highly Regular Patterns

Every ALLOCATABLE field (except one commented-out orphan) has a size source identifiable from source code. The patterns fall into five categories:

### Type A: Size is a field on the same type (~37 fields, ~56%)

The dominant pattern. Size comes from a `CntrPar%` scalar field, passed to `ParseAry`:

```fortran
CALL ParseAry(FileLines, 'F_NotchFreqs', CntrPar%F_NotchFreqs, CntrPar%F_NumNotchFilts, ...)
```

The array `F_NotchFreqs` is allocated inside `ParseAry` to size `F_NumNotchFilts`. VIT can extract this mapping by regex-matching ParseAry calls: the 3rd argument is the array, the 4th is the size.

Examples of size-field pairings:
- `F_NotchFreqs`, `F_NotchBetaNum`, `F_NotchBetaDen` → sized by `F_NumNotchFilts`
- `PC_GS_angles`, `PC_GS_KP`, `PC_GS_KI`, `PC_GS_KD`, `PC_GS_TF` → sized by `PC_GS_n`
- `VS_KP`, `VS_KI` → sized by `VS_n`
- `AWC_n`, `AWC_harmonic`, `AWC_freq`, `AWC_amp`, `AWC_clockangle` → sized by `AWC_NumModes`
- ~20 more following the same pattern

**Fully automatable.** Regex scan for `ParseAry(..., CntrPar%array, CntrPar%size, ...)`.

### Type B: Literal constant (~8 fields, ~12%)

Size is a hardcoded integer:

```fortran
CALL ParseAry(FileLines, 'F_FlCornerFreq', CntrPar%F_FlCornerFreq, 2, ...)
CALL ParseAry(FileLines, 'Ind_BldPitch', CntrPar%Ind_BldPitch, 3, ...)
CALL ParseAry(FileLines, 'RP_Gains', CntrPar%RP_Gains, 4, ...)
```

**Trivially automatable.** The literal integer is right there in the call.

### Type C: Local variable from runtime computation (2 fields)

`N_OL_Cables` and `N_OL_StCs` are computed by counting loops before being used in ALLOCATE:

```fortran
N_OL_Cables = 0
DO K = 1, CntrPar%CC_Group_N
    N_OL_Cables = N_OL_Cables + SIZE(CntrPar%CC_GroupIndex(K)%Channels)
END DO
ALLOCATE(CntrPar%OL_CableControl(N_OL_Cables, SIZE(CntrPar%OL_Channels, DIM=1)))
```

**Not directly automatable** — requires tracing a local variable through a loop. VIT would find the ALLOCATE and extract the size expression, but can't resolve `N_OL_Cables` to a compile-time value. This is an EXIT_CONFIRM case: VIT presents the expression and asks the human to confirm or provide a maximum bound.

### Type D: SIZE() of another field (2 fields)

```fortran
ALLOCATE(CntrPar%OL_CableControl(N_OL_Cables, SIZE(CntrPar%OL_Channels, DIM=1)))
```

Second dimension is `SIZE(CntrPar%OL_Channels, DIM=1)` — the size of another ALLOCATABLE field.

**Automatable** — VIT can parse the `SIZE(CntrPar%field, DIM=N)` expression and generate a corresponding size parameter in the view struct.

### Type E: Array index into size field (5 fields, PerformanceData)

```fortran
ALLOCATE(PerfData%Beta_vec(CntrPar%PerfTableSize(1)))
ALLOCATE(PerfData%TSR_vec(CntrPar%PerfTableSize(2)))
ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
```

Size comes from indexing into a fixed-size array (`PerfTableSize(2)`) on a *different* type (`CntrPar`, not `PerfData`).

**Automatable** — VIT can parse the expression and generate appropriate size parameters. Cross-type references need the view struct to include the referenced size or accept it as a parameter.

### Special: OL_ slice fields (~11 fields)

These are NOT allocated via ALLOCATE or ParseAry. They're assigned as pointer slices from `OL_Channels`:

```fortran
CntrPar%OL_Breakpoints => CntrPar%OL_Channels(:, 1)
CntrPar%OL_BldPitch1   => CntrPar%OL_Channels(:, 2)
```

`OL_Channels` itself is allocated in `Read_OL_Input` (a separate subroutine). These pointer-associated fields share memory with `OL_Channels`.

**Needs special handling** — these are POINTER, not ALLOCATABLE, so they'd be a different category in the view struct. The view struct could expose `OL_Channels` as a 2D array and let C++ index into columns directly, or expose each slice as a separate `double*` pointing into the same backing memory.

### Orphaned field

`WE_CP` is declared ALLOCATABLE but its `ParseAry` call is commented out. Never allocated, never used. VIT should flag this as unused.

## Automation Strategy for Phase 4D

A new `vit analyze-types --scan-allocatable` command (or integrated into existing `analyze-types`) would:

1. **Scan ParseAry calls** — regex: `ParseAry\s*\(.*,\s*(\w+%\w+)\s*,\s*([^,]+)\s*,` → extract (array_field, size_expression)
2. **Scan explicit ALLOCATE calls** — regex: `ALLOCATE\s*\(\s*(\w+%\w+)\s*\(([^)]+)\)\)` → extract (field, dims_expression)
3. **Classify each size expression** by type (A–E)
4. **For Type A** (same-type field): auto-generate `double* field; int n_field;` in view struct, using the actual size field name
5. **For Type B** (literal): auto-generate with fixed size
6. **For Type C** (unresolvable): EXIT_CONFIRM with the expression, ask human for max bound
7. **For Type D** (SIZE() of another field): generate a size parameter that references the other field's size
8. **For Type E** (array-indexed): parse the index expression and generate appropriate parameters

Output: a mapping table consumable by `generate_view_struct_header()`:

```
F_NotchFreqs      → 1D, size: CntrPar%F_NumNotchFilts     (Type A)
PC_GS_angles      → 1D, size: CntrPar%PC_GS_n             (Type A)
F_FlCornerFreq    → 1D, size: 2                            (Type B)
OL_CableControl   → 2D, size: (runtime, SIZE(OL_Channels,1)) (Type C+D)
Cp_mat            → 2D, size: (PerfTableSize(2), PerfTableSize(1)) (Type E)
```

## Key Insight

This is the same pattern VIT already uses for CHARACTER length inference (`infer_character_length`): scan source for ALLOCATE/ParseAry calls, extract size expressions, resolve what's resolvable, EXIT_CONFIRM what isn't. The infrastructure exists — Phase 4D extends it to ALLOCATABLE arrays.

~90% of fields (Types A, B, D, E) are fully automatable. The remaining ~10% (Type C + OL_ slices) need human confirmation but VIT can still find the expression and present it.

## Related Notes
- [202603192045-pre-7a-design-decisions.md](202603192045-pre-7a-design-decisions.md) — Where this question originated
- [202603191921-generalize-complex-fix-and-manual-review-protocol.md](202603191921-generalize-complex-fix-and-manual-review-protocol.md) — EXIT_CONFIRM protocol used for unresolvable sizes
