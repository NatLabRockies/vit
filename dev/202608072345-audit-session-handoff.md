# Audit Session Handoff

**Date:** 2026-08-07
**Status:** in-progress — written before compacting the audit session; the replication continues
**Scope:** how to resume auditing the ROSCO replication without interfering with it

## Read this first

You are the **audit channel** for a ROSCO replication running in a separate Claude Code session.
You do not run the replication. You watch it read-only, verify its claims against the original
campaign it is forbidden to see, and write findings here.

**The author is the only conduit between the two sessions.** Nothing you write reaches the
replication unless the author relays it. That is deliberate — see `202608072058`.

## The two sessions

| | Replication | This one (audit) |
|---|---|---|
| Directory | `vit_translation/ROSCO-replication` | `vit_translation_openfast/vit` |
| Project key | `<project-key>` | `<project-key>` |
| Container | `vit-dev` (mounts `vit_translation` as `/workspace`) | `vit-dev-openfast` |
| Role | does the work | verifies, records, does not instruct |

## Hard rules — violating these corrupts the measurement

1. **Never write anything under `vit_translation/`.** That whole tree is bind-mounted
   into the replication's container as `/workspace`, and the replication commits to
   `ROSCO-replication`, `vit` and `KGen` there. Reading is fine. Writing is not.
2. **All notes go in `vit_translation_openfast/vit/dev/`**, which is *not* mounted in
   `vit-dev` — verified. They critique the measured session by name; a measured subject that reads
   the critique may optimise against it.
3. **Do not merge this workspace's `vit` checkout into the ROSCO-workspace one until the run ends.**
   Same repository, converges on push/pull. That convergence must wait.
4. **Never run a `vit` command with cwd inside the replication tree**, and never build or run in
   `/workspace/ROSCO` (the original campaign) or `/workspace/ROSCO-replication`. A `vit` command
   there writes to the replication's cycle log and fabricates spans.
5. **Do not `git push`.** Author restriction, still standing.

## How to audit, read-only

Token/wall-clock join (pure Python, both inputs on the Mac, no container):

```bash
cd vit_translation/vit
python3 scripts/join_telemetry.py \
  --cycle-log vit_translation/ROSCO-replication/.vit/cycle_log.jsonl \
  --transcripts ~/.claude/projects/<project-key>/ \
  --dispositions vit_translation/ROSCO-replication/analysis/replication/dispositions.json \
  --out <scratchpad>/per_function.csv
```

**Always write the CSV to the scratchpad, never into the replication tree.** Pass
`--dispositions` or `AddToList` and `ReadEmptyLine` derive as `failed`.

To audit an *in-flight* span, snapshot the cycle log and append a synthetic `end` at now — an
unclosed start otherwise yields a zero-width span that attributes nothing.

## Backup protocol

A local backup script, kept outside the workspace root on purpose, captures all four repos as `git bundle`s (verified,
restore-tested), both transcript sets, a deduplicated usage-record extract (~194 KB vs 5.5 MB), and
the gitignored cycle log. Symlinked at `latest/`. **Run it at every checkpoint** — the transcripts
are the only copy of the token data and `ROSCO-replication` has no upstream at all.

Last: `20260807-234213`, 91 MB, four bundles verified.

## State at handoff

Replication: **27 attempted — 25 translated, 1 out of scope (`AddToList`), 1 deferred
(`ReadEmptyLine`), 0 blocked, 0 failed.** All 27/27 bit-identical. Next is #28 `GetWords`.
Heads: `ROSCO-replication 7f34de5`, `vit cc74922`, `KGen 5ac811f`.

Clean-cycle wall clock **n=16, median 400 s**, against 471 s across all 25
integrated — the gap is tooling gaps, and a mature VIT still hit a novel gap in 9 of
25 functions.


Fifteen tool fixes so far (VIT + KGen), three of them fails-green defects.

**Remaining scope**, recomputed from call sites: of 51 remaining, 13 unit-I/O deferred, 10
KGen-exposed, 13 alloc-trait, union 22 — **29 unobstructed**.

**Open decision, the author's:** whether to redirect from plan order to the control layer
(`Controllers.f90`, `ControllerBlocks.f90`). Every clean cycle so far is a leaf; the control layer
is the only place that answers whether the marginal cost holds for functions with *translated
callees*, and where the lost-update hazard fires. Not blockage-driven — I overstated that and was
corrected.

