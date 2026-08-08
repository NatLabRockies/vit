# A KGen Defect That Blocks a Layer, and the Prior That Broke Three Times

**Date:** 2026-08-07
**Status:** in-progress — defect diagnosed, deliberately not fixed; scope quantified here
**Scope:** KGen fork, replication verification profile, `CLAUDE.md` and the `kgen-bug-attribution`
memory

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The defect

Extracting `GetPath` (#24), the kernel failed to build. KGen emitted
`CHARACTER(LEN=accinfile_size), ALLOCATABLE`, which is not legal Fortran for a non-constant length.

Diagnosed by the replication session: **KGen hoists the call site's dummies into the driver as
locals.** `accINFILE` is declared `CHARACTER(accINFILE_size)` — automatic length, legal for a dummy
argument, illegal for a local variable. KGen's type emission does not distinguish the two contexts.

The concrete fix is three declarations — deferred length plus
`ALLOCATE(CHARACTER(n) :: ...)` — but it touches type emission used by **every working
extraction**.

## It is a class, and the class is large

Verified read-only. The tree carries several automatic-length dummies whose length expression is
itself a dummy argument:

```fortran
CHARACTER(accINFILE_size),  INTENT(IN) :: accINFILE(accINFILE_size)
CHARACTER(accINFILE_size),  INTENT(IN) :: RootName
CHARACTER(size_avcOUTNAME), INTENT(IN) :: RootName        ← three sites
```

`CHARACTER(MaxLineLength)` and `CHARACTER(MaxParamLength)` are unaffected: those lengths are
`PARAMETER` constants and are legal as locals. The defect fires only when the length is passed in.

Counted against `plan.json`, of **54 functions remaining**:

| file | remaining |
|---|---|
| `ROSCO_Helpers.f90` | **18** |
| `Controllers.f90` | 14 |
| `ControllerBlocks.f90` | 8 |
| `ReadSetParameters.f90` | **4** |
| `Functions.f90` | 3 |
| `ROSCO_IO.f90` | 3 |
| others | 4 |

**22 of 54 sit in the config-parsing layer** whose call chains carry `accINFILE_size` and
`size_avcOUTNAME`. Any extraction hoisting one of those dummies hits the same emission.

## Why the scope changes the decision

The session deferred the fix for `GetPath` and recorded the defect with its diagnosis, rather than
risk the 23 working functions for a kernel that would have captured one non-discriminating case
anyway. **For that function the judgment is right.**

As a policy it is not sustainable. Deferring once is prudence; deferring twenty times silently
converts an entire layer's verification profile from kernel-replay-plus-gate to
test-validate-plus-gate. The ROSCO accounting distinguishes those explicitly — the campaign
split 43 kernel-verified against 9 gated at the simulation layer only.

**Track it as a number, not a footnote.** How many functions ended kernel-verified versus verified
by other means, and why each. If the replication lands materially away from 43/9, that is a finding
about the tool's reach on a differently-shaped codebase, not a blemish on the run. It belongs
beside the Track A/Track B divergence in dev note `202608072149`.

## The judgment worth noticing

The stated reason for deferring was that the kernel "would capture one non-discriminating case
anyway."

That is the `PathIsRelative` finding from **one function earlier** (dev note `202608072250`),
already changing how a fix-or-defer decision is weighed. The question moved from *can I make the
kernel build* to *would the kernel prove anything if it did* — which is the correct question, and
one nothing in the tool asks.

Fifth occasion this run of declining to do something that would have looked like progress, and the
first where a finding from the run immediately altered subsequent judgment rather than being
recorded for later.

## The KGen prior has now broken three times

`CLAUDE.md` and the preloaded `kgen-bug-attribution` memory both assert that every "KGen bug" in
this project turned out to be a usage error, and instruct investigating exhaustively before
attributing anything to KGen.

Tonight, three genuine KGen defects:

| Defect | Status |
|---|---|
| `kgsearch` missing `search_Char_Selector` | fixed, `c3c8f91` |
| `gen_kernel_callsite_file` emitting `PUBLIC` at non-module scope | fixed, `5ac811f` |
| **automatic-length dummies hoisted as locals** | **diagnosed, not fixed** |

Alongside three VIT defects that merely *presented* as KGen failures, because VIT's output is
KGen's input and the diagnostic surfaces far from the cause (dev note `202608072214`).

**The prior held through the entire original campaign and all of AeroDyn, and broke because
ROSCO's helper layer exercises Fortran constructs neither prior campaign's translated subset
reached** — CHARACTER handling, automatic-length dummies, block constructs in call headers. Same
mechanism as `--reverse-copy` never taking its FUNCTION branch (dev note `202608072114`): never
reached, so never visibly broken.

**The rule should stay. The exceptions must be recorded**, or the next session under-investigates a
real KGen bug on the strength of a prior that no longer holds for this shape of code. Already on
the after-the-run list in dev note `202608072043`; this is the third instance justifying it.

## Open

- [ ] Fix KGen's type emission for automatic-length dummies hoisted as driver locals. Deferred
      deliberately; revisit when the config layer is reached in bulk, or if a function in it needs a
      discriminating kernel.
- [ ] Amend the `kgen-bug-attribution` memory and `CLAUDE.md` with the three exceptions.
- [ ] Report the final kernel-verified versus other-means split against the campaign's 43/9.

## References

- Dev note `202608072250` — the non-discriminating input; the reasoning that justified deferring
- Dev note `202608072214` — VIT defects presenting as KGen failures; the pattern
- Dev note `202608072149` — Track A/Track B; the other verification-profile divergence
- Dev note `202608072043` — the tool-fix taxonomy and the memory-amendment item
