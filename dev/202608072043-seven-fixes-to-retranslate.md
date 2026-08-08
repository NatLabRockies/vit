# Seven Infrastructure Fixes to Re-Translate a Codebase VIT Had Already Migrated

**Date:** 2026-08-07
**Status:** in-progress — replication at roughly function 14 of 78
**Scope:** VIT, KGen fork, `CLAUDE.md`, replication plan §1

## Placement

Same as dev notes `202608071913`, `202608071945`, `202608072000` and `202608072004`: the OpenFAST
workspace, because the ROSCO-workspace checkout is bind-mounted into the replication's container
and is the repository that session commits to. **Do not merge into that checkout until the run has
ended.**

## The count

Through roughly fourteen functions of a codebase VIT had already migrated end to end:

| Commit | Component | Defect | Path |
|---|---|---|---|
| `a8ac134` | VIT | `fortran_inventory` dropped a file on cpp line-splices | analysis |
| `7ad4e9c` | VIT | `integrate` hardcoded a `vit_types.h` include | measured |
| `e72235f` | VIT | `interface_gen` omitted LOGICAL→INTEGER conversion | measured |
| `badd9f8` | VIT | `CHARACTER(*)` length never passed to C++ | measured |
| `badd9f8` | VIT | scaffold guard inverted: clobbered implementations | measured |
| `c3c8f91` | KGen | `kgsearch` missing `search_Char_Selector` | measured |
| (in flight) | KGen | `gen_kernel_callsite_file.py` | measured |

**Six of the seven are in the path that produces translations.**

Not counted here: two fixes to `join_telemetry.py` (`06811f0`, `90a496d`), which are measurement
tooling written for this run and not part of VIT's translation path.

## This falsifies a premise of the replication plan

Dev note `202608071634` §1, caveat 3, states:

> **Different tool maturity.** The campaign built VIT while using it. This replication starts with
> the finished tool. The original's cost included the tool's construction; this does not.

**The tool was not finished. It was finished *for AeroDyn*.** Pointing it back at ROSCO — the
codebase it was built on and had completely migrated — surfaced seven defects in fourteen
functions.

The caveat should be rewritten before any number from this run is reported. The honest version is
that the replication measures a tool mature on one codebase being re-pointed at another, and that
this cost seven fixes, which is a result rather than a caveat.

## Two categories, and the second is the more interesting one

### Capabilities lost in transit to AeroDyn

`e72235f` is the clearest: commit `1deb7e3` (2026-04-25, AeroDyn work) remapped `LOGICAL(4)` from
`C_BOOL` to `C_INT`, and the call generator keys its conversion on the *C* type, so the change
silently disabled it. The original campaign's `LPFilter` wrapper emitted `LOGICAL(reset, C_BOOL)`
correctly. Full analysis in dev note `202608071945`.

`c3c8f91` and the `CHARACTER(*)` gap are the same shape without a single identifiable commit:
ROSCO exercises string handling that AeroDyn's translated subset does not, so the machinery either
never existed or rotted unobserved.

These share a mechanism: **the tool changed under a codebase that was never re-run.** ROSCO had
already become pure C++, so its Fortran bridges no longer existed to break, and the damage was
invisible until a fresh translation regenerated them.

### Defects that were always there and got documented as constraints

The inverted scaffold guard is the important one. `vit/cli.py` read:

```python
if "// TODO:" not in existing_scaffold:
    out_path.write_text(scaffold)     # overwrites a COMPLETED implementation
else:
    # comment claimed: don't overwrite an existing implementation
```

Backwards in both directions. It clobbered hand-written implementations and refused to refresh
stale placeholders. The analogous guard in `cmd_test_validate` (`cli.py:1310`, marker
`"// --- TODO:"`) is correct, which is why this went unnoticed: one of two sites was right.

**It had already been observed, misdiagnosed as design, and written into the project's
instructions.** `CLAUDE.md` in the OpenFAST workspace says:

> **Warning:** `vit translate` overwrites the scaffold file every time. Do not re-run it after
> writing the C++ implementation unless you have saved the implementation elsewhere.

That is a rule instructing every future session to work around an inverted condition. A guard
existed whose entire purpose was to prevent the described behavior; nobody read it. The fix in
`badd9f8` makes the rule obsolete in both directions — re-running after implementing is now safe,
and it correctly refreshes a stale placeholder, which is the case that matters when a signature
changes mid-cycle.

**`CLAUDE.md` must be updated after the run.** It currently tells future sessions to avoid a
capability that works.

This category is the more interesting one. The failure was not
that a bug existed. It is that the bug was encountered, absorbed as a workflow constraint,
documented, and propagated to every subsequent session — without anyone asking whether the tool
was supposed to behave that way. The gates verify translations. Nothing verifies the instructions.

### It also cost a live debugging detour

After adding length parameters to `build_c_params`, the replication session observed the scaffold
was unchanged and searched `type_map`, `build_c_params`, `translate.py` and back before locating
the writer four lines away. The symptom pointed at the type system; the cause was the guard
refusing to refresh a placeholder that still contained `// TODO:`. That detour is inside a
function span and inflates that function's cost.

## What this means for generality

Dev note `202608071945` framed the `LOGICAL` mapping as one regression. Seven fixes make it a
rate, and the claim available is stronger and better evidenced.

VIT was carried to a second codebase without forking it, and the no-fork constraint acts as a
test designed to expose non-generality. The test has now returned a
result: **the constraint held, no fork was ever made, and the tool lost the ability to translate
its first codebase in at least six distinct ways, none detected, because nothing re-runs a
finished codebase after the tool changes.**

Generality across n=2 is not a property acquired and kept. It is one that must be continuously
re-verified, and no part of the workflow does that.

A companion observation: the campaign's discipline gates *translations*, not the *tool*
and not the *instructions*. Two of the seven defects were latent for months behind a documented
workaround.

## Consequences for the measurement

**The measured tool was in motion.** Unlike the joiner edits, `interface_gen.py`, `cli.py` and the
KGen fork are the measured path. The tool that generated function #1's bridge is not the tool
generating #14's. This is not invalidating — the changes are unblocking bug fixes, not performance
work — but any account of the run must say the tool changed during the run, alongside the extract-scenario
change point recorded in `202608071913`.

**Early functions carry infrastructure-repair cost in their spans.** Already true of `saturate`
(dev note `202608071913`) and now of whichever functions absorbed the `LOGICAL`, `CHARACTER(*)`
and scaffold-guard work. A cost curve that falls across the run reflects a tool being repaired,
not an agent improving.

## Actions after the run

- [ ] Rewrite replication plan §1 caveat 3; "starts with the finished tool" is false
- [ ] Update `CLAUDE.md`: remove the `vit translate` overwrite warning, which now misinstructs
- [ ] Amend the `kgen-bug-attribution` memory: the prior that it is "almost never a KGen bug" was
      broken twice in one day, by exactly the investigation the rule demands. Keep the rule,
      record the exceptions.
- [ ] Check AeroDyn for live instances of the LOGICAL defect (dev note `202608071945`)
- [ ] Consider a periodic ROSCO re-translation as a VIT regression test; that is what caught all
      of this

## References

- Dev note `202608071945` — the LOGICAL regression; **this note generalizes it**
- Dev note `202608071634` — the replication plan; §1 caveat 3 falsified here
- Dev note `202608071913` — measured-path change points
