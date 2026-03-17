# Type System Deep Investigation: All Types Can Become C Structs

**Date:** 2026-03-17
**Status:** completed
**Scope:** derived types, Phase 4.5 planning

## Background

The previous investigation (dev note 202603171631) established that field extraction doesn't scale and introduced Phase 4.5 for early struct mapping. However, it left ControlParameters as an "opaque pointer + accessor" and ErrorVariables as perpetual passthrough. This investigation pushed on all four problematic types to find complete, elegant solutions.

## Investigation Scope

Three parallel deep dives:
1. **LocalVariables**: Complete field inventory, all BIND(C) blockers, usage of every problematic field.
2. **ControlParameters**: Complete field inventory, all ALLOCATABLE arrays with allocation patterns and sizes, read-only verification.
3. **PerformanceData + ErrorVariables**: Complete field inventories, usage across all modules, C++ interop strategies.

## Findings

### LocalVariables: 4 Blockers, Not 1

The earlier analysis said "only ACC_INFILE blocks BIND(C)." The deep dive found 3 additional blockers:

1. **ACC_INFILE** (`CHARACTER(:), ALLOCATABLE`) — init-only, move to separate parameter
2. **AWC_complexangle(3)** (`COMPLEX(DbKi)`) — split into real + imaginary REAL arrays. Only REAL part is ever extracted.
3. **WriteThisStep** (`LOGICAL`) — change to `INTEGER(C_INT)`. Rarely used.
4. **restart** (`LOGICAL`) — change to `INTEGER(C_INT)`. Heavily used but fix is just a type change.

All 4 are surgical fixes. After them, all 161 fields (including 5 nested BIND(C)-compatible types) form a plain C struct.

### ControlParameters: Can Be a C Struct After All

This overturned the "opaque pointer" strategy. Key findings:

- **Read-only confirmed**: Every access in Controllers, Filters, ControllerBlocks is read-only. `INTENT(INOUT)` is legacy.
- **76 ALLOCATABLE arrays**: All allocated once at init from config file. Sizes from scalar parameters (PC_GS_n, VS_n, etc.), never reallocated.
- **Sizes bounded**: Gain schedules 20-50, notch filters 0-10, performance tables 36×26, open-loop 100-1000.
- **Strategy**: Replace ALLOCATABLE with fixed-size arrays (`double PC_GS_angles[MAX_GS_POINTS]`) with compile-time max bounds. Size scalars remain to indicate valid count.
- **Result**: Plain C struct, ~500 KB, allocated once.

### PerformanceData: Same Fixed-Size Pattern

- 5 ALLOCATABLE fields (TSR_vec, Beta_vec, Cp_mat, Ct_mat, Cq_mat)
- Allocated once from Cp_Ct_Cq file, typical 36×26
- Never modified after init
- Fixed-size arrays with max bounds, same as ControlParameters

### ErrorVariables: Passthrough Is Complete, Not Deferred

- **ErrVar never affects control outputs.** Purely error reporting: set aviFAIL, write ErrMsg, caller checks.
- `aviFAIL` and `ErrStat` already `INTEGER(C_INT)` — directly C-compatible.
- `ErrMsg`: Replace `CHARACTER(:), ALLOCATABLE` with `CHARACTER(1024)`. DLL interface already uses 1024-char buffer.
- For C++ functions needing errors: `int* aviFAIL` + `char* ErrMsg` as separate C args.
- **No future work needed.** This solution is complete for all phases.

## Impact on Plan

Phase 4.5 now solves the **entire** type system:

| Type | Old Strategy | New Strategy |
|------|-------------|-------------|
| 7 state types | C struct (unchanged) | C struct (unchanged) |
| LocalVariables | "Externalize 1 field" | 4 surgical fixes, then C struct |
| ControlParameters | Opaque pointer + accessor | Fixed-size arrays, plain C struct |
| PerformanceData | Pointer+size pairs | Fixed-size arrays, plain C struct |
| ErrorVariables | Perpetual passthrough | Fixed CHARACTER(1024) + C error args when needed |

**No opaque pointers. No accessor APIs. No deferred Phase 8 work for types.** Phase 8 reduces to: make C++ the canonical type owner (move definitions from Fortran to C++ headers).

## Key Insight

The assumption that ALLOCATABLE = "can't be C struct" was wrong for ROSCO. ALLOCATABLE means dynamic allocation, but ROSCO's configuration arrays are allocated once at startup to bounded sizes. Replacing them with fixed-max arrays is a clean solution because:
- Sizes are bounded by turbine physics (you don't need 10,000 gain schedule points)
- The struct is allocated once and passed by pointer (size doesn't matter for performance)
- It eliminates all dynamic allocation complexity from the type system
