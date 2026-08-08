# Scenario 13 Is Dead in the Campaign Too, and a Probe's Verdict Depends on Its Shape

**Date:** 2026-08-08
**Status:** completed — #42; two findings, one campaign-side, one epistemic
**Scope:** gate liveness, mutation probing

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## 1. Scenario 13 runs no controller — in the campaign as well, and it still does

The replication found it by compiling counters into branches rather than reading configuration:
scenario 13 patches `VS_FBP: 1` while `VS_ConstPower` keeps its base value of 1. `CheckInputs`
rejects that pair, sets `aviFAIL = -1`, and DISCON then skips every guarded block for the whole run.
The scenario completes, exits 0, and writes a full-length output file of a dead turbine. The baseline
was captured the same way, so it compared a dead turbine against itself — eleven of thirteen channels
identically zero.

**The campaign has the identical defect, unfixed.** `ROSCO/Examples/vit_sim.py:1052`:

```python
write_discon(turbine, controller, cp_filename, param_filename, patches={
    'VS_FBP': 1,
    'PC_ControlMode': 0,
    'VS_ControlMode': 1,
})
```

No `VS_ConstPower`. The replication's fix adds `'VS_ConstPower': 0` with the comment *"base is 1;
conflicts with VS_FBP>0 in CheckInputs"* — the campaign's block has no such line, then or now.

And twenty lines later:

```python
print("Scenario 13: PASSED (VS_FBP=1 power overspeed exercised)")
```

**Unconditional.** It prints regardless of what the simulation did, and what it did was nothing. This
is the third printed assertion in this project that names an event that did not occur, after
`ExtController`'s `kernel PASSED` stamp (`202608080058`) and `REGRESS PASS: 27/27` from an empty
comparison script (`202608080346`). All three are the same construction: **a success message emitted
from a code path that cannot fail.**

So the campaign's 27-scenario gate is **26 live scenarios**, and has been since scenario 13 was
written. That does not invalidate the campaign's bit-identical result — 26 live scenarios still
compared 672,000 values and found zero differences — but "27 scenarios" overstates the evidence by
one, in the campaign's own documentation and in mine.

I had established scenario 13 was dead in the campaign earlier in this audit. What is new is the
mechanism, which the replication identified precisely and which I had not: it is not that the
scenario is redundant, it is that `CheckInputs` disables the controller and the harness cannot tell
the difference between a turbine under control and a turbine under none.

**The hardening the replication recorded but did not implement is the right one:** `regress.sh`
should fail a scenario whose run emits `ROSCO: error` text, not only one that exits non-zero. Two
scenarios failed silently this way. Worth carrying to AeroDyn, where `verify_aerodyn_baselines.sh`
has the same exit-code-only structure.

## 2. A ×2 probe and a +1e-9 probe disagree, and the difference is a class of blindness

This is the more interesting finding and it has no precedent in the run.

Probing `PIIController`'s return value to test whether the gate observes it:

| Probe | Effect on the gate |
|---|---|
| `× 2` | **nothing moved** |
| `+ 1e-9` | **three channels moved** |

The function runs ~12,000 times and returns identically zero every call. Multiplying zero changes
nothing; adding to it changes everything downstream.

**So the gate detects additive and structural errors in this function and is blind to scaling
errors.** That is a third way for a gate to be weaker than its pass rate suggests, alongside
non-discriminating inputs (`202608072250`) and narrow output comparison (`202608080246`), and it is
distinct from both: here the input is discriminating, the output *is* compared, and the mutation is
simply invisible because it is multiplicative on a quantity that is always zero.

The methodological point is sharper than the finding:

> Had I run the additive probe first, it would have looked plainly exercised.

**A mutation probe reports a property of the probe as much as of the code.** One operator gives one
answer; the confident conclusion "this function is exercised" or "this branch is dead" depends on
which operator you happened to reach for. Every reachability claim in this run that rests on a single
perturbation should say which perturbation.

This also, incidentally, vindicates the replication's decision to settle reachability by **compiled
counters** rather than by probing or by reading configuration. A counter has no shape.

## 3. My retraction is corroborated independently

`202608080517` retracted the coverage-regression finding and corrected the `SS_Mode` claim, from
reading the scenario definitions. The replication reached the same two conclusions from the other
direction, by measurement: `SetpointSmoother`'s dead-`ELSE` flag is dropped, and `PIIController` moves
from `integrated_unexercised` to `integrated`.

Two methods, same answer, neither informed by the other. That is the strongest form the audit
channel can produce, and it is worth noting that it happened here for a correction rather than for a
finding.

## 4. A standing caveat closed by measurement

Regenerating all 27 baselines returned **26 byte-identical**. That closes the outstanding question of
whether the baselines predate the `ErrMsg` change — immaterial by measurement rather than by
argument, which is the distinction this project keeps having to relearn.

The repaired gate was also broken on purpose to confirm it can go red, and all 42 functions re-gated
at 0/351. The rule from `202608080346` — *a green result must be able to name what it compared* — now
has its companion: *and it must be able to go red on demand.*

## Accounting

42 attempted: **37 integrated, 2 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **100% first-pass**, unbroken.

`PreFilterMeasuredSignals`: 1,145 s.

Worth noting against `202608080246`'s finding: this kernel compared **434 distinct fields**, including
every filter state array and all five instance counters, against `ResController`'s one. The
output-side narrowness is a property of the function's argument shape, not a constant of the tool —
which makes it diagnosable in advance rather than only in hindsight.

## Takeaways

Mutation probing has an operator-dependence that the project did not know about. A single
probe is evidence about that probe. Reachability by compiled counter is the sound version.

A third unconditional success message, in the campaign's own harness, asserting that a
scenario "exercised" a mode it never reached. Three instances now, in three different tools, all the
same construction.

## References

- Dev note `202608080346` — the empty comparison script; second unconditional success message
- Dev note `202608080058` — the `kernel PASSED` stamp; first
- Dev note `202608080517` — the retraction this corroborates
- Dev note `202608080246` — output-side narrowness, which #42 bounds
