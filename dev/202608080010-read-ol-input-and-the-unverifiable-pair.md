# Read_OL_Input: The Campaign Solved Allocate-on-Return by Hand, and Never Generalised It

**Date:** 2026-08-08
**Status:** completed — #29 blocked; all claims verified read-only; contains a recommendation on #30/#31
**Scope:** allocate-on-return convention, disposition space

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The block, verified

`ROSCO_Helpers.f90:1351`. The claim holds exactly:

```fortran
REAL(DbKi), INTENT(OUT), DIMENSION(:,:), ALLOCATABLE :: Channels
...
ALLOCATE(Channels(NumDataLines,NumChannels))     ! NumDataLines is a local, discovered by reading
```

`Unit_OL_Input` is `INTENT(IN)` — a bare number. The single call site is
`ReadSetParameters.f90:701` in `ReadControlParameterFileSub`. The subroutine `INQUIRE`s, `OPEN`s,
`REWIND`s and `CLOSE`s the unit itself; the caller passes a number from `GetNewUnit` and never
touches it again.

`BLOCKED` is the right disposition, and the deviation from "never work around infrastructure" is
recorded the way `GetPath`'s was, with the gap named. Nothing to correct.

## What the replication cannot see: the campaign hit this and solved it by hand

**`read_ol_input.cpp` does not exist.** The campaign's 55 `.cpp` files do not include it, and
`Read_OL_Input` is still Fortran in the campaign tree at `ROSCO_Helpers.f90:1351`. So the campaign
did not translate it either.

But it did translate the caller, and `readcontrolparameterfilesub.cpp` shows exactly how:

```cpp
extern "C" {
void ReadControlParameterFileSub_c(..., int32_t* n_OL_rows, int32_t* OL_Count)
...
    // Count OL file rows if OL_Mode > 0
    *n_OL_rows = 0;
    if (CntrPar->OL_Mode > 0) {
        std::string olFilename = trimFortranString(CntrPar->OL_Filename, 1024);
        ...                                        // C++ counts the data lines itself
    }
```

**The campaign moved the size discovery into C++ and returned the extent through the ABI**, leaving
the `ALLOCATE` and the actual read in Fortran. The view structs in `cablecontrol.cpp` and
`activewakecontrol.cpp` then consume the result as `double* OL_Channels` plus
`n_OL_Channels_rows` / `n_OL_Channels_cols`.

That is precisely the convention the replication says is missing — **and the campaign built it once,
by hand, inside one function, and never lifted it into VIT.** It is not in `allocate_extract`; it is
not a strategy in `vit.yaml`; it is thirty lines of bespoke C++ in a single caller.

### Why this matters more than the block itself

The 14 alloc-trait functions were never unblocked in the campaign. They were individually routed
around by a human at each call site. The replication is the first attempt to make allocate-on-return
a *convention* rather than a per-function workaround, and it is blocked because no such convention
exists anywhere — not in VIT, and not in the campaign it is replicating.

This is the cleanest instance yet of a pattern the run keeps producing: **the campaign's apparent
completeness was achieved by human judgement at the boundary, and the replication makes visible how
much of the method was actually that judgement rather than the tool.** `AddToList` was a disposition
the tool never had to represent; `GetWords` was a layer the tool never had to reach; this is a
convention the tool never had to encode. Three different shapes of the same fact.

The campaign's approach also sketches the fix, and it is not speculative: hoist size discovery into
the C++ caller, return the extent as an out-parameter, allocate on the Fortran side of the boundary
until the caller is itself C++. Worth recording as the design starting point when the convention is
built.

## The two corrections, both sound

**The unit-I/O list is an upper bound, not a measurement.** `Read_OL_Input` owns its unit
completely, so it is translatable once the ABI allows it, not deferred like `ReadEmptyLine`. Marking
the count ≤14 and declining to re-audit the rest in bulk is the right call — a bulk re-audit would
be exactly the kind of inference-from-a-flag that produced the 22-vs-11 error I made earlier
(`202608072254`). The distinction is now three-way: **cannot own its file** (deferred), **owns its
file** (translatable), **merely writes to an internal `CHARACTER`** (translates normally). The
`file_io` trait separates none of them.

**The luck claim is verified.** The captured filenames in `kernel/Read_OL_Input/` are absolute:

```
/workspace/ROSCO-replication/Examples/example_inputs/OL_Mode1_Input.dat
/workspace/ROSCO-replication/Examples/example_inputs/OL_Mode1_CC_StC_Input.dat
```

A driver running from `kernel/Read_OL_Input/` would have found the real file. Had `OL_Filename`
been relative, `INQUIRE` would report it missing, the function would take its error branch, and the
kernel would have verified the error path while printing a normal pass.

**That is a sixth fails-green shape, and a distinct one:** the previous five are all about the
apparatus overstating what it compared. This one is about the apparatus comparing the *wrong
execution* — faithfully, bit-identically, on a path the production run never takes. KGen captures
variable state; it does not capture the filesystem, so a kernel of any file-reading function is
verifying a replay whose environment is not part of the fixture. Nothing in the fixture records that
dependency, and nothing in the pass/fail output would hint at it.

