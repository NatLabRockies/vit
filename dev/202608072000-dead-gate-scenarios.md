# Four Gate Scenarios That Passed Green While Computing Nothing

**Date:** 2026-08-07
**Status:** in-progress — three repaired in the replication; scenario 13 open and predates both campaigns
**Scope:** ROSCO 27-scenario gate, replication carry-over

## Placement

Same as dev notes `202608071913` and `202608071945`: the OpenFAST workspace, because the
ROSCO-workspace checkout is bind-mounted into the replication's container and is the repository
that session commits to. **Do not merge into that checkout until the run has ended.**

## Summary

Of the 27 scenarios in ROSCO's simulation gate, four produced no controller output at all. In each
the controller errored during `SetParameters`, `aviFAIL` went negative, and `DISCON` skipped every
control calculation. The scenarios ran, wrote `.npz` files, compared bit-identical against
baselines that recorded the same nothing, and counted toward "27/27".

Two independent causes:

| Scenarios | Cause | Origin |
|---|---|---|
| 10, 14, 24 | Open-loop input `.dat` files absent from the replication tree | Replication carry-over gap |
| 13 | `VS_FBP=1` and `VS_ConstPower` both enabled; ROSCO's own validator rejects the pair | **The original campaign's harness. Pre-existing.** |

Live channels across the suite went from 148 of 351 to 161 of 351 after the first repair.
Scenario 13 remains at one live channel of thirteen.

## Cause 1: the carry-over gap (repaired)

`Examples/vit_sim.py` was carried into the replication tree as the verification apparatus. It
reads three open-loop input files that were **not** carried, because they do not exist at baseline
commit `974290e` and were not recognized as harness dependencies:

```
Examples/example_inputs/OL_Mode1_Input.dat
Examples/example_inputs/OL_Mode2_Input.dat
Examples/example_inputs/OL_Mode1_CC_StC_Input.dat
```

The replication session copied them, reverted the controller to pure Fortran, regenerated
baselines, and re-gated all eight completed functions — which passed, now against genuinely
stronger evidence. Exactly the three affected scenarios changed; the other 24 were byte-identical,
so the repair was surgical.

**The pure-Fortran revert was the critical step and I got it wrong.** My recommendation read
"copy the files, regenerate baselines, re-run the gate on the current integrated tree." Followed
literally, that regenerates the reference from a build containing eight C++ translations, which
enshrines the translations as ground truth and destroys the three-build chain. The replication
session caught it unprompted and reverted first. Recorded because the error is easy to repeat and
its consequence is silent.

### This is the third instance of one pattern

`rosco_constants.h` (dev note `202608071913`), now three `.dat` files. **The carried harness
references artifacts that were not carried, and every gap fails green.** The carry-over set was
chosen by what looked like harness code rather than derived from what `vit_sim.py` actually reads.
A dependency closure would have caught all four files at setup.

### Outstanding: the new baselines encode unverified source changes

The Fortran now differs from `974290e` in three semantic ways beyond line endings:

- `ROSCO_Types.f90` — `ErrMsg`: `CHARACTER(:), ALLOCATABLE` → `CHARACTER(1024)`
- `ExtControl.f90` — `avcMSG = TRANSFER(C_NULL_CHAR, avcMSG)` → `avcMSG = C_NULL_CHAR`
- `Controllers.f90`, `Filters.f90` — the two Flp_Mode uninitialized-variable fixes

The first two were gated at 27/27 **against the old baselines, where scenarios 10, 13, 14 and 24
were dead** — and those are precisely the open-loop paths now alive. So the repaired baselines
bake in whatever those changes do on paths that were never checked, and the result becomes ground
truth by definition rather than by verification.

This is what the three-build chain exists to prevent. The replication has established
*modified ≡ C++* throughout; it has never established *upstream ≡ modified* on the OL scenarios.

**The check, if it is worth 20 seconds:** build clean `974290e` plus the three `.dat` files only,
run scenarios 10, 14, 24 against the repaired baselines. Match confirms the source changes are
neutral there. Difference means a change that passed on a weaker suite does not hold on a stronger
one, which would be worth more than the scenarios. Scenario 4 will still segfault on pure upstream
(the Flp bug) and is irrelevant to this check.

## Cause 2: scenario 13 — a defect in the campaign's own harness

Not a carry-over gap. `vit_sim.py` was carried verbatim, and the fault is in the file:

```
ROSCO:SetParameters:CheckInputs:Fixed blade pitch control (VS_FBP) and
constant power torque control (VS_ConstPower) cannot both be enabled.
```

Scenario 13 sets `VS_FBP=1` to exercise fixed-blade-pitch power-overspeed mode, but leaves
`VS_ConstPower` enabled. The docstring even records the constraint — *"Requires PC_ControlMode=0
(mutual exclusion with VS_FBP>0)"* — and the configuration violates a different clause of the same
rule.

**Verified against the original campaign's committed `baseline_arrays`: scenario 13 is dead there
too, and it is the only dead scenario in that tree.** Scenarios 10, 14 and 24 are alive there, as
expected since that tree has the `.dat` files. So the replication reproduced the original faithfully,
and this defect predates both campaigns.

### Consequence for the ROSCO accounting

The ROSCO campaign gated 9 functions plus the entry point on "the 27-scenario
simulation suite." One of those 27 has computed nothing since it was written. **The effective
suite is 26**, and the VS_FBP path it was meant to exercise — the FBP lookup tables and the
`interp1d`-based torque reference — was never gated by simulation in either campaign.

This belongs with the `ExtController` / `UpdateZeroMQ` correction made earlier today. Both are
the same category: **the accounting claims coverage the gate does not deliver.** Two instances
found in one day, both by looking rather than assuming.

It should **not** affect the 2.56 million nontrivial-values figure, since a dead scenario
contributes constants and those are already excluded as trivially zero. Verify that rather than
assuming it.

### Recommendation: record, do not repair

Repairing scenario 13 would *create* coverage neither campaign had, mid-run, requiring another
pure-Fortran regeneration and another before/after comparison. No translated function has touched
the VS_FBP path yet. Recording it is sufficient; the durable fix is the assertion below.

## The durable fix

Add a liveness assertion to `scripts/regress.sh`: **fail if any scenario has fewer than two
varying channels.**

Ten lines. It would have caught all four scenarios the first time baselines were generated, in
both campaigns. Two independent causes, four scenarios, every one of them silent and green, one of
them undetected for the entire original campaign.

It is the same guard as `set -o pipefail` on the rebuild and the `libdiscon` path check, and it is
the third time this class — *the apparatus reports success while measuring nothing* — has bitten
this project.

## References

- Dev note `202608071913` — telemetry accounting; the `rosco_constants.h` carry-over gap
- Dev note `202608071945` — the LOGICAL mapping regression, also found only by re-running ROSCO
- Dev note `202608071634` — the replication plan; §1 caveat 4 lists what was carried
