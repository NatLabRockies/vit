# Which Calls a Kernel Captures, and What They Cover

**Date:** 2026-10-01
**Status:** completed
**Scope:** `vit extract` (test case, calls captured, state kept between calls), `vit verify` (kernel coverage), the work order's test case, the inventory's `has_save`, and the documents that describe them

## Why

The author asked whether KGen's roughly twenty calls per kernel are chosen in the best way. They
were not. Every kernel held the first twenty calls of one test case picked by hand, and nothing
reported what those calls exercised. Dev note `202608080640` had found this in August, as the
common cause of four instances of thin kernel evidence, and recommended a configuration change that
was never made.

## What was found

**The first twenty calls are a tiny prefix of a run.** In AeroDyn's committed coverage, in the case
that runs each most, `FindBoundingTables` is called 367,967 times, `DBEMT_CalcOutput` 72,060 and
`DBEMT_UpdateStates` 38,400. Across the 26 integrated functions the coverage covers, the median is
90, and only 3 have twenty calls or fewer.

**The red test does not measure this.** It shows that the comparison can fail when an input
changes, not that the captured calls reach the function's branches.

**KGen already accepts any set of calls**: several ranges and single calls in one
`--invocation`. Spreading them needs no KGen change. KGen's own selection, its code-coverage model,
takes calls in order from the first: its loop keys on (block, line, call number), so every new call
counts as new. It would also cost an extra instrumented build and run.

**The effect, shown.** On the quickstart's `drag` given a branch that only calls after the
hundredth take, in the container: from the first twenty calls, `vit verify` passed with the
captured calls running 2 of the 3 lines the full run executes, and listed the missed one. Spread
over the 500 calls, it passed with 3 of 3.

**The state rule's first version was wrong on real code.** Run over AeroDyn and the NWTC Library at
`edab46311`, it reported 44 procedures; 30 were `Data(LB(1):) => Data` in `ModReg.f90`, a pointer
assignment to a variable named `Data`, taken for a DATA statement. With the statement required to
assign nothing outside parentheses, it reports 14 procedures, all genuine, through 18 statements:
12 SAVE attributes and 6 locals initialised in their declarations. The inventory, which looked for
SAVE only, reported 1 in AeroDyn's source. The committed `function_inventory.json` predates the new
rule.

**No integrated AeroDyn function needs consecutive calls.** None of the call trees of the 13 checked
has state that its most-executing case runs, so `auto` would spread all of them.

**A partial capture replayed out of order.** When some requested calls never come, KGen writes no
`kgen_statefile.lst`, and the kernel lists the files with `ls`, in name order: call 10 before call
2. That was already so for the old default and a function called fewer than twenty times. It only
matters for a function that keeps state between calls, and VIT now writes the list in call order.

## What changed

| Commit | Change |
|---|---|
| `e55c3c4` | `vit verify` and `vit run` report which lines of the function the captured calls run |
| `8a50a15` | `vit extract` chooses the test case from coverage, or takes `--case` |
| `b579090` | `kgen.invocation: auto`, the new default, spreads the calls over the run; one rule for state kept between calls |
| `234055e` | The work order names the case `vit extract` chooses |
| `290c411` | A partial capture is listed in call order, and the report says why it is partial |
| `56d159f` | The documents |

## Decisions

**Coverage is measured at verification, in a build of its own.** At extraction the kernel is
KGen's raw output, which often needs VIT's repairs to build; at verification VIT has made them, and
already builds the Fortran reference. The measurement is a third build, in a copy of the kernel
directory, at `-O0` with `--coverage`: instrumentation and optimisation change gfortran's code
generation, as with the 1-ULP trigonometric differences of dev note `202604260357`, and the
reference results the verification compares against must not move. At `-O0` gcov also attributes
counts to the lines that ran. Kernel lines are matched to the source by text, which KGen keeps.

**A case is chosen by lines, not calls.** Among the cases that run the call, the one that runs the
most lines of the function's body, then the one that runs the call most often, then by name. Calls
measure how hot a call site is; lines measure which behaviours a case reaches. The work order uses
the same function, so the two cannot name different cases.

**The spread includes the first and the last call.** Call 1 keeps initialisation in the kernel,
where the old default had it; the last call and those between cover the steady state and the end
of the run. An explicit range is honoured and, when it is the first twenty of a known count, the
report says so.

**State kept between calls keeps the calls consecutive.** KGen captures arguments and the module
variables a call uses, not SAVE variables, so a kernel of a function whose call tree keeps state
replays correctly only from the first call on. The search is conservative where it can be: a name
that is also an array's is followed, and every definition of a name in a compiled file counts. It
leaves out what cannot matter: files the build did not compile, as with OpenFAST's one `WrNR` per
platform, and callees the case never runs, whose state stays initial in the run and in the kernel.
The generated kernel is checked as well, which catches what a name search misses, such as a call
through a procedure pointer.

**Rejected:** KGen's code-coverage model (above); capturing many calls and replaying a subset chosen
by coverage, which is the better selection but needs a coverage build per candidate call; and
capturing from several cases into one kernel, deferred until kernel coverage shows a function that
no single case covers.

## What remains

- Not run on ROSCO or AeroDyn: the new extraction and verification were exercised on the
  quickstart and its variants in the container. The AeroDyn restart will be the first real run;
  its workspace `vit.yaml` needs `coverage.file` and `coverage.cases` for any of this to apply.
- The call count is from line coverage: a call in a one-line IF is counted each time the IF runs,
  and coverage counts every process and thread while KGen counts process 0, thread 0.
- Kernel coverage adds a kernel build and run to every passing verification;
  `--no-kernel-coverage` skips it.
- `vit run` verifies without the red test, as before.
- AeroDyn's committed inventory data has `has_save` from the old rule.
- Several cases into one kernel, if kernel coverage shows the need.

## References

- Dev notes `202608080640` (every kernel samples the first twenty calls), `202604260357`
  (vectorisation changed by a different build), `202610011847` (extraction's step records and the
  reach marker), `202610011349` (coverage per test case)
- `vit/kernel_coverage.py`, `vit/extract.py` `capture_invocation`, `vit/coverage.py`
  `choose_case` and `state_in_call_tree`, `vit/fortran_parser.py` `save_state`
- `docs/EXTRACTION_INTERNALS.md` section 3, `docs/VERIFICATION_METHODS.md` section 1
