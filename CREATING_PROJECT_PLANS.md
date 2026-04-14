# CREATING_PROJECT_PLANS.md

A methodology for producing `PROJECT_PLAN_<TARGET>.md` documents for VIT translation efforts. This is not a template to fill in — it is a set of principles and practices derived from what worked and what drifted during the ROSCO translation project. Read this before starting a new plan document.

## 1. Purpose

Every VIT translation effort past a certain size needs a plan document. The plan serves several overlapping purposes simultaneously:

- **Shared mental model** — collaborators (including future-you) understand what we are doing and why
- **Scope boundary** — what is in, what is out, where the edges are
- **Decision record** — why we chose this approach over alternatives, so nobody redoes those decisions
- **Progress tracker** — what is done, what is in flight, what is blocked
- **Forecasting aid** — rough effort estimate, always imprecise
- **Retrospective anchor** — a reader six months from now can reconstruct what we knew when

The ROSCO plan (`PROJECT_PLAN_ROSCO.md`) served some of these purposes well (progress tracking, mental model, partial decision records) and others poorly (scope negotiation was implicit, forecasting was wrong about granularity, strategy shifts were invisible). This document codifies what we learned so future plans inherit the strengths and avoid the drift.

## 2. When you actually need a plan document

Not every piece of work needs a plan. Cost-benefit: a plan document is worth ~1-3 days of upfront work and ongoing maintenance cost. It only pays back if the effort is large enough that collaborators would lose alignment without it.

**Write a plan when:**
- Scope spans multiple weeks of focused work
- Work decomposes into multiple phases with dependencies between them
- Multiple people (or multiple AI sessions) will touch the project over time
- The "done" criteria are non-obvious and likely to be negotiated
- The target is a whole codebase, a whole module, or a substantial feature

**Do not write a plan for:**
- One-off scripts, small refactors, single-day investigations
- Bug fixes, even complex ones (use a dev note instead)
- Exploratory experiments where the outcome is "we will learn something"
- Work where the plan doc would take longer to write than the work itself

If you are unsure, default to writing a dev note first. Promote it to a plan document only if the scope grows.

## 3. What ROSCO taught us (honest retrospective)

`PROJECT_PLAN_ROSCO.md` is not a failure. It delivered a bit-identical pure-C++ port of a 53-file, 8-module Fortran controller in 38 calendar days with rigorous verification. But it was wrong in specific, informative ways that a better methodology would have avoided. Each failure mode below is a principle inverted.

**1. Granularity drift.** Phases 4, 5, 6, 7, and 8 all broke into sub-phases during execution (4A–D, 5A–B, 6A–B, 7A–B, 8A–C). The original plan specified these as single phases. The goal of each phase was right; the step count was wrong. Root cause: the plan tried to impose uniform granularity across phases of non-uniform complexity. Fix: mark confidence levels, let complex phases be sketches initially.

**2. Implicit strategy decisions.** Phase 10 shifted from "mirror-translate I/O line-by-line through KGen" to "write idiomatic C++ for I/O, verify at simulation level." This was the single biggest strategy decision in the project. It was smart. But it arrived mid-execution, not up front. The original plan was silent on the question "can KGen verify file I/O?" even though the answer was known to be no from day one. Fix: make key decisions first-class, with explicit "committed" vs "deferred" markers.

**3. Unknown unknowns that should have been flagged.** FITPACK stack scrubber, FMA contraction fix, KGen BIND(C) parser bug, PIIController requiring upstream Flp_Mode=2 bug fixes, `avr_size=500` silent heap overflow — none of these were in the plan. They couldn't have been predicted, but they collectively consumed significant fraction of project time. Fix: plans should budget for toolchain investigation, not assume the toolchain works.

**4. Phase 11.5 was entirely post-hoc.** The verification expansion push (scenarios 12–27, 13-array output, upstream comparison chain, adversarial audit, performance benchmark, integrated branch cleanup) was a legitimate 10-day effort that did not exist in the plan until after it was done. The "done" bar moved from "diff is empty" to "explain every bit." Fix: name the done criteria explicitly, and treat any change to them as a plan update requiring a change-log entry.

