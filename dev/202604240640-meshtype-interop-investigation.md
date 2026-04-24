# MeshType Interop Investigation — OQ7 Sized and De-Risked
**Date:** 2026-04-24
**Status:** completed
**Scope:** Investigate MeshType interop for AeroDyn C++ translation (OQ7), assess whether NWTC Library translation is needed

## Background

During Phase 0, the C++ Registry generator emits `void*` for all MeshType fields because MeshType is an NWTC Library type (external, not translated). The user flagged this as a potential red flag: AeroDyn physics code needs to actually read mesh data (Position, TranslationVel, Orientation, etc.), and `void*` blocks that access.

This led to a broader question: does the decision to keep NWTC Library in Fortran (KD5) create a tax that will keep biting us? Should we translate NWTC Library — or at least the mesh infrastructure — to eliminate the interop overhead?

## Key Finding: The Mesh Problem Is Smaller Than It Looks

### AeroDyn doesn't call mesh operations — it reads mesh data

AeroDyn's 25 unique mesh-related external callees (MeshCreate, MeshCopy, MeshDestroy, MeshMapCreate, etc.) are called from:
- Registry-generated `_Types.f90` boilerplate (Pack/Unpack/Copy/Destroy) — stays Fortran
- Init/setup routines in AeroDyn.f90 and driver code (Phase 6-7)
- The OpenFAST framework (not our code)

The physics code only **reads mesh data fields** — `u%BladeMotion(k)%Position(:,j)`, `u%HubMotion%TranslationVel(:,1)`, etc. It never calls MeshCreate or MeshCopy.

### Mesh field access is concentrated in late phases

| Phase | Mesh field dereferences | Files involved |
|-------|------------------------|----------------|
| Phase 1 (AirfoilInfo + DBEMT) | **0** | — |
| Phase 2 (BEMTUncoupled + UnsteadyAero) | **0** | — |
| Phase 3 (BEMT) | **0** | — |
| Phase 4 (FVW/OLAF) | **11** | FVW_Wings.f90, FVW_Subs.f90 |
| Phase 5 (AeroAcoustics) | **0** | — |
| Phase 6 (Core AeroDyn) | **271** | AeroDyn.f90, AeroDyn_IO.f90, AeroDyn_AllBldNdOuts_IO.f90 |
| Phase 7 (Drivers/C-binding) | **107** | AeroDyn_Driver_Subs.f90, AeroDyn_Inflow_C_Binding.f90 |

**Phases 1-3 and 5 have zero mesh dereferences.** The `void*` representation is correct and harmless for these phases. The mesh interop problem first surfaces in Phase 4 (11 accesses in FVW) and becomes heavy in Phase 6 (271 accesses).

### What physics code actually accesses

All 389 mesh field dereferences across AeroDyn access these fields:

| Field | Fortran type | C++ equivalent | Dimensions |
|-------|-------------|----------------|------------|
| `Position` | `REAL(ReKi), POINTER` | `float*` | (3, NNodes) |
| `RefOrientation` | `REAL(R8Ki), POINTER` | `double*` | (3, 3, NNodes) |
| `Orientation` | `REAL(R8Ki), ALLOCATABLE` | `double*` | (3, 3, NNodes) |
| `TranslationDisp` | `REAL(R8Ki), ALLOCATABLE` | `double*` | (3, NNodes) |
| `TranslationVel` | `REAL(ReKi), ALLOCATABLE` | `float*` | (3, NNodes) |
| `RotationVel` | `REAL(ReKi), ALLOCATABLE` | `float*` | (3, NNodes) |
| `TranslationAcc` | `REAL(ReKi), ALLOCATABLE` | `float*` | (3, NNodes) |
| `RotationAcc` | `REAL(ReKi), ALLOCATABLE` | `float*` | (3, NNodes) |
| `Force` | `REAL(ReKi), ALLOCATABLE` | `float*` | (3, NNodes) |
| `Moment` | `REAL(ReKi), ALLOCATABLE` | `float*` | (3, NNodes) |

These are all flat 2D or 3D arrays of floats/doubles. The physics code indexes them as `field(:, iNode)` or `field(:, :, iNode)`.

## NWTC Library Translation: Not Needed

The original concern was that KD5 (NWTC Library stays Fortran) would create a persistent interop tax. Investigation shows this is overblown:

### Could NWTC Library be translated without touching other modules?

