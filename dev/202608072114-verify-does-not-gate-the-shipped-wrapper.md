# The Kernel Gate Verifies a Wrapper Integration Does Not Generate

**Date:** 2026-08-07
**Status:** in-progress — fixed in VIT `ce7f660`; two functions knowingly left unrepaired
**Scope:** `vit/kernel_verify.py`, `vit/interface_gen.py`

## Placement

Same as the other replication notes: the OpenFAST workspace, outside the replication's container
mount and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout
until the run has ended.**

## The defect

`--reverse-copy` copies scalar fields back out of view-type INOUT arguments after the C++ call.
The runbook prescribes it whenever a translated function mutates such a field.
`generate_fortran_wrapper` threaded `reverse_copy` into `_generate_wrapper_subroutine` and dropped
it for `_generate_wrapper_function`, which did not accept the parameter at all.

**So the flag was a silent no-op for every FUNCTION.** `vit integrate --reverse-copy` accepted it,
reported success, and emitted a wrapper with no copy-back.

Found on `interp1d`, which writes `ErrVar%aviFAIL` and `ErrVar%ErrMsg`. Not by a failure — by
reading the emitted wrapper. Fixed in `ce7f660`, 699 tests pass, copy-back confirmed by reading
the wrapper again.

## Why nothing could have caught it

This is the part that matters, and it is a property of the pipeline rather than of this bug.

```
kernel_verify.py:1405   generate_fortran_wrapper(..., is_kernel=True, reverse_copy=True)
integrate.py:224        generate_fortran_wrapper(..., reverse_copy=reverse_copy)   # default False
```

**Kernel verification builds its wrapper with copy-back unconditionally. Integration builds it
only if asked.** So `vit verify` exercises a Fortran↔C++ boundary that differs from the one
shipped, and a clean 20/20 says nothing about the integrated path.

The simulation gate could not see it either: no scenario drives `aviFAIL` negative, so the fields
that would have differed are never observed.

Two gates, and the defect is invisible to both — one because it verifies a different artifact, the
other because it never reaches the affected inputs.

## Why this is a new category of residual risk

The obvious argument is that residual risk concentrates in **unexercised inputs**. That is a
coverage argument: more inputs would help.

This is not that. It is a **fidelity gap between the verification artifact and the production
artifact**. No amount of input coverage closes it, because the thing verified is not the thing
run.

It pairs with the other structural blind spot this run surfaced: kernel replay restores
`objInst%inst*` counters as captured input, so a counter-sequencing error cannot be detected at
any input coverage — the mechanism restores the very state whose sequencing would reveal the
error. The simulation gate caught that one; replay could not have.

**Two independent mechanisms by which replay is blind by construction rather than by sampling.**

## A third category for the tool-fix taxonomy

Dev note `202608072043` sorts the fixes into two categories. This one needs a third:

| Category | Example | Detectable by |
|---|---|---|
| Lost in transit to AeroDyn | `LOGICAL` mapping (`aafe272`), `Char_Selector`, `CHARACTER(*)` | re-running the first codebase |
| Always broken, documented as design | inverted scaffold guard | reading the code behind the workaround |
| **Always broken, never reached** | `--reverse-copy` on FUNCTIONs | pointing the tool at a differently-shaped codebase |

Verified: **the original ROSCO campaign used `--reverse-copy` zero times**, and all five AeroDyn
uses are on `SUBROUTINE`s (`ComputeUA360_AttachedFlow`, `CalculateUACoeffs`, `DBEMT_UpdateStates`,
`DBEMT_InitStates_AllNodes`, `DBEMT_ReInit`). The FUNCTION branch had never been taken by either
campaign.

The third category is the most uncomfortable because no signal could have found it. Not the gates,
which see only exercised paths. Not the test suite, unless someone had written a test for an
unreached branch. It surfaced because ROSCO's leaf layer is function-heavy where AeroDyn's is
subroutine-heavy. **The tool was finished for AeroDyn's *shape*, not merely for its type system.**

## Deliberate open exposure — narrower than first stated, twice

`sigma` and `unwrap` lack copy-back. The replication session recorded them as open rather than
repairing them, because re-integration is project-level work that would have contaminated
`interp1d`'s span.

**First correction, from the replication session.** An earlier version of this note placed them
under this bug. That is wrong. `--reverse-copy` was never *passed* for either function, so nothing
was silently stripped — the wrapper correctly omits a copy-back that was never requested. Whether
the flag should have been passed is a separate, pre-existing omission. **The cost of the bug
documented here was that `interp1d` was integrated incorrectly on the first attempt and nothing in
the pipeline could have said so.**

**Second correction, checked directly in the emitted C++ rather than assumed.** Neither function
originates an error:

```
sigma.cpp    aviFAIL / ErrStat assignments: none — read-only
unwrap.cpp   aviFAIL / ErrStat assignments: none — read-only
interp1d.cpp aviFAIL = -1 at two sites
```

