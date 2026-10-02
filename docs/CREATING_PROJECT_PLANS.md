# Creating Project Plans

How to write the plan for translating a Fortran codebase, or a large part of one, to C++ with VIT.
This is not a template to fill in. It is a set of principles and practices, learned from planning
real translations, about what keeps a plan useful and what lets it drift.

## 1. Purpose

A translation large enough to need a plan is too large to hold in one head or one session. The plan
serves several purposes at once:

- **Shared mental model:** collaborators, including your future self and future agent sessions,
  understand what is being done and why
- **Scope boundary:** what is in, what is out, and where the edges are
- **Decision record:** why one approach was chosen over the alternatives, so no one has to make
  the same decision twice
- **Progress tracker:** what is done, in flight or blocked
- **Forecasting aid:** a rough estimate of the remaining effort, always imprecise
- **Retrospective anchor:** a reader months later can reconstruct what was known when

## 2. When you need a plan

A plan costs a few days up front and steady maintenance afterwards. It pays back only when the work
is large enough that people would otherwise lose alignment.

**Write a plan when:**
- the work spans weeks
- it decomposes into phases that depend on one another
- several people, or several agent sessions, will work on it over time
- what "done" means is not obvious and is likely to be negotiated
- the target is a whole codebase, a whole module or a substantial subsystem

**Do not write one for:**
- one-off scripts, small refactors and single-day investigations
- bug fixes, even complex ones: write a dev note instead
- experiments whose outcome is "we will learn something"
- work that takes less time than the plan would

If unsure, write a dev note first, and promote it to a plan if the scope grows.

## 3. How plans drift

Translation plans fail in recognisable ways. Each is a principle in section 4, inverted.

1. **Granularity drift.** Phases planned as single steps break into sub-phases during execution,
   because the plan gave phases of very different complexity the same level of detail.
2. **Implicit strategy decisions.** The biggest decision of a project, such as verifying file I/O
   at the simulation level because kernels cannot capture it, arrives mid-execution because the
   plan never asked the question.
3. **Unbudgeted toolchain work.** Compiler differences, parser bugs and build-system surprises
   consume a large share of the time, and the plan assumed the toolchain would just work.
4. **Post-hoc phases.** Work the plan never mentioned, such as a verification push after the
   translation is "complete", happens anyway, and the plan learns about it afterwards.
5. **Conflated scope.** Verification rigor grows severalfold while the translated surface stays
   the same, and a plan that tracks only "functions translated" cannot show it.
6. **Uniform confidence.** Trivial phases and guesses are written in the same declarative voice, so
   a reader cannot tell which parts of the plan are solid.
7. **No stopping points.** Natural moments to re-evaluate pass without anyone stopping, because the
   plan did not name them.
8. **Past and future mixed.** Months-old history and speculation sit side by side without markers.
9. **Thin cross-references.** Some phases cite their commits and dev notes and others do not, so
   the detail behind a phase has to be hunted for.
10. **A stale Current State.** Work continues while the plan's status section keeps its old date,
    and the plan quietly stops describing the project. An intention to update it is not enough; the
    update has to be part of the workflow.

## 4. Principles

### P1: Research precedes prescription

Write no plan text until you have structural facts about the target. Spend the first days producing
data: a procedure inventory, the call graph, the type and module structure, uncalled procedures,
the external interface and test coverage (Step 1 below). Commit the data alongside the plan, then
write the plan from it, not from guesses.

### P2: Confidence is explicit

Every phase, decision and open question gets a confidence label:

- **committed:** scope fully understood, work defined, ready to execute
- **planned:** the general shape is clear; details will come from analysis or earlier phases
- **speculative:** known to be needed, no concrete scope yet; will be revised before execution
- **unknown:** an acknowledged gap, awaiting investigation

A plan that labels everything "committed" is not telling the truth.

### P3: Facts, decisions and conjectures are labeled separately

Three kinds of content live in a plan, and they should not blend:

- **Facts** come from measurement: file, line and procedure counts from the inventory, or which
  tests reach a function from coverage. A figure taken before the inventory is an estimate, not a
  fact, and it can be wrong.
- **Decisions** are choices you have committed to: "generated types are produced by extending the
  code generator; hand-written types are handled by VIT's type analysis".
- **Conjectures** are what you expect, subject to revision: "the I/O routines will probably be
  rewritten as idiomatic C++ and verified at the simulation level, because a kernel cannot capture
  file state".

Most drift starts when a conjecture reads like a decision and takes months to un-decide.

### P4: Open questions are first-class

The plan has an Open Questions section. Each entry has:
- the question
- why it matters: what it blocks
- what information would answer it
- when that information is expected, or what work would produce it
- who decides, once the information is in

