# Repository Root Review, and a Document-by-Document Revision Before Anything Moves
**Date:** 2026-09-30
**Status:** in-progress (review complete; no file moved, cut or revised yet)
**Scope:** everything at the repository root; the documents slated to move into `docs/`

## Why this note exists

The repository is being prepared for public release. Its root is untidy: ten loose files
alongside the directories, several of them campaign records rather than user documentation.
This note records the review of every root item and the recommendation for each: stay, cut, or
move (and where).

It also records the decision taken afterwards, which governs everything below. **Before anything
moves, every document in scope is reviewed top to bottom, one at a time, and rewritten or revised
as necessary to be current with the codebase and the project's status.** Moving a stale document
into `docs/` only gives it a more authoritative-looking address. The moves happen once, after the
revisions, in a single reviewable commit.

## How the review was done

For each root item the review collected:
- its size and the date of its last change
- how many other tracked files reference it by name, split into code/tests/docker, dev notes, and
  other
- its first lines, or the relevant section

Where a recommendation rests on a specific fact, that fact was checked directly; the references
below give the file and line. Three counts were first taken with `git grep -E` and a `\b` word
boundary. That form silently matches nothing: git's extended regex does not support `\b`. The
counts were redone with plain `grep`, and only those figures appear here.

## Recommendations

### Cut

| Item | Why |
|---|---|
| `.vit_build_wrapper.sh` | **Generated, not source.** `vit/extract.py:1134` writes it into the current directory of the project being extracted, and nothing reads it from this repository. It arrived in `93ceefe`, whose message ("Index dev note 202608091210 and flag the corrected claim") does not mention it. The same commit also added `SCENARIO16_RATE_LIMITER_ARTIFACT.md` and the 185,607-line `analysis/aerodyn/line_coverage.json`, which points to an accidental `git add -A`. Add it to `.gitignore` too, since running `vit` from the repository root would recreate it. |
| `SCENARIO16_RATE_LIMITER_ARTIFACT.md` | **States a superseded explanation as its answer.** Its "Short Answer" attributes the Scenario 16 `2^-70` residual to code generation in the ROSCO rate limiter at a zero crossing. Dev note `202607240344` (2026-07-24) reproduced the residual and showed that is "incorrect as stated": the rate limiter compiles to bit-identical arithmetic, and the true cause is a sub-float32 divergence in the double state, seeded in the wind-speed-estimator EKF (`a2e1d1c` traces it to a small-matrix MATMUL). The root document never mentions that result. The dev note is the correct record, and git history keeps the old document. |

### Move

| Item | Move to | Why |
|---|---|---|
| `CLAUDE.md` | `docs/case-studies/aerodyn/AGENT_MANUAL.md`, with a short new root `CLAUDE.md` | It is the operating manual for the AeroDyn workspace: AeroDyn phase status, `docker exec vit-dev-openfast`, `reset_to_clean.sh`/`integrate_all.sh` (which live in the OpenFAST repository), and git rules for that workspace. Claude Code loads a root `CLAUDE.md` automatically, so every contributor's agent would receive the campaign's instructions. The new root file should cover developing VIT itself: layout, running the tests, the check registry, and dev-note conventions. |
| `GETTING_STARTED.md` | `docs/GETTING_STARTED.md` | Collaborator onboarding for ROSCO. Outside users cannot follow it as written (see the known issues below). |
| `guide_ROSCO.md` | `docs/ROSCO_WALKTHROUGH.md` | The main end-to-end tutorial, which belongs with the other guides. `README.md` links it at lines 84 and 294. |
| `CREATING_PROJECT_PLANS.md` | `docs/CREATING_PROJECT_PLANS.md` | A generic method for planning a translation campaign, useful to outside users. It is referenced from `docker/Dockerfile.openfast` (lines 15 and 151) and `scripts/fortran_inventory.py:6`. |
| `PROJECT_PLAN_ROSCO.md` | `docs/case-studies/rosco/PROJECT_PLAN.md` | The record of the completed ROSCO campaign, not something users need at the top level. |
| `PROJECT_PLAN_AERODYN.md` | `docs/case-studies/aerodyn/PROJECT_PLAN.md` | The same reasoning. It is referenced from `CLAUDE.md` and `docker/Dockerfile.openfast` (lines 14 and 152). |
| `analysis/` | `docs/case-studies/aerodyn/data/` | 5.8 MB of AeroDyn-only data: the procedure inventory, call graph, type and module inventories, and gcov line coverage. `scripts/query_coverage.py:35` defaults to `analysis/aerodyn/line_coverage.json`, so its default must change with the move. Cutting it instead is reasonable: it can be regenerated from the OpenFAST workspace, and `line_coverage.json` alone is 4.4 MB. |
| `docs/NWTC_UTILITIES.md` (not a root item, but the same cleanup) | `docs/case-studies/aerodyn/` | It documents `vit_nwtc.h`/`vit_nwtc.cpp`, which live in the OpenFAST translations, not in this repository. |

