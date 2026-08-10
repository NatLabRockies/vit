# A Passing Kernel Over a Non-Discriminating Input

**Date:** 2026-08-07
**Status:** in-progress — observed at function #23; **not fixable in the tool**
**Scope:** what the gates certify — strongest item of the run

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The finding

`PathIsRelative` (#23) produced a kernel that captured one case and compared **197 fields**. Not
the vacuous pass `CurTime` had (dev note `202608072233`); a technically valid verification by every
measure the tool applies.

It is also worthless as evidence. The captured input is `PerfFileName` — a bare relative filename —
and **every plausible mistranslation returns `.TRUE.` for it.**

The simulation gate cannot discriminate either: the default `DISCON.IN` contains only bare
filenames. So both gates pass, both logs read normally, and every count is real.

**This is not detectable by any tool.** There is no counter to check, no line to parse, no
threshold to set. The kernel *is* a real verification — it simply does not separate the candidate
readings of the source.

## The trap it fails to catch

Verified read-only against baseline `974290e`:

```fortran
 IF ( ( INDEX( GivenFil, ":/") == 0 ) .AND. ( INDEX( GivenFil, ":\") == 0 ) ) THEN
    IF ( INDEX( "/\", GivenFil(1:1) ) == 0 ) THEN     ! The file name doesn't start with "\" or "/"
       PathIsRelative = .TRUE.
    END IF
 END IF
```

The two `INDEX` calls take their arguments in **opposite orders**:

| call | haystack | needle | asks |
|---|---|---|---|
| `INDEX( GivenFil, ":/" )` | the filename | `":/"` | does the name contain a drive spec |
| `INDEX( "/\", GivenFil(1:1) )` | `"/\"` | first character | is the first character a separator |

Read the second the natural way — as searching `GivenFil` — and the result **inverts for every
relative path containing a subdirectory.** The comment beside it describes the correct behaviour,
so the comment is right and the call shape is what misleads.

The replication session also confirmed `"/\"` survives `-cpp` as two literal bytes rather than an
escape, which is the kind of check that separates a translation from a guess.

## Why this outranks the other gate findings

Six earlier instances this run were the apparatus reporting success while overstating what it
established. All six were *mechanisms* — a stale library, a masked build failure, dead scenarios, a
wrapper mismatch, an unreported case count, a zero-field comparison. Every one is detectable, and
five have been fixed.

This one is different in kind:

| | `CurTime` | `PathIsRelative` |
|---|---|---|
| Fields compared | **0** | **197** |
| Kernel validity | vacuous | technically valid |
| Tool-detectable | yes — fixed in `d4dc843` | **no** |
| What the input establishes | nothing | nothing |

The existing risk argument holds that residual risk concentrates in **unexercised inputs**, and quantifies how
often the escaping conditions hold — 0 of 153,600, 0 of 500, 0 of 4,253,824. That is a coverage
argument.

**This is stronger: the gates cannot tell you that your inputs are non-discriminating.** They report
success identically whether the input space separates the candidate readings or not. Coverage
metrics do not help, because the input *was* exercised — 197 fields compared — and the exercise
proved nothing.

## The pattern across three functions

The replication session named it, and it is the right frame:

| Function | Green by | Caught by |
|---|---|---|
| `CurTime` | kernel comparing zero variables | the tool, after a fix |
| `PathIsRelative` | kernel over a non-discriminating input | **reading the function** |
| `NonDecreasing` | gate with no plateau in any scenario | **reading the function** |

Three functions verified green by a mechanism that had not tested them. **One was tool-detectable.
Two were catchable only by reading the source and asking what input would tell the difference.**

That is a quantified boundary on how far the automated gate carries this project, and it is the
honest counterweight to the autonomy figure in dev note `202608072058`. The autonomy
is real — and it rests on a judgment no gate supplies. The agent supplied it here; nothing
in the pipeline would have.

**What the gates certify:** bit-identical replay and bit-identical baselines certify that a
translation agrees with the original on the inputs the campaign exercises. They do not certify that
those inputs distinguish the translation from a plausible wrong one. Three of twenty-three
functions in a re-run of an already-migrated codebase passed a gate that could not have failed
them.

## What was done instead

`test-validate` skips CHARACTER arguments and emits a bridge calling the function with an
uninitialized local `CHARACTER(8192)` — it compiles and tests nothing. A fourth `test-validate`
gap, found during `Conv2UC` (#22).

The session **deleted the generated files rather than fixing it there**, because `Conv2UC` had a
genuine kernel (21 real cases, 21 compared fields) and fixing project infrastructure inside that
span would have contaminated the clean measurement #21–23 existed to produce. It then built
CHARACTER-argument support at `PathIsRelative` (`25bcc1e`), where it was actually needed, and wrote
cases separating the two readings — **expected values derived by reading the Fortran, not by
running it**, which is the only way that test has any independence from the thing it checks.

That is the fourth occasion this run of declining to flatter the measurement, and the first where
the deferral had a payoff: the infrastructure was bought at the function that required it.

## Two smaller items

**`std::toupper` would be wrong** in `Conv2UC`: locale-dependent and undefined on negative `char`.
The Fortran is exactly ASCII −32 guarded by `'a'..'z'`, so the C++ compares through `unsigned char`.
Another instance of the faithful translation having to reproduce a property invisible in the
expression, alongside truncated `Pi`, `a*x**2` and `ReKi = C_FLOAT`.

**The plan's per-function method is a proposal, not a finding.** `Conv2UC` was planned for dual-call
on an assumed one-shot reachability; its call site runs once per parsed parameter, so extraction
captured 21 real cases. Recorded as a deviation *toward* the runbook default, with the principle
stated: the plan proposes a method before extraction, and extraction is the evidence.

## Cost

`Conv2UC` 471 s — cheapest of the batch, first clean cycle since `NotchFilterSlopes`.
`PathIsRelative` 488 s, carrying the CHARACTER-argument build. Clean-cycle population
**n=14**.

## References

- Dev note `202608072233` — the vacuous pass; **this is the undetectable counterpart**
- Dev note `202608072214` — `NonDecreasing`; the third member of the pattern
- Dev note `202608072158` — evidence strength not reported
- Dev note `202608072058` — the autonomy figure this counterweights
