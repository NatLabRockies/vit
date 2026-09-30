# ROSCO-Specific Code Audit and Extraction Protocol Simplification
**Date:** 2026-03-24
**Status:** completed
**Scope:** VIT code quality, vit.yaml configuration design, extraction protocol

## Motivation

After completing the Phase 7B extraction campaign (all 7 functions extracting with 20 cases each), a review flagged two concerns:

1. **ROSCO-specific code in VIT** — The DLL SAVE persistence workaround in `extract.py` hardcoded ROSCO variable names (`LocalVar%AlreadyInitialized`, `CALL ReadAvrSWAP`, `NINT(avrSWAP(1))`) directly in VIT's Python source. VIT is meant to be a general tool; project-specific details belong in configuration.

2. **Unvalidated ROSCO bug fix** — The Filters.f90 notch filter fix (`DO n = 1,F_GenSpdNotch_N`) was being applied as a manual sed during extraction prep every session, never properly committed or validated.

## Audit Results

### ROSCO-Specific Code Found in VIT

**Location:** `vit/vit/extract.py`, `generate_state_capture_build_script()`, lines 408-433

Three ROSCO-specific elements in the build wrapper's Step 2c:

| Element | What it is | Why it's ROSCO-specific |
|---------|------------|------------------------|
| `CALL ReadAvrSWAP` | Sed anchor for AlreadyInitialized reset | Only DISCON.F90 calls this subroutine |
| `LocalVar%AlreadyInitialized = 0` | DLL load guard reset | ROSCO's specific guard variable name |
| `NINT(avrSWAP(1)) == 0` | Init call detection condition | ROSCO's Bladed DLL interface convention for iStatus |

**NOT ROSCO-specific** (correctly general):
- `IF (.NOT. ALLOCATED(kgen_isstop))` — KGen-generated, exists in all instrumented files
- `DEALLOCATE(kgen_isstop, kgen_issave, ...)` — KGen-generated variable names
- The assumed-size array config (`assumed_size_arrays` in vit.yaml) — already configurable

### Filters.f90 Bug Validation

**Conclusion: Genuine bug.** Rigorous trace through PreFilterMeasuredSignals confirmed:

- `n` is declared as `INTEGER(IntKi)` with no default initialization
- Two prior DO loops assign `n`: `DO n = 1,F_GenSpdNotch_N` and `DO n = 1,F_TwrTopNotch_N`
- After each loop, `n = N+1` (Fortran semantics); if N=0, the loop never executes and `n` is never assigned
- The `Flp_Mode == 2` branch uses `n` to index `F_NotchFreqs(F_GenSpdNotch_Ind(n))` — undefined when `F_GenSpdNotch_N=0`
- DISCON_flp.IN (the Flp_Mode=2 test config) has `F_GenSpdNotch_N = 0`
- The DO loop fix is correct: with N=0, the loop is a no-op (safe); with N>0, it applies the right notch filters

The bug is already documented in `ROSCO/ROSCO_BUG_REPORT.md` (Bug 1) and fixed at commit `dba7738`.

## Changes Made

### 1. Moved DLL Persistence to vit.yaml Configuration

**New config section:**
```yaml
kgen:
  dll_persistence:
    init_condition: "NINT(avrSWAP(1)) == 0"
    resets:
      - anchor: "CALL ReadAvrSWAP"
        code: "LocalVar%AlreadyInitialized = 0"
```

**VIT implementation** (`config.py`, `extract.py`, `cli.py`):
- `VitConfig.dll_persistence: dict` — parsed from `kgen.dll_persistence`
- `generate_state_capture_build_script()` reads `dll_persistence` to generate seds:
  - Each `resets` entry → `sed -i '/{anchor}/i\    {code}'` on all source files
  - KGen-generic DEALLOCATE → anchored on `IF (.NOT. ALLOCATED(kgen_isstop))`, conditioned on `init_condition`
- If `dll_persistence` is empty/missing, no patches are generated (projects without DLL persistence issues skip this entirely)

**Result:** Zero ROSCO-specific variable names or control flow in VIT source. ROSCO declares its own workaround in vit.yaml. Other projects can configure their own or omit.

### 2. Simplified Extraction Protocol

**Discovery:** Commit `dba7738` Filters.f90 has ALL needed fixes (restart call-site fix + notch DO loop fix) and NO integration wrappers. Same for Functions.f90 at `dba7738` (no wrappers, and Functions.f90 has zero `LocalVar%restart` references — the sed on it was always a no-op).

**Old protocol (3+ steps):**
```bash
git checkout e8010f0 -- rosco/controller/src/Functions.f90 rosco/controller/src/Filters.f90
sed -i 's/LocalVar%restart/(LocalVar%restart \/= 0)/g' rosco/controller/src/Filters.f90 rosco/controller/src/Functions.f90
# Plus manual notch filter DO loop fix for Filters.f90
```

**New protocol (1 step, no sed):**
```bash
git checkout dba7738 -- rosco/controller/src/Functions.f90 rosco/controller/src/Filters.f90
```

Full extraction source state:

| File | Commit | What's there |
|------|--------|-------------|
| Controllers.f90 | `fde0498` | Phase 4C — restart fix, AWC decomposition, FlapControl K init fix |
| ControllerBlocks.f90 | `fde0498` | Phase 4C — restart fix |
| Functions.f90 | `dba7738` | No wrappers, no restart refs (sed was always no-op) |
| Filters.f90 | `dba7738` | Restart fix + notch DO loop fix, no wrappers |
| DISCON.F90 | `e8010f0` | Upstream, no wrappers |
| ROSCO_Types.f90 | keep HEAD | Phase 4C types |
| ExtControl.f90 | keep HEAD | KGen parser fix handles PROCEDURE POINTER |

CLAUDE.md updated with the new protocol.

## Verification

- FloatingFeedback: 20 cases (Controllers.f90:96, `--run-args '--scenario 3'`) — tests config-driven DEALLOCATE on non-DISCON file
- CableControl: 20 cases (DISCON.F90:132, `--run-args '--scenario 3'`) — tests config-driven AlreadyInitialized reset + DEALLOCATE
- 501 unit tests pass

## Design Principles Reinforced

### Configuration over hardcoding
The DLL persistence workaround is a legitimate feature — different Fortran projects have different initialization conventions. Making it configurable means VIT handles the general case (KGen SAVE array deallocation) while the project declares its specifics (which variable to reset, what condition means "init call").

### Commit-based source management over manual sed
Using `git checkout dba7738 --` for extraction source instead of `e8010f0` + manual sed eliminates a fragile, error-prone step. The correct source state is a single git command that references a specific, immutable commit. No sed patterns to get right, no manual patches to remember.

### Validate before trusting
The Filters.f90 notch fix was applied dozens of times across sessions without being validated. The audit confirmed it's correct, but it should have been validated when first introduced. The fix was already committed to ROSCO (`dba7738`) and documented in ROSCO_BUG_REPORT.md — the gap was in the extraction protocol not referencing the fixed commit.

## Commits

**VIT repo:** `f0775a2` — Move DLL persistence workaround from hardcoded to vit.yaml config
**ROSCO repo:** `0efd609` — Add dll_persistence config for DLL SAVE variable workaround
