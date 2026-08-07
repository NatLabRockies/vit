# ROSCO Replication with Per-Function Telemetry — Execution Plan

**Date:** 2026-08-07
**Status:** in-progress — plan written and reviewed against measurement 2026-08-07; execution not started
**Scope:** New ROSCO fork, VIT telemetry instrumentation, replication campaign
**Deadline context:** This work is time-boxed and will be cut off by the author.

---

## 1. What this is, and what it is not

**Objective.** Re-run the ROSCO migration from a clean fork with instrumentation that records, per
function, the tokens and wall clock consumed by a complete `extract → translate → verify →
integrate → regress` cycle, plus the number of attempts to first verification pass.

**This measures the cost of running a mature VIT today.** It does not measure what the original
campaign cost. Three reasons, all of which must appear beside any number this produces:

1. **Different model.** The campaign used Claude Opus 4.6, held fixed deliberately (dev note
   `202608071048`). This replication uses Opus 5.
2. **Different tokenizer.** Opus 4.7 introduced a tokenizer that Opus 4.8 and Opus 5 inherit;
   the same text yields roughly 30% more tokens than under 4.6. Counts here are **not in the
   campaign's units** and must never be presented as the campaign's cost.
3. **Different tool maturity.** The campaign built VIT while using it. This replication starts
   with the finished tool. The original's cost included the tool's construction; this does not.
4. **The verification apparatus is carried over, not rebuilt.** Verified 2026-08-07 against
   commit `974290e`: `Examples/vit_sim.py` (1,957 lines, the 27-scenario suite), `vit.yaml`,
   `scripts/reset_to_clean.sh` and `baseline_arrays/` are all **absent** at the baseline. They are
   products of the original campaign. This replication copies them in. The number it produces is
   therefore the cost of translating functions *given a working verification harness*, which is
   the adoption cost a reader needs, but it is not the cost of standing that harness up.
   `TRANSLATION_TIPS.md` (167 lines) is carried over on the same terms and for a stronger reason:
   §9's marginal-cost claim is specifically about the accumulated-guidance regime, so the guidance
   must be present. Both carry-overs are recorded in the manifest.

The defensible claim is: *"a mature VIT, driven by a current commodity agent, costs N tokens and
M minutes per function on this codebase."* That is the adoption cost, which is the number a
reader actually needs. Anything stronger is unsupported.

**Secondary objective, arguably more valuable than the number.** The telemetry itself is durable.
Once `vit` records cycles natively, AeroDyn Phase D onward produces per-function cost and attempt
counts with no reconstruction, and the attempt-count gap closes permanently.

---

## 2. Preconditions to verify before any execution

Do not skip these. Two are already known to be wrong or unverified.

| # | Check | Status | Action if it fails |
|---|---|---|---|
| P1 | Baseline commit is `974290e` (2026-02-04), the commit before `e8010f0` "Add VIT translation infrastructure" | **Unverified.** VIT's first commit is 2026-02-25 but ROSCO's first VIT-era commit is 2026-03-18. That 3-week gap is unexplained. | Find where translations landed before 2026-03-18 — another branch, a squashed history, or work done outside the ROSCO repo. Do not assume. |
| P2 | Function list and count | **Discrepant.** `ROSCO/vit.yaml` logs **49** translations; the project's own count is 52 functions plus the entry point. | Reconcile before building the plan in §5. The 52 may be right and the log incomplete, or the reverse. |
| P3 | `vit-dev` container runs the ROSCO toolchain (Python 2.7 KGen, cmake, gfortran) | **Verified 2026-08-07.** Container up; ran 27 scenarios and an incremental build successfully | Rebuild per `docs/CONTAINER_SETUP.md` |
| P4 | The 27-scenario baseline suite reproduces on the clean fork | **Partly verified.** The suite runs and reproduces the committed baselines to 348/351 channels; the 3 that differ are scenario 16 `bld_pitch{,_2,_3}`, the known 2^-70 EKF residual. Not yet run on the clean fork | Regenerate baselines locally from the clean Fortran build before the first integration. **Do not gate against committed `baseline_arrays/`** — see §4.4 |
| P5 | The model in effect is known for the whole run | **Superseded.** Do not rely on pinning. Every transcript record carries `message.model`; `join_telemetry.py` reads it and reports the distinct set per function | If more than one model ID appears in a function's interval, flag that function |
| **P6** | **The VIT that runs the replication is the current VIT** | **FAILS.** `vit` in `vit-dev` resolves to `/workspace/vit` = `vit_translation/vit`, branch `rosco-restart-objinst-fix`, **3 ahead / 186 behind `origin/main`**, 598 tests vs the OpenFAST checkout's 660 | Triage the 3 unmerged commits, then sync to `origin/main`. Telemetry must land in **this** checkout, not only the OpenFAST one |

