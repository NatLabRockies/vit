# vit extract Chooses the Call Site

**Date:** 2026-10-01
**Status:** completed. Follow-up the same day, in the commit after this note's: `vit find --coverage` ends with this choice instead of the call and case with the most executions, and the AeroDyn manual's Step 1, `VERIFICATION_METHODS.md` (whose work-order case was still "the case that calls it most") and the AeroDyn plan were brought into line.
**Scope:** `vit extract` (the call site when `-l` is not given), `vit init`, the order of `GETTING_STARTED.md`, and the documents that gave a call site

## Why

Reading `EXTRACTION_INTERNALS.md`, the author asked whether `vit extract` should be given `-l` at
all, whether every project should have coverage, and whether the documents should show the default
command, without `-l`.

## What was found

**Without `-l`, extraction stopped for a function with more than one call.** It searched the
working tree for calls by name and used the call only if there was exactly one; several were
listed and it stopped. In AeroDyn's committed call graph, 344 of the 1,023 procedures something
calls have two or more callers. The search did not use coverage, so a call in a file the build does
not compile, or one no test case runs, counted the same as any other. `-f` without `-l` was
ignored.

**The choice was made by hand, from tooling that already had what it needed.** `vit find
--coverage` (`8bcf898`) lists every call with each test case's count, reading each file as it was
measured (`44436f0`), so its line numbers match the counts. The AeroDyn manual's Step 1 told the
agent to read that output and take the pair with the most executions, then pass it with `-f` and
`-l`; every document gave the call site the same way.

**Coverage was presented as an extra.** `GETTING_STARTED.md` put `vit coverage run` under "Going
further", after the cycle, though the quickstart's `vit.yaml` already lists its test case, and
`vit init` wrote and said nothing about it. Yet the test case, the calls captured (dev note
`202610012000`) and now the call site all come from it.

**On AeroDyn, the rule mostly keeps the manual's call and often changes its case.** Run over the
548 hand-written procedures in AeroDyn's inventory, with the committed coverage read through an
OpenFAST checkout: 362 have at most one call in the files the build compiled, and 39 have several
that no test case runs, which extraction now refuses before KGen. Of the 147 with several calls a
case runs, the new rule takes the call the manual's rule took (the call and case with the most
executions) for 143, and the same case for 119. Where the case differs, the manual's was usually
the case that calls most, often `ad_timeseries_shutdown`, which runs fewer of the function's lines:
for `AFI_ComputeAirfoilCoefs`, 4 lines in 5,103,324 calls against 6 in 344,549 for
`ad_MHK_RM1_Fixed`; for `UA_ReInit`, 25 lines against 52 for `ad_Kite_OLAF`. For `DBEMT_ReInit`,
the manual's own example, the call is the same, in `BEMT_ReInit`, and the case is
`ad_BAR_SineMotion_UA4_DBEMT3`, which runs 18 of the 19 lines any case runs, against 13. The four
calls that differ follow from the case: `UA_CalcOutput`'s most-executed call is in `BEMT.f90`, with
`ad_BAR_SineMotion_UA4_DBEMT3` running 111 of its lines, while `ad_Kite_OLAF` runs 112 and calls it
only from `AeroDyn.f90`.

**A call found by text alone can be another call.** The checkout has integrated translations, and
13 of the chosen calls are in procedures in `AirfoilInfo.f90` and `DBEMT.f90` whose bodies are now
C++; AeroDyn extracts from clean source for this reason. The first
version of the working-tree lookup matched a call's text alone. For `AFI_ComputeAirfoilCoefs1D`,
the measured source has the same call in `AFI_ComputeAirfoilCoefs2D` and in the chosen
`AFI_ComputeAirfoilCoefs`; the checkout has only the first, so the lookup would have marked a
different call site and said nothing. A test of a call removed by integration found this before the
AeroDyn run confirmed it (`1f55862`).

## What changed

