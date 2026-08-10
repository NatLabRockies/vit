# The Replication Is Verifying Code the Campaign Had to Hand-Write

**Date:** 2026-08-07
**Status:** in-progress — observed at function #18–19, run continuing
**Scope:** replication plan framing; ROSCO Track A/Track B boundary

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## What happened

Reaching `Int2LStr`, the replication found that **CHARACTER-valued function results are not
supported anywhere in the VIT pipeline** — not in the type map, not in the scaffold generator, not
in the Fortran wrapper. Not a bug to patch; a feature that does not exist. The session began
implementing it.

Checked read-only, on the original campaign's tree:

- `Int2LStr` is **absent from the campaign's `vit.yaml`** and has no kernel fixture
- **No CHARACTER-valued function was ever translated by either campaign**, which is why no support
  exists
- Seven `.cpp` files exist in the campaign's `translations/` with **no `vit.yaml` entry at all**:
  `discon`, `ext_control`, `zmq_interface`, `debug_logger`, `checkinputs`,
  `readcontrolparameterfilesub`, `readcpfile`

Those seven are the campaign's **Track B**, recorded in the ROSCO-workspace memory
`project_phase10_idiomatic` (2026-04-01):

> **Decision:** KGen where it works, idiomatic C++ where it doesn't (config loading, restart,
> debug, platform modules). No new VIT verification modes — simulation-level comparison is
> sufficient.
>
> **Why:** Engineers said "give AI the math, let it build idiomatic C++ rather than mirroring
> Fortran."

## The reframing

The Track B decision reads, in the record, as a design choice about what deserves mirror
translation. It was also a **capability boundary**. VIT could not put string-formatting and file
I/O through the verified pipeline, and the team was building VIT while using it, so the sensible
move was to route around the gap and gate those functions at the simulation layer only.

**The replication is not routing around it.** It is building the missing capability and pushing
those functions through bit-identical verification. That is a stronger result than the campaign
produced: more of the codebase under the strong gate, less accepted on simulation-only evidence.

Two things changed since April, and they cannot be separated:

1. **VIT is more mature.** Fourteen months of AeroDyn work, plus seven fixes and one new feature
   during this run.
2. **The model changed.** The campaign ran Opus 4.6, held fixed deliberately (dev note
   `202608071048`). This run is Opus 5.

The plan already flags the model change as a confound for *cost* (§1 caveat 1–2). It is equally a
confound for *capability*, and that has not been stated anywhere.

## The observation that matters most

The replication was designed to measure adoption cost **with a finished tool** — plan §1 caveat 3,
already falsified by seven fixes (dev note `202608072043`). This goes further.

**The co-development pattern did not end with the campaign. It is happening again, right now, on a
tool that was supposed to be complete.** Seven defects fixed and a feature built, in nineteen
functions, by the same human–agent–tool arrangement that built it — with four prompts and three
bare continuations from the human (dev note `202608072058`).

That is not a failure of the replication's design. It is the co-development pattern
reproducing itself under conditions chosen to exclude it. The tool is never finished; it is
finished *for the shape of the codebase it last faced*. Point it somewhere new and the arrangement
that built it starts up again.

**A tool that co-develops is not a phase a project passes through. It is the steady state.**

## Consequences for the measurement

**The replication is not a replication past the Track A boundary.** The campaign put 49 procedures
through VIT and hand-wrote seven; the plan puts all 78 through it. Past that line the two are doing
different work, and the cost series must not be pooled across it:

- functions beyond Track A will systematically cost more, each potentially needing new
  infrastructure — `Int2LStr` is the first
- their cost is not comparable to the campaign's in either direction, because the campaign did not
  do this work through VIT at all
- this also resolves the deferred **P2**: 49 in `vit.yaml`, 52 in the project's own count, 78 in the replication
  plan. The gap is Track B plus the entry point.

**Mark the boundary in the dataset before the numbers are pooled.** The clean-cycle median (n=11,
393 s) is a Track A figure.

## Generality

The generality claim gets a second dimension. Dev note `202608072043` says carrying the
tool forward silently broke its first codebase in six ways. This says the opposite thing at the
same time: carrying it forward also made it capable of work the first campaign could not attempt.
Both are consequences of the same fact — nothing re-runs a finished codebase after the tool
changes, so neither the losses nor the gains are visible until you do.

**Honest counterweight**, and it belongs beside the claim: two variables moved. A better tool and a
better model. Nothing here separates them, and this run cannot.

## References

- Dev note `202608072043` — seven fixes; §1 caveat 3 falsified
- Dev note `202608072058` — the steering record; four prompts, three continuations
- Dev note `202608071048` — model held at Opus 4.6 for both campaigns
- ROSCO-workspace memory `project_phase10_idiomatic` — the Track B decision, 2026-04-01
