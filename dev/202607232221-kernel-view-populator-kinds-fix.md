# Kernel view-populator fails to compile (missing kinds scope) — PreFilterMeasuredSignals KGen verification

**Date:** 2026-07-23
**Status:** completed (fix a applied + verified); fix b recommended
**Scope:** VIT `view_populator.py` reverse-copy generation; `vit verify` error reporting; ROSCO PreFilterMeasuredSignals verification

## TL;DR

Confirming whether ROSCO's `PreFilterMeasuredSignals` (one of the 10 simulation-only
functions) *could* be KGen-verified surfaced a latent VIT generator bug. `vit extract`
captured 21 cases fine, but `vit verify` reported a misleading
`"VERIFICATION FAILED: no cases found"` that masked a **kernel compile failure**:
the generated `vit_controlparameters_view.f90` used the Fortran kind aliases
`IntKi`/`DbKi` in its reverse-copy subroutine but never brought them into scope, so
gfortran errored with `Symbol 'dbki'/'intki' has no IMPLICIT type`.

Root cause: **`view_populator.py` has never emitted a kinds `USE`** — the integrated
populators only compile because their on-disk copies were hand-patched with
`USE Constants, ONLY: IntKi, DbKi` after generation. A freshly regenerated populator
(as in a kernel-verification dir) has no such patch and fails.

**Fix (a), applied and verified:** the reverse-copy now casts via the intrinsic
`KIND(dest%field)` instead of a named kind alias, so it needs no project-specific
kinds module at all. After the fix, `vit verify PreFilterMeasuredSignals` passes
**21/21 IDENTICAL out-of-the-box** (9807 field comparisons). `PreFilterMeasuredSignals`
is therefore genuinely KGen-verifiable, shrinking ROSCO's simulation-only set from 10 to 9.

**Fix (b), recommended, not done:** `vit verify` should surface kernel build failures
instead of reporting them as "no cases found."

## How it surfaced

Of the 10 ROSCO functions verified by
simulation only, `PreFilterMeasuredSignals` and `SetParameters` were the two "orchestrators."
Assessment (dev discussion) held that `PreFilterMeasuredSignals` is a pure FP filter
orchestrator (0 file-I/O / SAVE / ALLOCATE) — structurally identical to the 42 KGen-verified
functions — and so *should* be KGen-verifiable, while `SetParameters` is an init/file-I/O
hybrid that is not. To confirm rather than assert (per the "run it, don't reason about it"
lesson from the restart investigation), we ran the actual extract+verify.

- `vit extract PreFilterMeasuredSignals -f rosco/controller/src/DISCON.F90 -l 96 --run-args '--scenario 1'`
  → **21 cases captured** (its sole call site, `CALL PreFilterMeasuredSignals(...)` inside
  `SUBROUTINE DISCON`).
- `vit verify PreFilterMeasuredSignals translations/Filters/prefiltermeasuredsignals.cpp -f rosco/controller/src/Filters.f90 --kernel-dir kernel/PreFilterMeasuredSignals`
  → `✗ VERIFICATION FAILED: no cases found`.

The 21 state files were present, so "no cases found" was wrong. Building the kernel by hand
(`make build`) exposed the real error:

```
Error: Symbol 'dbki' at (1) has no IMPLICIT type
Error: Symbol 'intki' at (1) has no IMPLICIT type
make: *** [Makefile:17: vit_controlparameters_view.o] Error 1
```

## Root cause

`view_populator.py` emits a module header of exactly:

```fortran
MODULE vit_controlparameters_view
    USE ISO_C_BINDING
    USE ROSCO_Types, ONLY: ControlParameters
    IMPLICIT NONE
```

but the reverse-copy subroutine it generates (`vit_copy_scalars_to_controlparameters`, added
in dev note `202604021023`) contains lines like:

```fortran
dest%LoggingLevel = INT(view%LoggingLevel, IntKi)
dest%some_real    = REAL(view%some_real, DbKi)
```

`_reverse_scalar_assignment` obtained the kind via `_extract_kind`, whose docstring said
"keep alias name for readability" — i.e. it deliberately emitted the alias `IntKi`/`DbKi`.
Those aliases are **never `USE`d**, so the module doesn't compile.

Confirmation it was never generated: `git log -S "IntKi, DbKi"` and `git log -S "USE Constants"`
over `view_populator.py` return nothing. The integrated
`rosco/controller/src/vit_controlparameters_view.f90:8` has `USE Constants, ONLY: IntKi, DbKi`,
but that line was **hand-added post-generation** and never fed back into the generator. Both
call sites use the same generator:
- `integrate.py:204` (integration) — works only because the on-disk file was hand-patched.
- `kernel_verify.py:192` (kernel verification) — regenerates fresh → the bug bites.

