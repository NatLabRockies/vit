# Extraction's Run Time and Its Timeout

**Date:** 2026-10-01
**Status:** completed
**Scope:** `vit coverage run` (each case's time), `vit extract` (how long the run will take, `kgen.timeout`), and stopping KGen at the timeout

## Why

The author asked whether extraction, now that it chooses the call site, the test case and the
calls (dev notes `202610012000`, `202610012159`), costs more to run than before.

## What was found

**Choosing costs seconds.** On AeroDyn's committed coverage, read through an OpenFAST checkout,
choosing the call site, the test case and the calls took 3 to 7 seconds for each of five functions
(`DBEMT_ReInit`, `DBEMT_UpdateStates`, `AFI_ComputeAirfoilCoefs`, `UA_CalcOutput`,
`FindBoundingTables`), against an extraction that builds the program twice and parses with KGen.

**The run now goes to the end of the test case.** KGen's instrumentation stops the program after
the last call requested: it writes `kgen_statefile.lst`, sleeps one second and executes `STOP 0`
(`gen_state_callsite_file.py` in the fork). Calls 1 to 20 stopped most runs early; calls spread to
the last one run the whole case. The case chosen can also be a heavier one than `kgen.run_cmd`'s:
`UA_CalcOutput`'s is `ad_Kite_OLAF`, a free-wake case. On the calls it does not capture, the
instrumentation costs what it did: a counter and a comparison.

**Verification adds one kernel build.** Kernel coverage builds the kernel's Fortran again, at
`-O0` with coverage, and replays the calls, beside the builds `vit verify` already makes;
`--no-kernel-coverage` skips it. Measuring coverage is once per project and again when the cases
change; for AeroDyn the manual gives about ten minutes for all its cases.

**Nothing said how long a case runs, and the limit was fixed.** AeroDyn's per-case times are not
recorded anywhere, and the 20-minute limit, which covers KGen's parse, the instrumented build and
the run, was a constant in the code.

**A timeout left the source modified, and the run going.** With `kgen.timeout: 1` on the
quickstart, the call-site file kept KGen's pragma after extraction had reported the failure, and
`state/` was left behind. `subprocess.run` killed only the shell it had started. KGen ran on,
finished the capture and ran its state Makefile's `recover`, which copied its saved original,
VIT's pragma included, over the source VIT had restored: KGen's log ended with "Application
built/run is finished", which it writes after `recover`, and the `run` step's record ended after
VIT's restore. The pragma lines are Fortran comments and the next extraction sweeps them, but the
source was not as the documents say, and with a long case the build and the run would have gone on
after VIT reported. This predates today's changes; running cases to the end, and a limit that can
be set lower, make it likelier.

## What changed

All in the commit that adds this note:

- `vit coverage run` times each case and records it in the coverage file as `case_seconds`.
- `vit extract` prints how long the run will take, and warns when that is more than half of
  `kgen.timeout`.
- `kgen.timeout` in `vit.yaml`, 1200 seconds unless set, and written only when set.
- KGen runs in a process group of its own, and a timeout kills the whole group. With nothing left
  running, KGen's source backups are settled after a timeout as after any other failure; they used
  to be left for the next extraction.

## Decisions

**The time is the case's time under `vit coverage run`.** That build is instrumented for coverage,
so the figure is approximate; timing a separate plain run of every case would cost a full run of
each. When the capture stops before the end, the time is estimated in proportion to the calls, as
if they were spread evenly over the run, which initialisation and output phases make rough.

**Warn at half the limit, and do not stop.** KGen's parse and the instrumented build share the
limit and are not known in advance, so half is a stated rule, not a measurement. The estimate comes
from a slower build, and stopping on it could refuse an extraction that would fit.

**Kill the group outright.** KGen gets no chance to clean up after itself, which is the point:
VIT's cleanup, which restores the build file and the sources, runs after nothing else can write.

**Not done:** keeping the calls captured before a timeout as a partial kernel. A timeout is a
failed extraction, and an earlier kernel stays as it was.

## How it was checked

- Tests: the setting's default and round trip; the limit reaching KGen's subprocess; the recorded
  case times; the run-time line and the warning; and two timeout tests with a stand-in KGen, one
  whose child writes a file after the limit and one that instruments a source and hangs. Both
  timeout tests fail with the old `subprocess.run`; the limit test fails if the setting is not
  passed on.
- In the container, the quickstart with `kgen.timeout: 1`: the step named, the source identical to
  the original, KGen not finished, and the earlier kernel's 20 calls kept. The killed processes
  stay as zombies there, since the container's first process, `sleep infinity`, does not reap
  them; `docker run --init` would.

## What remains

- AeroDyn's per-case times, which the restart's `vit coverage run` will record.

## References

- Dev notes `202610012000` (the calls captured, kernel coverage), `202610012159` (the call site),
  `202610011847` (step records, the timeout's report)
- `vit/coverage.py` `run_coverage` and `case_seconds`, `vit/cli.py` `_run_time`, `vit/extract.py`
  `_run_as_group`
- `docs/EXTRACTION_INTERNALS.md` sections 2, 3 and 6, `docs/VIT_REFERENCE.md` (`kgen.timeout`)
