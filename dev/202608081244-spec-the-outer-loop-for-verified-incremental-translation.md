# Spec: The Outer Loop for Verified Incremental Translation

**Date:** 2026-08-08
**Status:** in-progress — first complete draft; phases 1–4 are grounded in measurement, phase 5 is
proposed and untested
**Scope:** method, not code; the orchestration layer above a translation agent

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

---

## 0. What this specifies

An **outer loop**: the orchestration layer that drives a long-running, many-function verified
translation campaign performed by an LLM agent inside a coding harness (here, Claude Code).

It specifies the phases, the roles, the on-disk state contract, the boundary discipline, the exit
criteria for each phase, and the audit protocol. It does **not** specify the translation tooling
(VIT, KGen), the target language pair, or the verification technology. Those are parameters.

The loop is derived from three campaigns, one of which is still running:

| Campaign | Scope | Status | Role in this spec |
|---|---|---|---|
| ROSCO (original) | 52 functions, pure C++ `libdiscon.so` | complete, Apr 2026 | source of failure evidence |
| AeroDyn | 507 procedures, ~75 kLOC | in progress | source of workflow evidence |
| ROSCO replication | 78 planned, 47 attempted | running as of 2026-08-08 | source of measurement |

The replication is the only one instrumented from the first commit, so nearly every number here
comes from it.

### The claim this spec makes

> The loop body is mechanical. All judgment lives in three places — setup verification, escalation,
> and audit — and everything else is a fixed prompt, a state commit, and a compaction.

That is a strong claim and it is the reason the loop is automatable. Sections 5–10 are the
argument for it; §14 lists what would falsify it.

---

## 1. Evidence base and notation

Every substantive claim below is tagged:

- **[M]** — measured in this project, with the measurement named
- **[O]** — observed, but from a single instance; directional, not established
- **[P]** — proposed; consistent with the evidence but not itself tested
- **[X]** — a rule adopted after a specific failure; the failure is cited

Unmarked statements are definitions or restatements.

Costs are Anthropic API list prices for Opus 5 at the time of the run:
cache read at 0.1× input, one-hour cache write at 2× input. **Cache read is most of the bill and
output tokens are ~11% [M]** (`202608072355`, `202608080454`); any cost intuition built on output
tokens is wrong by an order of magnitude.

---

## 2. The three roles

The single most common design error would be to collapse these into one agent. They differ in what
they may write, what they can see, and what they are optimising.

### 2.1 Worker

The translation agent. Owns the target repository. Executes the per-function cycle. Writes code,
runs the tools, commits.

- **May write:** the target tree, its own state files, its tooling forks.
- **Must not:** modify the gate's existing cases (§7.4), inline a callee to route around
  infrastructure (§8.5), or invent a verification method not in the runbook without recording why.
- **Optimises:** functions completed correctly per unit of context.

### 2.2 Driver

The loop. Issues the post-boundary prompt, waits for the state commit, compacts, repeats.

- **May write:** nothing in the target tree.
- **Optimises:** boundary placement and prompt fidelity.
- **Is mechanical [M]:** in the replication, the Driver's entire contribution across five boundaries
  was a fixed three-line prompt and a compaction. §9 gives the prompt.

### 2.3 Auditor

An independent agent with **read access to everything the Worker cannot see** — the reference
implementation, the transcripts, prior campaigns, the telemetry — and **write access to nothing the
Worker owns.**

- **May write:** its own notes, in its own workspace.
- **Must not:** touch the measured tree in any way, including `git pull` [X — a `git pull` against a
  bind mount was the first of ten errors caught in the audit channel].
- **Optimises:** finding claims that are false, unfalsifiable, or unearned.

The Auditor exists because **every layer of this project's apparatus has, at some point, reported
success while establishing nothing** (§13). The Worker cannot audit itself on the axis that matters,
because the thing it must doubt is the instrument telling it it succeeded.

**However [M]:** once the discipline was relayed, the Worker caught three major defects unaided — the
vacuous gate, the dead scenario, and the missing copy-back. The discipline transfers. This bounds
how much Auditor time a mature loop needs, and suggests §3's principles belong in the runbook rather
than in a person.

---

## 3. Design principles

Nine principles, each earned from a specific failure or measurement.

### P1 — State lives on disk, not in context

Every fact the run depends on must be in a committed file before the context that produced it is
discarded.