A question such as "how do we verify the parts KGen cannot extract, like file I/O?" belongs here from
day one, with a decision point, not in the middle of execution.

### P5: Granularity follows complexity

Do not force phases to the same size. A phase that translates three small functions needs a few
checkboxes. A phase that translates a complex stateful subsystem should be marked speculative and
broken down only when earlier work has informed it.

### P6: Plans are living documents, not contracts

The plan's job is to be approximately right about the current state, not to predict perfectly.
Completed phases become history; upcoming phases are rewritten as information arrives. Commit plan
updates often.

### P7: Cross-reference everything

Every phase, decision and major event links to its delivering commits (by hash) and its dev notes
(by ID). A reader should reach the execution detail in one step. The plan is the skeleton and the dev
notes are the flesh.

### P8: Revisit triggers are named

Name the points at which to stop and re-evaluate, for example:
- "When phase 2 completes, re-examine phase 3's ordering against the updated type inventory."
- "Before the I/O phase starts, revisit how file I/O will be verified."
- "Monthly: check that Current State matches the repository."

Named triggers make pausing legitimate.

### P9: "Done" is explicit, and changes are recorded

The plan states its done criteria in section 1. If the bar moves, for example from "the simulation
output diff is empty" to "every difference is explained", the plan records the change, the date
and the reason. A project that cannot say what it was trying to achieve cannot say whether it did.

### P10: Verification rigor is its own scope axis

Distinguish two dimensions:
- **translation surface:** how much code is ported
- **verification rigor:** how thoroughly the port is checked

