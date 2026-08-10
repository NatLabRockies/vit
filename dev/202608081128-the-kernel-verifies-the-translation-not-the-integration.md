# The Kernel Verifies the Translation, Not the Integration

**Date:** 2026-08-08
**Status:** completed — #46; the run's first self-caught fails-green, and a new defect class
**Scope:** verification layers, integration wrappers

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The finding

The replication's first integration of `CheckInputs` produced a wrapper with no copy-back. Had it
shipped, **every input validation in ROSCO would have been silently disabled** — 125 checks
computing correct results into a view struct that was then thrown away.

Both existing verification layers passed it.

| Layer | Result | Why it cannot see the defect |
|---|---|---|
| KGen kernel | pass, 1/1 over 426 fields | It compares the **view struct**, which was correct. The copy-back is downstream of everything a kernel observes. |
| 33-scenario gate | pass, 429 channels, 0 mismatched | No scenario drives `aviFAIL` negative, so the un-propagated value is `0` either way. |

It was caught by re-running the differential harness **after** integration: 124 of 125 failed with
the copy-back removed, 125/125 with it restored, **and the gate said 429/0 both times.**

That last clause is what makes this solid. It is not an argument that the gate is blind here — it is
an experiment showing the gate is blind here, run in both directions.

## The general rule, and it is new

> **A kernel verifies the translation. It does not verify the integration.**

Everything the project has built assumes two independent layers. They are not independent in the way
the assumption requires: they are two views of *the same artifact*, the C++ function. Neither one
looks at the **wrapper** — the Fortran glue that populates views on the way in and copies scalars
back on the way out. The kernel calls the C++ directly and never sees a wrapper at all. The gate sees
the wrapper only through whatever reaches a simulation channel.

So there is a class of defect with **no verification layer at all**:

> a function whose effects the gate cannot observe, verified by an instrument that runs
> pre-integration, has nothing checking the integrated artifact.

Known members, all already recorded, all now visibly the same shape:

