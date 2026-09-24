# Prompt Log: Every Instruction the Replication Agent Received

**Date:** 2026-08-07 (extended 2026-08-08)
**Status:** in-progress — live log; extracted from the transcript, not reconstructed
**Scope:** ROSCO replication session steering

## Placement

Same as the other replication notes: the OpenFAST workspace, outside the replication's container
mount and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout
until the run has ended.**

## Provenance

**Extracted verbatim from the session transcript**, not reconstructed from what the audit session
drafted. An earlier version of this note was written from drafts and was wrong in three ways: it
listed a prompt that was never sent, it described technical guidance that never reached the agent,
and it undercounted how little steering the run actually took.

Extraction: `type: "user"`, non-`isMeta`, string content, from
`~/.claude/projects/<project-key>/`. That yields 13
records, of which 4 are slash-command wrappers or their stdout and 1 is the compaction summary.
Plus one `AskUserQuestion` answer, which arrives as a `tool_result` rather than a user message and
is easy to miss.

## The complete record

Session span **3.1 hours**, 14 functions integrated.

| # | Time (UTC) | Kind | Content |
|---|---|---|---|
| 1 | 21:55:31 | command | `/effort` → high |
| 2 | 21:56:44 | **prompt** | Opening — read the runbook, confirm memories, begin Phase 2 |
| 3 | 22:43:02 | **prompt** | Resume with `wrap_180`; joiner fixes; boundary-bracketing suggestion |
| 4 | 23:18:19 | continuation | `Continue` |
| 5 | 23:45:41 | continuation | `Continue` |
| 6 | 23:51:37 | **decision** | `AskUserQuestion` answer: "Copy files, regenerate baselines (Recommended)" |
| 7 | 00:02:48 | continuation | `continue` |
| 8 | 00:50:55 | **prompt** | Pre-compaction readiness checklist |
| 9 | 00:56:25 | command | `/compact` |
| 10 | 00:59:12 | **prompt** | Post-compaction resume |

**Four substantive prompts. Three bare continuations. One multiple-choice answer.**

That is **one human turn per several dozen agent requests.**

## What was not sent

The audit session produced a great deal of technical analysis during the run. **None of it reached
the agent.** Specifically not sent:

- that scenario 13 was a fourth dead scenario with a different cause, predating both campaigns
- the `GetRoot` aliasing analysis, and that the real hazard was Fortran's blank-padding semantics
- the seven-fixes tally and its two categories
- the `LOGICAL` mapping regression traced to commit `aafe272`
- the observation that `unwrap`'s tokens were recoverable under innermost-wins attribution

A boundary-bracketing prompt was drafted and **never sent** — the author questioned it instead
(*"doesn't that 'fix' just amount to a rename?"*), and it became post-processing in the joiner.
The agent independently decided to bracket the commit, from the joiner's behaviour rather than
from instruction.

The audit channel's actual function was to inform **the author**, who used it to decide what not
to send.

## The four prompts, verbatim

### 1. Opening — 2026-08-07 21:56:44

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

### 2. Resume — 2026-08-07 22:43:02

```
Resume with wrap_180. Before you start it, git pull in /workspace/vit — I fixed two joiner bugs at
the boundary (7cafd79): phase spans read phase instead of failed, and verify_runs/integrate_runs
now record raw invocations alongside attempts-to-first-pass, so a rerun-after-success is
distinguishable from a retry. Your reading of integrate_attempts: 1 was right; the table just
couldn't show it. 696 tests pass.

One thing worth folding in: all the unattributed tokens land in the gaps between spans — the
commit-and-report at each transition, about 4 records per boundary. Step 8's commit is part of the
cycle per the runbook, so consider bracketing it into the function's span. Your call, it's your
file.
```

The `git pull` instruction was wrong — `/workspace/vit` is a bind mount of the same checkout. The
agent corrected it.

### 3. Pre-compaction readiness — 2026-08-08 00:50:55

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
   overwrite warning is now wrong — ab78884 fixed the inverted guard, so re-running after
   implementing is safe and refreshing a stale scaffold works. If the runbook still says
   otherwise, correct it.

4. Regenerate analysis/replication/per_function.csv and commit it, so the dataset to date is on
   disk rather than reproducible-in-principle.

5. Write your current position at the top of DECISIONS.md or in a short STATUS note: last function
   completed, next function, and anything in flight.

Then report what you fixed, and compact.

