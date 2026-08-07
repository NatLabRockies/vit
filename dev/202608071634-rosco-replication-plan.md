# ROSCO Replication with Per-Function Telemetry — Execution Plan

**Date:** 2026-08-07
**Status:** in-progress — plan written, execution not started
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
| P3 | `vit-dev` container runs the ROSCO toolchain (Python 2.7 KGen, cmake, gfortran) | Container is up (2 weeks) | Rebuild per `docs/CONTAINER_SETUP.md` |
| P4 | The 27-scenario baseline suite reproduces on the clean fork | Unverified | Regenerate baselines locally from the clean Fortran build before the first integration |
| P5 | Claude Code can be pinned to a named model for the whole run | Unverified | Required so the measurement has a stated model; record the exact model ID in the run manifest |

**P1 and P2 are blocking.** A replication that starts from the wrong commit or translates the
wrong function set measures something, but not the thing it sets out to measure.

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
| `tok_total_billable` | `tok_in + tok_out + tok_cache_write` (cache reads priced separately) |
| `prompt_tokens_static` | measured from `generate_translation_prompt`, no agent needed |
| `model` | model ID in effect |

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

- Full suite green (currently 660 tests)
- A dry run on one AeroDyn function produces a populated `per_function.csv` row
- Unattributed token fraction under 10% on that dry run

---

## 4. Phase 1 — Clean fork and baseline

1. **Clone fresh.** `git clone <ROSCO upstream> ROSCO-replication && git checkout <P1 commit>`.
   Do **not** reuse `vit_translation/ROSCO` — it is on branch `restart-validation`
   with uncommitted modifications and is not a clean baseline.
2. **Write a run manifest** (`analysis/replication/manifest.json`): baseline commit, VIT commit,
   KGen commit, container image digest, model ID, start timestamp, compiler version.
   Everything needed to say what was measured.
3. **Copy `vit.yaml`** from the existing ROSCO tree; it is configuration, not a translation, and
   rebuilding it by hand would measure config authoring rather than translation.
4. **Build clean and regenerate the 27 baselines locally.** Record per-channel drift from the
   upstream-shipped outputs, per §III-F. This is the ground truth for every regress step.
5. **Commit the untouched fork** so the diff from baseline is exactly the replication's work.

---

## 5. Phase 2 — Codebase analysis

This is a measured step, not setup. Instrument it.

1. `vit analyze` (or the structural-inventory entry point) over `rosco/controller/src`:
   procedures, signatures, caller–callee graph, derived types, external API boundary.
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

### 7.3 The regress fidelity decision — **needs an explicit call**

The original campaign ran the full 27-scenario suite after every integration. At 52 functions
that is 1,404 scenario runs. Under a 3-day box this is likely infeasible.

Options, in decreasing fidelity:

- **(a) Full per-function regress.** True replication. Probably too slow.
- **(b) Batched regress** every N functions, with per-function kernel verification retained.
  Fast, but a baseline failure can no longer be attributed to one function without bisecting,
  and the measured per-function cost excludes most regress time.
- **(c) Per-function regress on a scenario subset**, full suite at checkpoints. Middle ground.

**Recommendation: (c)**, with the subset fixed in advance and named in the manifest. Whichever is
chosen must be stated wherever the numbers appear, because it changes what "per-function cost"
includes.

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
another few. Phase 4 is unbounded and will be cut.

The risk is not that the replication fails; it is that it consumes the time that other work
with a deadline requires.
Recommend hard-stopping the replication at whatever checkpoint is reached by a fixed time on
2026-08-09, regardless of progress.

## References

- Dev note `202608071048` — model held at Opus 4.6; why a different model here matters
- Dev note `202608071141` — extraction build wrapper; nine steps, relevant to Phase 4 step 1
- Dev note `202608071058` — kernel Makefile flags; relevant to verification fidelity