### Stay

| Item | Note |
|---|---|
| `README.md` | Stays, but needs a rewrite (see the known issues below). |
| `LICENSE`, `setup.py`, `environment.yml`, `.gitignore` | Standard root files. |
| `vit/`, `tests/`, `scripts/`, `examples/`, `docker/`, `docs/` | A standard layout. `docs/` receives the moves above. |
| `dev/` (338 notes) | Stays, with a new `dev/README.md` explaining that these are dated development notes, cited by ID. Moving it is cheap if preferred, for example to `docs/dev-notes/`: the 196 citations of dev notes outside `dev/` all use the bare ID (140 of them in `PROJECT_PLAN_AERODYN.md`) and none use a path, so only 18 path references inside `dev/` would change. The name `dev/` may read as tooling to an outside reader. |

**Resulting root:** `README.md`, `LICENSE`, `setup.py`, `environment.yml`, `.gitignore`, a short
`CLAUDE.md`, and seven directories.

**References to update when the moves happen:**
- `README.md` (two links to `guide_ROSCO.md`)
- `scripts/query_coverage.py` (the default data path), and the docstring examples in
  `scripts/fortran_inventory.py` and `scripts/parse_gcov.py`
- the comments in `docker/Dockerfile.openfast`, and the mention of `GETTING_STARTED.md` in
  `docker/README.md`
- the new `CLAUDE.md`

Dev notes are dated records and keep their old references.

**Side effect of moving `CLAUDE.md`.** It changes what the author's own agent loads when working
in this repository. If AeroDyn work sessions pick up this repository's `CLAUDE.md`, the AeroDyn
workspace needs its own `CLAUDE.md` pointing at the moved manual. The ROSCO workspace's
`CLAUDE.md` is a symlink to `vit/CLAUDE.md` in that workspace's own `vit` checkout. That checkout
still holds the pre-release history, so the symlink is unaffected unless the checkout is updated.

## The revision pass: review every document before moving any

### Rules

1. **One document at a time, top to bottom.** Do not skim several and batch the edits.
2. **Check every factual claim against the code and the current state rather than against memory
   or other documents.** That covers commands, flags, file paths, file names, counts, status
   lines and commit IDs. A command is current if it runs; a flag is current if `vit <cmd> --help`
   lists it; a path is current if it exists; a commit ID is current if it resolves in this
   history.
3. **Rewrite or revise as needed**, including removing sections that no longer apply. For a
   campaign record (the two project plans), decide whether it is a *historical* document, which
   should be marked as such with its as-of date, or a *living* one, which must be brought current.
   Do not leave it ambiguous.
4. **Record what changed** in each document's commit message, or in a follow-up dev note if the
   changes are substantial.
5. **Only after every document is done**, make the cuts and moves and update the references, as
   one commit.

### Documents in scope

These are the documents named in the recommendations above. Suggested order, with the reason:

| # | Document | Why at this point |
|---|---|---|
| 1 | `CLAUDE.md` | It is being split. Its generic workflow content (the translation and extraction workflows, workflow discipline) is the raw material for the new root `CLAUDE.md` and the user guides, so it is settled first. |
| 2 | `GETTING_STARTED.md` | The entry point for new users; the most visible stale content. |
| 3 | `guide_ROSCO.md` | The tutorial `GETTING_STARTED.md` leads into. |
| 4 | `CREATING_PROJECT_PLANS.md` | A generic method; check it against how the AeroDyn plan was actually built. |
| 5 | `PROJECT_PLAN_ROSCO.md` | A campaign record; decide historical or living (rule 3). |
| 6 | `PROJECT_PLAN_AERODYN.md` | A campaign record; decide historical or living (rule 3). |
| 7 | `docs/NWTC_UTILITIES.md` | Short; confirm it still matches `vit_nwtc.h`. |
| 8 | `SCENARIO16_RATE_LIMITER_ARTIFACT.md` | Recommended cut. Confirm that `202607240344` carries everything worth keeping, then cut. |
| 9 | `README.md` | Last, because it summarises and links the documents above. |