- `CheckInputs` (#46) — would have been the worst: all 125 validations disabled, gate green.
- `sigma` (#7) and `unwrap` (#9) — integrated before `--reverse-copy` existed; both write `ErrVar`
  only on error paths no scenario reaches. Already on the deferred list as "gate-invisible."

The mitigation is one line of procedure and costs a rerun: **when a function needs a purpose-built
harness, run it again against the integrated build, not only against the translation.** For
`CheckInputs` that rerun is the only thing between the run and a shipped catastrophe.

## Verified independently

Read-only against the replication tree, not taken from the report:

- `ReadSetParameters.f90` clean `CheckInputs` is **857 lines** with **125** `aviFAIL = -1` sites and
  126 `ErrVar%ErrMsg =` assignments — the 126th being the trailing `RoutineName//':'` prefix. Both
  figures in the report are exact.
- The integrated wrapper at HEAD (`ReadSetParameters.f90:882-906`) carries all three copy-backs:
  `vit_copy_scalars_to_localvariables`, `..._controlparameters`, `..._errorvariables`. The fix is in.
- `kernel/CheckInputs/` holds `.0.0.1` state files and nothing else — `Cases: 1` confirmed by the
  fixture's contents, not by the log line.
- The gap-scenario commit `a9248f8` adds **six** `baseline_arrays/scenario_*.npz` and modifies **zero**
  existing baselines. The addition-only claim is confirmed by the commit stat.

## The harness is a fifth verification approach

`gen_checkinputs_diff.py` (403 lines) generates `checkinputs_diff.f90` (845 lines): one perturbation
per check, each a small list of Fortran assignments that trips exactly that check, run through the
**original Fortran** and `checkinputs_c` on the same input, comparing `aviFAIL` **and the full
`ErrMsg`**.

Two design details worth keeping:

1. **It is differential, not expectation-based.** The oracle is the Fortran, not a recorded message.
   That makes it the same evidentiary shape as a KGen kernel — with **synthesised** inputs where
   capture cannot reach, instead of captured ones. That is the honest way to describe it: not a
   weaker substitute for kernel replay, but kernel replay's input side replaced when the call site
   executes once.
2. **It asserts its own baseline is clean before trusting anything** (`checkinputs_diff.f90:228-236`,
   with the comment "the baseline MUST come back clean or the whole design is void"). `aviFAIL` is
   sticky and the surviving message is the last failing check, so a dirty baseline would make every
   case compare the same message and pass. That is the fails-green failure mode of *this* harness,
   anticipated and closed in the harness itself.

Broken on purpose first — an off-by-one on `LoggingLevel > 3` and one reworded message turned exactly
those two red. Third harness in the run to be broken deliberately before use, and the discipline is
now consistent.

## A gap the repair of scenario 13 opened

Before 2026-08-08, scenario 13 was the **only** gate case in which `aviFAIL` went negative — because
`CheckInputs` rejected its `VS_FBP`/`VS_ConstPower` pair and shut the controller off for the whole
run. Repairing it was unambiguously right. The side effect is that the gate now has **zero coverage
of the `aviFAIL < 0` path**, which is precisely why it cannot see a missing copy-back on this
function.

Note the shape: the only scenario that exercised the error path did so *by accident*, and its
baseline was captured from the same errored run, so it compared an uncontrolled turbine against
itself. It could not have caught anything either. The coverage was never real — but it is worth
recording that repairing the null scenario removed the last nominal touch of that path.

**Recommendation, addition-only, consistent with `202608080700`:** the gate compares simulation
channels and nothing else, so it has no assertion on the error path at all. A gate case that
deliberately supplies an invalid configuration and asserts the expected `aviFAIL` and message is a
different kind of case — closer to the differential harness than to a channel comparison — and it is
the only thing that would make a copy-back regression visible to the routine gate rather than to a
one-off rerun. Author's call whether to relay.

## Coverage is not visibility

From the gap-scenario work, and it sharpens `202608080700`:

> A branch counter above zero says the code ran. Only a perturbation says the gate can see it.

Three of the six new scenarios needed retiming after their target branch was confirmed to execute —
torque ramping from zero and clamping at `VS_MinTq`, or a pitch rate applied to a command already
hard against `PC_MaxPit`. The branch ran; its output was pinned, so no channel moved. Coverage and
observability are different properties and the counter measures only the first.

This is the same lesson as `202608080729` (`interp2d` passing 20/20 while its bilinear branch never
executed) arriving from the opposite direction: there, a branch that never ran; here, a branch that
ran and could not be seen.

## Telemetry: the third attribution artifact

`CheckInputs` is the run's most expensive cycle — **2013 s** — and almost none
of it is translation. It is the cost of **building an instrument**, charged in full to the function
that needed it.

`integrate_runs = 3` is likewise an artifact: one integration on clean source, one without
`--reverse-copy`, one correct. `integrate_attempts = 1` is the real figure. The replication flagged
it in its own ledger.

Third distinct attribution defect, joining the two joiner bugs:

| Defect | Effect | Note |
|---|---|---|
| merged disjoint spans | four functions price at zero | `202608080826` |
| nested phase charged to enclosing function | inflates whoever was nearby | `202608080506` |
| **instrument-building charged to a function** | one function absorbs project-level cost | this note |

The first two are code bugs with fixes. This one is not a bug — it is a question of what a
per-function cost figure *means*, and it needs a stated convention before it is reported, not a patch.

## Accounting

46 attempted: **41 integrated, 2 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **100% first-pass** through #46.

## Unresolved thread (campaign tree)

Opened while checking whether the original campaign had this same defect, **not concluded**: the
campaign's committed `checkinputs.cpp` is a one-line `// stub` on `main`, `restart-validation` and
its only commit in that file's history, and absent entirely on `integrated`. Six other sources in its
53-file build list are also stubs — including `discon.cpp`, the DLL entry point. As committed, that
tree cannot link. So the campaign's shipped implementations are not in the tree I have been reading,
and **nothing about the campaign's `CheckInputs` can be inferred from it.** Where the shipped
sources live is an open question; until it is answered, no campaign-side claim about this function is
available.

## Summary

The two-layer design is described as independent. This is the case that shows the
independence has a boundary: both layers view the same C++ artifact and neither views the wrapper.
It is the honest limitation to state alongside the two-layer argument.

This belongs beside `ExtController` and is arguably the stronger exhibit, because it is
the same failure the campaign shipped — a wrapper whose defect no layer could see — caught here by an
instrument built for a different purpose and rerun at the right moment. `ExtController` shows what
happens when nothing catches it; this shows exactly what catching it requires.

A fifth verification approach: synthesised-input differential harness, generated from the
source, for functions whose call site executes once. Names the boundary of capture-based replay.

## References

- Dev note `202608080729` — coverage without observability, from the other direction
- Dev note `202608080700` — close gaps by addition; the retiming refines it
- Dev note `202608080826`, `202608080506` — the two joiner defects this joins in the ledger
- Dev note `202608080058` — `ExtController`, the campaign's shipped wrapper defect
- `ReadSetParameters.f90:882-906` (replication HEAD) — the wrapper with copy-back
- `translations/ReadSetParameters/gen_checkinputs_diff.py` — the harness generator
