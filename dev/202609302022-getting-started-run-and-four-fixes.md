# Running the Getting-Started Path on a New Project: Four Defects, Fixed, and a macOS Mount Hazard
**Date:** 2026-09-30
**Status:** completed (one defect left open; see the end). Fixed 2026-11-11: dev note `202611111904`.
**Scope:** `GETTING_STARTED.md` rewrite, `examples/quickstart`, `vit extract`, `vit init`, `vit integrate`, `vit status`

## Why

`GETTING_STARTED.md` is document #2 of the revision pass in dev note `202609301806`. It was
onboarding for collaborators on ROSCO: a container image loaded from an unpublished tarball, the
ROSCO workspace, and steps the agent was told to take from a `CLAUDE.md` that no longer describes
ROSCO. The author asked for a guide to using VIT on any codebase, not a ROSCO one.

A generic guide makes claims about what any project needs, and those claims had never been tested
outside ROSCO and AeroDyn. So every step was run, in the image built from `docker/Dockerfile`, on a
new project written for the purpose: about 40 lines of Fortran, with one module holding `drag` (the
function to translate) and `step` (its caller), a driver program, and a CMake build. That project is
now `examples/quickstart`.

## What the run found

The whole cycle (`init`, `find`, `extract`, `translate`, `check`, `verify`, `integrate`, rebuild,
compare) worked on the container's own filesystem from the first attempt: 20 cases captured, 20/20
verified with a working red test, and the rebuilt program matched the pure-Fortran baseline byte for
byte over 500 steps.

On a macOS host mount, which is how the guide and `docker/README.md` tell people to work, it failed
at extraction. Isolating that failure surfaced the following.

### 1. `vit extract` reported success when it captured nothing

The instrumented build failed, KGen produced a kernel with no state files, and VIT printed
`✓ Extraction successful`, then `WARNING: No state data captured.`, and exited 0. Anything driving
VIT by exit code (a script, an agent) would carry on and verify against an empty kernel.

**Fixed:** zero captured cases is now `✗ Extraction captured no state data` and exit 1. The
diagnostics that were printed before are still printed. Test:
`tests/test_cli.py::test_extract_fails_when_it_captured_nothing`, with KGen stubbed, for 0 and 20
cases. The 0-case variant failed against the old code.

### 2. The macOS mount breaks CMake Fortran rebuilds (not a VIT defect)

The extraction failure was `Error copying Fortran module from "PHYSICS.mod"`, from CMake's
`cmake_copy_f90_mod` step immediately after gfortran had compiled the module. Reproduced without
VIT: on the Colima virtiofs mount (`mountType: virtiofs`, `vmType: vz`), a plain `cmake --build`
after a change to the module's interface failed in 5 of 6 rebuilds. The same extraction on a copy
in the container's own filesystem captured 20 cases. `vit verify`, whose kernel build is plain make
without that CMake step, passed twice on the mount.

Extraction always rewrites a module file, because KGen instruments the source. So on this setup
every extraction into a build directory on the mount fails. The mitigation is to keep the CMake
build directory inside the container. That needed fix 3.

Docker Desktop's file sharing was not tested.

### 3. The extraction wrapper assumed one build layout

After adding `kgen_utils.f90` to the build file, the wrapper reconfigured with
`cd <build_dir> && cmake ..`, which works only when the build directory sits directly under the
top-level `CMakeLists.txt`. ROSCO's and AeroDyn's do. With the build directory at `/build/toy`,
`cmake ..` configured `/build`, the failure was discarded (`> /dev/null`) or stopped the wrapper
under `set -e`, and `kgen_utils.f90` never entered the build. The post-extraction cleanup had the
same `cmake ..`.

**Fixed:** one helper, `cmake_reconfigure_command`, used in all three places. In a configured build
tree it runs `cmake .`, which regenerates from the source directory recorded in the cache, wherever
that is; it falls back to `cmake ..` only for an unconfigured tree. Test:
`tests/test_extract.py::test_reconfigure_works_when_the_build_tree_is_not_a_child` runs real CMake
on a build tree outside the source tree, and also asserts that the old command fails there. With
this fix, extraction on the macOS mount with the build directory at `/build/<project>` captured 20
cases.

