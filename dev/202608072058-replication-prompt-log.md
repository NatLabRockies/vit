# Prompt Log: Every Instruction Given to the Replication Agent

**Date:** 2026-08-07
**Status:** in-progress — live log, appended as the run proceeds
**Scope:** ROSCO replication session steering

## Placement

Same as the other replication notes: the OpenFAST workspace, outside the replication's container
mount and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout
until the run has ended.**

## Why this exists

The replication agent runs in its own session. The author is its only conduit — every instruction
it receives passes through one person, who relays from an audit session that watches the telemetry
and the repositories read-only. So this log is the **complete steering bandwidth of the run**.

That makes it evidence rather than bookkeeping. §V argues for a human–agent–tool arrangement in
which the human adjudicates and the agent executes. A full record of how many interventions a
fourteen-function migration required, and what kind, is a measurement of that arrangement — the
kind of thing the OpenAI field report describes qualitatively and nobody quantifies.

**Completeness caveat.** This records prompts *drafted in the audit session for relay*. Several
were relayed verbatim; some the author sent in their own words, and that exact text is not
captured here. Those are marked. Interventions are listed in order.

---

## 1. Opening prompt

Sent at session start, cwd `vit_translation/ROSCO-replication`, Opus 5, high effort.

```
You are running the ROSCO replication. This session's transcript is the measurement — it is
the data, not a record of the work.

Start by reading REPLICATION_RUNBOOK.md in this directory. It is the procedure. Follow it.
CLAUDE.md here explains why the parent CLAUDE.md, which describes the original completed
campaign, is background and not a plan.

First, confirm your memories loaded: you should have ten, indexed in MEMORY.md, including
"Don't touch the original ROSCO" and "One function at a time." If MEMORY.md is empty or
absent, stop and tell me — the memory directory key was inferred and may be wrong.

Then begin Phase 2, the codebase analysis. Bracket it with telemetry as the runbook shows.
It is done when analysis/replication/plan.json and boundary.md exist and are committed:
a dependency-ordered function list, leaves first, with a proposed verification method per
function, derived from the inventory and the source rather than from the original campaign.

Three things that make the data worthless if you get them wrong. One function at a time,
no interleaving. Never build or run in /workspace/ROSCO. Never git push.

vit verify and vit integrate have not been exercised in this tree — setup stopped short so
the first function's cost is measured rather than spent. If either breaks on function one,
that is expected and cheap; investigate it, fix it, and record it in DECISIONS.md.

Record failures as data. A function you cannot complete is a result this project has never
had. Work at whatever pace the verification allows; I will tell you when to stop.
```

**Outcome.** Memories confirmed (9 files plus index). Phase 2 completed in 16 minutes; found and
fixed `fortran_inventory.py` silently dropping `Controllers.f90` on a cpp line-splice, taking the
inventory from 70 to 84 procedures. Rejected the tool's call graph as unusable for ordering
(case-sensitive, no generic resolution) and built its own. Phase 3 in 2.5 minutes.

---

## 2. Resume after the first joiner fix

```
Resume with wrap_180. Before you start it, git pull in /workspace/vit — I fixed two joiner bugs at
the boundary (06811f0): phase spans read phase instead of failed, and verify_runs/integrate_runs
now record raw invocations alongside attempts-to-first-pass, so a rerun-after-success is
distinguishable from a retry. Your reading of integrate_attempts: 1 was right; the table just
couldn't show it. 696 tests pass.

One thing worth folding in: all the unattributed tokens land in the gaps between spans — the
commit-and-report at each transition, about 4 records per boundary. Step 8's commit is part of the
cycle per the runbook, so consider bracketing it into the function's span. Your call, it's your
file.
```

**Outcome.** The agent corrected the instruction: `/workspace/vit` is a bind mount of the same
checkout, so there was nothing to pull. It then investigated the joiner's geometry before acting
on the bracketing suggestion, and confirmed the diagnosis about boundary tokens.

**Audit-session error recorded:** the `git pull` instruction was wrong.

---

## 3. Boundary bracketing

```
Bracket the commit as planned, but widen it to __boundary__ covering the whole inter-function
turn — commit, findings, report. All the unattributed tokens are there and it just crossed 10.1%,
which is structural rather than sloppy: routine boundaries run ~5–6k against ~47k per function, so
the threshold fails by construction. Naming the bucket turns a warning light into a category.
Then carry on with wrap_180.
```

**Superseded before it mattered.** The author pushed back — *"if unattributed just means boundary,
can't we just post-process it? Or, rather, doesn't that 'fix' just amount to a rename?"* — which
was correct. The bracket was ceremony; the joiner already knows both surrounding spans and can
derive it. Implemented as post-processing in `90a496d` instead, with the 10% threshold removed
rather than adjusted. See dev note `202608071913`.

---

## 4. Relayed in the author's own words (text not captured)

