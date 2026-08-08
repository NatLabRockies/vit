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

**~~Open decision, the author's:~~ CLOSED at #34.** The control-layer redirect is moot. Plan order
reached `ControllerBlocks.f90` at #32 and `Controllers.f90` at #34 unaided.

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
9. **Fix `join_telemetry.py` overlap handling** — a nested phase prices at zero and its cost lands on the enclosing function. Subtract it, or refuse to print a cost for a flagged row. See `202608080506`.
4. Re-verify AeroDyn's 17 baselines against the 15 changed tool commits.
5. Amend the `kgen-bug-attribution` memory and `CLAUDE.md` with the three genuine KGen exceptions.
6. Update `CLAUDE.md`'s stale `vit translate` overwrite warning.
7. Merge the two VIT checkouts.
8. Re-integrate `sigma`/`unwrap` with copy-back. Low priority, diagnostic fidelity only.

## The notes, and where to start

**Start with `202608080346` and then `202608080404`, in that order.** The replication's gate never
compared anything from the setup commit through #38 — `docker exec` without `-i` fed the comparison
an empty script, which exited 0 and printed `27/27 bit-identical`. When repaired it showed 95
mismatches, and those turned out to be **one missing line of CMake**: `CMAKE_CXX_FLAGS` never got
`-ffp-contract=off`, so g++ fused every `a*b + c` while the Fortran did not. Both defects trace to
the same setup commit, `bf25e35`. **HEAD is now bit-identical, 351 channels, 0 mismatched, all 38
functions.** "Every regress 27/27" is void as a per-commit claim; the translations themselves are
vindicated jointly at the tree level.

**Then `202608080058`** — the campaign shipped a false "kernel PASSED" stamp on `ExtController`, a
function that demonstrably diverges from its Fortran and that no gate scenario can execute.

Then, roughly by importance:

| Note | What it holds |
|---|---|
| `202608072250` | A passing kernel over a non-discriminating input — not fixable in any tool |
| `202608080003` | `GetWords`: generic interfaces hide dead code; call-site choice fixes fixture shape; the mirror constraint's cost set against one line of `std::istringstream` |
| `202608080010` | `Read_OL_Input`: the campaign solved allocate-on-return by hand and never generalised it; sixth fails-green shape; recommendation to translate #30/#31 under a fourth disposition |
| `202608080058` | **Read first.** The false `kernel PASSED` stamp on `ExtController`; the agent's refusal beat my instruction; plan order reaches the control layer at #32 without a redirect |
| `202608080128` | **Read second.** 30 campaign kernel bridges declare mismatched widths for the same argument, across 17 of 43 kernels; leaf-first left the whole callee-bridge path untested for 31 functions; third setup omission of mine |
| `202608080204` | **Read third.** The campaign's gate went 9→27 scenarios and silently lost `Flp_Mode=2` coverage; `PIIController` is verified in the campaign and unreachable in the gate that ships with it. Fourth setup omission: extending the gate was campaign practice the runbook never carried |
| `202608080218` | CLOSED, and wrong in its central claim — my sixth caught error, corrected at the head of the note. One gate run of 41 hit a stale library and was superseded before commit; all 36 final gates followed a good rebuild |
| `202608080640` | `invocation: 0:0:1-20` — every kernel in the project samples a function's *first* twenty calls, which unifies four earlier findings; plus the run's one disjoint-but-complete two-layer case |
| `202608080615` | Campaign scenario 13 runs no controller and prints "PASSED … exercised" — third unconditional success message; and a ×2 probe and a +1e-9 probe disagree, so a mutation probe reports its own shape |
| `202608080517` | **Retraction.** The gate never lost coverage — `DISCON*.IN` are generated outputs. `202608080204` withdrawn, `202608080128`'s SS_Mode claim corrected, `202608080058` confirmed on authoritative evidence |
| `202608080510` | Setup classification: everything copied carried a hash and is clean; the two re-implemented artifacts carried prose and both had a defect |
| `202608080246` | Kernel narrowness has two axes and the pass line shows neither; a reset branch with no evidence from any layer in four shipped functions; sixth upstream artifact |
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
