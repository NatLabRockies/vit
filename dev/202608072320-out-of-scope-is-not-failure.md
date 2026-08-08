# "Should Not Be Mirrored" Is Not "Could Not Be Mirrored"

**Date:** 2026-08-07
**Status:** in-progress — disposition added to runbook and joiner; a setup omission corrected
**Scope:** replication runbook §9b, `scripts/join_telemetry.py`, `vit/extract.py`

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## A gap in the procedure, and it was mine

`AddToList` grows an `INTEGER, ALLOCATABLE, INTENT(INOUT)` array by one element via
allocate/copy/append/`move_alloc`. VIT generates:

```c
void AddToList(int* list, int n_list, int element)
```

That signature cannot express the function: the caller's `list` keeps its old length and address,
so there is nowhere to put the (n+1)-th element and no way to report a new one. The session
verified the growth is observable rather than incidental — the caller evaluates
`ANY(All_OL_Indices < 0)` over the grown array — and recorded the function as **blocked**.

It also rejected the obvious workaround: let the Fortran wrapper size, allocate and `move_alloc`
while C++ fills the buffer. Correctly. That leaves the allocated-or-not test, the size arithmetic
and the growth policy in Fortran, and produces a `.cpp` claiming to be a translation of a function
implemented somewhere else.

**But "blocked" was the wrong disposition, and the reason is a procedure gap I introduced at
setup.**

Checked:

```
"idiomatic" / "rewrite" / "out of scope" as a disposition:
  REPLICATION_RUNBOOK.md    0 mentions
  analysis/replication/boundary.md   0 mentions
```

I authorised exactly one method — mirror-translate and verify bit-identically. When that does not
fit, the agent had two moves: fix the infrastructure, or blocked. **The original campaign had a
third and used it for seven files: rewrite idiomatically, gate at the simulation layer** (Track B,
dev note `202608072149`).

I withheld that deliberately. The Track B decision sat in the memory I classified as answer key,
alongside the function list. That was right for the *plan* — it should not inherit the campaign's
ordering — and wrong for the *method space*. Knowing that idiomatic rewrite is available when
mirroring does not fit is a **capability**, not an answer.

The omission propagated: `plan.json` puts all 78 procedures through VIT because the Phase 2
analysis had no concept that another disposition existed.

**This is the clearest thing I got wrong at setup.** It took twenty-five functions to surface,
because everything before it was leaf math where mirroring is obviously correct.

## The agent's argument is better than mine

I framed the correction as *the campaign routed this class around*. The session's reading needs no
campaign at all:

> Its own source comment credits a StackOverflow answer titled "how to add new element to
> dynamical array in Fortran 90", and adds "This is set up for integers, will need to make
> interface for other types." That's a description of `std::vector<T>` written by someone who
> didn't have one.

A function whose own comment describes the facility the target language provides natively should
not be mirrored, whatever any prior campaign concluded. Mirroring it would faithfully reproduce the
manual copy loop and the per-type duplication in a language where `push_back` exists.

The session also made the subsumption explicit, which stops the entry reading as unfinished work:
`AddToList` stays Fortran while its callers are Fortran, and disappears when they are translated,
because the growth becomes the container's.

## The two claims were correctly separated

Kept apart, and this matters:

1. **VIT cannot express a reallocating `ALLOCATABLE` dummy.** A real tooling limitation, on record.
   Unblocking it needs an output-buffer convention by which the wrapper learns the output bound;
   here it is n+1, which is not knowable from the signature.
2. **`AddToList` should not be mirror-translated even if it could be.**

Conflating them would hide a genuine gap behind a scope decision. Runbook §9b now states the
distinction in both directions: calling a tooling gap "out of scope" hides a limitation; calling a
container workaround "blocked" invents a failure the method never had.

## The failure rate changed meaning

"24 translated, 1 out of scope, 0 blocked" is not bookkeeping against "24 integrated, 1 blocked".
It is the difference between **a method that failed once and a method that has not failed yet.**

The joiner derived status from exit codes alone, so `AddToList` reported as `failed`, inflating the
failure rate with work that never failed. Its own docstring already warned that conflating cases
with `failed` corrupts a failure rate computed from the table — this was a **fourth** case it
structurally could not see, because a disposition is a judgement about a function rather than an
outcome of running it.

Added `--dispositions`, reading `{function: {status, reason}}`. It requires a reason and rejects an
unknown status, because a disposition without a stated reason is not reviewable; it prints what it
applied and warns on a name matching no span, which is almost always a typo that would otherwise
drop a function from the dataset silently.

## Seventh fails-green defect, and its trigger is the interesting part

Extracting `CurDate` reported 1 captured case for a call site guarded by `IF (CntrPar%Echo > 0)`
with `Echo = 0` in all 14 config files. The contradiction was real: `kernel/CurDate/` contained
`AddToList.0.0.1` — the previous extraction's state file — and no `CurDate.*` file at all.

`count_kernel_cases` matches `.+\.\d+\.\d+\.\d+$`, i.e. **any function's** state files. Its own
docstring says `<function>.0.0.1`, so the intent is per-function; the regex does not enforce it.

**Audited read-only across every kernel directory in the tree:**

```
kernel dirs containing state from a different function:
  CurDate     contains: AddToList

per-dir case counts, own vs total:
  CurDate     own=0 total=1   <-- MISCOUNT
```

**Isolated to `CurDate`. No earlier case count was inflated; nothing already integrated is
affected.**

It fired only now because `AddToList` was the first extraction to produce **no state at all** —
the function was blocked, not translated. Every prior extraction wrote its own correctly-named
files, which masked leftovers by being present. So the regex has been wrong for the entire run and
became observable only when a directory had nothing of its own to find.

Same shape as `--reverse-copy`'s FUNCTION branch (`202608072114`) and `kgen_utils` linkage: a
defect requiring an unusual path to become visible, sitting quietly behind normal operation. Third
instance this run whose trigger is *the first time something was absent rather than wrong*.

## One correction to the previous report

`plan.json` marks **14** functions with the alloc trait, not 5. Most allocate as part of doing real
work — parsing, file reading, derived-type fields — rather than as a container workaround, so
neither disposition applies in bulk and each needs its own judgement. But 14 is large enough to
matter when choosing between the helper layer and the control layer.

## Summary

The setup omission is a concrete instance of the note's own thesis about method spaces: an
agent constrained to one disposition will report a limitation where a scope judgement belongs. The
constraint was invisible until a function arrived that needed the third option, and it took a human
noticing to surface it — the only substantive contribution the audit channel has made to the run
that the agent could not have reached alone.

The distinction between *cannot* and *should not* belongs in the accounting. The campaign
reported 43 kernel-verified and 9 simulation-gated without a category for the seven functions it
chose not to mirror at all. Those seven are Track B, and they are not failures of the method.

## References

- Dev note `202608072149` — Track A/Track B; the capability boundary this corrects
- Dev note `202608072114` — defects visible only on unusual paths
- Dev note `202608072250` — the non-discriminating input; the other judgement no gate supplies
- Dev note `202608072254` — the KGen automatic-length defect; the other layer-wide blocker
