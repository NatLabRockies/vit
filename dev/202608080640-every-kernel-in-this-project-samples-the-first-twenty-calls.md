# Every Kernel in This Project Samples a Function's First Twenty Calls

**Date:** 2026-08-08
**Status:** completed — #43; identifies the common root of four earlier findings
**Scope:** kernel evidence

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The finding

The replication stated it as a caveat on `YawRateControl`. It is more than that.

```yaml
# ROSCO-replication/vit.yaml:13   and   ROSCO/vit.yaml:11
kgen:
  invocation: 0:0:1-20        # rank:thread:start-end
```

`vit/config.py:39` carries the same as VIT's default, and `vit/extract.py:966` says it outright:

> "Within a single run, KGen's invocation counters naturally exceed the capture range **(0:0:1-20)**
> after the first scenario, which is the desired behavior."

Verified against the fixtures: campaign `kernel/PIIController/` holds indices **1…20**, `saturate`
**1…20**, `SetpointSmoother` **1…20 plus 22**. Consecutive, from the first call.

**So every kernel in this project — 43 campaign fixtures and every replication fixture — samples a
function's first twenty invocations.** Not twenty invocations; *the* first twenty. It is the warm-up
window, and it is the least representative part of a controller's life: integrators unwound, state
machines not yet started, filters not yet converged, `reset` still true.

## This is the common root of four earlier findings

I have been recording instances of thin kernel evidence as separate discoveries. They are one thing.

| Note | What I called it | What it actually is |
|---|---|---|
| `202608072233` | a kernel that compared nothing | vacuous `verify_fields` — genuinely separate |
| `202608072250` | non-discriminating input | the first 20 calls are the most stereotyped |
| `202608080003` | `GetWords` — 21/21 all `NumWords == 2` | first-20 window on a stereotyped call site |
| `202608080148` | reset branch always reached in kernel cases | `reset` is true at `iStatus == 0`, i.e. **call 1** |
| `202608080246` | narrow on the output side | separate — argument shape, not the window |
| this note | `YawState`/`YawRateCom` zero in all 21 cases | the state machine has not started by call 21 |

Four of the six share the root. **The problem was never that twenty is a small sample. It is that
twenty from the start is a biased one**, and biased in the specific direction of hiding every
behaviour that depends on accumulated state — which, in a feedback controller, is most of the
interesting behaviour.

That is a substantially stronger and more actionable claim than "20 cases may not be enough," and it
is checkable in one line of config rather than argued from instances.

`20/20 IDENTICAL` therefore means: **the first twenty calls agree.** Nothing more. The pass count
does not distinguish 21/21 on a field that varies from 21/21 on a field that is constant across the
whole window — the replication's phrasing, and it is exactly right.

**The remedy is a config change, not a tool change.** `invocation` is already a `vit.yaml` field with
a `rank:thread:start-end` grammar. Sampling `0:0:1000-1020`, or two windows, costs nothing but a
longer capture run. Nothing has been wrong because of the current setting — that is a project-level
decision and worth stating as one rather than leaving as a default nobody chose.

## The disjoint-coverage case, which is the good news

`YawRateControl` is the first function in the run where **the kernel and the gate covered disjoint
parts, and between them covered the whole thing.**

| | Covered | Missed |
|---|---|---|
| kernel, 21 cases | the `wrap_180`/`LPFilter`/`atan2` chain — three fields vary | the entire yaw state machine; `YawState` and `YawRateCom` are 0 in all 21; `avrSWAP` is not a compared field at all |
| gate, 27 scenarios | the state machine — 10,273 yaw-left samples, 24 initiations, `YawRateCom` ±0.498 | — |

This is the opposite of the recurring worry, and it deserves saying plainly: the two-layer design
works here, and it works *because* the layers fail differently. The kernel catches the arithmetic the
gate would drown in feedback; the gate catches the state evolution the kernel's window cannot reach.
The failure mode this project keeps finding is not that two layers are useless — it is that neither
layer reports which part it covered, so a green pair looks the same whether the union is complete or
empty.

## The `SAVE` translation, and a caveat worth keeping

Four procedure-level `SAVE` locals, confirmed at `Controllers.f90:386-391`: `NacVaneOffset`,
`YawState`, `NacHeadingError`, `Tidx`. Function-local `static` is an exact mirror on lifetime,
zero-initialisation, and reset-on-process-exit — the last being why `regress.sh` isolating each
scenario in its own process matters more than it looks.

**KGen does not capture `SAVE` locals** — the state files hold only the derived-type arguments. The
kernel verifies them here only because the 21 cases are consecutive invocations replayed in order in
one process, so the static walks the same sequence the reference outputs came from. That reasoning
depends on contiguity, and the replication recorded the caveat rather than relying on it silently.

Worth flagging for AeroDyn: `CLAUDE.md` records one SAVE-state procedure in the 507-procedure
inventory. It inherits this caveat exactly, and the contiguity assumption should be checked before
its kernel result is trusted.

## Second probe-shape finding in two cycles

Last cycle: a `×2` probe and a `+1e-9` probe disagreed, because the probed value was identically zero
(`202608080615`).

This cycle: the output probe needed **1e-6, not 1e-10**, because `avrSWAP` is `REAL(ReKi)` —
single precision, confirmed at `DISCON.F90:47` — and a 1e-10 relative perturbation does not survive
narrowing to float32 at the DISCON boundary. It would have read as "nothing moved," which is
indistinguishable from the identically-zero case found one cycle earlier.

So a mutation probe has **both a form and a magnitude**, and either can produce a false "not
exercised." Two independent ways to reach the same wrong answer, found two cycles running. The sound
method remains compiled counters, which have neither.

## Accounting

43 attempted: **38 integrated, 2 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **100% first-pass**. All three repos clean.

Also measured, not read: the open-loop `interp1d` override branch is dead because `OL_Mode` and the
yaw index are never set together in any scenario — a real coverage hole, identified by counter rather
than by inference.

## Summary

**Kernel evidence should be understood through this.** The record so far reads as a list of ways kernel
evidence turned out thinner than it looked. Four of those instances have one cause, it is one line of
configuration, and it is stated in the tool's own source comment. The claim becomes: *KGen kernels in
this project verify a function's first twenty calls; for stateful code that is the least
representative window available, and the pass line does not say so.*

**And the counter-example belongs beside it.** `YawRateControl` is the run's demonstration that two
layers covering disjoint regions can be jointly complete. The defect is not the design; it is that
completeness is never reported, so it cannot be relied on.

## References

- Dev note `202608080615` — probe form; this adds probe magnitude
- Dev note `202608072250`, `202608080003`, `202608080148` — three of the four instances unified here
- Dev note `202608080246` — output-side narrowness, a genuinely separate axis
- `vit/config.py:39`, `vit/extract.py:966` — where the window is set and documented
