# Leaf-First Hid an Entire Subsystem, and the Campaign Verified Across 30 Mismatched Bridges

**Date:** 2026-08-08
**Status:** completed — #32 integrated; contains a second campaign defect found by re-derivation
**Scope:** ordering methodology, kernel bridge correctness

> **CORRECTION.** The `SS_Mode` claim below is wrong — one scenario patches `'SS_Mode': 0`
> (campaign `vit_sim.py:1486`, replication `:1509`), so `SetpointSmoother`'s `ELSE` branch **is
> reached**. I read the committed `DISCON*.IN` files, which are generated outputs. The 30
> width-mismatched kernel bridges, which are the point of this note, come from reading the bridge
> files directly and are unaffected. See `202608080517`.

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## What #32 established

`SetpointSmoother` is the first function in the run with an already-translated callee. It is two
arithmetic lines and one `LPFilter` call. It took **985 s** and cost several times a clean cycle
— and none of that was the translation, which was first-pass correct.

The cost was two things breaking that had never run before. Both are verified below, and one of them
turns out to be a defect the campaign shipped in seventeen of its forty-three kernels.

## 1. The campaign verified across 30 type-mismatched kernel bridges

The replication reported a real VIT gap: `type_map` sends Fortran `LOGICAL` across as
`INTEGER(C_INT)`; the wrapper direction was fixed at `a6fe65c`, but the bridge direction — C++
calling back into Fortran — never converted, and the existing guard only fired for the `C_BOOL`
mapping this project does not use.

`LPFilter`'s dummy is `LOGICAL(4), INTENT(IN) :: reset`, so the very first translated callee in the
run carries a `LOGICAL`. Confirmed.

**Then I checked what the campaign generated for the same bridge.** `kernel/SetpointSmoother/`:

```c
// vit_kernel_callees.h:12
double lpfilter_c(double InputSignal, double DT, double CornerFreq,
                  filterparameters_t* FP, int iStatus, int reset, ...);
```

```fortran
! vit_bridge_lpfilter.f90:14
LOGICAL(C_BOOL), VALUE :: reset
```

**The two sides disagree.** `int` is four bytes; `LOGICAL(C_BOOL)` is one. `BIND(C)` links by name,
so nothing cross-checks them, and each file compiles cleanly on its own.

This is not one bad file. Across the campaign's kernel tree:

- **30 bridges** declare `LOGICAL(C_BOOL), VALUE` on the Fortran side
- **all 30** have `int` for the same argument in their `vit_kernel_callees.h`
- they span **17 of the campaign's 43 kernel directories**: `ActiveWakeControl`, `CableControl`,
  `ComputeVariablesSetpoints`, `FlapControl`, `FloatingFeedback`, `ForeAftDamping`, `IPC`,
  `PIDController`, `PitchControl`, `PreFilterMeasuredSignals`, `RefSpeedExclusion`,
  `SetpointSmoother`, `Shutdown`, `Startup`, `VariableSpeedControl`, `WindSpeedEstimator`,
  `YawRateControl`
- nothing in the campaign maps `LOGICAL` consistently on both sides — there are zero
  `INTEGER(C_INT), VALUE :: reset` declarations

### Why it passed anyway

Inference from the x86-64 SysV ABI, not from execution — I have run nothing:

A small `VALUE` argument is passed in a register. The C++ caller writes a four-byte `int`; the
Fortran callee reads the low byte. `reset` is only ever `.TRUE.` or `.FALSE.`, so the low byte
carries the whole value and the call is correct. It would break if the C side ever passed a truthy
value other than 1, if the argument were passed on the stack, or on an ABI that does not put small
scalars in the low bits of a register.

**So seventeen of the campaign's forty-three kernel verifications ran through a bridge that was not
type-correct, and were bit-identical because of an ABI coincidence.** The results are almost
certainly still right on this platform. What is wrong is the claim they support: those kernels were
not testing the interface they appeared to be testing.

This is the second campaign defect surfaced by re-derivation rather than by inspection, after the
`ExtController` stamp (`202608080058`). Both were invisible from inside the campaign and visible
immediately from outside it. That is the argument for replication as a method, made twice.

The replication's fix (`4f568b7`, "convert `INTEGER(C_INT)` back to `LOGICAL` in kernel callee
bridges") makes both sides `INTEGER(C_INT)` and converts explicitly. It is correct where the
campaign's was not.

## 2. Leaf-first ordering left the entire callee-bridge path untested for 31 functions

The `LOGICAL` bug has two directions:

| Direction | What exercises it | Fixed |
|---|---|---|
| Fortran wrapper → C++ | any function with a `LOGICAL` argument | `a6fe65c`, 2026-08-07 19:40 |
| C++ → Fortran callee bridge | **only a function with a translated callee** | `4f568b7`, ~5.5 h later |

The second direction was **structurally unreachable until #32**. Thirty-one consecutive functions
verified 20/20 or better and said nothing whatsoever about it, because none of them had a callee to
bridge to.