## #30 and #31: recommendation

`UpdateZeroMQ` and `ExtController` are both `gate_can_verify: false`. Verified: `ZMQ_Mode` and
`Ext_Mode` are `0` in all **14** gate DISCON files under `Examples/` (17 `DISCON*.IN` exist; the two
outside the gate are an old `v2.2.0` file and a `control/` copy, neither declaring the parameters),
and the ZeroMQ body sits inside `#ifdef ZMQ_CLIENT` at `ZeroMQInterface.f90:20` and `:54`, which
this build does not define.

**The campaign translated both.** `updatezeromq.cpp` and `extcontroller.cpp` are among the 55.
**Neither has a kernel fixture** — the campaign has 43, and neither is among them. So both were
shipped on no evidence whatsoever: no kernel, and a gate that never executes them.

### And `ExtController` contains a defect the gate cannot see

`ExtControl.f90:75` declares the message buffer as an array of `C_CHAR`:

```fortran
CHARACTER(KIND=C_CHAR) :: avcMSG(LEN(ErrVar%ErrMsg)+1)
```

and line 111 reports its capacity to the external DLL as:

```fortran
ExtDLL%avrSWAP(49) = LEN(avcMSG) + 1     ! Record 49: max characters in the MESSAGE argument
```

`LEN(avcMSG)` is the **element** length, which is 1. `SIZE(avcMSG)` is the element count,
`LEN(ErrVar%ErrMsg)+1`. So Record 49 tells the external controller its error-message buffer holds
**2 characters** instead of `ErrMsgLen+1`. Lines 112 and 113 use `LEN_TRIM` correctly because those
operands are `CHARACTER` scalars; only line 111 has an array operand, and only line 111 is wrong.

This is **the same element-length-versus-element-count conflation** that produced the run's
sixteenth tool fix in `GetWords`, where three generators dropped `len_Words` and the staging buffer
indexed `x(i:i)`. The same confusion appears in upstream ROSCO, in VIT's generators, and in the
kernel diagnostic — three independent authors, one Fortran ABI wart.

### Recommendation: do both, in plan order, under a fourth disposition

Do not skip to `SetpointSmoother`. Three reasons:

1. **Skipping breaks the mirror.** The campaign translated both. A replication that declines the
   two functions the campaign shipped blind is no longer measuring the same campaign, and the
   comparison the whole run exists to support gets a hole exactly where it is most interesting.
2. **It is the sharpest exhibit of shipping unverified, and it needs both sides.** Right now the
   record says the campaign shipped `ExtController` and `UpdateZeroMQ` unverified. If the replication reaches them
   independently, is told nothing about the campaign, and arrives at the same "cannot be scored"
   verdict, the finding stops being a criticism of one person's diligence and becomes a property of
   the codebase and the gate. That is a much harder claim to dismiss.
3. **`ExtController` is a live demonstration.** A faithful mirror reproduces the Record 49 defect,
   the gate stays green, and the pipeline reports success. That is the fails-green thesis
   demonstrated on a real defect rather than argued from apparatus behaviour.

**But they must not be recorded as `integrated`.** Twenty-six functions in this dataset carry
27/27 gate evidence; these two would carry none. Filing them under the same status inflates the one
number the dataset is about. This calls for a fourth disposition — `unverified`, or
`integrated_unscored` — meaning *translated and integrated, no verification evidence exists or can
exist*.

That is the same category error as `out_of_scope` versus `blocked` (`202608072320`), which I got
wrong by authorising too few dispositions. The lesson from that one was that the procedure has to
carry an option for every outcome the work can actually produce. This is the second time the work
has produced an outcome the procedure has no name for. **The disposition space should be extended
before the functions are attempted, not after** — otherwise the dataset records a judgement it was
never designed to express.

## Accounting

29 attempted / 26 integrated / 1 out of scope / 1 deferred / 1 blocked / 0 failed.
`Read_OL_Input` cost 234 s — a cheap block, because the
integration preview surfaced all three ABI failures before any C++ was written.

## Summary

Sixth fails-green shape, structurally new: the kernel verifies the right function in the
wrong environment. Applies to every file-reading function in any codebase, and no amount of case
count fixes it.

The campaign's completeness was partly human judgement at the ABI boundary. Allocate-on-
return was solved once by hand and never generalised; 14 functions depend on a convention that does
not exist in the tool. This is the measurable version of "the tool was finished for AeroDyn, not
finished" (`202608072043`).

## References

- Dev note `202608080003` — `GetWords`; the same element-length/element-count conflation
- Dev note `202608072320` — disposition space; the error this recommendation is trying not to repeat
- Dev note `202608072334` — the unit-I/O list, now an upper bound
- Dev note `202608072329` — three ways the gate cannot speak; this adds the fourth
- Dev note `202608072149` — Track A/Track B