After compacting, re-read REPLICATION_RUNBOOK.md before resuming — do not work from memory of it.
```

Found one uncommitted KGen fix that a prior commit message had already described as committed.
Snapshotted the gitignored cycle log unprompted, closing a real durability gap.

### 4. Post-compaction resume — 2026-08-08 00:59:12

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

## The only message with technical content, and what the agent took from it

Prompt 2 is the sole instruction in the run carrying anything technical. It had three components.
**The agent used none of them as guidance.** Verbatim from the transcript, in order:

**"git pull in /workspace/vit"** — wrong, and corrected:

> `7cafd79` is already at HEAD locally — `/workspace/vit` is the same checkout via bind mount, so
> your fix is already here. No pull needed; `origin` is behind, not ahead. Verifying the change is
> live.

**A description of the two joiner fixes** — re-derived from the data rather than accepted:

> The fix is visible: `status: phase` for the brackets, and `integrate_runs: 2` against
> `integrate_attempts: 1` — the rerun is now distinguishable.

**The boundary-bracketing suggestion** — explicitly deferred until the mechanism was understood:

> On the bracketing question — before deciding, I need to know how the joiner attributes tokens and
> whether a wrapping span would trip its overlap check.

It then read the joiner, worked out the span geometry itself, confirmed the diagnosis against its
own measurement — *"Unattributed rose purely from the boundary turns since the last join,
which confirms your diagnosis"* — and only then acted.

**Net contribution of the message to the work: zero, arguably negative.** Correcting the `git pull`
cost a turn.

The behaviour worth naming is not that the agent proceeded without guidance. It is that it
**rejected** an instruction and **verified before adopting** a suggestion. An agent that ran the
`git pull` would have burned a turn on a no-op; one that adopted the bracketing uncritically would
have built ceremony the author later identified as a rename (see §3 above). Both times the check
that caught it was the agent's.

So the steering record reduces to **three procedural prompts** — start, prepare-to-compact,
resume-after-compact — three `Continue`s, and one authorization the agent had already framed and
recommended.

## The one technical decision, and how it was framed

At 23:51:29 the agent asked, and at 23:51:37 — **eight seconds later** — the author answered:

> Three gate scenarios (10, 14, 24) are degenerate because their open-loop input files were never
> carried into this tree. How should I proceed?
> → **"Copy files, regenerate baselines (Recommended)"**

The agent had already diagnosed the fault, scoped it, enumerated the options and marked its own
recommendation. The human contribution was authorization. And the agent then added the step the
audit session's parallel recommendation had *omitted* — reverting to a pure-Fortran build first,
without which the regenerated baselines would have enshrined eight C++ translations as their own
ground truth.

## What the log shows

From **three procedural prompts, three bare continuations, and one authorization it had itself
framed and recommended**, the agent:

- integrated **23 of 78 functions** (14 by compaction, continuing since), all 27/27 bit-identical
- found and fixed **nine VIT defects and two KGen defects**, none of which it was told about
- built a **new VIT capability** — CHARACTER-valued function results — that neither campaign
  possessed
- discovered that **four gate scenarios had been passing green while computing nothing**, one of
  them dead since the original campaign, and repaired three
- found that **`vit verify` had no floor**: a kernel comparing zero variables printed PASSED
- traced the `LOGICAL` mapping failure to an AeroDyn-era commit
- found an **inverted guard the project had documented as intended behaviour** in `CLAUDE.md`
- refused to relabel a nested span when doing so would have flattered a function's cost, refused to
  let `1/1 PASSED` stand beside `20/20`, and refused to count a function the gate cannot verify —
  three separate occasions of declining to flatter its own measurement

**Not one intervention concerned a translation.** Every prompt was setup, continuity, or hygiene.
The agent wrote twenty-three C++ implementations and none required correction from the human
channel; the gates handled that.

## The division of labour

This is a division in which the human adjudicates and the agent executes under a tool that makes
both trustworthy, measured rather than described. The division here was sharp:

- **the tool** supplied every correctness signal — 20/20 replay, 27/27 gate, and it is what caught
  the translation errors
- **the agent** did the technical work, including all eleven repairs to the tool itself, and
  rejected the single instruction that carried technical content
- **the human** authorized scope once, set up and closed down, and typed `Continue` three times

One human turn per several dozen agent requests, on production scientific code, gated bit-identically
throughout.

The honest counterweight, and it belongs beside the ratio: this worked because the gate is
bit-identical and cheap. Every one of those requests ran against a suite that would fail
loudly on a wrong answer in 87 seconds. **The ratio is a property of the verification, not of the
agent.** Remove the gate and the same autonomy would be indistinguishable from unsupervised
generation.

## References

- Dev note `202608071634` — the replication plan
- Dev notes `202608071913`, `202608071945`, `202608072000`, `202608072004`, `202608072043` — the
  findings the audit session produced, almost none of which were relayed