`202608072334` established that leaf-first ordering is wrong when a resource belongs to the caller.
This is a second, independent reason it misleads: **leaf-first defers the entire callee-bridge
subsystem to the end, so the tool accumulates a long record of green results over a code path it
has not executed once.** The record looks like evidence of tool maturity and is evidence of nothing
about that subsystem.

Generalised: any bottom-up campaign has a phase transition at the first non-leaf, and every defect
in the inter-function machinery is concentrated after it. Two of the run's nineteen tool fixes
landed in the first function past that boundary.

## 3. Third setup omission: the reset/integrate pair

The replication's other problem was that the kernel must contain the callee's **original** Fortran.
Extraction ran against the integrated tree, so KGen copied a `Filters.f90` in which `LPFilter` is a
wrapper calling `lpfilter_c`, while the bridge defines `lpfilter_c` as calling `Filters::LPFilter` —
a loop, surfacing as `Function 'lpfilter_c' has no IMPLICIT type`, which points nowhere near the
cause.

The agent's diagnosis is right and so is its attribution — neither a KGen nor a VIT bug. **And the
campaign had exactly this procedure:**

```
ROSCO/scripts/          reset_to_clean.sh  integrate_all.sh  extract_all.sh  verify_all.sh ...
ROSCO-replication/scripts/   rebuild.sh  regress.sh  telemetry.sh
```

`CLAUDE.md` documents the pair as mandatory for AeroDyn: *"Extraction requires clean (pre-integration)
Fortran source… The pair must be used together."* I built the replication runbook without it, and it
went unnoticed for 31 functions because every one of them was a leaf or had untranslated callees.

**Third setup omission of mine**, after the under-specified disposition set (`202608072320`) and the
three incomplete Phase 2 flags. It is the same shape as the others: a piece of the campaign's method
that lived in a script rather than in the documented workflow, and so did not get carried across.

Worth recording as a general lesson about this kind of replication: **what the original campaign
encoded in shell scripts is part of the method and is exactly what a re-derivation from documentation
will omit.** The scripts are not scaffolding around the method; for the reset/integrate cycle they
*are* the method.

The dead end is also worth keeping: swapping the callee file inside `kernel/<Name>/` after the fact
does not work, because KGen rewrites the module — 481 diff lines.

## 4. The dead `ELSE`, and a fair reading of it

Verified: `SS_Mode` is `1` in **all 14** gate DISCON files. The `ELSE` branch —
`LocalVar%SS_DelOmegaF = 0` — is unreachable in every scenario and in all 20 kernel cases. Getting
it wrong would have passed everything.

This is the non-discriminating-input problem (`202608072250`, `202608080003`) at **branch**
granularity rather than input-shape granularity. The kernel compared 4,680 fields and none of them
could have distinguished a wrong `ELSE`.

For fairness: the campaign got this branch right too (`setpointsmoother.cpp:1283-1284`,
`LocalVar->SS_DelOmegaF = 0`). There is no defect here. The finding is that **nothing would have
caught one**, which is worth exactly as much as the finding that something was wrong.

## 5. Two more self-corrections

The agent corrected its own commit message (`verify attempts: 2` against telemetry's 3, mismatch
recorded rather than silently reconciled) and moved the status counts from hand-incremented to
derived, catching that `integrated` was one high after `CurDate` moved to
`integrated_unexercised`.

**Fifth and sixth self-caught dataset errors.** The pattern is now worth stating: the
agent has caught more errors in its own dataset than I have caught in its work, and every one was
found by re-deriving a number rather than by reviewing it.

## 6. What #32 does not answer

`SetpointSmoother` cost several times the clean-cycle median. **That is not the marginal cost of
a non-leaf function.** It is the cost of the *first* non-leaf function, and it decomposes almost
entirely into two one-time infrastructure gaps. The translation itself was first-pass correct, as
all 26 have been.

The open question — whether the marginal cost holds once callees are translated — needs #33 or #34,
where the callee-bridge path is warm. **Do not quote this unit's cost as a non-leaf figure.** If the next
function with a translated callee lands near the clean-cycle median, the premium was setup; if it lands
near this one, it is structural.

## Accounting

32 attempted: **27 integrated, 2 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness 26/26 first-pass.

Nineteen tool fixes.

## Summary

Seventeen of the campaign's 43 kernels verified through a bridge whose two sides declared
different widths for the same argument. The verifications are probably still valid on x86-64; the
claim that they tested the interface is not.

The dead-`ELSE` case in its fair form: no defect, and no mechanism that could have found
one. Branch-level non-discrimination, distinct from the input-shape cases.

Two ordering results now: leaf-first is wrong for caller-owned resources (`202608072334`),
and leaf-first concentrates every inter-function defect after the first non-leaf. Plus the
replication lesson — method encoded in shell scripts does not survive re-derivation from
documentation.

## References

- Dev note `202608080058` — the `ExtController` stamp; the first campaign defect found this way
- Dev note `202608072334` — leaf-first and resource ownership; the first ordering result
- Dev note `202608072250` — non-discriminating input; this is its branch-level form
- Dev note `202608072320` — the disposition set; first setup omission
- Dev note `202608072355` — cost units
