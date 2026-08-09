# Retrospective: finding a silent restart bug, and the pitfalls along the way

**Date:** 2026-07-24
**Status:** completed
**Scope:** verification methodology; a case study in disciplined skepticism (incl. my own errors)

## What this note is

A retrospective on one investigation thread, kept because the *process* — including two wrong
turns I had to walk back — is more transferable than the single bug. The bug-specific record
(root cause, fix, verification) is `202607232319`; this note is the methodology and the honest
missteps. It is also a concrete instance of verification finding a real
defect that every existing gate had missed, and of the practitioner traps around it.

## The arc (compressed)

Starting question: could ROSCO's simulation-only functions be verified more rigorously? That
led, in one chain:
1. `PreFilterMeasuredSignals` (a "simulation-only orchestrator") was assessed as
   KGen-verifiable → running the extract+verify **found a VIT generator bug**: the kernel
   view-populator omitted `USE <kinds_module>, ONLY: IntKi, DbKi`, so a freshly regenerated
   populator didn't compile, and `vit verify` masked it as "no cases found." Fixed
   (`view_populator.py`: cast via `KIND(dest%field)`); `PreFilterMeasuredSignals` then verified
   **21/21 IDENTICAL** and moved from sim-only to KGen+sim.
2. The four *never-exercised* simulation-only functions were examined. The restart pair
   (`WriteRestartFile`/`ReadRestartFile`, `iStatus -8/-9`) had **no behavioral evidence** — the
   27-scenario gate never issues those status codes.
3. Built a targeted harness driving the real `-8/-9` dispatch. It **found a silent bug**: the
   C++ restart diverged ~4.2e6 from Fortran while the continuous path was bit-identical. Root
   cause: `objInst` instance counters reset *before* the `-9` `ReadRestartFile`, so the restored
   (checkpointed) counters clobbered the reset and the controllers indexed the wrong
   `FP/piP/rlP` state slots. Fixed, verified bit-identical, gate-checked, shipped to
   `integrated`, and audited for siblings (none).

Verification found two real bugs (one in the tool, one in a translation) that had passed every
prior gate — because no prior gate exercised them.

## The two wrong turns (recorded deliberately)

**1. I claimed the wrong verification bar.** I initially argued that *roundtrip
self-consistency* (does a restarted run reproduce the uninterrupted run?) was "the correct bar
for a self-contained checkpoint." It is not: roundtrip fails in **both** C++ and Fortran,
because ROSCO restart is checkpoint-restore + `SetParameters` re-init, not a bit-exact clone.
The empirics disproved my claim. The right bar is **cross-language output equivalence** (C++
restart ≡ Fortran restart): the checkpoint file formats differ by design, but a faithful
translation must reproduce the *outputs*.

**2. I reported a false pass ("0.0, verified").** An early manual run reported C++ restart ==
Fortran at `0.0` and I briefly declared the pair verified. That was wrong — it compared against
an **ambiguous/stale `libdiscon.so`**. ROSCO's real C++ `DISCON` entry lives only on the
`integrated` branch; `main`'s `discon.cpp` is a 1-line stub, so rebuilds from `main` produced a
DISCON-less library and provenance got confused across many reset/build cycles. The clean
apples-to-apples redo (integrated pure-C++ vs modified-Fortran, both `nm`/`ldd`-verified)
exposed the real ~4.2e6 divergence.

Both errors were caught by *re-running clean experiments and interrogating the pass* — the
project's own "interrogate every passing result / break-the-C++" discipline, applied to my own
work. That discipline is the point: a confident number is not evidence until its provenance and
its comparison are both verified.

## Transferable lessons

- **"Unverified" can mean "unverified AND wrong."** A function gated only at the simulation
  level and never exercised is not merely untested — it can be actively broken, silently. The
  restart pair was listed as "integrated" (implying equivalence) yet was wrong. The fix for
  this is cheap and specific: build a targeted harness for the unexercised path rather than
  leaving it on code-review.
- **Pick the achievable invariant.** When a natural bar (self-consistency) fails even for the
  reference implementation, it is the wrong bar; find one that the reference satisfies
  (cross-language output equivalence) and hold the translation to that.
- **Verify binary provenance before trusting a result.** `nm -D … | grep <entry>` and `ldd`
  (libgfortran vs libstdc++) on the *exact* binary that produced a number. Stale/ambiguous
  artifacts produce confident wrong answers; this cost real time here.
- **Restore-before-reset is a translation-ordering bug class.** Any state that is BOTH
  checkpointed AND reset per timestep must have its reset AFTER the restore. It is mechanically
  auditable: parse the checkpoint field list, flag any assignment to those fields before the
  restore call. Worth a lint if this recurs.
- **Localize by ruling things OUT rigorously.** The checkpoint serialization was *proven*
  correct first (310-field list + every array size matched), which redirected the search from
  "dropped field" to "dispatch ordering" and made the root cause findable. Disproving the
  obvious hypothesis cleanly is progress.

## Related

- Bug/fix record: `202607232319-rosco-restart-objinst-ordering-bug.md`.
- View-populator kernel-kinds fix: `202607232221-kernel-view-populator-kinds-fix.md`.
- Why restart was unverified: the simulation-only-10 / four-functions analysis (this session).
