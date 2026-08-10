# Retraction: The Gate Never Lost Coverage

**Date:** 2026-08-08
**Status:** completed — retracts `202608080204`; corrects `202608080128`; confirms `202608080058`
**Scope:** correction

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The method error

Committed `Examples/DISCON*.IN` files are **outputs**. Every scenario calls `write_discon()` with a
patches dict, so the values on disk are whatever the last scenario to run left there. The replication
told me this at #38. I recorded it in `202608080346` and flagged **two** of my claims as needing
re-derivation.

**Three claims rested on that method, and I re-checked only the two I was pointed at.** The third —
and it was the one I had ranked among the strongest findings — rested on reading the *campaign's*
git history of the same generated files, which is unsound for exactly the same reason. I treated the
falsification as applying to the claims someone named rather than to the method.

## RETRACTED — `202608080204`, "the gate lost coverage while growing"

That note claimed the campaign grew its gate 9 → 27 scenarios and silently deleted its only
`Flp_Mode = 2` coverage, leaving `PIIController` verified-but-unreachable. **This is false.**

The authoritative source, checked in both trees:

```
campaign      Examples/vit_sim.py:444    'Flp_Mode': 2,
replication   Examples/vit_sim.py:467    'Flp_Mode': 2,     # "to exercise PIIController"
```

Scenario 4 patches `Flp_Mode = 2` explicitly, in the campaign then and in the replication now. The
`DISCON_flp.IN` value I traced through git — `2` in March, `3` today — is the residue of whichever
scenario ran last before each commit, not a gate configuration. **No coverage was ever lost, and
`PIIController` is reachable in both gates.**

The "seventh fails-green shape — a suite losing reach while getting larger" does not exist. It should
be withdrawn, not softened.

**What survives from that note:**

- The campaign added scenarios specifically to reach under-exercised functions — `Add Scenario 6
  (IPC) for NotchFilterSlopes`, `Scenarios 7 and 8: synthetic inputs for under-exercised functions`,
  `Scenarios 15-18`, `Scenario 26`. Read from commit subjects, not from generated files. This is the
  gate-extension practice, and it remains the fourth setup omission.
- `kernel/PIIController/` holds 20 captured cases. On disk, verified.
- `gate_can_verify` is unreliable — true, but because it cannot see mode guards at all, not because
  of any coverage regression.

## CORRECTED — `202608080128`, `SetpointSmoother`'s dead `ELSE`

I wrote that `SS_Mode` is 1 in all 14 gate files, so `LocalVar%SS_DelOmegaF = 0` is unreachable.

```
campaign      Examples/vit_sim.py:1486   'SS_Mode': 0,
replication   Examples/vit_sim.py:1509   'SS_Mode': 0,
```

One scenario patches it to 0 in each tree. **The `ELSE` branch is reached.** The surrounding finding
in that note — the 30 width-mismatched kernel bridges — is unaffected; it comes from reading the
bridge files themselves.

## STANDS — `202608080058`, `ExtController`

This is the load-bearing one, and it survives on better evidence than it had:

```
Ext_Mode patch entries:   campaign 0    replication 0
ZMQ_Mode patch entries:   campaign 0    replication 0
```

**No scenario in either tree patches either mode.** They inherit the generated base value, which is
0. `ExtController` and `UpdateZeroMQ` are unreachable by every scenario in both gates — now
established from the scenario definitions rather than from their output.

So the false `kernel PASSED` stamp, the Record 49 divergence, and the claim that nothing in the
pipeline could have caught it all hold, and the reachability half is now argued correctly.

## The lesson, which is mine

When a method is falsified, **every claim it produced needs re-derivation — not the subset someone
happened to name.** I was told the committed `DISCON*.IN` files are outputs, wrote that down, listed
two affected claims, and left a third standing because nobody mentioned it. The third was the one I
had promoted to "read third" in the handoff index.

This is the tenth correction of mine in the run and the first full retraction. The pattern across all
ten is one thing: **I keep treating a specific correction as bounded when it falsifies a class.** The
`event`-vs-`phase` key, the scoped-out rebuild window, the file-location proxy for KGen exposure, and
now this — each time the disproof was available and each time I re-checked only what I was pointed
at.

Concretely, for the rest of this audit: **any claim of mine that reads a value out of a generated
artifact is suspect until re-derived from the generator.** That is `DISCON*.IN`, `baseline_arrays`,
anything under `kernel/`, and the per-function CSV.

Three claims from one unsound method; two re-derived when flagged, one left
standing for four hours because it was not named. The auditor's failure mode is not making things up
— it is under-propagating a correction he has already accepted and written down.

## References

- Dev note `202608080204` — retracted; correction header applied
- Dev note `202608080128` — the `SS_Mode` claim corrected; the bridge finding stands
- Dev note `202608080058` — confirmed on authoritative evidence
- Dev note `202608080346` — where I recorded the method error and under-applied it