Both write only `ErrMsg`, and only under `if (ErrVar->aviFAIL < 0)` — that is, when an error
raised by an *earlier* routine is already in flight. This is a ROSCO idiom: each routine prepends
its own name to a pending message, building a call-chain breadcrumb. `interp1d` is the contrast,
and raising errors itself is exactly why the missing copy-back mattered there and why that
function is what exposed the bug.

So the consequence for `sigma` and `unwrap` is:

- **No error can be lost.** `aviFAIL` is never written, so failure still propagates correctly.
- **What is lost is a breadcrumb** — their name would be missing from the message chain.
- It manifests only when an error is already in flight, which no scenario produces.

That is a **diagnostic-fidelity gap, not a correctness gap.** An earlier draft of this note said
both "write `ErrVar` only on error paths no scenario reaches," which is true but reads as though
error reporting could be silently discarded. It cannot be. The distinction matters because it is
the same overstatement this project keeps catching in itself, and because it changes the priority
of the open item from "correctness debt" to "cosmetic, fix at a convenient pause."

That is the same judgment as declining to fold `__gate_repair__` into `unwrap` (dev note
`202608072004`) — the third time this distinction has arisen, and the third time it was called
correctly. It is worth noting that the discipline of *not* silently improving the measurement has
now been exercised more often than any translation difficulty.

**Open item for after the run:** re-integrate `sigma` and `unwrap` with copy-back, and re-gate.
Low priority per the narrowing above — it restores a message prefix, nothing more, and no risk
accumulates while it waits.

## Two smaller observations from the same cycle

**`rebuild.sh` earned itself back.** The first rebuild failed because `vit integrate` copies shared
headers into kernel directories but not into the source tree. The script caught it instead of
moving the previous library forward — the `set -o pipefail` guard doing its job on a failure mode
nobody anticipated when it was added (dev note `202608071913`).

**`rosco_errvar.h` is the third occurrence of an idiom** that `sigma` and `unwrap` each spelled out
by hand: `ErrMsg` set and blank-pad, the `RoutineName//':'//TRIM(...)` prefix, Fortran `I2`
formatting. The accumulated-guidance mechanism producing shared *code* rather than shared prose.
It is also why `interp1d` cost what it did; the extraction amortizes across everything after it.

## Cost

`interp1d`: 687 s, 1 verify attempt, 2 integrate runs — against a clean-cycle
median of 393 s. Almost entirely the VIT fix, the same shape as `HPFilter` and `GetRoot`.
The translation was first-pass, now 13 of 15.

Boundary fraction rose, driven by the pre-compaction checklist and report, which are
inter-function by construction.

## A third blind spot, and this one is ahead of us

Found on `StateMachine` (#17), where `vit translate` auto-generated view structs for
`ControlParameters` (213 fields) and `LocalVariables` (167 fields) unprompted.

**`--reverse-copy` writes back every scalar in the view, not the ones the function touched.**
Measured: `vit_copy_scalars_to_localvariables` contains **328 assignments**, not the two fields
`StateMachine` modifies.

That is safe only while nothing executes between populating the view and copying it back. **The
callee-bridge design explicitly violates that assumption**, and the mechanism is already shipped:

```fortran
! Kernel stash: pointer to original Fortran type, set by wrapper before calling C++.
! Callee bridges USE this to pass the original type to Fortran callees.
TYPE(LocalVariables), POINTER, SAVE :: vit_original_localvariables => NULL()
```

Callee bridges pass the **original** Fortran type, not the view. So the lost-update sequence is
constructible today:

1. Translated function `F` populates the view from `LocalVar` — 328 scalars copied in
2. `F`'s C++ calls a Fortran callee through a bridge
3. The bridge hands it `vit_original_localvariables`, the real type
4. The callee writes `LocalVar%SomeField`
5. `F` returns; the wrapper copies 328 stale scalars back, overwriting it

The callee's write is silently discarded. Neither gate reliably catches it: the kernel harness
stashes the original, and the simulation gate sees it only if a scenario reaches the path *and*
the overwritten value propagates to a compared output channel.

**This is the third mechanism by which the gates are blind by construction**, alongside the
verify/integrate wrapper divergence above and kernel replay restoring `objInst` counters as
captured input. It differs from both in being visible *before* it bites — the first such case on
this run.

**Mitigation, stronger than recording it.** The copy-back should write only the fields the
translation actually assigns, which VIT can determine from the C++ it just generated. Copying
everything is safe only under an assumption the design contradicts. Recorded in the replication's
`DECISIONS.md` as a hazard for the depth-1 functions ahead; it should be fixed before the first
function that populates a view, calls back into Fortran, and copies back.

## References

- Dev note `202608072043` — the seven-fix tally; **this adds a third category**
- Dev note `202608072004` — ownership vs geometry; the same judgment, third occurrence
- Dev note `202608072000` — the dead gate scenarios; the apparatus-invisible-to-itself argument
