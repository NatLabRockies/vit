# One Canonical VIT, and What Actually Generalises

**Date:** 2026-08-09
**Status:** completed — branch `unify-vit`, not pushed
**Scope:** method; VIT generality; a review of the replication's 19 VIT commits

## 1. The review, and its verdict

The replication made **19 commits to `vit/`** between 7 and 9 August: +1,370/−160 across 12 modules,
+579 lines of tests. The question was whether they generalise beyond ROSCO.

**They do, and the evidence is mechanical.** Scanning every added line for ROSCO identifiers finds
**9 occurrences, all of them in comments**, naming the motivating case so a reader can go check it.
Zero in code. That convention is worth stating as a rule: *the rule is general, the example is named.*

The fixes themselves are Fortran-language-level — CHARACTER arguments/results/`CHARACTER(*)` arrays,
LOGICAL crossing as `INTEGER(C_INT)` both ways, `COMPLEX` kind-alias resolution, allocate-on-return
for `INTENT(OUT) ALLOCATABLE`, dummy/view-component aliasing, case-insensitive name uniqueness
(because *Fortran* is case-insensitive), `INTERFACE`-block nesting, block-construct call sites.

`56020f9` is the model: `COMPLEX(DbKi)` with `DbKi → REAL(8)` was rewritten to `REAL(8)`, dropping
the imaginary part silently. The fix takes the *width* from the alias and keeps the *declared base* —
a general truth about kind aliases, found through one function.

The reasoning was explicitly about generality, too. From the transcript: *"I rejected the obvious
workaround and recorded why, so it doesn't get re-proposed"*; *"calling a tooling gap 'out of scope'
hides a limitation, and calling a container workaround 'blocked' invents a failure the method never
had."*

## 2. The problem is not the fixes. It is how they were found.

Why did a *replication* of a finished campaign need 19 fixes?

| | procedures |
|---|---:|
| original campaign | 52 |
| replication | 67 |

The 26 new ones are almost entirely `ROSCO_Helpers` string and IO utilities — `conv2uc`, `getwords`,
`findline`, `getpath`, `pathisrelative`, `int2lstr`, `curdate`, `chkparsedata`, `reademptyline`,
`addtolist`. **That is exactly where the 19 fixes cluster.**

**VIT's language coverage was demand-driven, and the demand had been one codebase's numerical core.**
52 REAL-typed controllers produce a toolchain that handles REAL-typed controllers. The next codebase
will do this again in whatever corner of Fortran it uses that ROSCO does not, one function at a time,
at whatever moment it is reached.

## 3. Where the coupling actually is

Not in the Fortran. In the environment. Ranked as found:

1. **Build-file patching.** `sed -i '0,/^\( *src\/[A-Za-z_]*\.f90\)$/...'` assumed CMake, a `src/`
   prefix, lowercase `.f90`, and names free of digits and hyphens — and printed *"VIT: Patched
   CMakeLists.txt"* **whether or not it matched**. The build then succeeded, the `.so` linked with
   `kgen_init_vars` unresolved, the simulation ran, and the only symptom was an empty capture
   (`202608071141`). The project's own cardinal sin, inside its own tooling.
2. **Compiler hardcoding.** `FC = gfortran`, `CXX = g++` as literals in a generator whose Fortran
   compiler is a `vit.yaml` field — and my own `-lgfortran -lm` made it worse.
3. **Untested modules.** `type_map`, `derived_type_parser`, `view_populator` had **no test files**,
   and the first two held the `COMPLEX` narrowing and `apply_type_strategies`' silent no-op. The most
   general components, the least covered.
4. **KGen text-scraping.** Regexes over KGen's *generated Fortran*. See §5 — I was wrong about this.

## 4. Unification

The two checkouts had diverged from `03296de`. Measured: **all `vit/` source evolution happened on
the replication side**; this workspace's 57 commits touched only dev notes, analysis and
`CLAUDE.md`. The merge was disjoint and AeroDyn's VIT work, which predates the base, is untouched.

Canonical is now branch **`unify-vit`** here = campaign VIT + the 6 harness fixes + the 5 below.
**Not pushed** — the replication runs against its own checkout and must not receive this.