### 4. `vit init` wrote ROSCO's settings into every project

`target_name: discon` and ROSCO's kind aliases (`ReKi: REAL(8)` and three others) were the
defaults. ROSCO itself uses `ReKi: REAL(4)`, so the default was wrong even for the project it came
from, and a wrong kind alias changes every translated type without an error.

**Fixed:** `init` writes no target and no kind aliases, and does write `-ffp-contract=off` to
`fortran.flags`. With no target configured, `vit integrate` already found the `add_library` target
that lists the source; when it cannot, it now stops with an error that names
`build_system.target_name`, where before no CMake pattern matched and the C++ silently never
entered the build. Tests: `TestInitConfig::test_writes_no_project_specific_defaults`, and two in
`TestGenerateCmakePatches`.

### 5. `vit status` lost the verification record at integration

`vit verify` records `cases_passed`, `cases_total` and the red-test verdict. `vit integrate`
outranks it, and `record_translation` replaced the entry wholesale, so after the normal sequence
`vit status` said `drag: integrated (unverified)`.

**Fixed:** an upgrade keeps the earlier fields, and `status` shows
`integrated (verified 20/20 cases)` when the record holds them. Tests:
`TestRecordTranslation::test_upgrade_keeps_the_verification_record` and
`tests/test_cli.py::test_status_reports_an_integrated_function_as_verified`.

### 6. A grouping defect that the kernel and the full run both missed

A translation of `0.5_dp * 1.225_dp * cd * area * v**2` as `... * area * v * v` passed the kernel
20/20 and matched the 500-step run exactly: for these inputs, `(a*v)*v` and `a*(v*v)` round the
same. `vit check` flagged it (`exponent-grouping`). The guide uses this as its example of why the
check runs before verification, and says plainly that the example's inputs do not expose a
difference.

## A correction to `CLAUDE.md`

Revising `CLAUDE.md` (`a2af7eb`), the claim that the wrapper's `sed` matches
`^ *src/[A-Za-z_]*\.f90$` was marked verified against `vit/extract.py:830`. That line is a
docstring. The `sed` itself (`vit/extract.py`, step 2) was widened on 2026-08-09 (`2a077f2`)
to any single Fortran source path. What still holds is narrower: the inserted entry is `src/kgen_utils.f90`, so
the build file needs a `src/` directory beside it, and an unmatched pattern now stops the build with
an error naming `kgen.add_source_cmd`. `CLAUDE.md` and the docstring now say that.

## What changed

- `GETTING_STARTED.md`: rewritten as a generic guide, covering the image, the full cycle on
  `examples/quickstart`, what a project needs, the `vit.yaml` fields, and the exit-code protocol for
  agents.
- `examples/quickstart`: new. It replaces `examples/rosco`, which was from 2026-02-25 and no longer
  matched the ROSCO fork's configuration.
- `docker/README.md`: a note to keep CMake build directories inside the container on macOS.
- Code and tests as above. The suite went from 977 to 985 tests, all passing.

**Verification of the guide as written:** every command in sections 1 and 2 was run, in order, in a
fresh container from the image rebuilt with these changes, on a macOS virtiofs mount. Every step
exited 0. Extraction captured 20 cases, `verify` passed 20/20 with a working red test, the rebuilt
program matched the baseline byte for byte, and the integrated C++ compiled with
`-ffp-contract=off`.

**Not verified:** Docker Desktop, an x86_64 host, a Linux host, and a project whose build file
does not match the patch (the `kgen.add_source_cmd` path).

## Left open

**A later failing verification cannot downgrade the record.** `record_translation` never lowers
the status, and `non_discriminating` is not in its ranking. So when a function once recorded as
`verified` later verifies as `NON_DISCRIMINATING`, its status stays `verified`, with only the
detail fields updated. A failed re-verification is not recorded at all. Both report a pass that the
latest evidence does not support.
