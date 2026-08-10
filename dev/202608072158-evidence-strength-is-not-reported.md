# `vit verify` Reports Success Without Reporting How Much It Established

**Date:** 2026-08-07
**Status:** in-progress — observed at function #19; fix not made
**Scope:** `vit/kernel_verify.py`, `vit.yaml` schema

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The gap

`Int2LStr` is one-shot-guarded, so KGen extraction captured **one case**: `Num = 1`. Kernel
verification passed it. The output read `1/1 PASSED`.

That establishes nothing about sign handling, digit count, or field width. It is a green result on
a single input.

**`vit verify` has no notion of how much evidence it collected.** Checked: no case-count check, no
minimum, no warning anywhere in `kernel_verify.py`. `1/1 PASSED` and `20/20 PASSED` print
identically, carry the same authority in `vit.yaml`, and read the same in a commit message.

The replication session refused to let `1/1` stand beside the `20/20`s and recorded it as
non-comparable. That was a judgment the tool did nothing to prompt.

## Why this belongs with the others

This is the fifth instance this run of one pattern: **the apparatus reports success in a way that
overstates what it established.**

| # | Instance | Note |
|---|---|---|
| 1 | `libdiscon.so` resolving to the original tree | setup |
| 2 | `cmake --build \| tail` masking a failed build | `202608071913` |
| 3 | Four gate scenarios green while computing nothing | `202608072000` |
| 4 | Kernel verifying a wrapper integration does not generate | `202608072114` |
| 5 | **`1/1 PASSED` indistinguishable from `20/20 PASSED`** | this note |

Instances 1–4 are correctness gaps. This one is a **reporting** gap, which makes it cheaper to fix
and easier to leave: nothing computed the wrong answer, but a reader of `vit.yaml` cannot tell a
well-evidenced translation from a barely-evidenced one.

**Proposed fix, not made:** record the case count in `vit.yaml` beside the status, and warn below a
threshold. That converts a watcher-dependent judgment into a structural property. Belongs on the
after-the-run list.

## The counterweight: the least-evidenced kernel produced the best-evidenced translation

Having recorded the 1-case kernel as weak, the session used `vit test-validate` — already in VIT,
never exercised on this tree — which generates a BIND(C) bridge to the original Fortran and drives
both implementations over inputs the simulation never produces. For `Int2LStr`: **~4,100 values**,
covering both ends of `INTEGER(4)`, the digit-count rollovers, and the case where negating to
extract digits overflows. 6/6.

Compared with `memcmp`, not `strcmp` — Fortran CHARACTER is blank-padded and not NUL-terminated,
so a string compare would pass a translation that terminated early and left garbage beyond.

So `Int2LStr` now has **stronger evidence than any of the 20/20 functions**, from a kernel that
captured one case. Worth stating plainly: the number of replay cases is not a measure of
verification strength, and the tool currently presents it as though it were.

## Two more from the same pair of functions

### Mixed precision inside a single routine

`ReadAvrSWAP` (#18): `avrSWAP` is `REAL(ReKi)` and `Constants.f90` sets `ReKi = C_FLOAT`, so the
swap array is **single** precision while the rest of ROSCO is double. `-fdefault-real-8` does not
touch it because the kind is explicit. Three lines later, `REAL(NumBl)` has no kind and *is*
`REAL(8)`.

Two precisions in one routine, distinguishable only by reading kind parameters through a
`PARAMETER` in another file. VIT's scaffold got it right unprompted.

This belongs beside the truncated `Pi` (`3.14159265359`) and `a*x**2` as a third instance of the
same lesson: **the faithful translation must reproduce a property that is invisible in the
expression itself.** Of the three, this one is the least likely to be caught by review.

`ReadAvrSWAP` also unpacks ~40 hand-numbered Bladed slots, so an off-by-one yields a *plausible*
result rather than a wrong one — read slot 21 into `GenSpeed` instead of 20 and the controller
regulates against rotor speed with every unit check still passing. Transcribed as
`avrSWAP[20 - 1] /* 20 */` so each line is reviewable against Appendix A.

### VIT's test suite does not cover the integrate path

The first `Int2LStr` integration attempt was incomplete — two `result_is_array` guards missed in
`integrate.py`. **The build caught it. The 699 tests did not.** The suite covers units in
isolation; nothing exercises integrate end to end.

Structurally the same as `kernel_verify` verifying a wrapper `integrate` does not generate
(`202608072114`): in both cases the thing tested is not the thing shipped. It is the tool's own
verification exhibiting the defect attributed to gates generally.

### A principle worth naming

Rather than emit a callee bridge that would silently truncate a CHARACTER return, the session made
the generator raise `NotImplementedError`. **A loud unimplemented beats a quiet wrong answer** —
the exact inverse of the five silent-success instances tabulated above, and the first time on this
run that the choice was made deliberately at the point of writing rather than discovered afterward.

## Cost

`ReadAvrSWAP` 532 s, clean. `Int2LStr` 669 s, almost entirely tool work — VIT could
not express a CHARACTER-valued function result at all (dev note `202608072149`), and the
translation itself was first-pass.

Clean-cycle population n=13. `ReadAvrSWAP` at 532 s and `StateMachine` at 480 s are the two most
substantial clean translations, both well above the filter cluster near 390 s — 112 and 71 lines
against roughly 50.

**Open, recorded not done:** the generated kernel Makefile derives no project libraries (link line
written by hand; the next such function hits it again), and the callee bridge for CHARACTER returns
is unimplemented.

## References

- Dev note `202608072149` — CHARACTER-valued results; the capability boundary
- Dev note `202608072114` — the verification artifact is not the production artifact
- Dev note `202608072000` — apparatus invisible to itself
