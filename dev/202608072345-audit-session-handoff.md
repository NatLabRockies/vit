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

*Updated 2026-08-08 19:38 local, at #54.*

Replication: **past #53 `RefSpeedExclusion`** (54/54 kernel, described as the best-gated function of
the run) and into #54, of 78 planned. Confirm exact counts from `STATUS.md` in the replication tree
rather than from this note — it goes stale between updates and the tree does not.

**A second repository now exists.** `translation-loop` holds the **outer loop**: the
orchestration and audit layer this audit's findings turned into software. M0–M10 complete, 129 tests,
in a private repository. Its `SPEC.md` is archived here as `202608081244`; the repo's copy is
authoritative. It has its own memories under its own project key. **It is where campaign-audit
tooling now lives** — the claims audit, bridge/liveness/manifest checks, and the corrected telemetry
joiner all run from there, read-only against these trees.

**Translation correctness is 100% first-pass across all 41.** Not one wrong translation in the run.
Every verify failure has been infrastructure.

**The gate was broken for the first 38 functions** and is now repaired (`41436ec`, `b908585`), then
widened to **33 scenarios, 429 channels, 0 mismatched** by six added cases (`a9248f8`, addition-only:
six new baselines, zero modified — confirmed by commit stat). HEAD is jointly bit-identical.
Per-commit "regress 27/27" claims are void; joint tree-level bit-identity is what the dataset
supports. Any "function N passed its gate" must come from a run after `b908585`. See `202608080346`
and `202608080404`.