**5. Scope grew along an axis the plan did not acknowledge.** Verification coverage went from 692K values to 5.25M values — 7× growth. Translation scope stayed the same. These are different things. The plan conflated them. Fix: verification rigor is its own scope axis, separate from translation surface area.

**6. No confidence markers.** Phase 1 (three trivial functions) was 99% predictable. Phase 10 (I/O modules) was maybe 30% predictable. Phase 11 (DISCON entry point) was ~70%. A reader of the plan could not tell which was which — everything was written in uniform declarative voice. Fix: confidence labels on every phase, every decision, every open question.

**7. No revisit triggers.** There were natural moments in the project where stopping to re-evaluate would have been valuable — after Phase 4 completed, after the first `vit analyze-types` failure, after PIIController's bug excavation. These re-evaluations happened anyway, but implicitly. Nothing in the plan said "stop here and reassess." Fix: explicit dead-reckoning points in the roadmap.

**8. Historical and forward-looking content mixed without markers.** By Phase 11, the Phase 1 description was a 5-week-old historical record, the Phase 12 description was speculation, and "Current State" had drifted with every update. A reader couldn't tell which text was about the past vs the future without git-blaming individual paragraphs. Fix: temporal structure in the document format.

**9. No cross-reference discipline.** Some phase descriptions cited dev notes and commits; others didn't. A reader who wanted to understand Phase 8 had to guess which dev notes were relevant. Fix: every phase entry must link to its dev notes and delivering commits.

**10. The plan was never audited until the very end.** CLAUDE.md and PROJECT_PLAN.md were updated in April 2026 as a single cleanup pass after the project was done. That worked for documentation but not for execution guidance — the plan should have been wrong-but-updated throughout, not right-at-the-end. Fix: scheduled plan reviews, not opportunistic ones.

## 4. Principles

Ten principles distilled from the above. Every plan document should embody them.

### P1: Research precedes prescription

No plan text until there are structural facts about the target. Spend the first 2-3 days producing data artifacts (function inventory, call graph, type dependency graph, dead code list, module layering, framework boundaries). Commit those artifacts as data files alongside the plan. Then write plan text *using* them, not guessing.

The best-executed part of the ROSCO plan was Section 4 (Derived Type Translation Strategy) — and the reason was that it was research-backed before being committed to text. Every section should be written that way.

### P2: Confidence is explicit

Each phase, decision, and open question gets a confidence label:

- **committed** — scope is fully understood, work is defined, ready to execute
- **planned** — general shape is clear, details will fill in via analysis or early phases
- **speculative** — aware this is needed, no concrete scope yet, will be revised before execution
- **unknown** — acknowledged gap, awaiting investigation

A plan that labels everything "committed" is lying. A plan where late phases are honestly marked "speculative" is telling the truth about what is known.

### P3: Facts, decisions, and conjectures are labeled separately

Three kinds of content live in a plan document and they should not blend:

- **Facts**: what the codebase contains, derived from analysis. "AeroDyn is 42 Fortran files, 75,430 lines, 11 sub-modules." Not opinions, not plans — observations.
- **Decisions**: architectural choices we have committed to. "View structs for NWTC Framework state types, BIND(C) for scalar-only types." These were once conjectures, but we have decided.
- **Conjectures**: what we think we will do, subject to revision. "Phase 3 will probably translate BEMT before DBEMT because the call graph shows DBEMT depends on BEMT." These are best guesses that may change.

Mixing these is the root cause of most of ROSCO's drift. When a conjecture ("we will use KGen for I/O") read like a decision, it took months to un-decide.

### P4: Open questions are first-class

A plan document has a dedicated "Open Questions" section. Each entry has:
- The question itself
- Why it matters (what downstream decisions it blocks)
- What information would answer it
- When we expect to have that information (or: what work would produce it)
- Decision owner (who decides, once the information is in)

ROSCO's Phase 10 strategy shift should have been a named open question from day one: "How do we verify file I/O that KGen cannot extract? Decide before Phase 10 starts."

### P5: Granularity follows complexity

Do not force uniform phase size. A phase that translates three trivial functions should have 4-5 checkboxes and ~20 lines of description. A phase that translates a complex stateful module should be marked "speculative" and broken down only once the preceding work informs its scope.

