# I Cited the Disproof of My Own Claim

**Date:** 2026-08-08
**Status:** completed — corrects `202608080246`; #39 verified
**Scope:** correction, argument aliasing

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The correction, and it is worse than the agent's account of it

`202608080246` said:

> `LocalVar%restart` is assigned in exactly two places in the entire controller source —
> `ReadSetParameters.f90:124` and `:126` — and nowhere else. **No scenario takes that path.**

The first sentence is correct. The second is false, and **the two lines I cited are its disproof.**
Here they are, at `8840a45`, inside `ReadAvrSWAP`:

```fortran
IF (LocalVar%iStatus == 0) THEN   ! TODO: Technically, LocalVar%Time > 0, too, but this restart
    LocalVar%restart = .True.     !       is in many places as a reset
ELSE
    LocalVar%restart = .False.
ENDIF
```

`iStatus == 0` is the first call of **every** scenario. `restart` is `.True.` at initialisation
everywhere. I located the evidence, reported its line numbers accurately, and then attached to it a
conclusion I had taken from the agent's report and never checked — while the text falsifying it was
the thing I was quoting.

**Eighth claim of mine corrected in this run**, and the worst of the eight. The others were
inferences from partial views: a scoped-out window, a file-location proxy, a magnitude argument. This
one had the disproof on screen.

### The agent's diagnosis of the failure does not cover mine, and its lesson is still the better one

It wrote:

> My #37 grep of the Fortran found nothing because ReadAvrSWAP was translated at #18, so that
> assignment now lives in `readavrswap.cpp`. I searched the Fortran and believed the result — on a
> partially-translated tree that's unsound: every integrated function's logic has moved to `.cpp`,
> so the Fortran you see is no longer the whole program.

That is exactly right for its search, which ran against the integrated tree. **Mine ran against clean
source at `8840a45` and did find the assignment.** Two different errors reaching the same wrong
conclusion.

Its lesson generalises and mine does not: *on a partially-translated tree, grepping Fortran is
unsound, and gets more unsound with every function integrated.* That belongs in the runbook and
applies to AeroDyn immediately — 52 ROSCO functions and 30 AeroDyn functions have already moved out
of the Fortran there.

Mine has no general lesson beyond the one already written in `verify-before-asserting`: I did not
read what I was citing.

## The corrected picture

Reset-branch reachability, measured with `fprintf` counters rather than inferred:

| Function | reset reached? | evidence |
|---|---|---|
| `PIController` (#34) | **yes** | kernel cases |
| `ratelimit` (#35) | **yes** | 1 of 20 kernel cases |
| `PIDController` (#39) | **yes** | kernel |
| `ResController` (#37) | no | scenarios 5/8/27: 0 hits |

So `202608080246`'s sweeping claim — *"this applies backwards to `PIController`, `PIIController` and
`ratelimit`"* — collapses. Three of four are reached.

`ResController` remains unreached, and **for a different reason than I gave**: its call site is
guarded by `IF (CntrPar%AWC_Mode == 4)` at `Controllers.f90:814` *and* further by a `Time` condition.
Its `0.0`-on-reset divergence is still unobservable — but now by measurement rather than by my
reasoning, which is the difference that matters.

## The aliasing bug, verified

`Controllers.f90:346`:

```fortran
LocalVar%GenTqAz = PIDController(..., LocalVar%piP, LocalVar%restart, objInst, LocalVar)
```

`LocalVar%piP` and `LocalVar` are both passed. The wrapper copied `LocalVar` — including its nested
`piP` — into the view, handed the C++ a direct pointer to the real `LocalVar%piP`, and then copied
the stale view back over it, discarding every write.

**Fixed in VIT (`7886b80`), not worked around.** The design point is right and non-obvious: *whether
two arguments alias is a property of the call site, which the wrapper cannot see*, so the fix finds
candidates statically and resolves at runtime with `C_ASSOCIATED`. A purely static fix would be
either unsound or over-broad.

### Two lessons worth more than the bug

**A saturating output hides input differences.** The return value compared IDENTICAL throughout,
because it clamps at ±2·`VS_MaxTq` and the azimuth term drives it into the clamp. Only the
uncompared-until-now integrator state showed the divergence. Saturation is a *lossy* function, and
any comparison whose only observable is a saturated value is weaker than its field count suggests.
This is a new member of the family in `202608080246` (the kernel narrow on the output side): here the
output was compared and still could not discriminate.

**The diagnostic signature.** `iterm`/`itermlast`/`elast` at `OUT_TOL` with `n_diff=1` and
Normalized RMS `1.0` — exactly one array slot, one side exactly zero — while 200-plus other fields
were IDENTICAL. *A correct result with a lost store means the store was overwritten.* That is a
recognisable fingerprint for lost-update bugs and belongs in `TRANSLATION_TIPS.md` next to the
dual-computation diagnostic.

**And a third, which is today's recurring theme yet again:** its first draft of the fix had two bugs,
both of which surfaced as *the fix not firing*. It re-derived view-ness from the type name instead of
using VIT's own collector — view-ness comes from `vit.yaml`, not the name — and registered with the
wrong case. Its conclusion:

> A fix that silently does nothing looks exactly like a fix that works — read the generated wrapper,
> not just the result.

That is the fourth instance today of *nothing happened and it read as success*, after the empty
comparison script, the mistyped `event` key, and the container with no `~/.claude`. All four are the
same shape and all four were caught by looking at an artifact rather than a status.

## Accounting

39 attempted: **33 integrated, 3 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Gate re-confirmed 0/351 mismatched. First verify failure of the run that was infrastructure
rather than translation — translation correctness is still unbroken.

## Summary

A saturating output that compares IDENTICAL while the state behind it is wrong. Field counts
overstate discriminating power whenever an output is clamped, and nothing in the harness knows which
outputs saturate.

The fourth silent-nothing failure of the day, this time in a bug fix. The pattern is now
well enough evidenced to state as a class rather than a list: **in this toolchain, the default
failure mode is silence, and silence is the same signal as success.** Every mechanism that
distinguishes them — printing a compared-field count, reading the generated wrapper, `fprintf`
counters instead of static reasoning — is cheap, and none of them existed until a failure forced it.

## References

- Dev note `202608080246` — the note this corrects
- Dev note `202608080346` — the empty comparison script; same silent-nothing family
- Dev note `202608080404` — the CMake flag; the other half of today's setup defects