**The two layers are not independent in the way the design assumes** — both view the C++ artifact,
neither views the integration wrapper. `CheckInputs` (#46) would have shipped with every input
validation silently disabled and both layers green; the harness rerun after integration caught it.
See `202608081128`. Members of the same class already recorded: `sigma`, `unwrap`.

The joiner had two attribution defects that parked requests in two phantom spans and zeroed
twelve functions. The corrected joiner lives in the loop repo and is **validated but not applied** — one `cp` over
`vit_translation/vit/scripts/join_telemetry.py` once the run ends.

Twenty-odd tool fixes (VIT + KGen), several of them fails-green defects.

**~~Open decision, the author's:~~ CLOSED at #34.** The control-layer redirect is moot. Plan order
reached `ControllerBlocks.f90` at #32 and `Controllers.f90` at #34 unaided.

**Open audit thread, safe to pursue now (read-only):** the campaign tree's committed sources do not
contain implementations for 7 of its 53 built files — `discon.cpp`, `checkinputs.cpp`,
`setparameters.cpp`, `readrestartfile.cpp`, `writerestartfile.cpp`, `debug.cpp`,
`prefiltermeasuredsignals.cpp` are all one-line `// stub` at HEAD on `main` and
`restart-validation`, and `checkinputs.cpp` is absent entirely on `integrated`. As committed that
tree cannot link. Until it is known where the campaign's shipped sources live, **no campaign-side
claim about any of those seven functions is available** — including the one I went looking for, a
`CheckInputs` copy-back defect. See `202608081128`.

## Deferred until the run ends — do NOT do these now

Each of these touches the replication's tree or its tooling. They are safe the moment the run
finishes and unsafe before it.

1. **Fix `join_telemetry.py` — two separate defects, both required before any per-function cost is
   reported.** (a) It merges disjoint spans sharing a `(function, command)` key into one interval
   from first start to last end; this swallowed #42–#45 entirely (`202608080826`). Pair in order,
   with a queue. (b) A nested phase prices at zero and its cost lands on the enclosing function
   (`202608080506`). Subtract inner spans, or refuse to print a cost for a flagged row. Then re-run
   over the whole cycle log and redo `202608080454`.
2. **Add `tok_cache_read` and a derived cost column to `join_telemetry.py`'s summary output.** The
   summary currently reports `attributed` / `session_overhead` / `boundary` in
   `tok_total_uncached`, which is the minority of cost that is *not* cache read. The per-function CSV
   already carries the column; only the summary omits it. Leaf script, retroactive — the token data
   is in the transcripts and the backups. Blocked only because the script lives at
   `vit_translation/vit/scripts/`, inside the measured tree. See `202608072355`.
3. Add the fifth extraction-empty cause to `CLAUDE.md`'s pitfalls list: pragma correct, call site
   real, enclosing subroutine never entered because a generic interface routes every call to a
   sibling. Diagnostic — check coverage on the *enclosing* procedure. See `202608080003`.
4. Add to the runbook: when a function's call sites differ in the *shape* of what they pass, select
   by argument variety rather than hit count. Coverage-count selection picks the most stereotyped
   site. See `202608080003` §6.
5. **Fix `join_telemetry.py` overlap handling** — a nested phase prices at zero and its cost lands on the enclosing function. Subtract it, or refuse to print a cost for a flagged row. See `202608080506`.
6. Re-verify AeroDyn's 17 baselines against the 15 changed tool commits.
7. Amend the `kgen-bug-attribution` memory and `CLAUDE.md` with the three genuine KGen exceptions.
8. Update `CLAUDE.md`'s stale `vit translate` overwrite warning.
9. Merge the two VIT checkouts.
10. Re-integrate `sigma`/`unwrap` with copy-back. Low priority, diagnostic fidelity only.
11. **Fix the unconditional verification stamp in `vit/integrate.py:457`.** It emits
    `// After verification: {name} kernel PASSED` as a header template constant for every file
    `vit integrate` touches — beside `// Generated by VIT`, with no verification state consulted. So
    every integrated file in both campaigns asserts kernel verification independently of whether a
    kernel ever ran. One line, zero functional impact, and it affects the honesty of every file the
    campaign ships. Prefer emitting the *actual* method and result (`// Verified: 6/6
    vit test-validate`, `// NOT VERIFIED: integrated_unexercised`) over emitting conditionally —
    check first whether `integrate` can reach the verification record. Deferred rather than relayed
    because it blocks nothing and the truth is already recorded correctly in `STATUS.md`,
    `DECISIONS.md` and the commit messages; the stamp is the only place that lies. **Corrects
    `202608080058`'s attribution.** See `202608081354`.

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
| `202608081444` | **Corrects `202608080615`'s supporting statistic.** "11 of 13 channels identically zero" is true of the *repaired live* scenario too, so it discriminates nothing; rarity against the ensemble does. Also: the provenance check names both setup defects at commit `bf25e35`, before either was known — P4 is mechanically detectable, not two anecdotes. And the bridge fix is confirmed from artifacts (campaign 30, replication 0) rather than from commit messages |
| `202608081354` | **Corrects `202608080058`'s attribution.** The `kernel PASSED` stamp is a header template constant emitted unconditionally by `vit integrate` — every integrated file in both campaigns asserts kernel verification with no verification state consulted. Worse and more general than one false claim. Found by the outer loop's first check on its first run past its known answer; deferred fix is item 11 |
| `202608081128` | **Pairs with `202608080058`, and is the stronger exhibit.** The kernel verifies the translation, not the integration — both layers view the C++ artifact, neither views the wrapper. `CheckInputs` would have shipped with all 125 validations disabled, gate green in both directions. Defines the gate-invisible class (`sigma`, `unwrap`); fifth verification approach; third cost-attribution artifact |
| `202608080128` | **Read second.** 30 campaign kernel bridges declare mismatched widths for the same argument, across 17 of 43 kernels; leaf-first left the whole callee-bridge path untested for 31 functions; third setup omission of mine |
| `202608080204` | **Read third.** The campaign's gate went 9→27 scenarios and silently lost `Flp_Mode=2` coverage; `PIIController` is verified in the campaign and unreachable in the gate that ships with it. Fourth setup omission: extending the gate was campaign practice the runbook never carried |
| `202608080218` | CLOSED, and wrong in its central claim — my sixth caught error, corrected at the head of the note. One gate run of 41 hit a stale library and was superseded before commit; all 36 final gates followed a good rebuild |
| `202608080729` | `interp2d` passes 20/20 with 20 distinct values and never executes its bilinear algorithm; defeats every check the run had. KGen accepts comma-separated windows, so add one rather than move it |
| `202608080700` | Recommendation on the gate gaps: close by addition only. The campaign grew its gate 9→27 in nine additive commits and never modified a scenario |
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