The ROSCO plan's Phase 1 (three functions) and Phase 7B (10 main controllers) were presented at the same apparent granularity. They should not have been.

### P6: Plans are living documents, not contracts

The plan's job is to be *approximately right about current state*, not *perfectly predictive*. Revisions are expected. Every completed phase becomes part of the historical record; every upcoming phase is subject to being rewritten as new information arrives. Commit plan updates frequently.

### P7: Cross-reference everything with git and dev notes

Every phase, decision, and major event in the plan links to:
- Delivering commits (by hash)
- Associated dev notes (by filename)

A reader of a phase description should be able to reach the full execution detail in one click. The plan is a skeleton; dev notes are the flesh; cross-references are the ligaments.

### P8: Revisit triggers are named

Dead reckoning points: explicit markers for when to stop and re-evaluate. Examples:
- "After Phase 4 completes, re-examine Phase 5 sub-phase ordering using updated type registry."
- "Before starting Phase 10, revisit the mirror-vs-idiomatic decision with ROSCO developer + engineering review."
- "Quarterly: re-read Current State section and verify it matches actual repo state."

Revisit triggers prevent the "oh wait, should we stop and think?" moment from being lost. They create legitimacy for pausing the march.

### P9: "Done" is explicit and can change

The plan names its done criteria in Section 1 (Vision and Done Criteria) and flags any change as a first-class event. If the done bar moves — as it did for ROSCO, from "diff is empty" to "explain every bit" — the plan gets a Change Log entry recording what changed and why. This prevents retrospective confusion about what the project was actually trying to accomplish.

### P10: Verification rigor is its own scope axis

Plans should distinguish between:
- **Translation surface area** — how much code is ported
- **Verification rigor** — how thoroughly the port is validated

These are independent dimensions of scope. You can have 100% translation with 10% verification, or 10% translation with 100% verification. A plan that only tracks "functions translated" will miss the possibility of escalating rigor mid-project.

The plan should state its target rigor level explicitly ("byte-identical on 10 scenarios" vs "three-build chain on 27 scenarios with adversarial audit") and treat escalation as a named decision.

## 5. The methodology (step by step)

### Step 1: Structural inventory (2-3 days before any plan text)

**Goal:** produce queryable data about the target codebase.

**Deliverables** (committed as data files in `analysis/` directory alongside the plan doc):

