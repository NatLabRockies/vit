# What the Replication Telemetry Accounts For, and What It Does Not

**Date:** 2026-08-07
**Status:** in-progress — replication running; figures are a snapshot, not a result
**Scope:** `vit/telemetry.py`, `scripts/join_telemetry.py`, ROSCO replication measurement

## Placement — read this first

**This note lives in the OpenFAST workspace deliberately.** The ROSCO replication runs in
`vit_translation/`, which `vit-dev` bind-mounts as `/workspace`, and the replication
agent commits to `vit_translation/vit`. A note there would appear in its `git log`.

This note critiques the measurement and names specific behaviours of the measured session — idle
time read as work, function #1 inflated by tool-fixing. An agent that reads it may optimize
against it. **Do not merge this into the ROSCO-workspace checkout until the run has ended.**

The two checkouts are the same repository and converge on push/pull. That convergence must wait.

## Question this answers

Asked during the run: *is it accounting tokens and wall clock for everything?*

Tokens, yes. Wall clock, no. The difference matters because both would be reported the same way,
and only one of them means what a reader would assume.

## Tokens: complete, within a stated scope

Every usage record in the replication session's transcript lands in exactly one of four
categories. `tests/test_telemetry.py::test_nothing_is_lost` asserts it.

| Category | Meaning |
|---|---|
| `<function>` | inside that function's span |
| `session_overhead` | compaction records, and everything before the first span (session startup) |
| `boundary` | between one function's last command and the next function's first: commit, findings, report, deciding what is next |
| `unattributed` | no spans exist at all, so nothing can be placed |

Three scoping caveats belong beside any figure this produces:

1. **Only the replication session's transcript is counted.** The parallel session used for audit
   and guidance is a different project key and is not included. Neither is human time. The number
   is *what the replication session spent*, not *what it cost to get the replication done*. The
   human–agent–tool loop is wider than the measured window.
2. **Cache reads are recorded but excluded from `tok_total_uncached`.** They are the bulk of
   raw token volume and a large share of cost. They grow with session length, so a cost series
   rises with session position independently of function difficulty.
3. **Cost is derived, not observed.** Published rates applied to token counts; no billing data.

## Wall clock: partial, and the gap is human latency

`t_total_s` is span start to span end. It therefore includes agent thinking *and* time the session
sat idle waiting on a human. `t_<step>_s` covers only telemetry-logged commands. `boundary` and
`session_overhead` carry tokens but no wall clock at all.

Measured mid-run, all values in seconds:

| span | wall | commands | gap | idle >3 min | largest silence |
|---|---|---|---|---|---|
| `__analysis__` | 962 | 962 | 0 | 0 | 65 |
| `__plan__` | 154 | 154 | 0 | 0 | 59 |
| `saturate` | 935 | 586 | 349 | 235 | 235 |
| `wrap_180` | 406 | 362 | 44 | 0 | 96 |
| `wrap_360` | 226 | 194 | 33 | 0 | 98 |

`saturate` carries a single 235-second silence, about a quarter of its wall clock, which is human
reading latency rather than agent work. So per-function wall clock is **elapsed, not worked**, and
the two diverge by however closely the run is being watched.

**Not built, and available later.** Two columns would separate them, both derivable from data
already collected with no change to the measured path: `t_commands_s` (sum of measured command
durations) and `t_idle_s` (silent gaps over a threshold inside a span). Then
`t_total_s - t_commands_s - t_idle_s` is agent thinking time, which is the figure that actually
characterizes the workflow. Deliberately deferred: the run is producing data and the columns can
be computed retroactively.

## Decisions made during the run

**Boundary work is derived, not bracketed.** A `__boundary__` telemetry bracket was proposed and
rejected as ceremony. A record in the gap between two spans is inter-function work by
construction, and the joiner already knows both surrounding spans, so nothing needs to log it.
Post-processing costs nothing per cycle, cannot be forgotten, and applies retroactively. Commit
`90a496d`.

The rename mattered more than it sounds. `unattributed` asserts that attribution *failed*.
Boundary work is real project cost that belongs to no single function, which is a different claim.
`unattributed` now has exactly one meaning: no spans found, i.e. the caller pointed at the wrong
transcripts.

**The 10% unattributed threshold was removed, not adjusted.** It was set during Phase 0 without
knowledge of the workflow's shape and fails by construction: a routine boundary runs at about that share of
a function's uncached tokens, so that is the floor. It fired during
normal operation. A warning that cannot go below its own limit trains its reader to ignore
warnings. Boundary fraction is now reported as information; the surviving checks are the ones that
indicate real breakage — overlapping spans, unpaired events, more than one model ID.