**P1, P2 and P6 are blocking.** A replication that starts from the wrong commit, translates the
wrong function set, or runs a 186-commit-stale tool measures something, but not the thing it
sets out to measure.

---

## 3. Phase 0 — Telemetry instrumentation

Build this first. It is the durable artifact and everything downstream depends on it.

### 3.1 Design

The agent's token usage is recorded by Claude Code in its session transcript
(`~/.claude/projects/<project-key>/<session-id>.jsonl`), where `message.usage` carries
`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, and
each record carries a `timestamp`. VIT knows which function and which step it is running.

**Join them on time.** VIT writes cycle boundaries; a post-processing script attributes every
usage record falling inside a function's interval to that function. This needs no access to the
agent's internals and works retroactively on any session.

```
   agent session transcript          VIT cycle log
   ─────────────────────────         ─────────────────
   ts, usage{in,out,cache}    ⟵join⟶  ts_start, ts_end, function, command, exit_code
```

**Attribution rule.** Tokens spent between `vit translate saturate` and `vit verify saturate` are
attributed to `saturate`. That is correct: the agent's work on a function happens between that
function's commands. **This requires no interleaving** — one function at a time, no unrelated work
mid-cycle. Any deviation must be logged and that function excluded.

**The isolation requirement is stronger than "one function at a time."** Claude Code derives the
transcript project key from the working directory, so the replication must run from a **dedicated
session in the replication fork's directory**. Plan revision or any other work in
that same session lands in the same transcript and will be attributed to whichever function is in
flight. Do that work elsewhere.

**Session-level costs are not per-function costs.** Session start (system prompt, `CLAUDE.md`,
memory) and every compaction consume large token counts that belong to no function. The joiner
must classify records before the first `start` event, and records inside a compaction, as
`session_overhead` and report them as their own line, not smear them into the first function of
the session.

### 3.2 Files to create

**`vit/telemetry.py`** — new module, no dependencies beyond stdlib.

```python
def log_event(function: str, command: str, phase: str, **fields) -> None
    # Appends one JSON object per line to $VIT_TELEMETRY_LOG
    # (default: <project_root>/.vit/cycle_log.jsonl).
    # phase ∈ {"start", "end"}
    # Always includes: ts (ISO-8601 UTC, microsecond), function, command, phase, pid
    # On "end" also: duration_s, exit_code
    # Never raises: telemetry failure must not fail a translation cycle.
```

**Hook in `vit/cli.py`** — wrap the dispatch in `main()`:

```python
fn = getattr(args, "function_name", None)
telemetry.log_event(fn, args.command, "start", argv=sys.argv[1:])
t0 = time.monotonic()
try:
    rc = dispatch(args)
finally:
    telemetry.log_event(fn, args.command, "end",
                        duration_s=round(time.monotonic() - t0, 3), exit_code=rc)