This is not ROSCO-specific: any view-typed function whose reverse-copy touches a scalar of a
project kind alias would fail the same way on a fresh regeneration, in ROSCO or AeroDyn/OpenFAST.

## Fix (a) — applied

Cast to the destination field's kind via the intrinsic `KIND()` inquiry instead of a named
alias. `KIND(dest%field)` is always in scope (no `USE` needed) and yields the identical kind.

`vit/view_populator.py`, `_reverse_scalar_assignment`:

```fortran
! before:  dest%x = REAL(view%x, DbKi)      ! DbKi not in scope in a fresh kernel
! after:   dest%x = REAL(view%x, KIND(dest%x))
! before:  dest%x = INT(view%x, IntKi)
! after:   dest%x = INT(view%x, KIND(dest%x))
```

- Chosen over adding a `USE <kinds_module>` because the kinds module is project-specific
  (`Constants` for ROSCO, an NWTC/precision module for OpenFAST). A `KIND(dest%field)` cast
  needs no config and no codebase-specific module name — consistent with the
  "no codebase-specific logic" rule.
- `_extract_kind` became unreferenced and was removed (its only two callers were these lines).
- The forward populator and BIND(C) mirror were unaffected — they use C kinds
  (`C_INT`/`C_DOUBLE`) already in scope via `USE ISO_C_BINDING`. `IntKi`/`DbKi` appeared only
  in the reverse-copy.

**Verification (end-to-end, no manual patch):** with the fix live in the container
(`vit` runs the mounted `/workspace/vit/vit`), re-running `vit verify PreFilterMeasuredSignals`
regenerated the populator (139 `KIND(dest%..)` sites, 0 stale `IntKi`/`DbKi`/`USE Constants`
references) and reported:

```
✓ VERIFICATION PASSED: 21/21 passed
  Field log: kernel/PreFilterMeasuredSignals/verify_fields.csv (9807 entries)
```

## Fix (b) — recommended, NOT done

`vit verify` reported the kernel compile failure as `"no cases found"`, which reads as a
verification miss rather than a fixable infrastructure error and cost real debugging time.
The kernel build step (`make build` in the kernel dir) should have its exit status checked and
its compiler stderr surfaced. Look in `kernel_verify.py` around the kernel build/run
invocation; a non-zero build return should raise/report the compiler error rather than falling
through to the "count cases" path (which then finds zero field-log rows and says "no cases").

## Impact

- **`PreFilterMeasuredSignals` is now cleanly KGen-verifiable (21/21 IDENTICAL, float64).**
  It can move from simulation-only to KGen+sim, shrinking ROSCO's sim-only set 10 → 9.
  (`SetParameters` stays sim-only: its first-call block reads config files via
  `ReadControlParameterFileSub`/`READCpFile` — outside KGen's model.)
- **General benefit:** the fix repairs kernel verification for *every* view-typed function whose
  reverse-copy touches a project-kind scalar — relevant to the AeroDyn/OpenFAST campaign, where
  the same regeneration path is used. Worth a spot-check there.
- Latent-bug note: the integrated ROSCO populators still carry the hand-added `USE Constants`
  line and compile fine; they don't need changing, but regenerating any of them now produces
  the cleaner `KIND(dest%..)` form without that USE.

## Files / branch

- Repo: VIT. Branch: `fix-kernel-view-populator-kinds` (isolated; parallels the ROSCO-repo
  `restart-validation` branch used for this investigation).
- Changed: `vit/view_populator.py` (reverse-copy cast → `KIND(dest%field)`; removed dead
  `_extract_kind`).
- Not committed yet.

## Next-session checklist

- [ ] Commit the `view_populator.py` fix + this note.
- [ ] Fix (b): make `vit verify` surface kernel build failures (`kernel_verify.py`).
- [ ] Re-run a clean `vit extract`+`vit verify` for `PreFilterMeasuredSignals` and record it as
      KGen-verified in ROSCO `vit.yaml` (`verification: kgen`); update the ROSCO
      accounting (sim-only 10 → 9).
- [ ] Spot-check AeroDyn/OpenFAST: regenerate one view populator and confirm the kernel path
      no longer needs a hand-added kinds `USE`.

## Cross-references

- Reverse-copy origin (where the alias-kind reverse-copy was introduced): `202604021023`.
- The "run it, don't reason" lesson that motivated actually testing this: restart-validation
  investigation (ROSCO `-8`/`-9`, this session).
