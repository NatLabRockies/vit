# Verdict: ROSCO Source Changes — Genuine Defects vs. Our Responsibility
**Date:** 2026-03-20
**Status:** completed
**Scope:** Rigorous assessment of three ROSCO source modifications made during Phase 7A

## Context

During Phase 7A, we modified three ROSCO source files to make Flp_Mode=2 work in the 1-DOF simulation for PIIController extraction. We initially called all three "upstream ROSCO bugs." This note re-examines each change with primary-source evidence to determine what is genuinely ROSCO's defect and what is our responsibility.

ROSCO is production code from NREL. We should not claim defects in it without airtight evidence.

## Change 1: Uninitialized K in FlapControl Init Block

**File:** `Controllers.f90:669` (upstream at `e8010f0`)
**Our change:** Wrapped `PIIController(RootMyb_VelErr(K), ...)` in `DO K = 1,LocalVar%NumBl`

### Evidence

The upstream code in FlapControl's initialization block (iStatus==0, Flp_Mode==2):

```fortran
! Internal Variables
INTEGER(IntKi)              :: K       ← local, no SAVE, no DATA, no default

IF (CntrPar%Flp_Mode > 0) THEN
    IF (LocalVar%iStatus == 0) THEN
        LocalVar%Flp_Angle(1) = CntrPar%Flp_Angle   ← hardcoded indices 1,2,3
        LocalVar%Flp_Angle(2) = CntrPar%Flp_Angle
        LocalVar%Flp_Angle(3) = CntrPar%Flp_Angle
        IF (CntrPar%Flp_Mode == 2) THEN
            LocalVar%Flp_Angle(K) = PIIController(RootMyb_VelErr(K), ...)  ← K undefined
        ENDIF
    ELSEIF (CntrPar%Flp_Mode == 2) THEN
        DO K = 1,LocalVar%NumBl                                             ← K properly looped
            LocalVar%Flp_Angle(K) = PIIController(...)
        END DO
```

- `K` is declared as a local `INTEGER(IntKi)` — no outer scope, no SAVE attribute, no default initialization
- Per Fortran standard, local variables without explicit initialization are **undefined**
- The steady-state block (ELSEIF) uses `DO K = 1,LocalVar%NumBl` — establishing the intended pattern
- The init block does NOT — using bare `K` as an array index
- Both blocks were added in the same commit: `b994e4d` (ROSCO v2.9.0, Jan 2024, author: dzalkind)
- PIIController uses `inst` (incremented each call) to index piP%ITerm, piP%ITerm2, etc. Calling it once initializes 1 instance; the steady-state calls it 3 times per timestep (one per blade). Without the loop, 2 of 3 blade instances start with uninitialized PI state.

### Verdict

**Genuine ROSCO defect.** The missing DO loop is not a valid coding pattern — K is undefined per the Fortran standard, the steady-state block shows the correct pattern, and the PI instance counter requires one init call per blade. This is a missing loop, not an intentional single-blade initialization.

---

## Change 2: Uninitialized n in Flp_Mode==2 Notch Filter

**File:** `Filters.f90:412` (upstream at `e8010f0`)
**Our change:** Wrapped NotchFilter call in `DO n = 1,CntrPar%F_GenSpdNotch_N`

### Evidence

The upstream code in PreFilterMeasuredSignals:

```fortran
INTEGER(IntKi) :: n   ← local, declared at line 336

! Gen-speed notch loop (line 355):
DO n = 1,CntrPar%F_GenSpdNotch_N        ← sets n IF F_GenSpdNotch_N > 0
    LocalVar%GenSpeedF = NotchFilter(..., CntrPar%F_GenSpdNotch_Ind(n), ...)
END DO

! ... ~50 lines later, in blade root filtering ...

ELSEIF ( CntrPar%Flp_Mode == 2 ) THEN
    LocalVar%RootMOOPF(K) = SecLPFilter(...)
    LocalVar%RootMOOPF(K) = HPFilter(...)
    ! Use same as generator speed signal because that's how it was before
    LocalVar%RootMOOPF(K) = NotchFilter(..., CntrPar%F_GenSpdNotch_Ind(n), ...)  ← n undefined when F_GenSpdNotch_N=0
```