## Deferred until the run ends — do NOT do these now

Each of these touches the replication's tree or its tooling. They are safe the moment the run
finishes and unsafe before it.

1. **Add `tok_cache_read` and a derived cost column to `join_telemetry.py`'s summary output.** The
   summary currently reports `attributed` / `session_overhead` / `boundary` in
   `tok_total_uncached`, which is the minority of cost that is *not* cache read. The per-function CSV
   already carries the column; only the summary omits it. Leaf script, retroactive — the token data
   is in the transcripts and the backups. Blocked only because the script lives at
   `vit_translation/vit/scripts/`, inside the measured tree. See `202608072355`.
2. Add the fifth extraction-empty cause to `CLAUDE.md`'s pitfalls list: pragma correct, call site
   real, enclosing subroutine never entered because a generic interface routes every call to a
   sibling. Diagnostic — check coverage on the *enclosing* procedure. See `202608080003`.
3. Add to the runbook: when a function's call sites differ in the *shape* of what they pass, select
   by argument variety rather than hit count. Coverage-count selection picks the most stereotyped
   site. See `202608080003` §6.
4. Re-verify AeroDyn's 17 baselines against the 15 changed tool commits.
5. Amend the `kgen-bug-attribution` memory and `CLAUDE.md` with the three genuine KGen exceptions.
6. Update `CLAUDE.md`'s stale `vit translate` overwrite warning.
7. Merge the two VIT checkouts.
8. Re-integrate `sigma`/`unwrap` with copy-back. Low priority, diagnostic fidelity only.

## The notes, and where to start


Then, roughly by importance:

| Note | What it holds |
|---|---|
| `202608072250` | A passing kernel over a non-discriminating input — not fixable in any tool |
| `202608080003` | `GetWords`: generic interfaces hide dead code; call-site choice fixes fixture shape; the mirror constraint's cost set against one line of `std::istringstream` |
| `202608080010` | `Read_OL_Input`: the campaign solved allocate-on-return by hand and never generalised it; sixth fails-green shape; recommendation to translate #30/#31 under a fourth disposition |
| `202608072058` | The steering record. Three procedural prompts, three continuations, one authorization |
| `202608072043` | Seven→fifteen tool fixes; the tool was finished *for AeroDyn*, not finished |
| `202608072149` | Track A/B; the replication verifies code the campaign hand-wrote |
| `202608072233` | A kernel passed having compared nothing; the 22-of-43 check |
| `202608072214` | `NonDecreasing` — the escaping defect class arising naturally |
| `202608072329` | Three structurally different reasons the gate cannot speak |
| `202608072320` | `out_of_scope` ≠ `blocked`; **my setup omission** |
| `202608072334` | Leaf-first ordering is wrong for resource dependencies |
| `202608071913` | Telemetry accounting scope; wall clock is elapsed, not worked |
| `202608071634` | The replication plan; §1 caveat 3 falsified |

## What I have gotten wrong, so you calibrate

Four defects of mine caught by the replication session, not by me:

1. `git pull` in `/workspace/vit` — it is a bind mount of the same checkout
2. A baseline-repair instruction that omitted the pure-Fortran revert; followed literally it would
   have enshrined eight C++ translations as their own ground truth
3. `status_for` deriving `__gate_repair__` as `failed`, corrupting any failure rate
4. The KGen-blocked count: I inferred 22 from *file location* when the defect fires at *call
   sites*; the real number is 11, and the contradicting evidence was already in front of me

Plus a nesting rule in `202608071913` that the run disproved within hours, superseded by
`202608072004`.

**The pattern: every one was a confident claim from partial evidence.** Verify before asserting,
especially about the replication's own data — it checks, and it has been right every time.

## The one contribution the agent could not have made alone

`202608072320`. The replication's procedure authorised exactly one disposition — mirror-translate
and verify — because I withheld the campaign's Track B decision as answer key. Right for the plan,
wrong for the method space. It took a human noticing that the *procedure* was missing an option.
Everything else I have found required reading the campaign tree, which the agent is forbidden to
touch — work it was denied, not work it failed.

## References

- `202608071634` — the replication plan
