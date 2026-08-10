# Leaf-First Ordering Is Wrong When the Resource Belongs to the Caller

**Date:** 2026-08-07
**Status:** in-progress — observed at function #27; third disposition added
**Scope:** plan ordering methodology, replication runbook §9b

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The function

`ReadEmptyLine`'s entire body is `READ(Un, '(A)') Line` plus a counter increment. VIT generates:

```c
void ReadEmptyLine(int Un, int* CurLine)
```

`Un` is a Fortran **logical unit number** — an index into the Fortran runtime's unit table, not a
file descriptor and not a `FILE*`. C++ can increment the counter and cannot advance the file
position. Any translation written against that signature leaves the performance-file parse one line
behind on every call.

## Why neither existing disposition fits

- **Not `out_of_scope`.** Skipping a line is real work, not a workaround for a facility C++ lacks.
  `AddToList` dissolves into `std::vector`; this does not dissolve into anything.
- **Not `blocked`.** A C++-callable bridge that performs the read would have to *be* a Fortran
  routine doing `READ(Un,'(A)') Line` — which is this function. The translation would call itself.
  There is no tooling gap to close.

So a third disposition: **`deferred`**. Distinct from the other two because
**`AddToList` will never be mirrored; `ReadEmptyLine` will be.** Conflating them reports work that
will happen as work that never will, understating the method's eventual reach.

## The finding: the dependency is inverted relative to plan order

Verified read-only:

```
ReadEmptyLine call sites:  18, all in ReadSetParameters.f90
Calling procedure:         ReadCpFile   (all 18)
ReadCpFile defined at:     ReadSetParameters.f90:783
```

`ReadEmptyLine` is a **depth-0 leaf, scheduled at #27**. `ReadCpFile` — which opens the file and
owns the unit — is scheduled at **#74**. The leaf becomes translatable only once its caller owns the
handle, at which point skipping a line is `std::getline` on a stream C++ already holds.

**Plan order puts the leaf 47 positions before the thing that unblocks it.**

### Why the ordering criterion is wrong, precisely

Leaves-first ordering assumes **the callee's dependencies are a subset of the caller's**. That holds
for *data* dependencies, which is why the first 26 functions went smoothly: a leaf math routine
needs nothing its caller does not already have.

It inverts for *resource* dependencies. When the operand is a resource the caller owns — a file
handle, a unit number, an allocation, a lock — the callee cannot be expressed independently of the
owner, and translating the owner first makes the callee trivial.

**Dependency depth is the right criterion for data and the wrong one for resources.** Nothing in
the Phase 2 analysis distinguishes them. This generalises well beyond ROSCO: any codebase where a
helper operates on a caller-owned handle has the same inversion, and file I/O is the most common
case.

## Scope: 14 of 78, and the flag that hides them

The session scanned and found **14 procedures performing true Fortran unit I/O — 18% of the
codebase — all with the same inverted dependency, all scheduled early.**

The `file_io` trait in `plan.json` marks **24** and does not distinguish them from procedures whose
"I/O" is an internal `WRITE` to a `CHARACTER` variable, which translates normally — `interp1d`
already demonstrated that.

**Third incomplete Phase 2 flag**, after:

| Flag | Error |
|---|---|
| `gate_can_verify` | lists only unreachable functions; misses executes-but-uncompared (`202608072329`) |
| alloc trait | reported 5, actually 14 (`202608072320`) |
| `file_io` | marks 24; only 14 are true unit I/O, and the trait cannot tell them apart |

Worth stating plainly in any write-up: **the Phase 2 structural analysis was good enough to order
26 functions correctly and wrong in three separate ways about what it could not see from the call
graph alone.** That is not a criticism of the analysis — it is the measurement of how far static
structure carries, which is a result the project has never had.

## Where this leaves the remaining scope

Recomputed by the replication session from call sites rather than file location, of **51
remaining**:

| Wall | Total | Still ahead |
|---|---|---|
| Fortran unit I/O — deferred, inverted dependency | 14 | 13 |
| KGen automatic-length hoist | 11 | 10 |
| alloc trait — needs per-function judgement | 14 | 13 |
| **Union** | | **22 of 51 (43%)** |

**29 remaining functions are unobstructed** — materially more than an earlier note of mine claimed
(see the correction at the head of `202608072254`, where I inferred exposure from file location and
overstated the KGen-blocked set as 22 rather than 11). The control layer — `Controllers.f90` and `ControllerBlocks.f90` — is the only large
body of reachable, informative, comparable work remaining, and it is the only one that answers the
open question of whether the marginal cost holds for functions with translated callees.

## A fifth upstream ROSCO artifact

An **unguarded debug write at `ControllerBlocks.f90:58`** produces a `fort.401` file whenever
`PRC_Comm` is OpenLoop and `Ind_R_Speed` is set. Nothing in the gate touches it, and it is why
`PowerControlSetpoints` appears in the unit-I/O list at all.

Fifth artifact surfaced by translation pressure, after the two `Flp_Mode=2` uninitialized
variables, `ExtController`'s one-byte message buffer, and `NonDecreasing`'s name/behaviour
mismatch. Worth a line in the eventual upstream report.

## Limits of the ordering method

The workflow uses bottom-up phasing by dependency layer as the ordering
principle. This is a stated limit on it, with a number: 14 of 78 procedures, 18% of the codebase,
where bottom-up is exactly backwards. The remedy is not complicated — order by *resource ownership*
where a resource is involved — but nothing in the current method computes that.

This also adds to the setup-omission finding (`202608072320`): the plan was generated by an analysis
that could not see three properties it needed, and each surfaced only when a function required it.
Static structure orders a translation campaign well until it meets a property the call graph does
not carry.

## References

- Dev note `202608072320` — `out_of_scope` versus `blocked`; the disposition this extends
- Dev note `202608072329` — the `gate_can_verify` gap; first incomplete Phase 2 flag
- Dev note `202608072254` — the KGen automatic-length defect; the other layer-wide blocker
- Dev note `202608072149` — Track A/Track B; the campaign routed this layer around entirely