Yes. The approach: translate NWTC Library internals to C++, keep a thin Fortran wrapper module (`MODULE NWTC_Library`) that re-exports the C++ API via BIND(C). Every other Fortran module still does `USE NWTC_Library` unchanged. This is sound in principle.

### But it's unnecessary for the mesh problem

NWTC Library is 37 files, ~46,000 hand-written lines. The mesh infrastructure alone is:
- `ModMesh_Types.f90` — 241 lines (type definitions)
- `ModMesh.f90` — 3,424 lines, ~35 procedures (mesh lifecycle operations)
- `ModMesh_Mapping.f90` — 5,754 lines, ~44 procedures (spatial mapping algorithms)

These depend on:
- `ModReg.f90` — 6,095 lines (binary serialization registry)
- `NWTC_Num.f90` — 7,421 lines (math/precision)
- `NWTC_LAPACK.f90` — 2,017 lines (linear algebra wrappers)

Translating the mesh infrastructure properly would pull in ~25,000 lines of NWTC Library dependencies. This is disproportionate to the problem.

### The real solution: MeshType view struct (~80 lines total)

Since AeroDyn physics code only reads mesh data arrays (it never calls mesh lifecycle operations), the solution is a view struct — the same pattern VIT used for ROSCO's ALLOCATABLE types:

**C++ view struct (~50 lines):**
```cpp
typedef struct MeshType_View {
    int32_t Nnodes;
    int32_t fieldmask[9];
    
    // Data field pointers (filled by Fortran populator)
    float*  Position;          // (3, NNodes), column-major
    double* RefOrientation;    // (3, 3, NNodes)
    float*  Force;             // (3, NNodes)
    float*  Moment;            // (3, NNodes)
    double* Orientation;       // (3, 3, NNodes)
    double* TranslationDisp;   // (3, NNodes)
    float*  TranslationVel;    // (3, NNodes)
    float*  RotationVel;       // (3, NNodes)
    float*  TranslationAcc;    // (3, NNodes)
    float*  RotationAcc;       // (3, NNodes)
    float*  Scalars;           // (nScalars, NNodes)
    int32_t nScalars;
} MeshType_View;
```

**Fortran BIND(C) populator (~30 lines):**
```fortran
SUBROUTINE populate_mesh_view(mesh, view) BIND(C)
    TYPE(MeshType), INTENT(IN), TARGET :: mesh
    TYPE(MeshType_View), INTENT(OUT) :: view
    view%Nnodes = mesh%Nnodes
    view%fieldmask = TRANSFER(mesh%fieldmask, view%fieldmask)
    IF (ASSOCIATED(mesh%Position)) view%Position = C_LOC(mesh%Position(1,1))
    IF (ALLOCATED(mesh%Force)) view%Force = C_LOC(mesh%Force(1,1))
    ! ... etc for each field
END SUBROUTINE
```

This avoids layout-matching between the C++ struct and the Fortran MeshType (which has POINTER fields, element tables, and sibling mesh pointers between the metadata and data fields). The populator extracts just the data pointers the physics code needs.

The integration wrapper calls the populator before invoking the C++ physics function, same as VIT's existing view struct pattern.

## Decision

- **KD5 stands** — NWTC Library stays Fortran. The interop cost is bounded and manageable.
- **MeshType interop is a view struct problem, not a translation problem.** ~80 lines of code, needed starting Phase 4.
- **Phases 1-3 and 5 are unaffected.** The `void*` in generated Registry headers is correct for these phases.
- **The MeshType view struct should be implemented before Phase 4.** It can be done as part of Phase 4 prep or as a standalone task after Phase 3.
- **The 106 non-mesh NWTC utility calls** (SetErrStat, AllocAry, splines, string ops, etc.) remain as thin BIND(C) wrappers — mechanical and cheap, built incrementally as each phase encounters them.

## Scope Impact

**No change to the phase roadmap.** The mesh interop was already tracked as OQ7 with "before Phase 1" timing. This investigation narrows the answer: it's not needed until Phase 4, and it's ~80 lines of view struct code, not a library translation.

**OQ7 status: answered.** MeshType interop uses a view struct populator pattern, ~80 lines. Not needed until Phase 4.

## Related

- `PROJECT_PLAN_AERODYN.md` — OQ7 (to be updated)
- Dev note `202604240609` — Phase 0 implementation (generated void* for MeshType)
- Dev note `202604240622` — Phase 0 retrospective (flagged MeshType as a risk)
- `ModMesh_Types.f90` — MeshType definition (241 lines)
- `ModMesh.f90` — mesh operations (3,424 lines, not needed for translation)
