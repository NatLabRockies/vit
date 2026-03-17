# Derived Type Investigation: Field Extraction vs. Early Struct Mapping

**Date:** 2026-03-17
**Status:** completed
**Scope:** project planning, type system strategy

## Background

The project plan reflection identified Phase 8 (Type System Translation) as potentially needing to move earlier. The concern: field extraction — passing individual derived type fields as separate C arguments — might not scale to Filters and Controllers, where functions access dozens of fields. This investigation resolved the question definitively and restructured the plan.

## Investigation Approach

Two parallel analyses:

1. **Field access audit**: Read every function in Filters.f90 (~438 lines), Controllers.f90 (~1,228 lines), and ControllerBlocks.f90 (~820 lines) to count exactly how many derived type fields each function accesses and whether they're read or written.

2. **Type compatibility audit**: Read all 12 derived types in ROSCO_Types.f90 (544 lines) to determine which are BIND(C) compatible, which have ALLOCATABLE/CHARACTER blockers, and what the memory layout implications are.

## Key Finding: Field Extraction Does Not Scale

The field access counts are unambiguous:

| Function | Module | Unique Fields Accessed |
|----------|--------|----------------------|
| PitchControl | Controllers | ~70 |
| WindSpeedEstimator | ControllerBlocks | ~60 |
| ComputeVariablesSetpoints | ControllerBlocks | ~50 |
| VariableSpeedControl | Controllers | ~50 |
| PreFilterMeasuredSignals | Filters | ~30 |
| LPFilter | Filters | ~30 |

Passing 30-70 individual scalar arguments through a C interface is not viable. The wrappers would be unmaintainable, the C++ function signatures would be absurd, and every time ROSCO adds a field, everything breaks.

## Key Finding: Most State Types Are Already BIND(C) Compatible

7 of 12 derived types are pure numeric with fixed-size arrays:

| Type | Fields | All Numeric | Fixed-Size | BIND(C) Ready |
|------|--------|-------------|-----------|---------------|
| FilterParameters | 27 | Yes | Yes (1024 each) | Yes |
| piParams | 5 | Yes | Yes (1024 each) | Yes |
| resParams | 4 | Yes | Yes (1024 each) | Yes |
| rlParams | 1 | Yes | Yes (1024) | Yes |
| WE | 7 | Yes | Yes (3×3, 3×1) | Yes |
| ObjectInstances | 9 | Yes (INTEGER) | Yes | Yes |
| DebugVariables | 22 | Yes | Yes | Yes |

These can be passed directly across the Fortran/C boundary as C structs with identical memory layout. Zero-copy, zero-marshalling.

## Key Finding: LocalVariables Is 99% Compatible

LocalVariables has 161 fields: 139 REAL scalars, fixed-size arrays, and 5 nested types (all from the BIND(C)-compatible list above). The **only** blocker is 1 field: `ACC_INFILE`, an `ALLOCATABLE CHARACTER` array. Externalize that field and the entire type becomes a C struct.

This is the single most impactful change in the plan — it unlocks direct C++ access to the complete controller state.

## Key Finding: ControlParameters Cannot Be a C Struct

225 fields, 51 ALLOCATABLE arrays, 4 CHARACTER fields. This type is fundamentally incompatible with C. However, it's read-only configuration — never modified by algorithmic functions. Strategy: pass as opaque pointer with a C-callable accessor API until Phase 8 fully translates it.

## Decision: Add Phase 4.5

The investigation led to adding **Phase 4.5: State Type C Structs** between Phases 4 and 5. This phase:

1. Defines C++ structs for all 7 BIND(C)-compatible types
2. Refactors LocalVariables to externalize ACC_INFILE
3. Creates a C accessor pattern for ControlParameters
4. Adds BIND(C) attributes to Fortran TYPE definitions
5. Validates memory layout compatibility

This is done once, early, and then every subsequent phase (Filters, Controllers, ControllerBlocks) benefits. Without it, each of those phases would've hit the same wall.

## Impact on Plan

- Phase 4 unchanged (ratelimit uses rlParams C struct, AeroDynTorque uses field extraction for its 3 fields as a bridge)
- **New Phase 4.5** inserted — all state type C structs
- Phases 5-7 updated to reference struct-by-pointer pattern instead of field extraction
- Phase 8 reduced to final cleanup: translate ControlParameters, PerformanceData, ErrorVariables fully; make C++ the source of truth for all types

## Memory Layout Notes

- Fortran stores multi-dimensional arrays column-major; C++ structs must use same layout
- Without SEQUENCE attribute, compiler may pad fields — need validation tests
- FilterParameters is 27 × 1024 × 8 bytes = ~221 KB per instance
- LocalVariables total: ~305 KB (dominated by nested FilterParameters)
- These are large structs but manageable since they're passed by pointer, not copied

## Types That Remain Problematic

| Type | Issue | Strategy |
|------|-------|----------|
| ControlParameters | 51 ALLOCATABLE arrays | Opaque pointer + accessor (Phase 4.5), full rewrite (Phase 8) |
| PerformanceData | All ALLOCATABLE | Pass arrays as pointer+size pairs |
| ErrorVariables | ALLOCATABLE CHARACTER | Continue passthrough pattern |
| ExtControlType | ALLOCATABLE array | Keep in Fortran until Phase 9 |
