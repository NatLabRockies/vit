# `NonDecreasing`: The Escaping Defect Class, Arising Naturally

**Date:** 2026-08-07
**Status:** in-progress — observed at function #20
**Scope:** the injected-defect experiment, silent failure modes; VIT pragma insertion

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The function

Verified read-only against baseline commit `974290e`:

```fortran
    LOGICAL Function NonDecreasing(Array)
    ...
    NonDecreasing = .TRUE.
    ! Is Array non decreasing
    DO I_DIFF = 1, size(Array) - 1
        IF (Array(I_DIFF + 1) - Array(I_DIFF) <= 0) THEN
            NonDecreasing = .FALSE.
            RETURN
        END IF
    END DO
```

A difference of exactly zero — a plateau — returns `.FALSE.`. **The function named
`NonDecreasing`, whose own comment says "Is Array non decreasing", accepts only *strictly
increasing* arrays.** Its name, its comment, and its behaviour disagree, and the code is the odd
one out.

## Why this is the sharpest example of the run

Translate it to the name and the comment — `<` rather than `<=`, permitting plateaus — and:

- it is **more correct** by every reading except the code's
- it **silently widens every `CheckInputs` validation** that calls it
- **it passes the gate.** No scenario supplies a table with a plateau, so nothing observes the
  difference

That last property is what separates this from every other translation hazard the run has
surfaced. Compare:

| Hazard | The "more correct" translation |
|---|---|
| Truncated `Pi` = `3.14159265359` vs `M_PI` | **fails** the gate |
| `a*x**2` written `a*x*x` | **fails** the gate |
| Mixed precision (`ReKi = C_FLOAT`) | **fails** the gate |
| Collapsing `NotchFilterSlopes`' two init conditions | **fails** the gate |
| **`NonDecreasing` with `<`** | **passes** the gate |

The first four are translation-tips material: the gate catches you. This one belongs with the
injected-defect experiment, because the gate does not.

## It is the injected-defect experiment, happening by itself

The injected-defect experiment used fourteen single-line defects, of which **three escaped both gates**: a float
comparison relaxed from `>` to `>=`, a clamp differing once an index reaches array length, and
`>=` weakened to `>` in a binary search. All three are comparison-boundary defects, and the experiment
instruments the unmutated code to show the condition never holds across the suite — 0 of 153,600,
0 of 500, 0 of 4,253,824 evaluations.

**`NonDecreasing` is that same defect class, arising naturally rather than by injection**, and
arising from the most reasonable-looking reading of the source. Nobody had to mutate anything. The
function's name invites the defect, the comment corroborates it, and the gate cannot see it.

That closes a gap in the injected-defect argument. The injected-defect table demonstrates that
comparison-boundary defects can escape; one may reasonably ask how often such a defect would
be *written*. This is an instance where writing it is the more natural act than not.

**It is also a silent failure mode**, one of the cases where no
error signal is produced. Here the signal is worse than absent: the name and comment actively point
the wrong way.

**In one sentence:** the campaign's own validation helper is
misnamed, and a translation faithful to its name rather than its code would widen every input check
that uses it while passing 27 bit-identical scenarios. The gate certifies behaviour on exercised
inputs; a plateau is not among them.

The replication session covered it with a harness test — `equal_adjacent_is_rejected` — which is
the right response: the property is now pinned by something other than the gate.

## Upstream

This is arguably a fourth ROSCO defect surfaced by translation pressure, after the two
`Flp_Mode=2` uninitialized variables and `ExtController`'s one-byte message buffer. Whether the
name or the code is wrong is a question for the ROSCO developers — but the two cannot both be
right, and every caller currently gets strictly-increasing semantics from a function that promises
otherwise.

## Same cycle: a VIT bug wearing KGen's clothes, third instance

Extraction produced a kernel that would not compile. gfortran reported *"Expecting END DO
statement"* at `END MODULE ReadSetParameters` — roughly 900 lines from the cause, in a generated
file. `DO` statements were balanced. The real damage was an `IF` construct whose body had been
dropped and whose `ENDIF` was re-emitted ahead of its header.

Cause: all four `NonDecreasing` call sites have the shape

```fortran
IF (cond .AND. .NOT. NonDecreasing(x)) THEN
```

and `insert_kgen_pragma` handled continuation lines but knew nothing about **block constructs**. It
wrapped the header alone, leaving an unterminated `IF` inside the callsite region. KGen did exactly
what it was told.

Fixed in `698de50`: a construct-terminator scan covering `IF`/`DO`/`SELECT`/`WHERE`/`FORALL`/
`ASSOCIATE`/`BLOCK`, nesting counted, quote-aware comment stripping, six tests.

**This generalizes.** A call inside a construct header is a common shape, and until now every such
call site would have failed this way, with a diagnostic pointing nowhere near the cause.

### The pattern is now worth naming

Three times this run a VIT defect has presented as a KGen failure, because the symptom appears in
KGen's generated output far from its cause. The standing guidance — `CLAUDE.md` and the
`kgen-bug-attribution` memory — says it is almost never a KGen bug. That prior held here and is
being sharpened rather than overturned:

- **2 genuinely KGen** this run (`search_Char_Selector`, `gen_kernel_callsite_file` `PUBLIC`)
- **3 VIT defects that looked like KGen** (`kgen_utils` linkage aside, this pragma bug is the
  clearest)

So the rule stands, with an addition worth carrying: **the diagnostic will point at KGen even when
VIT is at fault, because VIT's output is KGen's input.** Distance between symptom and cause is the
norm here, not the exception.

## Cost and the shape of the dataset

`NonDecreasing`: 924 s, almost entirely tool work — the pragma fix plus three
`test-validate` fixes closing the open item recorded under `Int2LStr` (link derivation collected
only `.a`/`.so` and `-l` flags, so ROSCO's `discon` target — built straight from sources with no
intermediate libraries — yielded `LIBS = -lgfortran` and a harness that could not link against the
function it tests; now falls back to the target's objects via `rglob`, since a flat glob omits
`src/SysFiles/`).

**Four of the last six cycles were dominated by tool work** — `GetRoot`, `interp1d`, `Int2LStr`,
`NonDecreasing`. The clean-cycle population is **still n=13, unchanged over six functions**.

That is itself the result, and it deserves stating plainly: **the marginal-cost
figure (393 s) characterises the leaf-math class only.** Leaving that class costs
tool work at a rate of roughly one novel gap every third function. A median computed across the
whole run would describe neither population.

Boundary fraction drifted down to 27.1%.

## References

- Dev note `202608072158` — evidence strength not reported; the `test-validate` open item now closed
- Dev note `202608072149` — the capability boundary; why these functions cost what they do
