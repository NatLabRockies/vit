# The Kernel Can Be Narrow on the Output Side, and a Branch Can Be Dead to Both Layers

**Date:** 2026-08-08
**Status:** completed, but **the reset-branch section below is WRONG** — see `202608080436`
**Scope:** verification coverage

> **CORRECTION.** The claim that the reset branch is dead to both layers, and that this "applies
> backwards to `PIController`, `PIIController` and `ratelimit`", is false. `restart` is `.True.` on
> the first call of every scenario — `ReadSetParameters.f90:123-127`, inside `ReadAvrSWAP`, is
> `IF (LocalVar%iStatus == 0) THEN LocalVar%restart = .True.`. I cited lines 124 and 126 as evidence
> for the opposite of what they say. Measured reachability: reset is reached for `PIController`,
> `ratelimit` and `PIDController`; only `ResController` is unreached, and for a different reason.
> The output-side narrowness finding, which is the point of this note, is unaffected.

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## First: the stale-library window is closed, and I was wrong about it

See the correction at the head of `202608080218`. Summary: a from-scratch rebuild produced a
byte-identical library and 27/27; `rebuild.sh`'s telemetry was never fooled; the log carries five
genuine `exit_code: 2` rebuilds; exactly one gate run of 41 tested a stale library and was
superseded before its commit. All 36 functions' final gate followed a successful rebuild.

I claimed the telemetry could not record a rebuild failure, having looked only at spans from #32
onward — and all five failures predate #32. Sixth claim of mine the replication has caught.

**The agent's aside about how it nearly repeated the same mistake is the better lesson:**

> the cycle log's key is `phase`, not `event`. My first query used `event`, got zero rows, and that
> reads as "no failures ever" — the same fails-green shape as the bug being audited.

A query that returns nothing because the field name is wrong is indistinguishable from a query that
returns nothing because there is nothing to find. That is precisely what happened to me, by a
different mechanism — I scoped the window instead of misnaming the key. **The audit apparatus has the
same failure mode as the subject.** The study of
tools that report success they have not earned produced two such reports in its own instrumentation
within an hour.

## The finding: the kernel was narrow on the output side

`ResController` — 20/20 kernel, 27/27 gate, first pass, 608 s.

**The kernel compared exactly one field: the return value.** `resP`'s four 1024-element history
arrays and the `INTENT(INOUT)` instance counter were read as inputs and never compared as outputs.
A wrong history shift passes 20/20 in silence.

Every previous instance of insufficient kernel evidence in this run has been narrow on the **input**
side — one call site, one shape, one branch (`202608072250`, `202608080003`, `202608080148`). This
is the first that is narrow on the **output** side: the inputs were fine, and the comparison simply
did not cover most of what the function writes.

It is the same root as `202608072233`, where only 22 of the campaign's 43 `verify_fields` lists were
non-vacuous — but seen from the other end. There the kernel compared nothing; here it compared one
field out of five, which is worse in a way, because the result looks like a real pass.

**Both narrownesses are invisible in the pass/fail line.** "20/20 IDENTICAL" is emitted whether the
kernel compared five outputs or one. A count of compared fields alongside the case count would make
the difference legible at zero cost, and nothing in the tool reports it.

## A branch dead to both layers, and a correction the agent made against itself

`IF (reset)` in `ResController` is unreachable by kernel *and* gate. Verified:
`LocalVar%restart` is assigned in exactly two places in the entire controller source —
`ReadSetParameters.f90:124` and `:126` — and nowhere else. No scenario takes that path.

This applies backwards to `PIController`, `PIIController` and `ratelimit`, and **corrects a claim the
agent had already written into `TRANSLATION_TIPS.md`** stating that the gate covered `PIController`'s
reset path. It does not. The filters are unaffected, because their guard is
`(iStatus == 0) .OR. reset` and `iStatus == 0` fires at initialisation.

Two things worth recording about that correction. It was made unprompted, against a file the agent
had written itself and that is injected into every future translate prompt — so the error would have
propagated into every subsequent translation. And it is the *seventh* self-caught error in the
dataset, against six of mine that the agent has caught. Neither number is flattering to me.

`202608080148` recorded the branch-sampling pattern as "the kernel samples one branch and the gate
carries the rest." That is now too generous. For this family of functions **the gate does not carry
the rest** — the reset branch has no evidence from any layer, and four integrated functions contain
one.

## A sixth upstream ROSCO artifact, and a divergence recorded rather than hidden

`ResController`'s reset branch **never assigns the function result**:

```fortran
IF (reset) THEN
    ...                                    ! no `ResController = ...` anywhere in this branch
ELSE
    ResController = 1/b0*( ... )
    ResController = saturate(ResController, minValue, maxValue)
END IF
```

`PIDController`, twenty lines above, does assign (`PIDController = I0`). So `ResController` returns
an indeterminate value that the caller stores. Sixth upstream artifact surfaced by translation
pressure, after the two `Flp_Mode=2` uninitialised variables, `ExtController`'s Record 49,
`NonDecreasing`'s name/behaviour mismatch, and the unguarded `ControllerBlocks.f90:58` debug write.

An indeterminate return is undefined behaviour in C++, so the translation returns `0.0` — **logged as
a deliberate divergence, not a silent fix.**

**The campaign made the same choice and did not say so.** `rescontroller.cpp:64` declares
`double result = 0.0;` before the `if (reset)`, and line 84 returns it. Identical behaviour; the
campaign arrived at it through variable initialisation rather than a decision, and nothing in the
file records that the Fortran does something else.

Third instance of the campaign silently normalising upstream behaviour, after `ExtController`'s
Record 49 (`202608080058`) and inlining `saturate` (`202608080148`). **The distinction that matters
is not what the two translations do — it is that one of them is auditable.** A reader of the campaign
tree cannot tell that `rescontroller.cpp` diverges from its source; a reader of the replication's
`DECISIONS.md` can.

## Verified in passing

**`gate_can_verify: true` held for the first time in five checks** — and it was checked rather than
trusted, which is the practice that matters more than the outcome.

**Scenario selection by argument variety, again.** It chose scenario 27 over 8 because `AWC_amp` is
2.0 rather than 0.0, and only the larger amplitude drives `Error` far enough to reach the saturation
clamp. Identical modes and gains otherwise. That is the `202608080003` §6 lesson applied for the
third time unaided, now discriminating on a value rather than on argument presence.

**Two runbook corrections**, both real: clean-source extraction must revert the function's own file
when it already carries wrappers, since KGen copies the whole file; and `join_telemetry.py` must run
on the Mac, because in the container it finds no `~/.claude`, reports "1 transcript(s)", and writes a
CSV with every token column zero. The second is another silent-zero failure — a third instance in one
day of an empty result that reads as a clean result.

## Accounting

37 attempted: **31 integrated, 3 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **30/30 first-pass**.

Boundary fraction rose, attributed to the two compaction hand-offs and the
flush before them, and recorded in `DECISIONS.md`. That attribution is sound and worth keeping as a
measurement in its own right: **compaction is a real, quantifiable cost of long agentic sessions**,
it lands entirely in the unattributed bucket, and this is the first time the project has a number for
it.

## References

- Dev note `202608080218` — the stale-library note, with my correction at its head
- Dev note `202608072233` — vacuous `verify_fields`; the same root, seen from the other end
- Dev note `202608080148` — the branch-sampling claim this tightens
- Dev note `202608080003` — call-site selection by argument variety
- Dev note `202608080058` — `ExtController`; first instance of silent normalisation