```

Guard with `if os.environ.get("VIT_TELEMETRY", "1") != "0"` so it can be disabled.

`vit/cli.py:187` is a single `try:` wrapping an `if/elif` chain on `args.command`, and the
function argument is uniformly `args.function_name`. Wrap that `try` block; there is no `dispatch`
function to hook. Note the existing `except Exception` returns 1, so `exit_code` is recoverable
from the return value in every path.

**Steps 6 and 7 are not `vit` commands, so the CLI hook does not see them.** Rebuild is
`cmake --build`, and ROSCO has **no committed regression runner at all** — the procedure exists
only as a shell snippet in `ROSCO/INTEGRATION_TEST.md`. Without an addition here, `t_regress_s`
comes back empty and `t_total_s` silently understates the cycle. Two pieces are required:

- **`python3 -m vit.telemetry` CLI entry point** so shell scripts can emit the same events:
  `python3 -m vit.telemetry start --function saturate --command regress` / `... end --exit-code 0`.
- **`ROSCO/scripts/regress.sh`**, committed, wrapping the 27 isolated scenario invocations plus
  the array comparison, bracketed by those events. This also makes the regress step reproducible,
  which it currently is not.

**`scripts/join_telemetry.py`** — post-processor.

```
usage: join_telemetry.py --cycle-log .vit/cycle_log.jsonl \
                         --transcripts ~/.claude/projects/<key>/ \
                         --out analysis/replication/per_function.csv