### Known issues found so far (starting points, not the review)

Each of these was observed and checked during this session. The full review of each document
will find more.

- **`README.md`:**
  - the "Current Status" section stops at the 52 ROSCO functions (April) and never mentions AeroDyn
  - Quick Start assumes `/workspace` container paths
  - nothing says how to run the tests
  - "Links" points KGen at `NCAR/KGen`, but the extraction depends on fixes carried in the KGen fork
- **`GETTING_STARTED.md`:**
  - it tells users to load a Docker image tarball that is not published anywhere, and
    `docker/Dockerfile.openfast` builds `FROM vit-dev-image`, which has no Dockerfile
  - it still describes `TRANSLATION_TIPS.md` as automatically included in prompts (lines 26 and
    295), although `CLAUDE.md` records that file's deletion on 2026-08-10; the check registry
    (`vit check --list`) replaced it
  - it calls `CLAUDE.md` "the full operating manual (~50KB)" (line 91); the file is about 25 KB
  - "If you're on an Intel Mac, let us know"; the image is ARM-only
- **`guide_ROSCO.md`:** last changed 2026-03-20, before the AeroDyn phase and most of the
  check-registry and view-type work.
- **`CLAUDE.md`:**
  - the Git section says "`main` is canonical and pushed (`14d0a5b`, 2026-08-09)" and describes
    the `unify-vit` merge. `14d0a5b` does not resolve in the current history, and the history has
    been rewritten twice since (2026-09-24 and 2026-09-30).
  - it cites `translation-loop/POST_REPLICATION_ACTIONS.md`, a file in another, non-public
    repository
  - its AeroDyn status is as of Phase C (2026-04/05)
- **`CREATING_PROJECT_PLANS.md`:** last changed 2026-04-14. **`PROJECT_PLAN_ROSCO.md`:** last
  changed 2026-04-14. **`PROJECT_PLAN_AERODYN.md`:** last changed 2026-04-30, while `CLAUDE.md`
  reports DBEMT Phases A–C complete. Check whether the plan's status lags.
- **Packaging (not a document, but found during the same review):** `setup.py` never declared
  `numpy`, which `vit.cli` imports through `vit.sim_compare`. This is fixed in `e6634ad`, along
  with the new `environment.yml`.

### Open question

The existing documents in `docs/` that stay in place (`VIT_REFERENCE.md`,
`EXTRACTION_INTERNALS.md`, `VERIFICATION_METHODS.md`, `CONTAINER_SETUP.md`) were not reviewed
individually here, because they are not at the root. They will sit beside the revised documents.
Whether they get the same top-to-bottom pass before release is for the author to decide.
`CONTAINER_SETUP.md` has a concrete problem either way: it has four references to the Docker
assets removed below.

## Addendum: `docker/` reduced to one generic image

Taken up ahead of the document pass, at the author's request. `docker/` held three Dockerfiles,
none of them a generic VIT image:

- **`Dockerfile.openfast`** built `FROM vit-dev-image`, a local image that exists only as a
  tarball and has no Dockerfile. No one else could build it. It also added OpenFAST's build and
  test dependencies, and rebuilt Python 2.7 because the base image's copy was broken.
- **`Dockerfile.openfast-nrel-match`** and **`Dockerfile.openfast-rtest-match`** reproduced
  OpenFAST's CI toolchain and its r-test reference toolchain on emulated x86_64. They belonged to
  a closed investigation: `202604212346` concluded "accept ARM64 platform-specific baselines", and
  the 16/16 regression pass in `202604232054` was achieved on the ARM64 build.

`docker/README.md` was mostly internal history: how the original image was assembled by hand,
workarounds for a TLS-inspecting network, an unpublished tarball, and the author's workspace
layout.

