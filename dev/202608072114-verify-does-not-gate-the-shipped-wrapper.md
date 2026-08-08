# The Kernel Gate Verifies a Wrapper Integration Does Not Generate

**Date:** 2026-08-07
**Status:** in-progress — fixed in VIT `127a593`; two functions knowingly left unrepaired
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
reading the emitted wrapper. Fixed in `127a593`, 699 tests pass, copy-back confirmed by reading
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
| Lost in transit to AeroDyn | `LOGICAL` mapping (`1deb7e3`), `Char_Selector`, `CHARACTER(*)` | re-running the first codebase |
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

## Deliberate open exposure

`sigma` and `unwrap` were integrated before the fix and still lack copy-back. Both write `ErrVar`
only on error paths no scenario reaches, so the exposure is bounded and known. The replication
session recorded them as open rather than repairing them, because re-integration is project-level
work that would have contaminated `interp1d`'s span.

That is the same judgment as declining to fold `__gate_repair__` into `unwrap` (dev note
`202608072004`) — the third time this distinction has arisen, and the third time it was called
correctly. It is worth noting that the discipline of *not* silently improving the measurement has
now been exercised more often than any translation difficulty.

**Open item for after the run:** re-integrate `sigma` and `unwrap` with copy-back, and re-gate.

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

## References

- Dev note `202608072043` — the seven-fix tally; **this adds a third category**
- Dev note `202608072004` — ownership vs geometry; the same judgment, third occurrence
- Dev note `202608072000` — the dead gate scenarios; the apparatus-invisible-to-itself argument