**[M]** The replication's `STATUS.md` (664 lines), `REPLICATION_RUNBOOK.md` (729), `DECISIONS.md`
(3,113) and `TRANSLATION_TIPS.md` (1,226) let five compactions and one full clear pass without loss
of project state.

**[X]** Two consecutive sessions went looking for `STATUS.md` by name when it was still a block
inside `DECISIONS.md`. It was split out for exactly that reason. **A file that is looked for by name
should have that name.**

### P2 — The loop body is mechanical

If a boundary requires judgment about what to preserve, the state contract is incomplete. Fix the
contract, not the prompt.

**[M]** The one hand-written "pre-compact flush" commit in the replication's history (`e501241`)
exists only because that boundary landed 23 minutes into unrelated work. Boundaries landing on a
state commit needed no flush at all.

### P3 — A green result must be able to name what it compared, and be able to go red

Both halves are load-bearing. Printing the compared count catches the vacuous case; the red test
catches the case where the comparison exists but cannot fail.

**[X]** `regress.sh` printed `REGRESS PASS: 27/27 scenarios bit-identical` while comparing nothing —
`docker exec` without `-i`, so the heredoc never reached `python3 -`, which read an empty script and
exited 0. **38 functions were gated that way** (`202608080346`). The tell was the absent
`channels compared: N mismatched: M` line, which nobody missed because it had never been there.

**Corollary:** the red test is cheap and must be run **at first use, not at first doubt.** A
deliberate perturbation of the gate on day one would have cost ten minutes.

### P4 — Copy and hash-verify; never re-implement from prose

**[M]** `202608080510` classified every artifact transferred from the original campaign to the
replication. **Everything copied carried a hash and was clean. Both artifacts re-implemented from a
prose description carried a defect** — the gate script (P3 above) and the build flags (P5's
example). Two for two.

Any artifact reconstructed from a description is unverified by construction. If it must be
rewritten, it must be red-tested before use.

### P5 — Close gaps by addition, never modification

Adding a case leaves every prior result comparable. Changing a case silently reinterprets history.

**[M]** The original campaign grew its gate from 9 to 27 scenarios across nine additive commits and
never modified a scenario. The replication closed four coverage gaps at #46 by adding six scenarios
(`a9248f8`: six new baselines, zero modified — confirmed by commit stat) and by adding a second KGen
capture window rather than moving the first (`e164a3a`).

**[X]** The alternative was live: moving the kernel's `invocation` window would have given functions
#46+ stronger evidence than #1–#45 had, destroying the comparability of the first-pass rate the run
exists to measure (`202608080729`).

### P6 — Absence must not render as a value

A missing measurement must be distinguishable from a zero measurement, at the point of display.

