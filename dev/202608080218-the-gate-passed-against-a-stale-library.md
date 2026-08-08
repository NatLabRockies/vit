# The Gate Passed Against a Stale Library, and the Telemetry Recorded It as Success

**Date:** 2026-08-08
**Status:** open — the affected function is unidentified; one clean rebuild + regress settles it
**Scope:** dataset integrity; **action required before the run continues**

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## What happened

Reported by the replication in its pre-compaction summary, unprompted:

> piping `rebuild.sh` into `tail` inside a `&&` chain makes the pipeline exit status the pager's, so
> a failed build passes and the gate then reports 27/27 against the stale library. That was my
> command chaining, not a script defect, and it is the one moment today where the gate said PASS
> about something it had not tested.

## Why this is worse than it reads

**The telemetry recorded the failed rebuild as `exit_code: 0`.** The cycle log takes the exit status
of the bracketed command, which in this case was the pipeline's, which was `tail`'s. Every `rebuild`
span from #32 through #36 shows exit 0:

```
05:12:03  SetpointSmoother  rebuild  4.0  0        05:35:12  PIController   rebuild  3.0  0
05:13:29  SetpointSmoother  rebuild  3.0  0        05:36:25  PIController   rebuild  3.0  0
05:19:57  SetpointSmoother  rebuild  2.0  0        05:38:15  PIController   rebuild  1.0  0
05:25:47  PitchSaturation   rebuild  3.0  0        05:45:11  ratelimit      rebuild  3.0  0
05:27:21  PitchSaturation   rebuild  4.0  0        05:46:28  ratelimit      rebuild  4.0  0
05:32:09  PitchSaturation   rebuild  2.0  0        05:50:45  ratelimit      rebuild  4.0  0
                                                   05:56:27  PIIController  rebuild  1.0  0
```

**The evidence that would identify the affected function was destroyed by the defect itself.** No
amount of reading the log will recover it, and the `regress` durations are uniform (123–129 s), so
timing gives nothing either.

## The shape of the risk

If a build silently failed, the library on disk was the **previous** successful build. The 27/27 that
followed is a true statement about the previous source state and says nothing about the function just
integrated. So one function in the dataset may carry a gate result describing a build that did not
contain it.

That is structurally identical to the `ExtController` stamp (`202608080058`) — a green result
attached to something it did not test — except this one is inside the measurement data
rather than inside the translated artifact.

**Eighth fails-green shape.** Distinct from the previous seven: those are all about a check
establishing less than it claims. This one is about a check whose *subject* was silently swapped.

## The action

**One clean rebuild plus one `regress.sh` run, before the run continues.** Roughly 130 s plus build.

- 27/27 → the record is repaired and the question closes.
- Anything else → identifies the function, and re-gating one function is cheap.

Doing it before compaction is better than after, while the agent still holds what it ran. If it has
already been done and I missed it, that fact belongs in `STATUS.md`.

## The part worth keeping

`rebuild.sh` **already defends against exactly this.** Line 41 runs the build under
`set -o pipefail`, and lines 11-12 are a comment naming the hazard:

```
#   2. Letting a failed build reach the copy. `cmake --build | tail` returns
#      tail's exit status, so without pipefail a broken build still copies the
```

The failure mode was known, correctly diagnosed, and correctly fixed — **inside the script**. It then
recurred one layer out, in the ad-hoc shell chaining around the script, because the fix was local to
the place it was found and the *risk* was not.

That is the same shape as the
`LOGICAL` mapping being fixed five times in four generators (`202608080204`): a defect is repaired
where it surfaces, and the class it belongs to is never enumerated, so it reappears at the next
boundary. Here the two boundaries are three lines apart in the same file's comment block and the
shell that invokes it.

It is also, notably, **the second self-caught apparatus defect the agent has reported against its own
results** rather than against the tool — and this one it had every incentive to leave unmentioned,
since nothing in the record would have exposed it.

## References

- Dev note `202608080058` — the `ExtController` stamp; the same shape in the campaign
- Dev note `202608080204` — `LOGICAL` fixed five times; defect-class enumeration failure
- Dev note `202608072355` — cost units
- `ROSCO-replication/scripts/rebuild.sh` lines 11-12 and 41 — the fix that was already there