1. **Function inventory** — every procedure in scope. For each: name, module, file, line count, argument signature, `PUBLIC`/`PRIVATE`, `PURE`/`ELEMENTAL`/neither, has SAVE state (yes/no), Registry-generated (yes/no).
2. **Call graph** — who calls whom, across and within modules. Computed. Used to identify leaves (no callees → first translation targets) and roots (many callees → last targets).
3. **Type dependency graph** — which derived types contain other derived types. Transitive closure. Used to identify the type system layering.
4. **Module dependency graph** — which modules USE which modules. Topologically sorted.
5. **Dead code list** — procedures with zero call sites anywhere in the scope. Candidates for skipping entirely (like ROSCO's `matinv3`, `CPfunction`).
6. **Framework boundary list** — procedures called by code *outside* the translation scope. These are the external API that must be preserved bit-identically.
7. **Lines-of-code metrics** — total, per-module, per-file, per-function. Used for effort estimation and identifying unusually large functions.

**Tools:** for Fortran, `fparser2` is the recommended AST toolkit — mature, Python-native, handles modern Fortran, part of the PSyclone ecosystem. Alternatives (LFortran, flang, VIT's own `fortran_parser.py`, KGen's parser) each lose on at least one dimension. For other languages, pick an equivalent mature AST tool.

**Output format:** JSON or structured markdown tables. JSON is better for programmatic querying; markdown is better for human scanning. Both if time permits.

**Sanity check:** before writing any plan text, verify you can answer questions like "what is the leaf function with the smallest argument list?" or "how many functions in module X have zero callees?" purely from the data files, without opening source.

### Step 2: Scope negotiation

**Goal:** establish what is in, what is out, what "done" looks like.

Have an explicit conversation (with the user, in writing in the plan doc) about:
- Target codebase identity
- Target scope — entire codebase? one module? a specific function set?
- Target rigor — bit-identical? within-tolerance? matches-on-selected-outputs?
- Deadline or timeframe if any
- Stakeholders and their success criteria
- Explicit out-of-scope items

**Deliverable:** Section 1 of the plan doc — "Vision and Done Criteria" — with all of the above written down.

**Sanity check:** if someone asks "is feature X in scope?", can the plan answer without further consultation?

### Step 3: Key decisions identification

**Goal:** enumerate the architectural choices that will shape everything else.

Walk through the inventory and the scope. For each piece of the system, ask: what choice are we making? Examples from ROSCO:
- Mirror-translate Fortran line-by-line, or write idiomatic C++?
- BIND(C) structs for types, or view structs, or decomposed field args?
- In-place source modification, or out-of-tree translation?
- KGen kernel verification, or simulation-level verification, or both?

For each decision:
- State the decision
- State the alternatives considered
- State the rationale
- Label it **committed** (decided, will not revisit without plan update) or **deferred** (waiting on information, will decide at point X)

**Deliverable:** Section 3 of the plan doc — "Key Decisions."

### Step 4: Open questions enumeration

**Goal:** make known unknowns first-class.

Walk through the key decisions and identify every one labeled "deferred." Each deferred decision becomes an entry in the Open Questions section with:
- The question
- Why it matters (what it blocks)
- Information needed to answer
- When we expect to have that information
- Decision owner

Also include technical unknowns not yet surfaced as decisions: "does VIT's parser handle this Fortran pattern?", "does KGen work with this build system?", "what does the NWTC Framework interface contract actually require?" These become investigation items.

**Deliverable:** Section 4 — "Open Questions."

### Step 5: Draft phase roadmap

**Goal:** sequence the work, with explicit confidence levels.

For each phase:
- Name and one-sentence goal
- Confidence label (committed / planned / speculative / unknown)
- Dependencies on prior phases
- Revisit trigger (when do we stop and reassess this phase's scope?)
- Placeholder for delivering commits and dev notes (filled in as work happens)

Granularity follows complexity. A phase you are confident about can be detailed in checkbox form. A phase you are speculative about is a paragraph or two with a "will be revised" marker. Do not force uniform detail.

**Deliverable:** Section 5 — "Phase Roadmap."

### Step 6: Revisit triggers and review cadence

**Goal:** decide upfront when the plan will be reviewed.

Explicit triggers to set:
- After every completed phase → review adjacent phases for scope drift
- At named dead reckoning points in the roadmap → stop and reassess before continuing
- On strategy decisions → update plan immediately with a Change Log entry
- On "done criteria" changes → update plan immediately with a Change Log entry
- Weekly or biweekly → quick "is Current State still current?" check

**Deliverable:** Section 6 — "Revisit Triggers." Plus a commitment to actually follow them.

### Step 7: Write plan text using the above

Now, and only now, write prose for each section. The data, scope, decisions, questions, and roadmap structure are already laid out — you are converting them into readable text, not inventing them on the fly.

**Deliverable:** complete `PROJECT_PLAN_<TARGET>.md`, committed alongside the inventory data.

## 6. Document structure template

Every `PROJECT_PLAN_<TARGET>.md` should have these sections, in this order:

```markdown
# PROJECT PLAN: <Target> Translation

## 1. Vision and Done Criteria
   - Goal statement
   - Explicit done criteria (what "complete" looks like)
   - Explicit out-of-scope items
   - Target rigor level
   - Stakeholders and their success criteria

## 2. Structural Inventory
   - Summary stats (LOC, function count, module count)
   - Pointers to committed data files (analysis/*.json or analysis/*.md)
   - Key observations from the inventory that shaped the plan

## 3. Key Decisions
   - One subsection per committed decision, with rationale and alternatives
   - One subsection per deferred decision, with decision point marker

## 4. Open Questions
   - Numbered list; each entry has the five fields above (question, why it matters, info needed, when, owner)

## 5. Phase Roadmap
   - Phases with confidence labels (committed / planned / speculative / unknown)
   - Dependencies, revisit triggers
   - Checkboxes for executed work (filled as phases progress)
   - Cross-reference slots for commits and dev notes

## 6. Revisit Triggers / Dead Reckoning Points
   - Named triggers embedded in the roadmap
   - Review cadence commitment

## 7. Current State
   - Date-stamped snapshot of where the project is right now
   - Updated after every completed phase or major event
   - Historical snapshots archived in a subsection or via git history

## 8. Change Log
   - First-class record of plan revisions
   - Every strategy shift, scope change, and done-criteria change logged here
   - Entry format: date, what changed, why, link to commit
```

## 7. Confidence labels in practice

Apply the labels from Principle 2 to everything:

**Phases:**
- **committed** phases have full checkboxes and detailed scope
- **planned** phases have goal + rough outline, details deferred
- **speculative** phases have a paragraph-long sketch + "will revise" note
- **unknown** phases are named placeholders — "Phase X: something about Y, TBD"

**Decisions:**
- **committed** decisions are in Section 3, with rationale
- **deferred** decisions are in Section 3, with decision point, *and* in Section 4 as open questions

**Open questions:**
- **unknown** — no information yet
- **investigating** — information gathering in progress
- **answered** — decision made, moves to Section 3

Use the labels consistently. A plan that downgrades "committed" to "planned" when updated is telling the truth about what changed.

## 8. Maintenance practices

**After every completed phase:**
1. Re-read adjacent phases — has anything changed about how they should look?
2. Update Current State section
3. Move completed phase text to historical record (keep the text, do not retroactively rewrite)
4. Fill in cross-references (delivering commits, dev notes)
5. Commit the plan update with a descriptive message

**On strategy shifts:**
1. Write a dev note explaining the shift
2. Add an entry to Change Log
3. Update affected phases in the Roadmap
4. Update Key Decisions section if a decision changed
5. Commit

**On done-criteria changes:**
1. Change Log entry (mandatory — this is the highest-impact plan update)
2. Update Section 1 (Vision and Done Criteria)
3. Update Current State to reflect the new bar
4. Commit

**At revisit triggers:**
1. Stop the work
2. Re-read the affected phases in the plan
3. Ask explicitly: is this plan still right? What would I do differently?
4. If the answer is "same plan" — note the review in Change Log, continue
5. If the answer is "different plan" — revise, Change Log, commit, then continue

**Scheduled reviews (weekly or biweekly):**
1. Read Current State — does it match reality?
2. Read top three upcoming phases — are they still the right next steps?
3. Read Open Questions — has any answered since last review?
4. Commit any updates with "Plan review YYYY-MM-DD" message

## 9. Anti-patterns

Things that look reasonable but cause drift:

**Writing plan text before doing structural analysis.** The result is plans based on guesses about the codebase. The ROSCO plan had this in places where it guessed at Phase 5-8 sub-phase structures. Always do Step 1 (inventory) first.

**Uniform granularity across phases.** Phase 1 and Phase 10 should not look the same in the plan document. Complexity is non-uniform; detail should follow.

**Implicit strategy decisions.** "We will figure out how to verify file I/O when we get to Phase 10" is not a plan — it is a deferred decision that deserves first-class treatment. Name it in Section 4 with a decision point.

**Retroactively rewriting completed phases.** Preserve historical accuracy. If Phase 5A was executed as described, leave that description alone even when you learn something new. Corrections go in Current State or Change Log.

**Treating the plan as a contract.** The plan is not a commitment to execute specific steps — it is a commitment to stay approximately right about what we are trying to do. Revisions are expected, not failures.

**Letting Current State drift.** A 3-month-old Current State section is actively misleading. Every Current State section should have a date, and if more than a month has passed without an update, the plan is stale.

**Mixing historical and forward-looking content without markers.** Use temporal language. "Phase 3 (complete): delivered..." vs "Phase 7 (planned): will deliver..." Do not let a reader guess which is which.

**Forgetting to cross-reference dev notes.** The plan is the skeleton; dev notes are the flesh. A plan that does not link to dev notes leaves readers without the execution detail. Every completed phase should cite its dev notes.

**"Done" bar drift without a Change Log entry.** If the project's definition of "done" changes — even informally, even because "we decided we care more about X now" — that is a first-class event. Write it down.

**Writing the plan in isolation.** Plan creation is a conversation with the user about scope, decisions, and unknowns. A plan written without that conversation is a solo guess at what the user wants.

## 10. ROSCO as retrospective case study

`PROJECT_PLAN_ROSCO.md` (formerly `PROJECT_PLAN.md`) can be read as a case study in this methodology — where it embodies the principles and where it violates them. A few specific observations:

**What ROSCO's plan did well:**
- **P1 applied to types.** Section 4 (Derived Type Translation Strategy) was research-backed before text. The type classification (BIND(C) structs vs view structs, the 4-blocker fixes to LocalVariables) was derived from investigation, not guessed. That section held up end-to-end.
- **P7 partially applied.** Many phases cited commits and dev notes. The cross-reference discipline was not consistent, but it was present.
- **P6 in spirit.** The plan was updated as phases completed. It was always "approximately right" even when late phases drifted.
- **Checkbox structure.** `- [x]` vs `- [ ]` made progress visible at a glance. Kept for this methodology.
- **Two-level structure.** Plan as skeleton, dev notes as flesh, with cross-references. The ratio of plan-text-to-dev-note-text was roughly 1:10, and that felt right.

**What ROSCO's plan would do differently under this methodology:**

1. **Confidence labels on all phases from the start.** Phases 1-3 would have been marked `committed`; Phases 4-8 as `planned` (shape known, sub-phases TBD); Phase 9 as `speculative`; Phases 10-11 as `unknown` until research arrived. A reader would instantly see where the plan was solid and where it was guesswork.

2. **Explicit open question from day one: "How do we verify file I/O that KGen cannot extract?"** This was the biggest deferred decision in the project and it was invisible in the plan until Phase 10 arrived. Under this methodology it would have been Section 4 entry #1 with a decision point labeled "before Phase 10 starts, consult ROSCO developer and engineering review."

3. **Phase 11.5 would have been anticipated.** Once the "verification rigor" axis is separate from "translation surface area" (P10), the possibility of escalating rigor mid-project is visible. The plan would have had a placeholder: "Phase 11.5 (speculative): verification rigor escalation if the initial target is judged insufficient." That placeholder would have been ready for the adversarial question when it arrived.

4. **Revisit triggers named in advance.** "After Phase 4 completes, re-examine Phase 5 sub-phase ordering." "Before Phase 10 starts, re-read the mirror-vs-idiomatic question." These would have been in the plan from day one, not discovered in the moment.

5. **Change Log section.** The strategy shifts (Phase 10 approach change, Phase 11.5 scope emergence, the integrated-branch-cleanup retrofit) all happened without formal log entries. Under this methodology they would be recorded the moment they happened, making the retrospective reconstruction trivial.

The ROSCO plan is not a failure — it is a very good plan document with specific, enumerable weaknesses, and the AeroDyn plan (and all future plans) should inherit its strengths and fix its weaknesses.

## 11. Relationship to CLAUDE.md and dev notes

- **CLAUDE.md** is for day-to-day work inside the VIT codebase. It documents how to build, test, extract, verify, integrate, and commit. Read it when you are *doing* work.
- **CREATING_PROJECT_PLANS.md** (this file) is for starting a new translation effort. Read it when you are *planning* work.
- **PROJECT_PLAN_<TARGET>.md** is the output of applying this document to a specific target. There is one per project.
- **Dev notes** (in `vit/dev/`) are the flesh of execution detail, cross-referenced from plans.

When a new project begins, read CREATING_PROJECT_PLANS.md first, then apply its methodology to produce PROJECT_PLAN_<TARGET>.md, then execute the plan and write dev notes as work happens.

## 12. A note on the limits of this methodology

This document is derived from one completed project (ROSCO). One data point is not a proven methodology — it is informed speculation from a particular kind of project (Fortran-to-C++ translation, small team, bit-identical verification goal). It may not transfer cleanly to projects with different shapes: UI work, infrastructure migrations, multi-team efforts, projects without a clear "done" criterion. The principles above are the ones that seemed to generalize from ROSCO; they should be treated as hypotheses for the next project, not gospel.

Revise this document when the AeroDyn project teaches us something new. If a principle proves wrong, mark it. If a new principle emerges, add it. This document is itself a living artifact, subject to the same revision discipline it prescribes for plan documents.