**[X]** Four consecutive functions (#42–#45) price at **zero cost with zero requests** in the per-function
CSV because the telemetry joiner merged two disjoint spans into one 8,923-second window that
swallowed 182 records (`202608080826`). The rows are present and the numbers are zero, which reads
as free rather than as missing.

**[X]** A telemetry query keyed on `event` instead of `phase` returned zero rows, which reads as "no
failures ever."

### P7 — The oracle is the original source

Every verification ultimately compares against the original implementation, not against a recorded
expectation, not against a reference translation.

This is what makes the method portable: a reference translation may not exist, and **when one does
exist it may be wrong** — the original ROSCO campaign shipped `// After verification: ExtController
kernel PASSED` on a function with no kernel fixture and a real 512× divergence in a written field
(`202608080058`), and 30 of its kernel callee bridges declare a different width than the C++ header
for the same argument (`202608080128`).

**[M]** All five verification approaches used in this project reduce to a differential comparison
against the original: kernel replay (captured inputs), `test-validate` (branch coverage),
file-output comparison, simulation baselines, and the synthesised-input differential harness
(`202608081128`).

### P8 — Dispositions, not blockers

A function that cannot be completed must be able to end with a recorded disposition, not stop the
run.

**[M]** Five dispositions carried 47 functions with zero human escalations required for
classification: `integrated`, `integrated_unexercised`, `out_of_scope`, `deferred`, `blocked`.
Without them, `ExtController` alone would have halted the campaign indefinitely.

**Each disposition must state what evidence it rests on.** `integrated_unexercised` means "translated
and verified, but no scenario reaches it" — a materially different claim from `integrated`, and
collapsing them overstates the result (`202608072320`, `202608072158`).

### P9 — Coverage is not visibility

That a branch executed does not mean the gate can see its output.

**[M]** Three of six new gate scenarios needed retiming after their target branch was confirmed to
execute: the branch ran, but its output was pinned — torque clamped at `VS_MinTq`, or a pitch rate
applied to a command already hard against `PC_MaxPit`. A branch counter above zero says the code ran;
**only a perturbation says the gate can see it** (`202608081128`).

The converse also occurs: `interp2d` passed 20/20 with 20 distinct output values while never
executing its bilinear branch at all (`202608080729`).

---

## 4. The on-disk state contract

Six artifacts. Each has one owner and one purpose. **No fact appears in two of them.**

| Artifact | Owner | Purpose | Read at boundary |
|---|---|---|---|
| `RUNBOOK.md` | Worker | The executable procedure. Every step, every rule, every pitfall. | always |
| `STATUS.md` | Worker | Where things stand, now. Counts, next item, open threads. | always |
| `plan.json` | Setup | The work list with per-function metadata. | per function |
| `DECISIONS.md` | Worker | Append-only record of *why*. Never read end to end. | on demand |
| `TRANSLATION_TIPS.md` | Worker | Recurring translation pitfalls, numbered. | on demand |
| `cycle_log.jsonl` | Tooling | Machine telemetry. Never read by the Worker. | never |

### 4.1 Rules

1. **`STATUS.md` is the hand-off.** If it is current, the session is safe to discard. Nothing else
   may be required to resume.
2. **One copy of every count.** Duplicated counts diverge; the replication's `STATUS.md` says so
   explicitly at its head.
3. **`DECISIONS.md` is append-only and is never read in full.** At 3,113 lines it would consume a
   third of a fresh context. It exists for provenance, consulted by search.
4. **A project-level `CLAUDE.md` (or equivalent auto-loaded context file) is context, not
   procedure.** [X] The replication's own `CLAUDE.md` carries an explicit warning that the
   auto-loaded parent file describes a *different, completed* campaign and must not be treated as a
   plan. Auto-loaded files that describe a neighbouring project are an active hazard.
5. **The runbook wins over the auto-loaded file where they disagree**, and must say so.

### 4.2 `plan.json` per-function schema

Minimum fields, each of which proved necessary:

```
name              identifier
file, line        definition site
deps              callees, by name
traits            e.g. alloc, char_star, save_state, io
phase             init | one-shot | per-timestep | ...
proposed_verification   kernel | test-validate | file-output | differential | gate-only
disposition       (written on completion)
evidence          (written on completion — what the disposition rests on)
```

**[X] `phase` and `proposed_verification` are hypotheses, not facts.** `FindLine` was labelled
`one-shot` / `file-output` on a rationale copied from the `Parse*` family it sits among; it performs
no unit I/O and runs ~197 times per initialisation. A trial extraction reporting `Cases: 23`
falsified both labels in 78 seconds. **The runbook must require confirming these fields against
evidence before choosing an approach.**

---

## 5. Phase 1 — Substrate setup

**The highest-leverage phase, and the one this project handled worst.** Two setup defects between
them voided the evidence for 38 functions and produced 95 mismatched channels.

### 5.1 What it produces

1. A clean fork of the target at a pinned upstream commit.
2. A working build, with the translation-phase compiler flags applied **to every compiler involved**.
3. A working reset/restore pair (`reset_to_clean` / `integrate_all`) if extraction requires clean
   source.
4. Container or environment definitions, pinned.
5. Telemetry logging, verified end to end.
6. A hash manifest of every artifact transferred from a prior campaign.

### 5.2 The flags rule

**[X]** The original campaign set `-ffp-contract=off` in one line of `CMakeLists.txt` applied to
`CMAKE_CXX_FLAGS`. The replication's setup set `CMAKE_Fortran_FLAGS` and `CMAKE_C_FLAGS` and never
`CMAKE_CXX_FLAGS` — the one that matters, because the translations are C++. g++ then fused every
`a*b + c` into an FMA while gfortran did not. **VIT's kernel Makefile always set the flag**, so
kernels verified 20/20 while production diverged (`202608080404`).

Two rules:

- **Enumerate the compilers, not the languages you think you are using.** Set the flag on each, and
  assert it in the build log.
- **The kernel build and the production build must compile the translation with identical flags, and
  this must be checked mechanically.** "Kernel passes, gate fails" is documented as *suspect the
  translation*; it can also mean the two builds disagree, and that reading was missing from the
  runbook.

### 5.3 The setup gate

Before any function is attempted, every piece of apparatus is red-tested:

| Apparatus | Red test |
|---|---|
| Regression gate | Perturb one value in the translated build; confirm it reports mismatched channels and a non-zero exit |
| Kernel verification | Perturb one output field; confirm `0/N` |
| Extraction | Confirm `Cases: N` with `N > 1` on a known-live call site |
| Telemetry | Confirm a failed command appears with a non-zero exit code in the log |
| Reset/restore cycle | Run reset → build → restore → build; confirm both build and the second is bit-identical to the first |

**Exit criterion: every row above has been observed red at least once.** [X] Skipping this cost 38
functions of void evidence.

### 5.4 Copy manifest

For every artifact taken from a prior campaign, record the source path, the destination path, and a
content hash. **Anything not hash-identical to its source is a re-implementation and must be
red-tested** (P4).

### 5.5 Exit criteria

- [ ] Clean build from a pinned upstream commit
- [ ] Translation-phase flags applied to every compiler and asserted in the build log
- [ ] All five red tests in §5.3 observed red
- [ ] Reset/restore cycle round-trips bit-identically
- [ ] Copy manifest complete, every entry hashed or flagged as re-implemented
- [ ] Telemetry records a start and an end event with a correct exit code for a deliberately failing
      command

---

## 6. Phase 2 — Analysis and planning

### 6.1 What it produces

1. A complete procedure inventory: every hand-written procedure, its file, line, and size.
2. A dependency graph, and from it a topological work order.
3. Dead-code identification (never called; excluded from scope with reasons recorded).
4. Boundary identification: what is in scope for translation and what stays in the source language,
   crossed by a defined interface.
5. Line-coverage data from the full test suite.
6. `plan.json`.

### 6.2 Ordering

**[X] Leaf-first ordering is wrong as a default.** It defers the entire callee-bridge code path,
which is the most defect-dense part of the infrastructure. In the original campaign, leaf-first left
that path untested for 31 functions; 30 kernel callee bridges across 17 of 43 kernels declare a
different argument width than the C++ header for the same argument, and nothing caught it because no
leaf function exercises a bridge (`202608080128`, `202608072334`).

**[P] Order by dependency layer, but translate one non-leaf early** — deliberately, as an
infrastructure probe, before committing to a long leaf phase.

**[M]** The cost objection to non-leaf-first does not hold: a function with translated callees costs
only modestly more than a leaf once the callee-bridge path exists. The premium is setup, paid
once.

### 6.3 Coverage data is definitive; do not trace by hand

**[X]** Manually reasoning about which test case exercises a call site produced wrong answers
repeatedly. Line-coverage data answers it directly, and the extraction step depends on choosing a
call site with real traffic.

### 6.4 Exit criteria

- [ ] Inventory complete; every procedure classified in-scope, out-of-scope, or dead, with reasons
- [ ] Dependency graph built; work order derived from it, not from file order
- [ ] Coverage data generated from a clean build and committed
- [ ] `plan.json` complete, with `phase` and `proposed_verification` marked explicitly as hypotheses
- [ ] At least one non-leaf function identified as an early infrastructure probe

---

## 7. Phase 3 — Gate construction

Separated from Phase 2 because it is the load-bearing verification layer and because **both
campaigns deferred it and both paid.**

### 7.1 What the gate is

An end-to-end comparison: run the full system with the translated build, compare every output
channel against a baseline captured from the clean original build, require bit-identity.

**[M]** The gate is ground truth. Kernel verification is a smoke test that covers 20 of tens of
thousands of calls; the gate catches everything that reaches system output, including
feedback-amplified sub-ULP differences. When they disagree, the gate is right.

### 7.2 Baselines come from clean source

Baselines must be regenerated from the **pre-integration original**, never from a partially
integrated tree. [X] The replication regenerated all 27 during a repair and found 26 byte-identical
and one differing only because that scenario itself had been repaired — a check that cost nothing
and closed an open concern by measurement rather than argument.

### 7.3 Scenario coverage is a Phase 3 deliverable

**[X] Both campaigns discovered gate gaps late.** The original grew 9 → 27 scenarios across the run.
The replication ran 27 scenarios until #46, then found four uncovered branch families and added six
scenarios. That is Phase 3 work deferred by 45 functions, not new information.

The gate must be **branch-coverage measured** at construction, and its gaps recorded in a
known-gaps table in `STATUS.md`, with each gap either closed or explicitly accepted.

### 7.4 The addition-only rule

Once a scenario has a baseline and functions have been gated against it, **it is immutable.** New
coverage arrives as new scenarios (P5). Tooling should support generating a subset of baselines
without touching existing ones — [X] the replication needed a `--baseline "28 29 30"` subset form
precisely because the naive path would have wiped and regenerated all 27.

### 7.5 A scenario must be alive

**[X]** Scenario 13 in both trees configured an invalid parameter combination, which the target's
own input validation rejected, disabling the controller for the entire run. The scenario completed,
wrote a full-length output file, and gated green — comparing an uncontrolled system against itself.
Eleven of its thirteen channels were identically zero. **Every "27/27" for months was 26 live
scenarios and one null one** (`202608080615`).

Two checks, both cheap:

- **Assert the run was not rejected.** Any error/status flag the system exposes must be checked and
  recorded per scenario.
- **Assert output variance.** A channel that is identically constant across a scenario is a
  candidate null.

### 7.6 The gate cannot see everything

**[X]** The gate compares system output channels. It cannot see:

- error paths no scenario triggers (`sigma`, `unwrap`, `CheckInputs`)
- code executed with pinned output (P9)
- anything downstream of a disabled subsystem

**[M]** This is not a defect to fix but a limit to record. The `CheckInputs` copy-back defect passed
the gate in both directions — with and without the bug — which is an experiment, not an argument
(`202608081128`).

**[P]** A gate that compares only system channels has no assertion on the error path. Adding cases
that supply deliberately invalid configuration and assert the expected error code and message is a
different *kind* of gate case, and is the only way a copy-back regression on a validation function
becomes visible to the routine gate.

### 7.7 Exit criteria

- [ ] Gate runs, prints the compared count next to the pass count, and exits non-zero on mismatch
- [ ] Gate observed red under a deliberate perturbation
- [ ] Every scenario confirmed alive: no rejected runs, no all-constant channel sets
- [ ] Branch coverage of the gate measured; gaps recorded in a known-gaps table
- [ ] Baselines generated from clean pre-integration source, committed
- [ ] Subset-baseline generation exists and does not disturb existing baselines

---

## 8. Phase 4 — The function loop

### 8.1 The cycle

Per function, in order, no steps skipped:

1. **Confirm** the function's `plan.json` metadata against evidence (§6.4). Re-classify if wrong.
2. **Select** the call site and test case from coverage data.
3. **Extract** runtime state at a live call site.
4. **Scaffold** the translation with the tool, never from scratch.
5. **Write** the translation.
6. **Verify** against captured state.
7. **Integrate**, generating the wrapper.
8. **Rebuild.**
9. **Gate.**
10. **Commit** the function.
11. **Commit** the state (§8.3).

### 8.2 Verification is layered, and the layers have known limits

| Layer | Establishes | Cannot establish |
|---|---|---|
| Kernel replay | structural correctness on captured inputs | correctness on inputs outside the capture window; anything about the wrapper |
| Gate | joint bit-identity of everything reaching system output | anything the gate cannot observe (§7.6) |
| Purpose-built harness | whatever it was built to establish | anything it was not, and nothing at all unless re-run post-integration |

**[X] The two layers are not independent in the way the design assumes.** Both view the C++
artifact; neither views the integration wrapper. A function whose effects the gate cannot observe,
verified only by a pre-integration instrument, **has nothing checking the integrated artifact**
(`202608081128`).

**Rule: when a function requires a purpose-built harness, run that harness again against the
integrated build.** For `CheckInputs` that rerun was the only thing between the run and shipping a
build with all 125 input validations silently disabled.

### 8.3 The state commit contract

The last action of every function is a commit that brings `STATUS.md`, `DECISIONS.md`, the dataset,
and — where applicable — `TRANSLATION_TIPS.md` and `RUNBOOK.md` current.

**[M]** In the replication this takes one to two minutes: five of the last six functions were
followed by a state commit within that window.

**This commit is the compaction boundary** (§9). Its completeness is the run's only durable
protection.

### 8.4 Dispositions and escalation

A function ends in exactly one disposition (P8), and the disposition records the evidence it rests
on. Escalation to a human is required only when:

- the disposition would be `blocked` and the blockage is in the *substrate* rather than the function
- a proposed change would modify existing gate scenarios or verification defaults (P5)
- a finding invalidates prior results

**[O]** Across 47 functions the replication escalated four times that this spec's author observed
directly — a blocked function, the gate-vacuity discovery, a set of gate coverage gaps, and a
proposed change to the kernel capture default. Three of the four are category two or three. The
count is from relayed reports rather than from a log, so treat it as an order of magnitude: **a
disposition vocabulary reduces escalation to roughly one function in ten, not to zero.**

### 8.5 Prohibitions

- **Never inline a callee to route around infrastructure.** [X] Every instance in this project was
  wrong: one inlining masked three callee-bridge bugs; another produced fourteen copy-pasted loops
  before extraction. If a bridge fails, fix the bridge.
- **Never work around a tool bug you control.** [X] Every "KGen bug" in this project but one was a
  usage error found by tracing the tool's source. The one genuine bug was fixed in the fork. A
  workaround leaves the defect for the next function — and in the `CHARACTER(*)` case, that was 11
  other functions.
- **Never change a verification default mid-run** without recording the comparability cost (P5).
- **Never take a green result at face value on first use** (P3).

### 8.6 Reproducing defects rather than fixing them

The translation reproduces the original's behaviour, including its bugs. **[M]** Four deliberate
non-fixes are recorded in this run — an unprefixed error message, a NaN steered to a bound, swapped
dimension numbers in a message, and a sticky error flag. Each is recorded in `DECISIONS.md` at the
point of translation. A "fix" is a divergence and the gate will find it, at a cost far above the
cost of writing it down.

---

## 9. Boundary discipline

### 9.1 The measured economics

**[M]** Measured across six segments in the replication, the fixed cost of a boundary is cache
repopulation: the first five requests spend most of their money on cache writes at 2× input, twenty
times the cache-read rate. Against it, context grows with every request and cache read grows with it.

Taking the actual cumulative cost of the first N requests of each real segment — so the
re-orientation ramp is measured rather than modelled — and adding one boundary per N gives a broad
flat minimum from N≈20 to N≈50, substantially cheaper than the observed cadence. **The run's average function
falls inside that range**, so one boundary per function sits inside the optimum.

**[O] Treat the projected saving as an upper bound.** The projection assumes the same work fits in the
same number of requests. Break-even tolerates substantial request inflation, but the one clean post-boundary
observation available — #47 — is confounded by an unrelated tool fix carried inside
the same span. Unresolved; the next several functions test it cleanly.

### 9.2 The cadence rule

> Compact immediately after each function's state commit, never mid-function. That commit **is** the
> hand-off: if `STATUS.md`, `DECISIONS.md` and the dataset are current, the session is safe to lose.
> Do not write a separate pre-compact flush or hand-off summary — `STATUS.md` is the summary, and a
> second copy costs output tokens for nothing.

Two consequences for the Worker: end every function with a complete state commit, and assume the
session ends there.

### 9.3 The post-boundary prompt

Fixed, three lines, unchanged between iterations:

```
Read STATUS.md, then RUNBOOK.md. Do not work from memory.

Confirm the next item against plan.json, including its metadata fields, and continue.

Report back when it is integrated.
```

**[M]** `/clear` starts with a smaller context than `/compact`. With state on disk, clear gives up
little and costs less.

---

## 10. Phase 5 — Termination and final evaluation

**[P] — this phase has not been executed. Everything here is proposed.**

The governing requirement: **the final evaluation must be independent of the instruments used during
the run**, because those instruments are precisely what overstated. Re-running the gate at the end
proves nothing that was not already claimed.

### 10.1 The claims audit

Mechanically extract every verification claim in the tree — commit messages, source comments,
documentation, status files — and cross-reference each against the artifact it names.

**[X]** `// After verification: ExtController kernel PASSED` appears in a shipped source file with no
`kernel/ExtController` fixture anywhere in the repository. It survived an entire campaign.

**[M] The check costs one command and has no false positives.** Run against the original campaign
while writing this spec: **43 of 55 translated sources carry a variant of that stamp, and exactly
one — `extcontroller.cpp` — names a kernel with no fixture.** The other 42 resolve. A defect that
outlived a completed campaign is recoverable by a loop over `grep -l` and `ls -d`.

This is the strongest argument in the spec for Phase 5, and it generalises: the claim and the
artifact it names are both in the repository, so the cross-reference is mechanical.

Checks:

- every claimed kernel fixture exists and contains state files
- every claimed pass count is reproducible from a committed artifact
- every "N/N" has a recorded N that matches the artifact
- every disposition's `evidence` field names something that exists
- every bridge's declared argument widths match the header it is compiled against — **[M]** the same
  sweep finds 30 mismatched bridge files across 17 of 43 kernels in the original campaign

### 10.2 Instrument coverage audit

- **Branch coverage of the gate**, function by function, recorded as a table rather than a claim.
- **Which layer actually established each function's correctness** — for each, name the instrument
  and what it could not see. `202608072158` calls this "evidence strength", and the finding is that
  it is never reported: a 20/20 on a non-discriminating input and a 20/20 on a rich one print
  identically.
- **The set of functions whose only verification ran pre-integration** (§8.2). Each needs a
  post-integration check or an explicit accepted-risk entry.

### 10.3 Adversarial stress

- Perturb each translated function in turn and confirm *something* goes red. A function no
  instrument can turn red is unverified regardless of its recorded disposition.
- Re-run the full gate from a clean build of the final tree, from clean baselines.
- Differential fuzz where an oracle is cheap (P7): the original implementation is still available and
  is the oracle.

### 10.4 Final deliverables

1. The translated system, building clean.
2. A verification ledger: one row per function — disposition, instrument, what it established, what
   it could not.
3. The known-gaps table, closed or accepted.
4. The failure catalogue (§13), updated.
5. The measurement dataset with its attribution caveats stated (§12).

---

## 11. The Auditor protocol

Per Worker report:

1. **Verify counts against artifacts, not against the report.** Read the fixture directory, count the
   state files, grep the source. [M] Every count in the #46 report checked out; the value is in the
   checking being routine, not in the outcome.
2. **Re-derive any number that came from a generated artifact.** [X] A whole finding was retracted
   because it was built on reading generated configuration files that are outputs, not inputs
   (`202608080517`).
3. **Ask what the green result compared** (P3).
4. **Ask what the instrument could not see** (§8.2).
5. **When a method is falsified, re-derive every claim it produced — not the subset someone named.**
   [X] The retraction above left a third affected claim standing for hours because nobody mentioned
   it.
6. **Never leak reference-implementation evidence into the Worker's prompts.** The Worker's
   first-pass rate is the measurement; an answer key destroys it.

### 11.1 The Auditor's own failure mode

**[M]** Ten Auditor claims were caught wrong in this run, **none by the Auditor**. Every one was a
confident inference from partial evidence: a rebuild-window analysis scoped to exclude all five
contradicting rows; a magnitude argument that a value "cannot be drift" when it was; a citation of
the exact lines that disproved the claim they were cited for.

The rule that follows: **prefer measuring to estimating — the measurement is almost always cheaper
than the argument about it.** And read what you are citing.

---

## 12. Telemetry

### 12.1 What to log

Per tool invocation: function, command, phase, start/end timestamps, exit code. Joined against the
harness transcript by request ID, deduplicated.

**[X] The steps that are not tool invocations must log themselves** or they vanish from the totals.
Rebuild and regress are the two.

### 12.2 Known attribution defects

| Defect | Effect | Fix |
|---|---|---|
| Merged disjoint spans | one interval from first start to last end; swallows unrelated work | pair start/end in order, per key, with a queue |
| Nested phase charged to enclosing function | inflates whoever was nearby | subtract inner spans, or refuse to print a flagged row |
| Instrument-building charged to a function | one function absorbs project-level cost | convention, not a bug — state it |

The third is not fixable in code. **`CheckInputs` cost 2,013 seconds, almost
none of it translation.** The per-function figure must either exclude instrument-building spans or
declare that it includes them.

### 12.3 Reporting rules

- **Never quote a cost in output tokens.** They are a small fraction of the bill.
- **Report the cache-read column**, since it is 79–81% of it.
- **Flag rows the joiner could not attribute**; do not print them as zero (P6).
- **`verify_runs` is not a difficulty proxy** [X] — it counts harness invocations, not translation
  retries. `attempts` is the real figure and must be recorded separately.

---

## 13. Failure catalogue

Every substantial defect found in these campaigns, by shape. **All ten share one property: nothing
happened, and nothing happening looked exactly like success.**

| # | Defect | Reported as | Would have been caught by |
|---|---|---|---|
| 1 | Gate heredoc never reached the interpreter | `REGRESS PASS: 27/27` | red test at first use (P3) |
| 2 | False `kernel PASSED` comment, no fixture | a source comment | claims audit (§10.1) |
| 3 | Unconditional `Scenario N: PASSED (…exercised)` | a print statement | assert-then-report |
| 4 | Telemetry query keyed on the wrong field | zero rows | non-empty assertion |
| 5 | Joiner finds no transcripts in-container | "1 transcript(s)", all-zero CSV | P6 |
| 6 | An aliasing fix that never fired | a passing build | red test |
| 7 | `interp2d` never entered its bilinear branch | 20/20, 20 distinct values | branch counters (P9) |
| 8 | Missing copy-back on a validation function | kernel pass + gate pass | post-integration harness rerun (§8.2) |
| 9 | KGen state build failed silently on a generated name | "No state data captured" | `netimes` in the tool log |
| 10 | CHARACTER OUT/INOUT bridge returned garbage | a clean compile | the loud sibling failure |

Note #10's mechanism: the **scalar** case failed loudly with a rank mismatch; the OUT/INOUT case
compiled and called the original on an uninitialised local. **Only the loud one caused the silent one
to be found.** Where a defect family has both a loud and a silent member, finding the loud one is an
obligation to search for the silent one.

### 13.1 Recurring root causes

Worth naming because each recurred in three or more distinct locations:

- **Type-width mismatch across the language boundary** (`LOGICAL` ↔ `INTEGER(C_INT)`) — five sites
- **Source-language case-insensitivity** producing duplicate identifiers — four generators
- **Index orientation** at the boundary (row vs column of a column-major array) — two functions,
  both of which were entirely that problem
- **Operator precedence** — `a*x**2` is `a*(x*x)`, not `(a*x)*x`; 20/20 in the kernel, catastrophic
  divergence in the gate

---

## 14. What this spec does not know

Stated explicitly, because a spec that hides its uncertainty is the failure mode it is written
against.

1. **Whether the boundary economics hold under the new cadence.** §9.1's projected saving is an upper bound with
   one confounded observation against it. Testable within three or four functions.
2. **Whether Phase 5 works.** It has never been run. §10 is the only wholly proposed section.
3. **Whether the Auditor can be eliminated.** The evidence is encouraging — the Worker caught three
   major defects once the discipline was installed — but every one of those catches followed a
   relayed instruction, and the counterfactual is untested.
4. **Whether the failure catalogue is converging.** Ten instances, still finding roughly one per
   three functions. That rate has not fallen, which means either the catalogue is incomplete or the
   apparatus keeps growing new surfaces.
5. **Whether any of this generalises past Fortran → C++ with a bit-identity oracle.** Bit-identity is
   an unusually strong and unusually cheap oracle. A domain without it — where "correct" is a
   tolerance or a judgment — would need a different Phase 3, and §7 is most of this spec's weight.
6. **The cost model is single-vendor, single-model, single-point-in-time.** The 79–81% cache-read
   share is a fact about this pricing structure, not about the method.

---

## 15. References

Principles and failures above cite these notes. All are in this workspace's `dev/`.

**Setup and apparatus:** `202608080346` (the gate never compared), `202608080404` (one line of
CMake), `202608080510` (copied vs re-implemented), `202608080615` (dead scenario 13),
`202608072000` (dead gate scenarios)

**Kernel evidence:** `202608072233` (compared nothing), `202608072250` (non-discriminating input),
`202608080246` (narrow output side), `202608080640` (the `0:0:1-20` window), `202608080729`
(algorithm never ran)

**Integration and layering:** `202608081128` (kernel verifies the translation, not the integration),
`202608072114` (verify does not gate the shipped wrapper), `202608080058` (the false claim),
`202608080128` (30 mismatched bridges), `202608072214` (the defect class that escapes)

**Ordering and cost:** `202608072334` (leaf-first is wrong), `202608080148` (the non-leaf premium),
`202608072355` (cost is context re-read), `202608080454` (full cost audit), `202608080003` (the price
of mirroring)

**Measurement integrity:** `202608080826` (merged spans), `202608080506` (nested phases),
`202608071913` (accounting scope), `202608072004` (attribution is ownership), `202608072158`
(evidence strength is not reported)

**Method:** `202608080700` (close by addition), `202608072320` (out of scope is not failure),
`202608080517` (the retraction), `202608080436` (citing one's own disproof)
