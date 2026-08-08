# Containment Is Geometry, Ownership Is Semantics

**Date:** 2026-08-07
**Status:** in-progress — joiner fix revised and still deferred to a function boundary
**Scope:** `scripts/join_telemetry.py`, replication runbook

## Placement

Same as dev notes `202608071913`, `202608071945` and `202608072000`: the OpenFAST workspace,
because the ROSCO-workspace checkout is bind-mounted into the replication's container and is the
repository that session commits to. **Do not merge into that checkout until the run has ended.**

## The fix I proposed was wrong

Dev note `202608071913` records three deferred joiner defects and a replacement rule. Its first
clause read:

> span fully contained in another → `nested` (no warning; tokens roll up to the parent, keep its
> own wall clock)

**That rule is wrong, and the replication proved it within hours of my writing it.**

Two nested spans occurred, and they need opposite treatment:

| Nested span | Parent | Correct treatment | Why |
|---|---|---|---|
| `__typefix__` | `sigma` | fold into parent | `sigma` *caused* it. One declaration, a genuine prerequisite for translating that function. |
| `__gate_repair__` | `unwrap` | keep separate | `unwrap` merely *triggered* it. It regenerated project ground truth and re-verified eight prior functions. |

Auto-rolling up would have made a twenty-line loop the most expensive translation in the run, and
would have done it silently. **Containment is a geometric property. Ownership is a semantic one.
Geometry cannot decide it.**

The replication session made this call by hand, declining to relabel the second case after having
relabelled the first, and recorded the distinction as a runbook rule. That judgment was better
than my rule.

## The correct rule: innermost wins, no roll-up

The current joiner attributes a record to the **first** containing span, and spans are ordered by
start time, so an outer span always wins. Attributing to the **innermost** (shortest) containing
span instead gives each row its own cost, and the analyst decides what to combine.

Measured on the live data, first-wins charges the whole gate repair to `unwrap`; innermost-wins
splits the combined span roughly in half between `unwrap` and `__gate_repair__`.

Two consequences, both good:

- **`unwrap` is recoverable.** Its real translation cost is about half what first-wins charged, which puts
  it beside `sigma` and `HPFilter` rather than making it the most expensive function in the run.
  It does not need excluding from medians after all.
- **The gate repair gets a price.** Its own row is a number worth having: what a
  mid-run repair of the verification apparatus cost.

Note this would not have disturbed the `sigma` case, because those four entries were already
relabelled to `sigma` in the cycle log — there is no nested span there any more.

### Revised replacement rule

- innermost containing span wins; **no roll-up**
- report containment as a `contained_by` column rather than a warning
- reserve the overlap **warning** for genuine partial overlap, which is the only case where
  attribution is actually ambiguous
- no cycle commands → `phase`
- any nonzero exit code → `failed`
- passing integrate → `integrated`; passing verify → `verified`
- otherwise → `in_progress`

Still deferred to a function boundary, and `LPFilter` (#10, 21 call sites) is a poor one to
interrupt. Still confined to `scripts/join_telemetry.py`, which nothing imports — see
`202608071913` for that analysis.

### The better fix is upstream of the tooling

The replication's own runbook rule — **close a function's span before doing project-level work** —
prevents the ambiguity rather than resolving it after the fact. The joiner change is a safety net
for when that is forgotten, not a substitute for it.

## A gap I flagged has closed, in the opposite direction from how it was reported

Dev note `202608072000` warned that the repaired baselines encode source changes gated only on the
weaker suite: `ErrMsg` (`CHARACTER(:), ALLOCATABLE` → `CHARACTER(1024)`) and `avcMSG`.

The replication session recorded, as an inexactness it chose not to smooth over, that the baseline
build predates the `ErrMsg` fix because no commit carries that fix without also carrying C++
integrations — so "the baseline is pure upstream Fortran" is now slightly untrue.

**It is backwards. That property is what makes the chain sound.** Verified:

```
fced772:ROSCO_Types.f90   CHARACTER(:), ALLOCATABLE :: ErrMsg
HEAD:ROSCO_Types.f90      CHARACTER(1024)           :: ErrMsg
```

The baseline is *closer* to upstream, not further. The current tree carries the `ErrMsg` change,
the `avcMSG` change and nine C++ translations, and matches that baseline bit-for-bit across all 27
scenarios including the three newly-live ones. That is the *upstream ≡ modified* leg of the
three-build chain, now covering the open-loop paths. The 20-second check recommended in
`202608072000` has effectively been performed.

Residual, known and unavoidable: the Flp_Mode fixes are in the baseline, so
*true-upstream ≡ baseline* is not established. Pure upstream segfaults on scenario 4.

## The sentence worth keeping

From the replication session's own report, on the dead-scenario finding:

> a bit-identical 27/27 gate was reporting full coverage while three scenarios ran an error path,
> and it was undetectable from inside the gate because the baselines were generated in the same
> broken state

That is not a ROSCO fact. It is a property of **any** golden-output regression suite: the
reference and the subject share the apparatus, so an apparatus fault is invisible to the
comparison by construction. It surfaced only because an unrelated extraction failed and the
session probed the cause instead of trying another scenario.

The obvious argument is that residual risk concentrates in unexercised inputs. This is a different
axis — the gate can be wrong about what it is exercising at all, and no amount of input coverage
detects it. The replication's sentence is stronger than anything in my own notes on the subject.

The corresponding defence is cheap and belongs in `regress.sh` regardless: **fail if any scenario
has fewer than two varying channels** (dev note `202608072000`).

## References

- Dev note `202608071913` — the deferred joiner defects; **its nesting rule is superseded here**
- Dev note `202608072000` — the dead gate scenarios; its outstanding check is closed above
- Dev note `202608071945` — the LOGICAL mapping regression
