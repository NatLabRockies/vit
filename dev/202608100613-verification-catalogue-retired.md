# Retiring the verification catalogue: 49 tips, four mechanisms, one measurement

**Date:** 2026-08-10
**Status:** completed
**Scope:** translation-loop / VIT verification method

Implements `translation-loop/POST_REPLICATION_ACTIONS.md` §8, Step 7. This note is
the destination the catalogue was migrating toward: the tips stay as *design
evidence for why four mechanisms exist*, not as instructions anyone reads.

## The claim

Every one of `VERIFICATION_TIPS.md`'s 49 entries has the same shape:

> *layer L cannot distinguish implementation A from B under condition C.*

The zero gain, the single row, the saturating clamp, the dead plant, the
conversion factor on a dead-signal path — all instances of one question. And
that question is **decidable without knowing C in advance**, which is what makes
the catalogue retirable rather than merely long.

The seven sections, and where each goes:

| section | entries | subsumed by |
|---|---|---|
| What a kernel does and does not sample | 8 | mutation score |
| Choosing the scenario and the window | 8 | input rules R1–R6 |
| Probes: making reachability measurable | 7 | `vit reachability` (deletion probe) |
| Where both layers are blind at once | 10 | mutation score |
| Harnesses that lie | 5 | red test (`vit verify`) |
| Extraction and tooling mechanics | 11 | VIT bug reports, not guidance |

The last row is the one the migration keeps rediscovering: **when a tip explains
the tool's behaviour rather than the language's, it is a bug report with a note
attached.** Those eleven belong in issues, not in a catalogue.

## The measurement §8 asked for, and the answer it did not expect

§8's second bullet asserted a co-design gap:

> `const_tweak` perturbs by ±1 while every R6 neighbour is ULP-scale; the two
> disagree about scale, so the operator set and the input rules must be
> co-designed. Neither is complete alone.

That is a reasoned concern, not a measured one. The decidable form is
per-operator: **does ablating R6 change whether that operator's mutants die?**
Measured on the tiny campaign — 8 units, 57 non-equivalent mutants, the
campaign's own kill logic:

```
operator            R6 ON          R6 OFF        delta
arith_op            16/16  1.00    16/16  1.00   +0.00
assoc_reorder        1/1   1.00     1/1   1.00   +0.00
collapse_stride      1/1   1.00     1/1   1.00   +0.00
compare_op           4/4   1.00     3/4   0.75   +0.25   <-- R6 load-bearing
const_tweak         13/13  1.00    10/13  0.77   +0.23   <-- R6 load-bearing
drop_factor          9/9   1.00     9/9   1.00   +0.00
index_offset         1/1   1.00     1/1   1.00   +0.00
negate_cond          6/6   1.00     6/6   1.00   +0.00
swap_operands        1/1   1.00     1/1   1.00   +0.00
swap_returns         5/5   1.00     5/5   1.00   +0.00

total with R6 ON: 57/57 = 1.000
```

**The concern is false as stated.** `const_tweak` is one of exactly two
operators R6 is load-bearing for — its kill rate falls from 1.00 to 0.77 when R6
is ablated. The two do meet.

The reason the reasoning went wrong is worth more than the result. The premise
("every R6 neighbour is ULP-scale") is *true of the neighbours* and false of the
rule: `_r6` emits `L, -L, 2L, -2L, 3L, nextafter(L, ±inf), L - |L|·1e-12` — the
literal's **own value and its multiples**, not only its ULP neighbourhood. A ±1
tweak of a constant changes behaviour at inputs near that constant, and R6 puts
inputs exactly there. The scale observation was correct; the conclusion drawn
from it described a rule that does not exist.

This also confirms, from the other direction, the finding already recorded in
`_r6`'s docstring: widening R6 to unit scale was measured to change *nothing*.
Consistent — the coverage was already present.

**So: no change to either set.** The operator set and the input rules are
co-designed as they stand, and that is now a measurement rather than an
assumption.

### Scope, stated rather than implied

Eight units and 57 mutants is a small corpus, and every unit scores 1.000 under
the full rule set, so this measures which rules are *necessary* on that corpus —
not that the pairing is complete on a harder one. What it does settle is the
specific claim §8 made, which was about a mechanism, not a corpus size.

### The instrument had to be validated first

The first version of this measurement reimplemented the reference call
convention and reported **0/57 killed** — impossible, because the suite asserts
1.000 per unit. Reimplementing the oracle produced a broken oracle. The fix was
to call `mutation.py`'s own `kills_under_harness` rather than write a second
one, and the total then reproduced the suite's 1.000 exactly, which is what
makes the per-operator deltas trustworthy.

Same discipline as "never inline a callee to work around an infrastructure
problem", one level up: **do not reimplement the oracle you are measuring
with.** A measurement instrument needs its own calibration against a known
answer before its novel readings mean anything — and here the known answer was
sitting in the test suite.

## What remains judgement

Unchanged from §8, and small:

- **Admissible ranges** — ten entries in `ranges.toml` for `CableControl`,
  correctly quarantined in one reviewable file.
- **The mutation operator set itself** — a defect outside it is not measured.
  That is a real limit, but it is a different limit from the scale mismatch this
  note falsifies.

## Disposition of the files

`campaigns/rosco-tips/VERIFICATION_TIPS.md` (1,434 lines) and
`TRANSLATION_TIPS.md` (703 lines) stay where they are, as campaign evidence.
Neither is injected into any prompt; `TRANSLATION_TIPS.md`'s live descendants
were deleted 2026-08-10 (loop `8c8910c`, openfast `78eac3f9a`) and the
translate prompt is now generated from `vit/checks.py` plus the shared headers.

Related: dev notes `202608092050` (a generated harness kills CableControl),
`202608092223` (three VIT generator disagreements).
