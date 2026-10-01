# Planning and Coverage Tooling from the Document Review

**Date:** 2026-10-01
**Status:** completed
**Scope:** `vit inventory` (ordering exceptions, work order), `vit coverage` (run, per-file measured content, per-procedure report), `vit find`, `vit.yaml` rewriting, telemetry join; the AeroDyn coverage data; the documents that describe them

## Why

After the document revision pass (`ba21695`), the author began reading the documents and sending
notes. Each note on `CREATING_PROJECT_PLANS.md` turned into a question about the code:

1. The planning method called leaves "the first translation targets", although the August ROSCO
   re-translation had found two places where that is wrong (dev notes `202608072334`,
   `202608080128`). Could a tool find those cases, rather than an agent?
2. "Add test coverage" named no tool. Could coverage be one command?
3. Could the whole translation order be computed, so that nothing about it is a guess, and what
   would still need a person?
4. Is there a list of the procedures no test case reaches, and is it always produced?

This note records what was built, what building it found, and why it was built the way it was. The
commit messages hold the detail; this is the record the other documents cite.

## What changed

| Commit | Change |
|---|---|
| `ca0fd6f` | The telemetry join moved from `scripts/` into `vit/telemetry.py`, as `python3 -m vit.telemetry join` |
| `649045c` | `scripts/fortran_inventory.py` became `vit inventory`; fparser is the `vit[inventory]` extra |
| `252f628` | `vit find` no longer lists a typed function's definition as a call, and finds `CALL x` without arguments |
| `8bcf898` | `vit coverage import`, `vit coverage line`, `vit find --coverage`, replacing AeroDyn's two coverage scripts |
| `073625f` | Rewriting `vit.yaml` keeps every key VIT does not manage |
| `44436f0` | Coverage records the content measured, per file, instead of one commit |
| `3c75d2d` | AeroDyn's coverage data records the source it was actually measured on |
| `430aa5e` | `vit coverage run`: build with coverage, run each case, collect gcov output, import |
| `26f5069` | `vit inventory` reports the exceptions to leaf-first ordering (`ordering.json`) |
| `c220f19` | `CREATING_PROJECT_PLANS.md`: the exceptions, and measuring coverage |
| `4e755e1` | Every coverage run writes which procedures each test case calls and which none reaches |
| `0542c46`, `ca0d0f0` | `vit inventory` writes the work order |
| `67fe031`, `23291ea`, `1ee57f4`, `8ebb063`, `3935c5a`, `cfcd9af`, `cfb871d`, `5539415` | Documents brought up to date with all of the above |

No `scripts/` directory remains in the repository.

## Findings

Each was found by checking a result against something independent of it, usually real code.

**The AeroDyn coverage data recorded the wrong source.** `line_coverage.json` named `edab46311`
as its source. It was measured on 2026-04-25, four minutes after the fork's `c4cfa46e3` added the
generation script, after `reset_to_clean.sh` had restored `AirfoilInfo.f90` from `edab46311`. So
68 of its 69 files were measured at `c4cfa46e3`, and 52 of those differ between the two commits.
gcov counts only lines with code, which made this measurable: at `edab46311`, 4,776 of the 20,696
covered lines fell on blank or comment lines; at `c4cfa46e3`, 223, all in `AirfoilInfo.f90`, which
has none at `edab46311`. Lookups in the 52 files had been reporting other lines' counts since April.
`BEMT.f90` and `DBEMT.f90` are identical in both commits, so the AeroDyn manual's `DBEMT_ReInit`
example was right. After the repair, none of the 20,633 checkable covered lines falls on a blank or
comment line. The cause generalises: any project that measures pre-integration source restores old
files into a newer checkout, so HEAD never holds what was measured.

**A claim of mine held for the wrong reason.** The commit that added `vit find --coverage`
(`8bcf898`) said every count agreed with the old AeroDyn script, site by site. They agreed because
both read the same wrong commit; the comparison could not have failed. It was not evidence of
correctness, and the repair above is.

**gcov counts the END line of procedures that never ran.** In the AeroDyn data, the only counted
line of `DBEMT_InitStates_AllNodes`, which the AeroDyn plan records as having no coverage, is its
`end subroutine`. Counting END lines made 317 AeroDyn procedures look reached. Line-based procedure
coverage now ignores them.

