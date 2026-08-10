# The Gate Lost Coverage While Growing, and Nothing Noticed

**Date:** 2026-08-08
**Status:** completed — #35 and #36; seventh fails-green shape, and a new kind
**Scope:** gate construction, `gate_can_verify`

> # RETRACTED — see `202608080517`
> The central claim is false. Committed `Examples/DISCON*.IN` files are **outputs** of
> `write_discon()`; the `Flp_Mode` values I traced through git are the residue of whichever scenario
> ran last. Scenario 4 patches `'Flp_Mode': 2` explicitly — campaign `vit_sim.py:444`, replication
> `:467` — so `PIIController` is reachable in both gates and **no coverage was ever lost.** The
> "suite losing reach while getting larger" finding does not exist.
> What survives: the campaign's scenario-adding practice (read from commit subjects), the 20 captured
> kernel cases, and `gate_can_verify` being unreliable.

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The finding

The replication reported `PIIController` as unreachable: both call sites sit under
`IF (CntrPar%Flp_Mode == 2)`, and `Flp_Mode` is 0, 1 or 3 across all 14 gate input files. Verified —
`DISCON_flap26.IN` and `DISCON_flp.IN` are 3, `DISCON_filters.IN` and `DISCON_synth.IN` are 1, the
other ten are 0.

**The campaign verified this function.** `kernel/PIIController/` holds **20 captured cases**, 938 KB
each, dated 2026-03-27, and `piicontroller.cpp` carries a `kernel PASSED` stamp that is truthful.

The kernel fixture names its source: `DISCON_flp.IN`. And in the campaign's history:

```
9f872a0  2026-03-19  Flp_Mode=2   Phase 7A: integrate PIController, PIIController, ResController
0e9313d  2026-03-23  Flp_Mode=2   Phase 4D Step 9: ForeAftDamping verified 40/40
ef832a9  2026-03-24  Flp_Mode=2   Add Scenario 3 DISCON .IN fixes and ROSCO bug report
e91eb9a  2026-03-28  Flp_Mode=2   Phase 8C: integrate all 39 functions, all 9 scenarios
e730fd3  2026-04-01  Flp_Mode=2   WIP on integrated: 39 Fortran…
```

`DISCON_flp.IN` carried `Flp_Mode = 2` for the entire period in which `PIIController` was translated
and verified. **It is 3 today, in both trees.**

The change is visible in the campaign's own scenario history:

```
d84782e  Add Scenario 26: Flp_Mode=3 with synthetic rootMOOP for non-zero flap output
173fd5d  Add Scenarios 15-18: AWC_Mode=2, Flp_Mode=3, WE_Mode=1, IPC 2P
b046e00  Add Scenarios 7 and 8: synthetic inputs for under-exercised functions
813d0fe  Add Scenario 6 (IPC) for NotchFilterSlopes, extract with --scenario 4/6
```

The campaign grew its gate from 9 scenarios to 27 specifically to reach under-exercised functions.
Somewhere in that growth, `DISCON_flp.IN` moved from `Flp_Mode = 2` to `Flp_Mode = 3` to gain
coverage of the flap-mode-3 branch — **and silently lost the only coverage of `Flp_Mode = 2`.**

### The gate is not monotonic in coverage, and nothing measured it

This is the part worth keeping. The gate went from 9 scenarios to 27. Every step was an addition.
Coverage still went *down* for one branch, because a scenario was not added — an existing input file
was repurposed.

The result is that **the campaign's final 27-scenario gate, the one it shipped and the one the
replication uses, cannot execute a function the campaign verified.** `piicontroller.cpp` sits in the
campaign tree today in exactly the state the replication describes: integrated, historically
kernel-verified, and unexercised by the gate that guards it. Nothing in the campaign flagged the
regression, because nothing tracked which branches the gate reached — only whether it passed.

**Seventh fails-green shape, and structurally new.** The previous six are all about a single check
establishing less than it appears. This one is about a *suite* losing reach while getting larger, with
every individual check still passing. It is the failure mode that scales with campaign length, and
it is invisible to every mechanism the project has.

### It also means the replication and the campaign are not measured against the same gate

For most functions this does not matter. For `PIIController` it does: the campaign verified it under
a gate configuration that no longer exists, and the replication is judged against the later one. Any
comparison of "the campaign verified X, the replication could not" has to check whether the gate
changed underneath, and until now nothing did.

Not a large effect — one function so far — but it is a systematic bias in the dataset's favour when
reading the replication as under-performing, and it should be stated.

## The remedy already exists in the tree

