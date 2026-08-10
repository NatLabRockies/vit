# What the Translation Loop Turned Out to Be

**Date:** 2026-08-09
**Status:** completed — reflection at M13, written before compaction
**Scope:** method

## Placement

Same as the other loop notes: OpenFAST workspace, outside the replication's container mount. The
repository itself is `translation-loop` (a private repository,
HEAD `969c01d`). **Do not merge this note into the ROSCO-workspace checkout until the
replication run has ended.**

## 1. It is not an orchestrator

The starting framing — automate the drive-loop, stop pasting prompts — turned out to be the least
interesting part. `loop/driver.py` is 263 lines and works. What consumed the effort and carries the
value is the machinery deciding whether a reported success is real.

What it became is closer to an **argument with running code as evidence**: that verification
apparatus in scientific computing routinely reports success while establishing nothing, and that this
is *detectable, cheaply*, if you measure the instrument rather than the result.

**The thesis compresses to one number.** The same 56 wrong translations, measured against two
instruments: the gate catches 32, the generated harness catches 56. Both print green on the real
translation.

## 2. The recursion, and why it converges

Every mechanism built to catch a fails-green had to be pointed at itself, and each time found
something:

| mechanism | what it found in its own author's work |
|---|---|
| mandatory `red_test()` | a red test passing on dot-free IDs while the parser could not read the real file, whose IDs contain dots |
| the phase gate | a gate exit path that omitted the compared-count line — the exact tell whose absence hid the original defect |
| the Phase 5 evaluation | it **destroyed the evidence it was checking**; now read-only, verified idempotent |
| `checks/brackets.py` | committed and pushed while documented nowhere |
| `tests/test_layout.py` | written because the README said 134 tests against 141; immediately found a directory diagram stale through an entire build |
| `harness/independence.py` | its first inference was backwards, and it reported *not applicable* as *dead weight* — **P6 violated inside the module that enforces P6** |

This is not bad luck. It is the shape of the domain: **any apparatus can produce the right-looking
output without doing the work, and that failure is invisible from the output.** The only defence is a
second thing checking the first, which has the same failure mode.

**What makes it tractable is that each level is far cheaper than the one below.** Checking that a
gate compared something costs a fraction of the gate; checking that a check can go red costs a
fraction of the check. The regress converges, and that is the only reason the discipline is
affordable. It is the answer to the obvious objection.

## 3. The claim kept getting smaller, and that was the work

What "verified" meant, in order:

1. 20/20 kernel IDENTICAL
2. …over 20 of tens of thousands of calls, sampled from the front
3. …bit-identical across 27 scenarios that compared **zero channels**
4. …passes both layers and is still wrong in six distinguishable ways
5. …because the gate observes the unit **through the simulation**, which chooses the inputs, the code
   that runs, *and* the outputs that are visible
6. …and 57% of plausible wrong versions get through
7. …measured by a rule set validated against eight toy units

**Every step reduced what the evidence supported.** The project's main output has been a steadily
more accurate and steadily smaller claim. Uncomfortable to write up; exactly what honest measurement
does.

## 4. The dependency it exists to remove is the one that built it

**The sharpest observation, and not previously stated plainly.**

Nearly every significant correction came from the user asking a question, not from the machinery:

| question | what it produced |
|---|---|
| *Should there even be exploratory validation work?* | reframed the whole cost analysis — most phase spend was retrofitted setup |
| *Shouldn't any non-standard bracket be a flag?* | replaced a name→criterion map that would have passed anything unrecognised |
| *Is the red test part of the loop?* | `E4.6` / `P12` |
| *Does anything act on the survivors?* | ledger wiring, and the retry prompt that no longer asks for a higher number |
| *Couldn't there be an infinite number of rules?* | all of M13 |
| *Each should be distinct — it should be mathematical* | the independence analysis; joint ablation; `dominates()` |
| *Redundancy costs maintainability and legibility* | changed a default that was set wrong |

The repository exists to remove the dependency on a human noticing, **and was built almost entirely
by a human noticing.**

That is not a failure — it locates the frontier precisely. The mechanisms catch **known shapes** of
failure reliably. Not one has ever caught a **new shape**. Every new shape arrived through a question
no rule was asking. This is `PLAN.md` §12.3 stated as the central fact rather than as an item.

## 5. Two economics that inverted, for the same reason

- **Mutation testing** was rejected as too expensive. The estimate assumed the wrong oracle: a mutant
  against a 33-scenario gate sweep is expensive, against a direct-call harness it is a function call,
  and fifty cost less than one gate run.
- **Exploratory validation** looked like the price of rigour until categorised: 62% was Phase 1–3 work
  discovered late, and doing setup properly is several times cheaper than deriving it function by function.

**A cost that looks intrinsic is usually a cost of the arrangement.**

## 6. What is actually load-bearing

Five sentences. Everything else is these applied:

- a green must name what it compared, and be able to go red
- `NOT_EVALUABLE` is never a pass
- absence must not render as a value
- report, do not classify
- read the artifact, not the message

57 rules, 12 predicates, 6 input rules, 10 operators — all downstream. **A method with few principles
and many instances is a method; the reverse is a checklist.**

## 7. What I trust least

1. **None of it has touched real code.** Validated on eight units of C and Python, written by me,
   containing defects I planted because I had read about the real ones. Strong evidence of internal
   consistency, weak evidence of external validity — the fixture and the tool were designed by the
   same mind in the same week. `harness/corpus.py` and the escape-coverage metric are the antidote
   and they are **empty**.
2. **The mutation retry prompt is a mitigation, not a solution.** An agent told a number is too low,
   holding a lever that raises it for free (declaring survivors equivalent), will sometimes pull it.
   Writing *"the score is not the objective"* helps; it does not make the cheap path stop existing.
3. **Nothing says whether the rule sets converge.** The escape-driven ratchet is the right shape.
   Whether real codebases produce a long tail of genuinely novel defect classes or a short one is
   unknown, and the corpus is what would answer it.

## 8. Concretely, where things stand

**M0–M13 built**, 227 tests collected, HEAD `969c01d` pushed. The last five commits are the ones a
reader should start from:

- `7d339d7` M11 — generated harness; five seeded defects killed with the random block off entirely
- `3c7e2d9` M12 — mutation score; gate 0.571 vs harness 1.000 on 56 mutants
- `176402a` — admissible ranges shared per quantity rather than restated per unit
- `6b42980` — independence: joint ablation, behavioural distinctness for operators
- `f1588ee` — the corpus, append-only, escapes as labelled positives

**Blocking any real use:** the front-ends are C and Python. The targets are Fortran and C++. That is
the next work and it is not small.

## References

- `translation-loop` — `SPEC.md`, `PLAN.md` §8 M11–M13, §10.7–10.8, §12
- Dev note `202608081244` — the spec, archived
- Dev note `202608081938` — the corrected cost dataset
- Dev note `202608082006` — what validation cost and what most of it actually was