```

Emits one row per function:

| column | meaning |
|---|---|
| `function` | function name |
| `status` | `verified` / `integrated` / `failed` / `abandoned` |
| `t_extract_s`, `t_translate_s`, `t_verify_s`, `t_integrate_s`, `t_regress_s` | wall clock per step, summed over attempts |
| `t_total_s` | end-to-end |
| `verify_attempts` | invocations of `vit verify` before first pass |
| `integrate_attempts` | invocations of `vit integrate` |
| `tok_in`, `tok_out`, `tok_cache_read`, `tok_cache_write` | summed over the function's intervals |
| `tok_total_uncached` | `tok_in + tok_out + tok_cache_write`. **Not "billable"** — cache reads are billed too, at a reduced rate. If a cost figure is wanted, price all four components and call it `cost_usd` |
| `prompt_tokens_static` | measured from `generate_translation_prompt`, no agent needed |
| `model` | distinct `message.model` values observed in the function's intervals; more than one is a flag |

**`scripts/measure_prompt.py`** — already prototyped this session. Calls
`parse_signature_from_source` → `generate_translation_scaffold` →
`generate_translation_prompt` and reports character and token counts without invoking an agent.
Current measurements on AeroDyn: 2,099–2,478 tokens per function, of which ~1,480 is the fixed
`TRANSLATION_TIPS.md`. Re-run against ROSCO.

### 3.3 Tests

Add to `tests/test_telemetry.py`:

- `log_event` writes valid JSONL and is append-only
- a raised exception inside dispatch still produces an `end` record
- telemetry disabled by env var produces no file
- `join_telemetry` attributes a synthetic usage record to the correct function interval
- a usage record outside all intervals is counted as `unattributed`, not dropped silently

The last one matters: **unattributed tokens must be reported, not discarded.** A large
unattributed fraction invalidates the per-function figures and must be visible.

### 3.4 Acceptance for Phase 0

- Full suite green in **both** checkouts. OpenFAST workspace is at 660 tests; ROSCO workspace
  (`vit_translation/vit`, the one `vit-dev` actually runs) is at 598 and must be
  synced first per P6, after which the counts should agree.
- A dry run on one AeroDyn function produces a populated `per_function.csv` row
- Unattributed token fraction under 10% on that dry run, with `session_overhead` reported
  separately rather than counted as attributed

---

## 4. Phase 1 — Clean fork and baseline

1. **Clone fresh, into the mounted workspace.** `vit-dev` mounts
   `vit_translation → /workspace`, so the fork must live at
   `vit_translation/ROSCO-replication` (`/workspace/ROSCO-replication` inside the
   container). Anywhere else and the container cannot see it. No container rebuild is needed.
   Check out `974290e`. Do **not** reuse `vit_translation/ROSCO` — it is on branch
   `restart-validation` with uncommitted modifications and is not a clean baseline.
2. **Write a run manifest** (`analysis/replication/manifest.json`): baseline commit, VIT commit,
   KGen commit, container image digest, model ID, start timestamp, compiler version, **and the
   explicit list of carried-over campaign artifacts from §1 caveat 4**.
3. **Carry over the campaign's harness**, and record every file in the manifest. None of it
   exists at `974290e`:
   - `vit.yaml` — configuration. **Its paths are absolute (`/workspace/ROSCO/...`) and must be
     rewritten** to the replication directory or the first `vit extract` fails.
   - `Examples/vit_sim.py` — the 27-scenario suite, 1,957 lines.
   - `TRANSLATION_TIPS.md` — the accumulated guidance §9's claim is about.
   - `scripts/reset_to_clean.sh`, `scripts/extract_all.sh`, `scripts/verify_all.sh` as needed.
   Rebuilding any of these by hand would measure harness authoring rather than translation, and
   would not reproduce the same gate.
4. **Build clean and regenerate the 27 baselines on the fork.** These, not the committed
   `baseline_arrays/`, are the ground truth for every regress step. Measured 2026-08-07: a fresh
   run of the existing tree matches its committed baselines on 348 of 351 channel-arrays; the 3
   that differ are scenario 16 `bld_pitch{,_2,_3}`, the known 2^-70 EKF covariance residual.
   Gating against the committed arrays would fail every function for a reason that has nothing to
   do with that function. Record per-channel drift from the upstream-shipped outputs.
5. **Commit the untouched fork** so the diff from baseline is exactly the replication's work.

---

## 5. Phase 2 — Codebase analysis

This is a measured step, not setup. Instrument it.

1. Structural inventory over `rosco/controller/src`: procedures, signatures, caller–callee graph,
   derived types, external API boundary. **There is no `vit analyze` command.** The actual entry
   points are `scripts/fortran_inventory.py` (procedure and call-graph extraction) and
   `vit analyze-types` (derived-type C compatibility), plus agent reading for the boundary
   decisions. Bracket the whole step with telemetry events so its cost is captured.
2. Record wall clock and tokens for the analysis itself. It is meant to be a one-time
   cost; this is the opportunity to put a number on it.
3. Produce the **dependency-ordered function list** — leaves first, bottom-up. Persist as
   `analysis/replication/plan.json` with, for each function: name, source file, call-site count,
   dependency depth, and whether the original campaign kernel-verified it or gated it only at
   the simulation layer (43 vs 9 in the original campaign).
4. **Reconcile against P2.** If the generated list disagrees with `vit.yaml`'s 49 or the project's
   52, resolve it here and record the resolution. This is a finding either way.

---

## 6. Phase 3 — Translation plan

Written output, committed before execution starts:

- Ordered function list with dependency rationale
- Which verification method each function gets (kernel replay / dual-call / file-output /
  simulation-only), matching the original campaign's assignment so the comparison holds
- Expected gate per function (20–25 invocations for kernel replay; 27 scenarios for regress)
- Explicit **batching decision for regress** — see §7.3, this is the main fidelity risk
- Checkpoint boundaries (§8)

---

## 7. Phase 4 — Execution loop

### 7.1 Per-function procedure

For each function in plan order:

```
1.  vit extract   <fn>          # KGen pragma at a call site, capture ~20 invocations
2.  vit translate <fn>          # generates scaffold + prompt
3.  agent writes the C++ body   # the only model-authored artifact
4.  vit verify    <fn>          # bit-identical replay; on fail, return to 3
5.  vit integrate <fn> --apply  # patch source + build system
6.  rebuild
7.  regress                     # 27 scenarios, or batched per §7.3
8.  git commit                  # one commit per function, message records attempt count
```

### 7.2 Discipline required for valid attribution

- **One function at a time.** No interleaved work, no side quests mid-cycle.
- **No manual C++ edits outside step 3.** If a translation needs hand-fixing outside the agent
  loop, log it and mark the function as deviating.
- **Every verify failure is a data point.** Do not squash retries. The attempt count is a result.
- **Record abandonment.** If a function cannot be completed, record it as `failed` with the
  reason. This is failure-rate data the project currently lacks entirely.

### 7.3 The regress fidelity decision — **settled by measurement, 2026-08-07**

This section previously argued that full per-function regress was "probably too slow" and
recommended a scenario subset. **That was a guess, and it was wrong by roughly two orders of
magnitude.** Measured on the existing tree in `vit-dev`:

| Step | Wall clock |
|---|---|
| Single scenario | 4.2 s |
| **Full 27-scenario suite, 27 isolated `docker exec` calls** | **51 s** |
| Incremental rebuild after one changed file | 2.5 s |

All 27 `.npz` produced, comparison against baselines completed in the same pass. Per-scenario
process isolation is required (DLL SAVE contamination, `INTEGRATION_TEST.md`) and is already
included in the 51 s.

Full-fidelity regress therefore costs **under a minute per function**, or about **45 minutes of
wall clock across the entire 52-function campaign**. Option (a) stands, and no longer as a
sacrifice: batching would save nothing worth having and would cost per-function attribution.

The consequence for §12 is direct — regression is not the throughput constraint. Agent authoring
time is.

**Note on `INTEGRATION_TEST.md`:** it documents 9 scenarios and 672,000 values. The suite is now
27 scenarios and 351 channel-arrays. The document is stale; fix it.

---

## 8. Checkpoints and stopping

The author will cut this off. Make stopping clean rather than abrupt.

**Checkpoint after every function:** commit, telemetry flushed, `per_function.csv` regenerated.
The dataset is valid at any n.

**Named checkpoints:**

| CP | After | Yields |
|---|---|---|
| CP0 | Phase 0 complete | Instrumentation, tests green — valuable even if nothing else runs |
| CP1 | Phase 2 complete | Codebase-analysis cost; function list reconciled |
| CP2 | First 5 functions | First real distribution; enough for a claim with n stated |
| CP3 | First 15 | Distribution across difficulty classes |
| CP4 | All | Full replication |

**Minimum useful outcome is CP0 + CP2.** Below that, report the static prompt measurement only,
which needs no replication at all.

---

## 9. Analysis outputs

- `analysis/replication/per_function.csv` — the dataset
- `analysis/replication/summary.md` — median and range for tokens and wall clock per function;
  attempts-to-first-pass distribution; unattributed token fraction; static prompt tokens and the
  fixed/variable split
- One summary sentence, with n, model, and the regress decision named

**Candidate claim, if the data supports it:** the marginal cost of translating one more
function is X tokens and Y minutes, of which the fixed accumulated-guidance prompt is Z — which
would quantify that mechanism directly.

---

## 10. What would invalidate this

- Interleaved work during a cycle (attribution breaks)
- Unattributed token fraction above ~10%
- Model changed mid-run
- Baseline commit wrong (P1)
- Function set wrong (P2)
- Presenting these numbers as the original campaign's cost — the single most likely misreading,
  and the reason §1 of this note exists
- Presenting them without stating that the 27-scenario harness and `TRANSLATION_TIPS.md` were
  carried over rather than rebuilt (§1 caveat 4) — the second most likely misreading
- Running a stale VIT (P6) and reporting it as the current tool

---

## 11. Decisions (resolved 2026-08-07)

**P1 — RESOLVED. Baseline is `974290e`** (dzalkind, 2026-02-04, "Update inputs for openfast
4.2.0 (#491)"). Rule given by the author: the last commit on the fork that he did not make. That
is the last non-`kevinmenear` commit, and it sits immediately before `e8010f0` (kevinmenear,
2026-03-18, "Add VIT translation infrastructure and golden kernel fixtures").

The 2026-02-25 to 2026-03-18 gap is not missing work. VIT's own repository was started 2026-02-25
and early translations were verified against kernel fixtures before anything landed in the ROSCO
fork. Nothing is unaccounted for; §2's P1 concern is withdrawn.

**P2 — DEFERRED.** The 49-vs-52 discrepancy between `ROSCO/vit.yaml` and the project's own count stands
unresolved. The author will investigate separately. **This does not block the replication** — the
plan in §5 is generated from the codebase analysis, not from the log — but it remains an open
issue against the ROSCO accounting and must not be forgotten. Whatever the analysis produces here is
evidence toward resolving it.

**§7.3 — RESOLVED: full fidelity, option (a).** Full 27-scenario regression after every
integration, exactly as the original campaign ran it. No batching, no subset. This makes the
per-function cost figure include the true regress cost and keeps the replication a replication.
The consequence is accepted: throughput will be low and the run will be cut off partway through
ROSCO rather than completing it.

**Goal — as far as we get.** Not a fixed n. Every completed function is a valid data point and
the dataset is analysable at any size (§8). The author will stop the run when the remaining time
is needed elsewhere.

---

## 12. Honest assessment

Phase 0 is a few hours and is worth doing regardless. Phases 1–2 are
another few. Phase 4 is bounded only by agent authoring time: measurement (§7.3) puts the entire
mechanical cost of a cycle — build plus full 27-scenario regress — at under a minute, so
everything else in the loop is extraction, translation authoring, and verify iteration.

The risk is not that the replication fails; it is that it consumes the time that other work
with a deadline requires.
Recommend hard-stopping the replication at whatever checkpoint is reached by a fixed time on
2026-08-09, regardless of progress.

## 13. Measurements taken 2026-08-07 (plan review)

Recorded so the revisions above are checkable and so nothing here has to be re-derived.
All against `vit_translation/ROSCO` (branch `restart-validation`) in `vit-dev`.

| Fact | Value | How |
|---|---|---|
| Single scenario | 4.2 s | `docker exec vit-dev ... vit_sim.py --scenario 1` |
| Full 27-scenario regress, isolated | 51.4 s | loop over `scenario_order`, one `docker exec` each |
| Incremental rebuild | 2.5 s | `cmake --build . -j8`, no-op link |
| Fresh run vs committed baselines | 348/351 channels identical | `np.array_equal` over all 27 `.npz` |
| The 3 that differ | scenario 16 `bld_pitch`, `bld_pitch_2`, `bld_pitch_3` | known 2^-70 EKF residual |
| Files absent at `974290e` | `Examples/vit_sim.py`, `vit.yaml`, `scripts/reset_to_clean.sh`, `baseline_arrays/` | `git cat-file -e 974290e:<path>` |
| Carried-over harness size | `vit_sim.py` 1,957 lines; `TRANSLATION_TIPS.md` 167 lines; `scripts/` 1,324 lines | `wc -l` |
| `vit` in `vit-dev` resolves to | `/workspace/vit/vit/__init__.py` | `python3 -c 'import vit; print(vit.__file__)'` |
| That checkout vs `origin/main` | 3 ahead, 186 behind; 598 tests vs 660 | `git rev-list --left-right --count`, `pytest -q` |
| Container mount | `vit_translation → /workspace` | `docker inspect vit-dev` |
| `vit-dev` has no `git` | confirmed | `git: command not found` |

## References

- Dev note `202608071048` — model held at Opus 4.6; why a different model here matters
- Dev note `202608071141` — extraction build wrapper; nine steps, relevant to Phase 4 step 1
- Dev note `202608071058` — kernel Makefile flags; relevant to verification fidelity