**`vit find` listed definitions as calls.** A typed function's definition, `REAL(ReKi) FUNCTION
ComputeUA360_CnOffset(...)` or the quickstart's `real(dp) function drag(...)`, matched the call
rule, and `GETTING_STARTED.md` described the result as expected. With coverage, the definition has
the most executions of any line, so it would have been recommended as the place to extract, which
is where KGen captures nothing. `CALL Reset`, with no argument list, was missed.

**Rewriting `vit.yaml` deleted settings.** `save_config` wrote only the fields it manages, so the
first `vit verify` deleted `kgen.add_source_cmd`, `fortran.cxx_compiler`, `fortran.runtime_libs`
and anything else a project had added. A `coverage` section would have been deleted the same way.

**The inventory silently dropped whole files.** fparser's Fortran 2003 parser rejects
`OPEN(NEWUNIT=...)`, and `NWTC_IO.f90` failed on `__DATE__(8:11)`, a predefined preprocessor macro.
Either failure dropped the file with a warning on stderr only. The inventory now uses the Fortran
2008 parser, reads the predefined macros as names, and lists unparsed files in its summary. A
procedure named like an intrinsic and called with a different number of arguments, such as a
one-argument `scale`, still fails to parse.

**Same-named internal procedures merged.** ROSCO_Helpers has one `Cleanup` per `Parse*` routine.
Keyed by name and file they collapsed into one record, and a call to an internal procedure resolved
to every internal procedure of that name in the file. On AeroDyn that joined 30 unrelated
procedures, `AD_Init` and `AD_CalcOutput` among them, into one step of the work order. Procedures
are now told apart by file and start line, and an internal procedure is visible only from its host.

**Generic interfaces hid callers.** ROSCO's `Parse*` routines are reached only through the generic
interfaces `ParseInput` and `ParseAry`, so their callers, and the owners of the units they read,
were invisible until calls through generics were expanded to their specifics, as dev note
`202608080003` had found for the uncalled analysis.

**The test-code rule matched letters, not words.** "TEST" anywhere in a name made `LATEST_VALUE`
test code and missed `Test_Foo`. And the call graph records only calls from procedures, so a
procedure called from a driver program and from a test looked test-only; the work order failed its
own test this way, leaving out procedures a main program calls.

**Entry points are partly a fact and partly a decision.** With the whole OpenFAST tree as
`--search` and no entry-point list, the inventory finds the callers of 3 of AeroDyn's 7 uncalled
entry points. The other four, `AD_CalcConstrStateResidual`, `AD_JacobianPConstrState`,
`AD_JacobianPDiscState` and `BEMT_End`, are called by nothing in OpenFAST; the list keeps them
because the module template requires them. Without the list the inventory calls them dead, which is
accurate.

**A narrow `--src` makes callers disappear.** On the quickstart, `vit inventory --src src` left
`step` out of the work order as dead: its only caller is the program in `app/`. `--search .` finds
it. `GETTING_STARTED.md` gives the command with `--search`.

**The ordering exceptions match the August findings.** On ROSCO's `main`, `vit inventory` flags
`ReadEmptyLine` with owner `ReadCpFile` (dev note `202608072334` derived this by hand), the ten
`Parse*` specifics with their owners, and `PowerControlSetpoints`'s write to unit 401, which nothing
opens (the "unguarded debug write" of the same note). `SetpointSmoother`, the re-translation's first
non-leaf (`202608080128`), is among its probe candidates. On AeroDyn it flags 12 procedures, every
owner chain checked against the source for the echo-file and AeroAcoustics cases, and the work
order leaves out `DBEMT_End` as test code: in all of OpenFAST only `DBEMT_Test.f90` calls it.

## Decisions

**Coverage records what it measured, per file.** Each covered file gets its git blob id, and its
text when the checked-out commit does not hold that content, or when there is no git repository.
Rejected: recording a snapshot commit and keeping it alive with a ref, which writes refs into the
project's repository; and requiring a clean checkout, which rules out the reset-to-clean workflow
that makes the problem in the first place.

**`vit coverage run` owns everything but the cases.** It builds a separate instrumented copy
(CMake with `--coverage` by default, replaceable), resets the counters before each case, runs `gcov
-j` per object directory with names kept apart, and imports. It reads the Fortran compiler CMake
chose from `CMakeCache.txt` and takes the gcov from the same GCC release, because a bare `gcov` is
often another release's, or LLVM's on macOS. A failing case fails the run after the coverage is
written.

**Procedures in coverage are found in the measured source and matched by start line.** gcov names
module procedures, internal procedures, `BIND(C)` procedures and external ones differently, but
always records the start line; matching on it needs no demangling and leaves out compiler-generated
functions, which have no definition at their line.

**The work order's I/O groups are whole.** A procedure doing I/O on a unit another procedure owns
cannot be translated before its owners, since C++ cannot read a Fortran unit, and its owners cannot
be translated before it, since they would hold a C++ stream a Fortran routine cannot use. So the
procedure, every owner, and every procedure that passes the unit between them are one step; a
stored unit's opener joins all its users the same way. A unit no procedure in scope owns is
reported as needing a decision.

**Ties are broken by a stated rule.** Callee steps first, by layer; the smallest probe candidate a
test case reaches, with its callees, before everything else; within a layer, steps a test case
reaches, then smaller, then by name. The rule is arbitrary where it has to be and is the same every
run.

**What a person decides comes in as checked input.** Scope, entry points, and an overrides file
whose two statuses, `out_of_scope` and `deferred`, come from the telemetry's disposition vocabulary.
An override needs a reason, and a name matching no procedure or several is an error, so an override
cannot silently do nothing.

**The telemetry join is not a `vit` command.** Every `vit` command logs itself to the cycle log,
including one that reads it; `python3 -m vit.telemetry` already held `start` and `end`.

## What remains

- Main programs are callers in the work order's test-code rule but are not placed in the order.
- AeroDyn's committed coverage has line counts only. A fresh coverage run gives call counts per
  procedure and tells "not built" from "not reached".
- Static analysis does not see calls through procedure pointers, and it resolves calls by name,
  file, module and USE rather than by full Fortran scoping. `docs/VIT_REFERENCE.md` lists these
  limits.
- The probe candidates are the five smallest depth-1 procedures that have a caller; the work order
  prefers one a test case reaches. A better probe might weigh which callee bridges it exercises.

## References

- Dev notes `202608072334` (leaf-first and caller-owned resources), `202608080128` (leaf-first and
  the callee-bridge path), `202608080003` (generic interfaces), `202604250803` (coverage chooses
  the extraction case)
- `docs/VIT_REFERENCE.md` (commands, settings, telemetry, limits), `docs/CREATING_PROJECT_PLANS.md`
  (Steps 1, 2 and 5), `docs/case-studies/aerodyn/PROJECT_PLAN.md` (section 2 and Current State)