Several interventions were relayed by the author directly. Their content, from the agent's
responses:

- **Scenario 13 still dead.** After the open-loop repair, the audit found a fourth dead scenario
  with a different cause (`VS_FBP` and `VS_ConstPower` mutually exclusive) that predates both
  campaigns. Dev note `202608072000`.
- **`GetRoot` hazards.** That the aliasing in `CALL GetRoot(RootName, RootName)` is benign because
  every assignment writes a prefix of the source, and that the real risk is Fortran's blank-padding
  to the destination's full declared length.
- **The seven-fixes tally**, and that the `vit translate` overwrite warning in `CLAUDE.md` and the
  runbook documented a bug as if it were design.

**One decision was made through the agent's own AskUserQuestion**, not through this channel: given
three degenerate gate scenarios, the author chose *"Copy files, regenerate baselines."* The agent
then added the step the audit session's recommendation had omitted — reverting to a pure-Fortran
build first, without which the regenerated baselines would have enshrined eight C++ translations
as their own ground truth.

**Audit-session error recorded:** the repair instruction omitted the pure-Fortran revert.

---

## 5. Pre-compaction readiness

```
Before we compact: make sure nothing you'd need to continue lives only in your context.

Check and fix as needed:

1. Working tree clean, everything committed — both this repo and /workspace/vit and /workspace/KGen.
   Two KGen fixes and several VIT fixes were made during this run; confirm none are sitting
   uncommitted.

2. DECISIONS.md carries what a fresh session couldn't reconstruct: which functions are
   contaminated for token attribution and why (unwrap), which cycles were infrastructure rather
   than translation, the extract-scenario change point, and any deviation still unresolved.

3. The runbook reflects reality after the seven tool fixes. In particular the vit translate
   overwrite warning is now wrong — badd9f8 fixed the inverted guard, so re-running after
   implementing is safe and refreshing a stale scaffold works. If the runbook still says
   otherwise, correct it.

4. Regenerate analysis/replication/per_function.csv and commit it, so the dataset to date is on
   disk rather than reproducible-in-principle.

5. Write your current position at the top of DECISIONS.md or in a short STATUS note: last function
   completed, next function, and anything in flight.

Then report what you fixed, and compact.

After compacting, re-read REPLICATION_RUNBOOK.md before resuming — do not work from memory of it.
```

**Outcome.** Found one uncommitted fix (KGen `PUBLIC`, now `5ac811f`) that a previous commit
message had already referred to as committed. Added an explicit usable/not-usable attribution
ledger to `DECISIONS.md`. Corrected the runbook's overwrite warning. Committed `per_function.csv`
and — unprompted, and a real gap — snapshotted the gitignored cycle log to
`analysis/replication/cycle_log_snapshot.jsonl`, without which the primary measurement record
existed in exactly one untracked place. All three repositories verified clean; 14 `_c` symbols in
the library matching 14 integrated functions.

---

## 6. Post-compaction resume

```
Resuming the ROSCO replication after compaction.

Re-read REPLICATION_RUNBOOK.md before doing anything — you committed to that rather than working
from memory of it. Then read the STATUS block at the top of DECISIONS.md for your position.

You are at 14 of 78, next is #15 interp1d. Continue the cycle in plan order.

Do not re-derive what is already recorded. DECISIONS.md carries the attribution ledger (which
functions are usable in a median and why), the extract-scenario change point, and the seven tool
fixes with commit IDs. If something in the toolchain looks broken, check that list before
investigating fresh — several things that would look like new bugs were fixed during this run.

Same discipline: one function at a time, no interleaving, commit each one, append findings to
DECISIONS.md and lessons to TRANSLATION_TIPS.md as you go.

Report after interp1d and I'll tell you whether to keep going in plan order.
```

---

## What the log shows so far

Fourteen functions, 27/27 bit-identical throughout, first-pass verify 12/14. Six substantive
interventions, of which:

- **two were setup or continuity** (opening, post-compaction resume)
- **one was hygiene** (pre-compaction readiness)
- **three were technical guidance**, and the agent **corrected or improved all three**: the
  nonexistent `git pull`, the bracketing that should have been post-processing, and the repair
  instruction missing its pure-Fortran revert.

**No intervention was needed to correct a translation.** Every one concerned measurement
apparatus, tooling, or process. The agent found and fixed seven infrastructure defects across VIT
and KGen without being told any of them existed.

That asymmetry is the §V observation with numbers attached: the human channel adjudicated scope
and caught framing errors, the agent executed and self-corrected on technical detail, and the tool
supplied the gate that made both trustworthy. It is also, uncomfortably, a record of an audit
session being wrong three times in a row on specifics while being useful on direction.

## References

- Dev note `202608071634` — the replication plan
- Dev notes `202608071913`, `202608071945`, `202608072000`, `202608072004`, `202608072043` — the
  findings these prompts responded to
