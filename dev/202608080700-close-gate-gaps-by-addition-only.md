# Close Gate Gaps by Addition Only — the Campaign Never Modified a Scenario

**Date:** 2026-08-08
**Status:** completed — #44; contains a recommendation on the open question the replication raised
**Scope:** gate construction, kernel evidence

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The question

The replication found three gate gaps, recorded each with a one-line fix in a new `STATUS.md`
section, and did not act:

> If you'd rather I close those gaps, say so — each is roughly a `__scenario13__`-sized job.

It also drew the distinction that decides the answer: repairing scenario 13 restored a scenario that
produced *nothing*; extending scenario 9's `tlen` would change a scenario that produces something
real, re-baselining the gate mid-measurement.

## The answer, from the campaign's own history

```
Add Scenario 11: AWC_Mode=1 (complex number method)
Add Scenarios 12-14: VS_ControlMode=1, VS_FBP=1, OL_Mode=1
Add Scenarios 15-18: AWC_Mode=2, Flp_Mode=3, WE_Mode=1, IPC 2P
Add Scenarios 19-23: remaining low-priority mode coverage
Add Scenarios 24-25, fix OL_CableControl/OL_StructControl allocation
Add Scenario 26: Flp_Mode=3 with synthetic rootMOOP for non-zero flap output
Add Scenario 27: 11-mode stress test with all synthetic inputs
Add Scenario 6 (IPC) for NotchFilterSlopes, extract with --scenario 4/6
Add Scenarios 7 and 8: synthetic inputs for under-exercised functions
```

**Nine commits, every one an addition.** The campaign grew its gate 9 → 27 *during* the campaign,
repeatedly, specifically to reach under-exercised functions — and never once modified an existing
scenario's parameters for coverage.

So the recommendation is not "close them" or "leave them." It is:

> **Close them by addition. Never by modification.**

- Adding a scenario touches no existing baseline. All 44 functions' joint bit-identity result stands
  unchanged, and the new coverage is strictly additive.
- Extending scenario 9's `tlen` from 300 to 400 re-baselines that scenario for every function
  already gated. The dataset's central correctness claim would then refer to a gate that no longer
  exists — the same failure I wrongly attributed to the campaign in `202608080204` and had to
  retract, except this time it would be real and self-inflicted.

**Growing the gate mid-run is replication, not deviation.** It is precisely what the campaign did,
nine times. What the campaign never did is the thing the replication was right to hesitate over.

One condition: `STATUS.md` should record the gate's composition per function, so that "passed the
27-scenario gate" and "passed the 29-scenario gate" remain distinguishable later. Without that, the
addition creates the same per-commit ambiguity the `b908585` re-gate already created.

## Why this matters more than three branches

`Shutdown` confirms that the kernel narrowness in `202608080640` is systematic, and its numbers are
the starkest yet: **five of eight compared outputs are constant across all 21 kernel cases** —
`sd_trigger`, `sd_stage`, `sd_maxpitchrate`, `sd_maxtorquerate`, `sd_stagestarttime`, all zero.

The cause is the `0:0:1-20` window, and here it is quantifiable. `SD_Time` is 250 s; `DT` is 0.025 s;
the capture window is invocations 1–20, i.e. **t = 0 to t ≈ 0.475 s**. The shutdown logic begins
**ten thousand timesteps after the last captured case.** So 21/21 attests to the filter chain and says
nothing whatever about the shutdown logic, which is the entire reason the function exists.

Two consecutive functions — `YawRateControl`, then `Shutdown` — both state machines, both with a pass
count that materially overstates what was checked. That is not a coincidence; **state machines are
exactly the shape the first-twenty window cannot see.**

The follow-up check the replication has made standard is the right instrument and costs nothing:

> count distinct values per field in `verify_fields.csv`. A field with one distinct value wasn't
> tested, whatever the pass count says.

That is the missing report from `202608080640` — the pass line cannot distinguish 21/21 on a varying
field from 21/21 on a constant one, and this counts it directly. It should be printed by
`kernel_verify` rather than run by hand.

## Scenario 9's off-by-one, verified exactly

```
'SD_Time': 250,  'SD_StageTime': '50 50',  dt = 0.025,  tlen = 300
```

`np.arange(0, 300, 0.025)` ends at **t = 299.975**. The stage advance fires at
`Time >= SD_Time + SD_StageTime(1)` = **300**. It misses by one timestep, so `maxStage` reaches 1 and
the advance, the stage-2 arm and everything downstream are dead.

`tlen = 400` fixes it — and is exactly the modification not to make. **A new scenario with
`tlen = 400` fixes it too, and costs nothing already banked.**

The scenario's own docstring says it was written to make "progress within the first 20 invocations,"
which is a second, independent artifact of the same window: the gate's scenarios were shaped partly
to serve kernel capture, and that shaping is why the late-time behaviour has no coverage.

## Recommendation to relay

Close all three gaps as new scenarios, at the point that fits the plan order rather than immediately:
`Shutdown`'s stage advance, the empty `Y_ControlMode`/`OL_Mode` intersection from #43, and
`Shutdown`'s method 2. Re-gate the affected already-integrated functions against the enlarged suite.
Record the gate composition per function in `STATUS.md`.

Do not touch `tlen`, `SD_Time`, or any parameter of an existing scenario.

## Accounting

44 attempted: **39 integrated, 2 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **100% first-pass**.

Observability is fine — a ×1.000001 probe moved five channels in scenario 9 — so the gap is purely
coverage, not comparison. Third probe-calibration data point after `202608080615` and `202608080640`.

Also recorded, and worth keeping for the upstream report: the four shutdown triggers are separate
`IF`s with first-match-wins priority (pitch < yaw < genspeed < time), so an `ELSE IF` rewrite would
silently change behaviour. And three functions use two conventions for the same `LPFilter` reset
argument — `LocalVar%restart` in some, a literal `.FALSE.` in others — with no comment explaining
either.

## Takeaways

The first-twenty window has its sharpest instance here: a shutdown controller whose shutdown
logic begins 10,000 timesteps after the last captured case, verified 21/21. And the cheap remedy —
distinct-value count per compared field — turns an unfalsifiable pass line into a falsifiable one.

Gate growth is part of the method, and the campaign's nine additive commits show the
discipline: add scenarios, never edit them. A verification suite that is edited in place cannot
support claims made against its earlier states.

## References

- Dev note `202608080640` — the `0:0:1-20` window; this is its starkest instance
- Dev note `202608080517` — the retraction; why re-baselining mid-run is the failure to avoid
- Dev note `202608080615` — scenario 13, repaired because it produced nothing at all