| Commit | Change |
|---|---|
| `8cc172a` | `vit extract` chooses the call site from coverage |
| `004cffb` | `GETTING_STARTED.md` measures coverage before extracting; `vit init` says to |
| `1fe6b2f` | The documents lead with `vit extract FuncName` |
| `1f55862` | A chosen call is found in the procedure that made it; the error names integration |
| this note's commit | `VIT_REFERENCE.md` lists the calls VIT cannot find by name |

## Decisions

**The call site and the test case are chosen together.** The case is chosen by the rule of dev
note `202610012000`, over every call: the most lines of the function's body, since lines tell a
function's behaviours apart and calls do not. The site is then the call that case runs most often,
then the first by path and line. Given that site, `choose_case` picks the same case, so the two
choices cannot disagree. Line coverage cannot say which of the function's lines each call reaches,
only which run in each case; the number of calls is the one measure each call has. `vit verify`'s
kernel coverage is the check: it lists the lines the full run executes and the captured calls do
not, and `-f` and `-l` then extract at another call.

**A call is found in the working tree by its text, in the procedure of the same name.** Coverage
numbers lines as measured, and integrating a translation edits the source afterwards, removing the
calls the translated procedure made. Of identical calls in that procedure, the one at the same
position among them is taken, if it still has as many; otherwise extraction stops, and says that
the source has changed since it was measured, for example by integration.

**Extraction stops before KGen when no call can capture anything.** If coverage says no test case
runs any call, or `--case` runs none of them, a run would capture nothing; the error names `vit
find --coverage`, a fresh `vit coverage run`, and `-f` and `-l` to extract regardless. `-l` without
`-f` is now an error rather than ignored.

**Coverage is the standard path, not a requirement.** It costs an instrumented build and one run
of each case. The data is gcov's, so a project that cannot build with gfortran cannot have it, and
extraction without it still works: it uses the only call, needs `-f` and `-l` for several, and
captures the first 20 calls of `kgen.run_cmd`, and says each of these when it happens.

**Rejected:** the manual's rule, the call and case with the most executions, which ignores lines
(above); requiring coverage; and capturing at several call sites into one kernel, which KGen does
not do in one extraction and which waits, with several cases into one kernel (dev note
`202610012000`), for kernel coverage to show a function that needs it.

## How it was checked

- Eleven tests in `tests/test_extraction_case.py`: two identical calls in one file and one in
  another, `-f` and `--case` as limits, a source edited after it was measured, a call removed by
  integration, the stops before KGen, and no coverage. With the working-tree lookup made to return
  the measured line, the edited-source test fails, and without the procedure check the removed-call
  test fails: either way the pragma lands on the other identical call.
- In the container, the quickstart in the documented order: `vit coverage run`, then `vit extract
  drag` with no call site, `vit verify` comparing the kernel's lines with case `default`, and
  `vit integrate`; the rebuilt program's output matched a clean Fortran build byte for byte.
- On AeroDyn's committed coverage, read through the OpenFAST checkout (above).

## What remains

- The AeroDyn workspace's `vit.yaml` sets neither `coverage.file` nor `coverage.cases`; the
  restart will need both.
- Calls VIT cannot find by name need `-f` and `-l`: through a generic interface or a renamed
  import, split so the name and `(` are on different continuation lines, or in a `.F90` file
  (`find_call_sites` and `coverage.call_sites` read `.f90` only; in OpenFAST's `modules/`, 40
  files are `.F90`, mostly tests, and every file AeroDyn's coverage covers is `.f90` or `.f`).
- Several call sites into one kernel, if kernel coverage shows the need.

## References

- Dev notes `202610012000` (the test case and the calls), `202610011349` (`vit coverage run`, `vit
  find --coverage`, coverage per file), `202605010623` (a pragma at the definition),
  `202604250803` (coverage chooses the extraction case)
- `vit/cli.py` `_extraction_site`, `vit/coverage.py` `choose_call_site` and `working_tree_line`
- `docs/EXTRACTION_INTERNALS.md` section 3, `docs/GETTING_STARTED.md` section 2