All three Dockerfiles were replaced by a single `docker/Dockerfile`:
- It starts from `ubuntu:24.04` and installs the Fortran/C/C++ toolchain and strace.
- Python 2.7.18 is built from source, verified against a pinned SHA-256 that matches pyenv's
  published value.
- It installs the KGen fork at a pinned commit (`d3d6516`), with `kgen` on `PATH`.
- VIT goes into a Python 3 virtual environment, installed in editable mode from `/opt/vit`.
- A translation target's own dependencies come in through `EXTRA_APT_PACKAGES` and
  `EXTRA_PIP_PACKAGES`.

`docker/Dockerfile.dockerignore` is meant to keep `.git`, `dev/` and `analysis/` out of the build
context when BuildKit is used. That path is not verified; see below. `docker/README.md` was rewritten to say what the image is for and how to
build, run and test it.

**Verification, 2026-09-30.** Built with Docker 29.2.1 on a native ARM64 VM, using the legacy
builder because the `buildx` plugin is not installed.

- **Tools:** the image carries everything its README lists:
  - Python 3.12.3 in `/opt/venv`
  - Python 2.7.18, whose source passed its SHA-256 check
  - `kgen` at `d3d6516`
  - `vit` resolving to `/opt/vit`
  - gfortran and g++ 13.3.0, cmake 3.28.3, strace 6.8

  `vit --help` and `kgen --help` run.
- **Test suite:** 976 passed, 0 skipped, exit 0, both on the image's own copy and on a checkout
  mounted over `/opt/vit`. The mounted run created or modified 0 files in the checkout.
- **KGen smoke test:** `kgen` was invoked with the option set `vit extract` passes, on KGen's own
  `simple` example. It captured state, and the generated kernel built, ran and reported
  "calc: PASSED verification" (1/1 cases). The test failed twice before passing, both times for
  real reasons, not flakiness:
  - The example's `Makefile` enables KGen's coverage option, which emits uppercase `#IFDEF`
    directives that gfortran's preprocessor rejects. VIT never passes that option.
  - The kernel's default `make` target builds the kernel without running it.
- **OpenFAST example:** the build documented in the README
  (`EXTRA_APT_PACKAGES="liblapack-dev libopenblas-dev libyaml-cpp-dev"`,
  `EXTRA_PIP_PACKAGES="vtk bokeh nptdms pandas"`) builds. vtk 9.7.1, bokeh 3.10.0, nptdms 1.11.0
  and pandas 3.0.6 import, and the three libraries are installed.

**Two defects found by testing, and fixed:**
1. **Host bytecode caches were copied into the image.** The legacy builder ignores
   `docker/Dockerfile.dockerignore`, so the host's `__pycache__` directories were copied in.
   pytest reused its host-compiled `.pyc` files, whose embedded source path is a host path;
   `inspect.getsource` then failed, and `test_bridge_and_abi_agree_about_extent_order` failed
   (975/976). A published image built that way would also carry the builder's home-directory path.
   Fixed: the install step deletes copied `__pycache__` and `*.egg-info` before installing.
2. **The README's own mounted-checkout command hit the same failure.** It failed on any checkout
   whose tests had been run on the host (reproduced: 975/976). It would also have written
   root-owned caches into the user's tree. Fixed: `PYTHONPYCACHEPREFIX` and
   `PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest_cache"` keep both caches inside the container.
   pytest's rewrite cache honours `sys.pycache_prefix`, as checked in its source.

One more trap, in the build environment rather than the image: the local `ubuntu:24.04` tag
pointed at an amd64 image loaded for the old toolchain-matching work, so the first build ran
under emulation. Pulling the arm64 variant fixed it. The image pins no platform, so Docker uses
whatever `ubuntu:24.04` resolves to locally.

**Not verified:**
- a build on an x86_64 host
- the BuildKit path, in particular that `docker/Dockerfile.dockerignore` is honoured
- the 17 AeroDyn baselines in an image built this way

Still to do in the document pass: `docs/CONTAINER_SETUP.md` and `GETTING_STARTED.md` describe the
removed images (4 and 3 references). The AeroDyn workflow keeps using its existing
`vit-dev-openfast` container. Before a rebuilt image replaces that container, it must reproduce
the 17 AeroDyn baselines, which are specific to ARM64 with OpenBLAS.

## Next step

Start the revision pass with `CLAUDE.md` (#1 in the table above).
