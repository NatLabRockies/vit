# The Liveness Statistic Did Not Discriminate, and the Manifest Check Would Have Caught Both Setup Defects

**Date:** 2026-08-08
**Status:** completed — M2 of the outer loop; one correction, one strong validation
**Scope:** audit tooling; **correction to `202608080615`'s supporting statistic**

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The correction

`202608080615` established that campaign scenario 13 ran no controller. That finding stands, and it
rests on the right evidence: `CheckInputs` rejects `VS_FBP=1` with `VS_ConstPower=1`, sets
`aviFAIL = -1` on the first call, and every guarded block in `DISCON` is skipped for the whole run.

The note also offers a supporting statistic:

> Eleven of its thirteen channels were identically zero.

**That statistic does not discriminate.** Measured across both trees:

| | campaign scenario 13 (dead) | replication scenario 13 (repaired, live) |
|---|---|---|
| channels identically zero | **11 of 13** | **11 of 13** |

The same eleven, in both. They are the channels for subsystems the scenario does not enable — yaw,
flaps, cable control, structural control. The count reflects **which subsystems a scenario turns on,
not whether the scenario ran.**

This was going to become a check. `PLAN.md` §6.3 specified "fraction of channels that are identically
constant" as the liveness discriminator, on the strength of that sentence. A check built on it would
have reported both trees identically and found nothing — a check that cannot go red, shipped into the
apparatus whose entire purpose is catching checks that cannot go red.

Caught before implementation only because the data was inspected first.

## What actually discriminates

**Rarity against the ensemble.** For each channel, count the scenarios in which it is constant:

| campaign | live in | dead in |
|---|---:|---|
| `gen_power` | 26 of 27 | **{13} only** |
| `gen_speed` | 27 of 27 | — |
| `gen_torque` | 27 of 27 | — (2 distinct values in 13) |
| `bld_pitch` | 24 of 27 | {10, 13, 23} |

`gen_power` is dead in exactly one scenario out of 27. In the repaired replication it is live in all
33. That is the signal, and it is a lone outlier rather than a threshold.

`bld_pitch` is dead in three scenarios of **both** trees — a scenario family (fixed pitch), not a
defect. So the rule needs the rarity term or it produces nine false positives per tree:

- constant in **exactly one** scenario of ≥10 → **error**
- constant in ≤25% of scenarios → warning, "likely a family, unverified"
- constant in all or none → not reported

**It needs no target-specific knowledge of which channels ought to move**, which is what makes it
portable to a codebase nobody has read.

## The strong result: the manifest check would have produced P4 on day one

P4 — *copy and hash-verify; never re-implement from prose* — was inferred after the fact from two
instances. `202608080510` classified the setup artifacts and found that everything copied carried a
hash and was clean, and both artifacts re-implemented from prose carried a defect.

The M2 manifest check, pointed at the replication's **setup commit `bf25e35`**, names exactly those
two and nothing else:

```
[error] scripts/regress.sh@bf25e35
    claim:   declared to come from scripts/regress.sh in ROSCO
    reality: that source does not exist -- this artifact was written from a prose
             description, the class that produced both known setup defects

[error] rosco/controller/CMakeLists.txt@bf25e35
    claim:   intent=copy: byte-identical to rosco/controller/CMakeLists.txt
    reality: differs (dest 93e2491571935719, src c15a67bf519090ad)

[warning] Examples/vit_sim.py@bf25e35
    intent=derive, red_tested=false
```

Those are the gate that compared nothing for 38 functions, and the build flags that produced 95
mismatched channels. **Both named at setup time, before either defect existed as knowledge, by a
check that inspects provenance rather than behaviour.**

The third entry is the control: `vit_sim.py` legitimately differs — it grows as scenarios are added —
and is declared `derive`, so it warns only for lacking a red test rather than erroring. The check
distinguishes "differs because it was rewritten" from "differs because it evolved."

This is the clearest evidence so far that the loop's value is in the checks rather than the loop.
Ten minutes of work at setup, and it names the run's two most expensive defects.

## The bridge fix is closed, measured rather than asserted

The bridges check compares each generated bridge's Fortran declarations against the C++ header it is
compiled with, by argument name, canonicalised to (base type, pointerness):

| tree | mismatched arguments | files | kernels |
|---|---:|---:|---|
| campaign | 31 | 30 | 17 of 43 |
| replication | **0** | 0 | 0 |

The campaign figure is the known answer from `202608080128`, reproduced exactly by a type-aware
parser rather than the `grep` proxy that produced it.

The replication's **zero** is new information. The `LOGICAL` ↔ `INTEGER(C_INT)` root cause has been
found in five separate locations across this project, and every fix was recorded in a commit message.
This is the first evidence the fixes actually took **derived from the generated artifacts themselves**
rather than from the claims about them — which is the distinction the whole project is about.

## What M2 says about method

Three checks, three known answers reproduced, and the interesting outcomes were not the reproductions:

1. **A designed check was wrong and the data said so.** The discriminator was specified from a
   remembered statistic; ten minutes of measurement falsified it before a line was written. *Inspect
   the data before implementing the check that reads it.*
2. **A check validated a rule retroactively.** The manifest check confirms P4 was not overfitted to
   two anecdotes — it is mechanically detectable, at setup, from provenance alone.
3. **A check confirmed a fix.** Twenty-odd tool fixes are recorded in this project by commit message.
   Exactly one is now confirmed from the artifacts.

## References

- Dev note `202608080615` — **supporting statistic corrected here**; its finding stands
- Dev note `202608080510` — copied vs re-implemented; P4's origin
- Dev note `202608080128` — the 30 mismatched bridges, now reproduced by a type-aware check
- Dev note `202608080346`, `202608080404` — the two setup defects the manifest check names
- `translation-loop/` — `checks/liveness.py`, `checks/manifest.py`, `checks/bridges.py`;
  known answers in `tests/known_answers/claims.toml`