They are independent: full translation with thin verification is possible, and so is the reverse.
State the target rigor ("byte-identical output on the existing regression tests", or "byte-identical
on a scenario set that exercises every mode, with an independent audit of the comparison"), and
treat raising it as a decision.

## 5. The method, step by step

### Step 1: Structural inventory (before any plan text)

**Goal:** queryable data about the target codebase.

VIT includes a command for this, `vit inventory`. It parses the source with fparser2, an optional
dependency that the container image provides (elsewhere, `pip install 'vit[inventory]'`):

```bash
vit inventory --src path/to/src --output inventory/ \
    [--deps path/to/dependency/src] [--search path/to/whole/repo] \
    [--generated-marker TEXT] [--entry-point NAME | --entry-points-file FILE]
```

It writes the following, each as JSON, plus a markdown summary:

1. **Procedure inventory:** for every procedure, its module, file, line count, arguments,
   visibility, `PURE`/`ELEMENTAL`, SAVE state, `BIND(C)`, and whether it is machine-generated.
2. **Call graph:** who calls whom. Leaves (no callees) are the natural first layer, with the
   exceptions in Step 5.
3. **Type inventory:** every derived type and its fields, including those from dependencies.
4. **Module dependency graph:** the USE graph, topologically sorted.
5. **Uncalled procedures:** procedures with no call site in the scanned source, each with the
   likely reason.
6. **External interface:** procedures called from outside the scope, which must keep their
   behaviour exactly.
7. **Line counts:** per file, module and procedure.
8. **Ordering exceptions:** procedures that do I/O on a Fortran unit they do not open, with the
   procedures that own it, and small non-leaf procedures to translate early (Step 5).
9. **Work order:** every procedure in scope, in the order to translate it, with the reason for its
   place and, given coverage, how to verify it (Step 5).

Two inputs only you can give it:
- **`--generated-marker`:** the text a code generator puts in the files it writes, so generated
  code is counted apart from hand-written code. Generated code is usually best handled by changing
  the generator, not by translating its output.
- **`--entry-point`:** the procedures a host program or framework calls. Nothing in the source
  shows those calls, so the tool cannot find them for itself.

**Uncalled is not dead.** A procedure with no call site in the scanned source may be:
- a C entry point (`BIND(C)`)
- called from a driver program or a test
- passed as a callback
- called from a commented-out line
- called from outside the scanned tree: pass the wider tree with `--search`

Investigate each before excluding any from scope. Expect the genuinely dead ones to be a small
fraction of the first count.

**Measure line coverage per test case.** Extraction needs a run that reaches the call site. List the
program's test cases in the `coverage` section of `vit.yaml` and run `vit coverage run`: it builds
an instrumented copy, runs each case and records which lines each reaches. `vit find FUNC
--coverage` then answers "which test case runs this function's call sites, and how often?"
without tracing code paths by hand. With it, `vit extract` runs the test case that runs the most
of the function and spreads the captured calls over the run, and `vit verify` reports which of the
function's lines they run. Each run also writes the procedures no test case reaches,
beside the coverage file. For each of those, add a test case that reaches it, or plan another
verification method:
[`VERIFICATION_METHODS.md`](VERIFICATION_METHODS.md) says which, and `vit reachability` settles
whether it is called at all.

**Sanity check:** before writing plan text, make sure you can answer "which leaf procedure has the
fewest arguments?" or "how many procedures in module X have no callees?" from the data alone.

### Step 2: Scope negotiation

**Goal:** agree what is in, what is out and what "done" means.

Have the conversation explicitly, and write the outcome into the plan:
- the target codebase
- the scope: the whole codebase, one module, or a set of functions
- the rigor: bit-identical, within a tolerance, or matching on selected outputs
- any deadline
- the stakeholders and their success criteria
- explicit out-of-scope items

Then record the scope where the tools read it, not only in prose, so the work order (Step 5)
follows it:
- the code to translate and the code that stays as it is: `vit inventory --src` and `--deps`
- the wider tree its callers live in, such as the whole repository: `--search`
- the entry points: procedures a host outside that tree calls, and procedures kept although
  nothing calls them (`--entry-point`, `--entry-points-file`)
- procedures left out or deferred by decision, each with its reason: an `--overrides` file

**Deliverable:** section 1 of the plan, "Vision and Done Criteria", and those inputs.

**Sanity check:** can the plan answer "is X in scope?" without asking anyone? With the scope
recorded as inputs, the work order answers it: X is in a step, or in the left-out list with a
reason.

### Step 3: Key decisions

**Goal:** enumerate the architectural choices that shape everything else.

Walk through the inventory and the scope, asking at each part of the system what choice is being
made. For example:
- translate line by line, or write idiomatic C++ for some parts and verify those differently?
- pass derived types as `BIND(C)` structs, as view structs, or as separate arguments?
- modify the source in place, or translate out of tree?
- verify with KGen kernels, at the simulation level, or both?
- for generated code: translate its output, or extend its generator?

For each decision, record the decision, the alternatives, the rationale and its status:
**committed**, or **deferred** to a named decision point.

**Deliverable:** section 3, "Key Decisions". Decisions made later, during execution, are added
here too, not left in the roadmap where they were made.

### Step 4: Open questions

**Goal:** make known unknowns first-class.

Every deferred decision becomes an Open Questions entry with the five fields from P4. Add the
technical unknowns that are not yet decisions: "does VIT handle this Fortran pattern?", "does KGen
work with this build?", "what does the framework's interface contract require?"

**Deliverable:** section 4, "Open Questions".

### Step 5: Phase roadmap

**Goal:** sequence the work, with explicit confidence.

For each phase:
- a name and a one-sentence goal
- its confidence label
- its dependencies on earlier phases
- its revisit trigger
- a place for delivering commits and dev notes, filled in as the work happens

A phase you are confident about can be a checklist; a speculative one is a paragraph marked "will be
revised".

**Ordering.** The order is computed, not chosen: `vit inventory` writes `work_order.md` (and
`work_order.json`), and the roadmap's phases are cut from it. Given `--coverage` from `vit coverage
run`, each procedure also gets its verification route: a kernel, with the test case that calls it
most to extract with, or "not reached", which needs another method. The rules:
- callees before callers, by layer; within a layer, procedures a test case reaches first, then
  smaller ones, then by name
- generated code, procedures nothing calls, and test code are left out, each with its reason; an
  internal procedure is translated with its host; procedures that call each other in a cycle are
  one step

The person running it supplies only what the source cannot show: the scope (`--src`, `--deps`), the
entry points a host outside the repository calls or that are kept although nothing calls them, the
test cases, and an `--overrides` file for the rare procedure to leave out or defer by decision, such
as a subsystem rewritten as idiomatic C++ rather than mirrored. Each override needs a reason, and
one that names no procedure is an error.

Leaf-first has two exceptions, both applied in the work order and listed in `ordering.json`:
- **A procedure that works on a resource another procedure owns,** such as a Fortran unit it did
  not open, cannot be translated on its own, because C++ cannot read from a Fortran unit. Translate
  it with its owner, or after it; once the owner holds the file as a C++ stream, such a leaf often
  shrinks to a line of standard library. The inventory names each one's owners.
- **The first non-leaf.** Calls between translated procedures go through machinery no leaf
  exercises, so a long leaf-only phase builds a record of passes that says nothing about it, and
  that machinery's defects all appear at the first non-leaf. Translate one non-leaf early, on
  purpose, as a probe; the work order puts the smallest candidate a test case reaches first, after
  its callees.

**Deliverable:** section 5, "Phase Roadmap".

### Step 6: Revisit triggers and review cadence

**Goal:** decide in advance when the plan will be reviewed:
- after every completed phase: review the adjacent phases for drift
- at the named triggers in the roadmap: stop and reassess
- on any strategy or done-criteria change: update the plan at once, with a Change Log entry
- on a fixed cadence: check that Current State is still current

**Deliverable:** section 6, "Revisit Triggers".

### Step 7: Write the plan text

Only now write prose. The data, scope, decisions, questions and roadmap already exist; you are
turning them into readable text, not inventing them.

**Deliverable:** the plan, committed alongside the inventory data.

## 6. Document structure

Every plan has these sections, in this order:

```markdown
# PROJECT PLAN: <Target> Translation

## 1. Vision and Done Criteria
   - Goal, done criteria, out-of-scope items, target rigor, stakeholders

## 2. Structural Inventory
   - Summary figures, pointers to the committed data, observations that shaped the plan

## 3. Key Decisions
   - One entry per decision: rationale, alternatives, committed or deferred

## 4. Open Questions
   - Each with the five fields from P4

## 5. Phase Roadmap
   - Phases with confidence labels, dependencies, revisit triggers, checkboxes and
     cross-references

## 6. Revisit Triggers
   - Named triggers and the review cadence

## 7. Current State
   - A dated snapshot of where the project is

## 8. Change Log
   - Changes to strategy, scope and done criteria: date, what changed, why, link
```

Keep the Change Log to plan-level changes. A log that also tries to record every session duplicates
the dev notes, is never complete, and gets abandoned. Point to the dev notes for execution history,
grouped by phase.

## 7. Confidence labels in practice

**Phases:**
- **committed** phases have full checklists and detailed scope
- **planned** phases have a goal and a rough outline
- **speculative** phases have a short sketch and a "will revise" note
- **unknown** phases are named placeholders

**Decisions:** committed decisions in section 3 with their rationale; deferred ones in section 3
with their decision point, and in section 4 as open questions.

**Open questions:** **unknown** (no information yet), **investigating**, or **answered** (the
decision moves to section 3).

A plan that downgrades "committed" to "planned" when it learns something is telling the truth.

## 8. Maintenance practices

**Make updating the plan part of finishing the work.** Update Current State, and tick the phase's
checkboxes, in the same commit or session that completes a function or a phase. A plan updated "later"
is a plan that goes stale.

**After every completed phase:**
1. Re-read the adjacent phases: has anything changed?
2. Update Current State, with the date.
3. Leave the completed phase's text as history; do not rewrite it.
4. Fill in its cross-references.
5. Commit.

**On a strategy shift:**
1. Write a dev note explaining it.
2. Add a Change Log entry.
3. Update the affected phases, and Key Decisions.
4. Commit.

**On a change to the done criteria:**
1. Add a Change Log entry. This is the most important kind.
2. Update section 1.
3. Update Current State against the new bar.
4. Commit.

**At a revisit trigger:**
1. Stop.
2. Re-read the affected phases.
3. Ask whether the plan is still right.
4. If it is, note the review in the Change Log and continue.
5. If not, revise, log, commit, then continue.

**On the review cadence:**
1. Does Current State match the repository?
2. Are the next three phases still the right ones?
3. Has any open question been answered?
4. Commit any updates.

## 9. Anti-patterns

- **Writing plan text before the inventory.** The plan is then built on guesses about the code.
- **Uniform granularity.** A trivial phase and a complex one should not look alike.
- **Implicit strategy decisions.** "We will work out how to verify X when we get there" is a deferred
  decision; give it an entry and a decision point.
- **Rewriting completed phases.** Keep history accurate; corrections go in Current State or the
  Change Log.
- **Treating the plan as a contract.** Revisions are expected, not failures.
- **A Current State without a recent date.** If it has not changed in a month while work has, the
  plan is stale, and a stale plan misleads.
- **Past and future without markers.** Write "Phase 3 (complete): delivered..." and "Phase 7
  (planned): will deliver...".
- **Missing cross-references.** Every completed phase cites its dev notes and commits.
- **A moved "done" bar with no Change Log entry.**
- **Writing the plan alone.** A plan is a conversation with the people it serves, about scope,
  decisions and unknowns.

## 10. How this fits with the other documents

- **The plan** says what is being done and why, phase by phase. There is one per translation.
- **An operating manual for the workspace,** such as a `CLAUDE.md` an agent loads, says how each
  step is done: the workflow, the commands, the test cases and the rules.
- **Dev notes** record what happened, dated and in detail, and the plan cites them.

Start a translation with this document, write the plan, then execute it and write dev notes as the
work happens.

The [case studies](case-studies/README.md) hold two real plans: ROSCO's, written before this method
and kept with a retrospective of where it went wrong, and AeroDyn's, written with it and maintained
as the work continues.

## 11. Limits

These practices come from Fortran-to-C++ translation with a small team and a bit-identical
verification goal. They are hypotheses for the next project, not settled method, and may not carry
over to work of a different shape. When a principle proves wrong, mark it here; when a new one
emerges, add it.