**`status` and invocation counts.** Phase brackets (`__analysis__`, `__plan__`) were reported as
`failed` because they have no verify or integrate step, which would have put two phantom failures
into any failure rate computed from the table. They are now `phase`, and an unclosed cycle is
`in_progress`. Separately, the table carried only attempts-to-first-pass, so a command re-run
*after* succeeding was indistinguishable from a retry; `verify_runs` and `integrate_runs` now
report raw invocations alongside. Commit `06811f0`.

That second one arose from a disagreement about whether `integrate_attempts: 1` was wrong for
`saturate`, which ran `vit integrate` twice. It was correct — the second run regenerated an
artifact after a VIT fix rather than retrying a failure. The problem was that the data could not
settle the question.

## How these numbers should be read

Snapshot at the time of writing, five spans closed: boundary work was a substantial share of the total.

Per-function figures so far, uncached tokens: `saturate` highest, then `wrap_180` and `wrap_360`
substantially cheaper. Wall clock 935 s, 406 s, 226 s.

**Function #1 is not a data point about function #1.** Its span contains a VIT bug fix
(`7ad4e9c`), two gate runs, and the first exercise of `vit verify` and `vit integrate` in a fresh
tree.

**The wall-clock series is not homogeneous. A configuration change moved it mid-run.** Between
functions #2 and #3 the session narrowed `vit.yaml`'s `run_cmd` to a single scenario, and extract
time fell from ~180 s to ~25 s:

| # | function | extract | wall |
|---|---|---|---|
| 1 | `saturate` | 182 | 935 |
| 2 | `wrap_180` | 178 | 406 |
| 3 | `wrap_360` | **25** | 238 |
| 4 | `ColemanTransform` | 25 | 311 |
| 5 | `identity` | 54 | 257 |
| 6 | `ColemanTransformInverse` | 28 | 379 |

`wrap_180` at the later extract cost would have been ~253 s, which sits inside the range of
functions #3–#6. **Most of the apparent improvement from #2 to #3 is the configuration change,
not amortization and not the agent getting better.** An earlier draft of this note attributed the
decreasing curve to infrastructure discovery alone; that was incomplete.

Consequences: functions #1–#2 and #3 onward are not comparable on wall clock without adjusting
for extract, the change point belongs in the manifest, and any median should either exclude the
pre-change functions or report extract separately. Token counts are unaffected — the change moved
machine time, not agent work.

**`verify` spikes when shared infrastructure changes**, not with function difficulty: 229 s on
`saturate` (first run in a fresh tree) and 227 s on `ColemanTransformInverse` (after
`rosco_constants.h` entered `shared_files`), against 79–80 s elsewhere.

**Boundary fraction rises with function count, not with function cost.** Boundary work is roughly
constant per transition; attributed cost per function varies. Reporting the ratio without that
caveat invites a reader to conclude overhead is growing.

## The carried guidance references artifacts that were not carried

`TRANSLATION_TIPS.md` was carried into the replication tree deliberately, so that the measurement
would be of the accumulated-guidance regime (manifest §1 caveat 4). It instructs translations to
include `translations/rosco_constants.h` — a campaign artifact that was **not** carried. The
session had to derive the header from `Constants.f90` and register it in `vit.yaml`'s
`shared_files`.

So the carry-over was incomplete in a way nobody noticed at setup: guidance points at things, and
carrying the guidance without the things it points at partially restores from-scratch cost. This
is honest — the rebuild is real work and it is measured — but the manifest currently claims a
cleaner separation than exists, and should say so.

It also produced the sharpest fidelity example of the run. ROSCO's Pi is written
`3.14159265359`, truncated. Using `M_PI` would be more accurate, would look more correct in
review, and would fail the gate. That is the source-level counterpart to the finding that
bit-level equivalence sometimes requires preserving implementation choices rather than the
mathematics — there the compiler's, here the original author's.

## Two findings worth keeping

**Leaf-first ordering exposes infrastructure assumptions that later functions cannot.** Both VIT
bugs found so far have this shape: `fortran_inventory.py` silently dropped an entire file, and
`integrate.py` hardcoded an include that only breaks when the translated function uses no derived
types. Neither could surface in the original campaign's later functions. The migration order that
minimizes dependency risk maximizes exposure of tooling assumptions.

**Relay guidance at function boundaries, never mid-cycle.** Anything the replication session
responds to while a span is open is charged to that function. This is the same rule as compacting
between functions rather than during one, and for the same reason: cost and context both belong to
the cycle they land in.

## References

- Dev note `202608071634` — the replication plan; §3 specifies this telemetry
- Commits `cd4ea6a` (Phase 0), `06811f0`, `90a496d` (in the ROSCO-workspace checkout)