## 5. The five fixes, one of which was my own error

**1 — a failed build-file patch is fatal.** The pattern is wider, but that is not the fix: the next
layout breaks it too. An unmatched pattern now `exit 1`s and names the override; a missing
`cmake_file` likewise. New `kgen.add_source_cmd`, so a build system the default cannot match is a
config line rather than a patch to VIT.

**3 — 20 tests for the two untested modules.** `apply_type_strategies` now accepts both shapes,
**raises** on anything else, and **returns** the names that matched nothing. A strategy the code
cannot read must not look like one it declined to apply.

**5 — the toolchain is configuration.** `FC`/`CXX` from config; the Fortran runtime derived per
compiler (gfortran, ifx/ifort, flang, flang-new, nvfortran) with `fortran.runtime_libs` to override.
An unknown compiler falls back **and says it guessed**.

**2 — the conformance matrix.** §6.

**4 — I was wrong.** I recommended *"have KGen emit structured results; stop regexing its output"* on
the strength of seeing regexes. Tracing what the verdict depends on: it comes from KGen's own printed
summary and output-variable count, guarded at three points — `rc != 0`; an explicit
`Number of output variables: 0` (`eca3e99`); and the field-log override, which requires a non-empty
C++ log and already counts any Fortran-only field as a difference. **If the patterns stop matching, the
result is fewer diagnostics, not a wrong verdict.** Rewriting KGen's emitter would have been work
against a hazard already closed. What was left: the log can silently *under-report* coverage, so
`field_log_coverage()` now states what was compared and the override carries it — a green that
overrides a failure has to be auditable.

## 6. The conformance matrix, and the two defects it found immediately

`tests/conformance/conformance.f90` — 37 procedures over type × shape × intent × attribute ×
procedure-kind, including every gap the replication hit. `matrix.toml` records what VIT does with
each; the test fails when record and reality diverge **in either direction**, so an improvement
cannot drift in unacknowledged either. Every non-crossing cell must state a reason.

**The compile stage is the part that matters.** `bridge = yes` only means the generator did not
raise — the weak green this project distrusts, and exactly what `CableControl`'s bridge had for
months while never once compiling. Adding `gfortran -fsyntax-only` took the honest count **from 36 to
21**; finding out why took it to **32 of 37**.

Two real defects fell out, both general:

- **The enclosing module was the first module in the file.** `find_function_in_file` scanned forward
  and stopped at the first `MODULE`. In a file with two — a types module beside the module that uses
  it, the commonest layout in scientific Fortran — the bridge emits `USE <wrong-module>`, the
  procedure has no visible interface, **a SUBROUTINE still compiles** because Fortran permits an
  implicit interface, and only a FUNCTION fails with *"has no IMPLICIT type"*, which reads like a
  problem in the function. ROSCO puts one module per file. Now tracks module scope.
- **`COMPLEX` had no branch in the bridge.** The argument was named in the parameter list and never
  declared. `type_map` learned `COMPLEX` at `56020f9`; the bridge did not, because no test-validate
  had ever run on a COMPLEX argument. **Half a fix, and the missing half was invisible until the
  fixture asked directly.**

The five remaining cells are documented limits with stated causes: ALLOCATABLE dummies on the
reference side, rank-2 assumed-shape, array-valued FUNCTION results, deferred-length CHARACTER
results.

Also new: `tests/test_cli.py`. A **SyntaxError in `cli.py` survived 748 tests** — mine, from
inserting a keyword argument into the wrong call.

## 7. What this changes

Before: *we will find out what VIT cannot do when a translation fails.*
After: *here is the table, measured, and here is what each gap costs.*

798 tests. The matrix is the artifact to hand someone asking "will this work on my codebase" — not
because it says yes, but because it says exactly where it says no.

## References

- Branch `unify-vit` (local); `translation-loop/vit-patches/` for the harness-side fixes
- Dev note `202608091210` — the six `test-validate` defects found reaching `CableControl`
- Dev note `202608071141` — the silent build-wrapper failure that fix 1 closes