- `n` is a local variable (line 336), no SAVE, no default
- The gen-speed notch loop at line 355 sets `n` only if `F_GenSpdNotch_N > 0`
- When `F_GenSpdNotch_N = 0`, the loop body never executes, `n` is never assigned
- The Flp_Mode==2 branch uses `n` unconditionally as an array index
- The comment "Use same as generator speed signal because that's how it was before" confirms this was copied from the gen-speed notch loop — but without the enclosing `DO n` iteration
- **BAR_10 is the only real turbine config with Flp_Mode=2, and it has `F_GenSpdNotch_N=0`** — the exact combination that makes `n` undefined
- No validation in ReadSetParameters links `F_GenSpdNotch_N` to `Flp_Mode`
- The IPC/Flp_Mode==3 branch does NOT use `n` — it uses `NotchFilterSlopes` with different parameters
- Both the gen-speed loop and the Flp_Mode==2 branch were added in the same commit: `b994e4d` (ROSCO v2.9.0)

### Verdict

**Genuine ROSCO defect.** Copy-paste from the gen-speed notch loop without the enclosing `DO n` iteration. The only real-world Flp_Mode=2 configuration (BAR_10) has `F_GenSpdNotch_N=0`, guaranteeing `n` is undefined. Our fix (`DO n = 1,F_GenSpdNotch_N`) correctly skips the notch when no notch filters are configured (N=0, loop body never executes) and applies all configured notches when N>0.

---

## Change 3: ACC_INFILE Character Assignment

**File:** `ReadSetParameters.f90:216`
**Our change:** Replaced `LocalVar%ACC_INFILE = accINFILE` with element-wise copy loop

### Evidence

The upstream ROSCO_Types.f90 at `e8010f0`:
```fortran
CHARACTER, DIMENSION(:), ALLOCATABLE     :: ACC_INFILE
```

Our Phase 4A changed it to:
```fortran
CHARACTER(1024) :: ACC_INFILE
```

The original assignment `LocalVar%ACC_INFILE = accINFILE` worked with ALLOCATABLE (Fortran allocates to the right size). After our change to fixed CHARACTER(1024), the assignment from a C_CHAR array of size 13 is a type/shape mismatch that gfortran's bounds checker catches and that causes a segfault in production builds.

### Verdict

**Our responsibility, not a ROSCO defect.** The original ALLOCATABLE code worked correctly. Phase 4A's type conversion broke it. Our element-wise copy loop restores the functionality we broke. This should NOT be documented as an ROSCO bug.

---

## Summary

| Change | Verdict | Introduced | Evidence |
|--------|---------|-----------|----------|
| Uninitialized K (Controllers.f90:669) | **ROSCO defect** | `b994e4d` (v2.9.0, Jan 2024) | Undefined local variable per Fortran standard; steady-state block shows correct pattern; PI instance counter requires per-blade init |
| Uninitialized n (Filters.f90:412) | **ROSCO defect** | `b994e4d` (v2.9.0, Jan 2024) | Copy-paste from gen-speed notch loop without DO n; only real Flp_Mode=2 config has F_GenSpdNotch_N=0; comment confirms copy-paste intent |
| ACC_INFILE assignment (ReadSetParameters.f90:216) | **Our responsibility** | Phase 4A (VIT) | Original ALLOCATABLE worked; our CHARACTER(1024) conversion broke it |

### Why upstream CI didn't catch the ROSCO defects

- ROSCO's CI runs compile-only (pip) or OpenFAST-based testing (conda). Neither exercises Flp_Mode=2 in isolation.
- No test file mentions `Flp`, `flap`, or `BAR`. The BAR_10 DISCON.IN with Flp_Mode=2 exists in the repo but is never executed by any test.
- Compile-time checking does not detect uninitialized variables. gfortran with `-Wall` warns about some cases but not all, and the warnings may have been ignored in the large v2.9.0 release.
- The 1-DOF Python sim (which we use) was never run with Flp_Mode=2 by ROSCO's developers. Their testing path is OpenFAST, where the crash may not manifest due to different memory initialization patterns or because the OpenFAST test suite doesn't enable Flp_Mode=2.