`Examples/Test_Cases/BAR_10/BAR_10_DISCON.IN` has `Flp_Mode = 2`. It is present in **both** trees and
is not part of either gate.

So `PIIController` is not unverifiable in the `UpdateZeroMQ` sense at all. A scenario built on an
input file already sitting in the repository would execute it. `integrated_unexercised` is the
correct classification *given the current gate*, and the current gate is a choice, not a constraint.

This also reframes my `ExtController` recommendation (`202608080058`, §"Option 2"). Extending the
gate to reach an unreachable function is not a novel proposal — **it is what the campaign did at
least four times**, and the runbook I wrote for the replication carried none of it. **Fourth setup
omission**, after the disposition set, the Phase 2 flags, and the reset/integrate pair. It is the most
consequential of the four, because it silently redefines "unverifiable" from *no scenario reaches
this* to *no scenario I was given reaches this*.

## The replication's own analysis of verifiability is better than the flag

`gate_can_verify` in `plan.json` marks only `UpdateZeroMQ` and `ExtController`. Four functions are now
known unreachable: those two plus `CurDate` and `PIIController`. **Third time the flag has been
wrong**, and the agent's response — record it in STATUS as unreliable, treat `true` as unchecked — is
right. The Phase 2 pass derived reachability from the call graph without checking mode guards against
the input files, which is the same class of error as not resolving generic interfaces
(`202608080003`).

More useful is the distinction it drew, which no flag in the project expresses:

> `PIIController` **could** be verified — 6/6 differential, comparing the returned value, the
> instance counter, and all 5 × 1024 doubles of `piParams` from identical state. `UpdateZeroMQ` could
> not. The difference is not effort, it is **argument shape**: `piparams_t` has no `ALLOCATABLE`, so
> the bridge passes `TYPE(C_PTR)` and writes come back; `UpdateZeroMQ`'s four view types decompose
> into 500+ by-`VALUE` scalars and nothing it writes can be observed.

That is a real predicate — *can the function's effects cross the bridge at all* — and it is checkable
from the signature before any work is done. It belongs in the plan alongside `gate_can_verify`, and
it is a better question than the one the plan asks.

## Verified in passing

**`ITermLast2` is stale.** `Controllers.f90` writes `piP%ITermLast2(inst) = I0` on the reset branch
and never again; the `ELSE` branch updates `ITerm2` but not `ITermLast2`. Mirroring rather than fixing
is correct, and pinning it explicitly in the harness is better than leaving it implied.

**The third `LOGICAL` generator.** `ece0c3d` (`test_validate`) joins `e72235f` (wrapper direction) and
`05b70bc` (kernel callee bridge). The agent's framing — *when a type mapping is wrong it is wrong in
every generator that crosses the boundary, and fixing the one in front of you leaves the rest to be
found one function at a time* — is correct and understated. The VIT history shows the same mapping
being fixed further back, in the AeroDyn era: `1deb7e3` ("6 infrastructure fixes for
CalculateUACoeffs: LOGICAL mapping, …") and `fa3f38d` ("Fix 8 stale tests: LOGICAL int32_t, …").

**`LOGICAL` has now been fixed at least five times, in four generators, across two codebases.** That
is not a bug; it is a missing abstraction. There is no single place where "how does a Fortran
`LOGICAL` cross the boundary" is decided, so every generator decides it again.

**`ratelimit`'s call-site choice.** It extracted deliberately at the one site of four that passes the
`OPTIONAL` `ResetValue`, on the grounds that `PRESENT` true is the branch more likely to be got wrong
and the absent case is covered by the other sites. That is exactly the correction `202608080003` §6
identified — select by argument variety, not hit count — arrived at independently and without being
told.

## Accounting

36 attempted: **30 integrated, 3 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **29/29 first-pass**.

| Function | Wall | |
|---|---:|---|
| `ratelimit` (#35) | 489 s | third clean cycle in a row |
| `PIIController` (#36) | 302 s | `integrated_unexercised`, 6/6 differential |

## Summary

`gate_can_verify` wrong three times. Reachability is not a call-graph property when it is
gated on configuration values.

Fourth setup omission. The pattern across all four is the same: the campaign's method
included steps encoded in scripts, input files, and habit rather than in documentation, and a
re-derivation from documentation drops them one at a time as the work demands them.

## References

- Dev note `202608080058` — `ExtController`; the "extend the gate" option, which the campaign already used
- Dev note `202608080128` — the callee-bridge subsystem; reset/integrate omission
- Dev note `202608080003` — call-site selection; the lesson `ratelimit` applied unaided
- Dev note `202608072329` — three ways the gate cannot speak; this adds gate regression
