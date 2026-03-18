# Phase 4A Review and Followup Fixes
**Date:** 2026-03-18
**Status:** completed
**Scope:** Post-implementation review of Phase 4A — 5 fixes applied, 1 design item identified for future planning

## Background

After completing Phase 4A (analyze-types command, ErrorVariables fix, build-fix loop), we conducted a systematic review of assumptions made and loose ends left over. This surfaced 6 items, 5 of which were resolved immediately and 1 that requires its own design phase.

## Items Reviewed and Resolved

### 1. `is_c_compatible` vs `classify_field_blockers` disagreement on LOGICAL

**Problem:** `DerivedTypeInfo.is_c_compatible` considered LOGICAL fields fine (they map to `int` via `LOGICAL(C_BOOL)` and C struct generation works). But `classify_field_blockers` flagged LOGICAL as a blocker with the suggestion "LOGICAL fields may need BIND(C) or INTEGER replacement." If a type had only LOGICAL fields, these two would disagree — `is_c_compatible=True` but `TypeAnalysis.is_compatible=False`.

**Resolution:** Removed LOGICAL from `classify_field_blockers`. It's not a real blocker — LOGICAL works today in C structs. The LOGICAL→INTEGER rewrite is a cleanup for semantic correctness (Phase 4C's `--fix logical`), not a prerequisite for functionality. When Phase 4C implements `--fix logical`, it can add LOGICAL detection back with proper handling.

**Principle:** `classify_field_blockers` should only report things that actually prevent correct C interop. Advisory items belong in the phase that provides their fix.

### 2. build_fixer is gfortran-specific

**Problem:** `parse_gfortran_errors()` uses regex tuned to gfortran's output format. If a project uses ifort or flang, errors won't be parsed and the build-fix loop will silently fail to fix anything. The config has `fortran.compiler` but `build_and_fix()` didn't check it.

**Resolution:** Added a `compiler` parameter to `build_and_fix()`. If compiler is not "gfortran", prints a warning to stderr. The CLI passes `config.fortran_compiler` through. This acts as documentation and prevents silent failures when VIT is used with other compilers.

### 3. interp1d not tested end-to-end

**Problem:** Phase 4A claims to "unblock" interp1d by making ErrorVariables C-compatible, but we never actually ran interp1d through the pipeline.

**Resolution:** Accepted as-is. The "unblocked" claim is based on type compatibility (`is_c_compatible=True`, C struct generates correctly with `char ErrMsg[1024]`). Phase 5 will be the definitive test when interp1d is actually translated and verified.

### 4. Leftover `errvar_fix_output.txt` in ROSCO

**Problem:** Transient simulation output file left untracked in ROSCO after the diff test. Could cause confusion.

**Resolution:** Deleted the file. Added a note to CLAUDE.md's Clean Build Test section: after confirming the diff is empty, delete transient output files (`integrated_output.txt`, etc.) but keep `baseline_output.txt` as reference.

### 5. vit.yaml translation statuses downgraded

**Problem:** Running golden fixture verification regression (`vit verify saturate ...`) called `record_translation(config, "saturate", "verified", {...})`, which overwrote the "integrated" status with "verified". Integration is a superset of verification — the status went backwards.

**Resolution:** Modified `record_translation()` in `config.py` to refuse status downgrades. Added a `_STATUS_RANK` dict that defines the progression: `blocked → scaffold → translated → verified → integrated`. If the new status has a lower rank than the existing status, only the details are updated (e.g., `cases_passed`) without changing the status. Added 2 new tests: `test_does_not_downgrade_status` and `test_upgrade_status_works`.

Restored the ROSCO vit.yaml statuses for saturate, wrap_180, wrap_360, and ratelimit back to "integrated".

## Item Identified for Future Planning

### 6. VIT → Claude → User interaction protocol for assumptions

**Problem:** `fix_character_allocatable` defaults to CHARACTER(1024), which happens to be correct for ROSCO. But this is an assumption that could be wrong for other codebases. The tool should try to infer the right length (e.g., from ALLOCATE calls) but must always prompt for confirmation.

**The deeper insight:** VIT's standard workflow is being run by Claude Code, not directly by a human. When VIT encounters an assumption that needs human judgment, the interaction chain is:

```
VIT needs input → signals to Claude → Claude discusses with user → Claude feeds answer back to VIT
```

This is a general design problem, not just about CHARACTER length. It applies to any assumption VIT makes:
- "This ALLOCATE uses `size_avcMSG - 1` — inferred length is 1024. Is that right?"
- "This type has ALLOCATABLE arrays with runtime-determined sizes. What fixed sizes should we use?"
- "The build-fix loop found an error it can't auto-fix. Here's the error — what should we do?"
- "This function has 3 call sites. Which one should I extract from?"

This needs its own rigorous design phase. The interaction protocol between VIT and Claude Code must handle:
- How VIT signals it needs input (exit code? structured output? exception?)
- How Claude interprets the signal and formulates a question for the user
- How the user's answer gets fed back to VIT (command-line args? stdin? config?)
- How to handle cases where Claude can answer without bothering the user vs. cases that require human judgment

**Status:** Deferred to its own planning session. This is architecturally significant and affects how all future VIT commands handle assumptions.

## Test Results After Fixes

- 335 unit tests (333 + 2 new config tests), zero failures
- Golden fixture verification: 40/40 (saturate confirmed, status correctly stayed "integrated")
- No regressions

## Commits

**VIT repo:**
- `8f769bd`: Phase 4A followup — all 5 fixes (LOGICAL removal, gfortran guard, record_translation non-downgrade, CLAUDE.md cleanup note, compiler passthrough)

**ROSCO repo:**
- `555ea68`: Restore integrated status in vit.yaml
