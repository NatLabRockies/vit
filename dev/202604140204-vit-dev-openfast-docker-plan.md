# vit-dev-openfast Container — Plan and Dockerfile Draft
**Date:** 2026-04-14
**Status:** in-progress
**Scope:** Docker environment for OpenFAST / AeroDyn translation, Dockerfile draft, decision rationale

## Context

The `vit-dev` container used for the ROSCO project is mounted at `vit_translation/ → /workspace/` and therefore cannot see the new OpenFAST workspace at `vit_translation_openfast/`. This is a hard blocker for AeroDyn work: KGen does not work on macOS (so VIT commands must run inside a container), and the current container cannot see the OpenFAST files at all.

Three resolution paths were considered in the conversation that produced this dev note:

1. **Extend the existing `vit-dev` mount to the workspace root → `/workspace/`** so both sibling workspaces are visible. Requires container recreation and Python 2.7 rebuild from the tarball. Path literals in all ROSCO documentation (`/workspace/ROSCO/...`) would change to `/workspace/vit_translation/ROSCO/...`, which would mean a documentation sweep through CLAUDE.md.
2. **Create a second container `vit-dev-openfast`** dedicated to OpenFAST work. Full isolation — the ROSCO environment remains untouched and cannot be accidentally broken by OpenFAST-specific dependency installs. Tradeoff: two container environments to maintain.
3. **Add a second bind mount to `vit-dev`** for `vit_translation_openfast/ → /workspace/openfast_workspace/`. Docker does not allow adding mounts to a running container, so this still requires full recreation (same Python 2.7 rebuild cost as option 1), but leaves ROSCO paths unchanged.

The user chose **option 2 (separate container)**. The decisive argument was that OpenFAST has genuinely different build dependencies from ROSCO (LAPACK/BLAS, CMake 3.15+, yaml-cpp, additional Python packages for `reg_tests/`), and adding those to the known-good ROSCO environment risks breaking the ability to rebuild ROSCO cleanly. Given that we may need to return to ROSCO for debugging, Exawind integration support, or regression checks, full isolation is the safest path. This dev note records that decision and the resulting Dockerfile draft.

## Dependency audit: what OpenFAST needs that vit-dev doesn't have

**System packages (apt):**
- **libopenblas-dev + liblapack-dev** — OpenFAST uses LAPACK/BLAS extensively for matrix operations in ElastoDyn (mode shapes, eigenvalue problems), SubDyn (Craig-Bampton reduction), BeamDyn (structural dynamics). ROSCO has no linear algebra beyond scalar math, so `vit-dev` almost certainly does not have these installed.
- **libyaml-cpp-dev** — newer OpenFAST modules use yaml-cpp for some config parsing. Whether AeroDyn specifically needs it is unconfirmed, but installing it pre-empts a later rebuild.
- **Optional:** libhdf5-dev, libnetcdf-dev — used by some `reg_tests/` configurations for large output files. Not strictly required for AeroDyn. Commented out in the Dockerfile; can be enabled later.

**CMake version:** OpenFAST's top `CMakeLists.txt` has `cmake_minimum_required(VERSION 3.15)`. Ubuntu 24.04's default apt cmake is 3.28, so the `vit-dev` base image almost certainly has a sufficient version already. Flagged as a TODO in the Dockerfile to verify once Colima is running and `docker exec vit-dev cmake --version` can be checked.

**Python packages (pip):**
- **fparser** — the Fortran AST toolkit from STFC's PSyclone project. Required by VIT's structural inventory step (Phase 0 of the methodology in `CREATING_PROJECT_PLANS.md`). This is a brand-new dependency VIT has never had. It will be used to produce the function inventory, call graph, type dependency graph, and module layering data files that inform `PROJECT_PLAN_AERODYN.md`. Package name at install time is `fparser` (the `fparser2` submodule is accessed via `from fparser.two.parser import ParserFactory`).
- **vtk, Bokeh (>=2.4, pinned to avoid 3.0.0–3.0.3), pytest, nptdms, pandas** — OpenFAST's `reg_tests/` framework requirements from `openfast/requirements.txt`. Needed if we want to run OpenFAST's native regression tests for verification (one plausible path for AeroDyn module-level verification). If we end up using a custom AeroDyn Driver-based harness instead, these are harmless overhead.
- **numpy** — already present in the `vit-dev-image` base from ROSCO's `vit_sim.py` use. No install needed.

**Things NOT added but worth noting for future:**
- **HDF5 tooling** — if AeroDyn reg_tests need HDF5 output
- **NetCDF** — same
- **LAPACK variants** — we're installing OpenBLAS + reference LAPACK, which should cover every OpenFAST need. If OpenFAST configures with a different backend (MKL, Atlas), we'd need to adjust.
- **A newer gfortran** — OpenFAST uses Fortran 2003+ features. Ubuntu 24.04's gfortran 13.x should be sufficient, but flagged as "verify at first build" in case we hit Fortran standard issues.

## Base image strategy: `FROM vit-dev-image`, not `FROM ubuntu:24.04`

The Dockerfile starts from the committed `vit-dev-image` rather than rebuilding from scratch on `ubuntu:24.04`. This decision has two motivations:

1. **Minimize network footprint during `docker build`.** The vit-dev-image already contains gfortran, g++, cmake, Python 3.12, Python 2.7 (built from source), KGen, and VIT's Python dependencies. Starting from there means the OpenFAST Dockerfile only needs to install the *deltas* — OpenFAST-specific apt packages, OpenFAST-specific Python packages, and fparser. That's roughly 10 package installs instead of ~50, which makes the build more likely to succeed even if network access is flaky.

2. **Reuse known-good ROSCO foundation.** Every piece of environment setup in `vit-dev-image` has been validated by ROSCO's 38-day end-to-end verification chain. Rebuilding from ubuntu:24.04 reintroduces every possible failure mode that was solved during the original vit-dev setup (Python 2.7 build, gfortran version pinning, KGen install procedure). Starting from vit-dev-image sidesteps all of that.

**The tradeoff** is a prerequisite: the committed image must be loaded as a Docker image before `docker build` works:

```bash
docker load < vit-dev-image.tar.gz
```

Without this, the `FROM vit-dev-image` line fails with a "pull access denied" error. This is documented in the Dockerfile header and in `vit/docker/README.md`.

## Network access question (open)

The Dockerfile includes `apt-get update && apt-get install ...` and `pip3 install ...` steps. These require network access during `docker build`. Dev note `202603231054-collaborator-onboarding.md` documented that the ROSCO container was built interactively (not from a Dockerfile) specifically because "corporate TLS inspection blocks network access during `docker build` in some environments." That constraint may or may not still apply.

The question is whether the Mac's `docker build` environment can reach apt repositories and PyPI through the corporate network. Factors at play:
- `docker build` runs on the host network stack, which is the Mac's network. The Mac clearly has functioning HTTPS to GitHub (we've been pushing git commits throughout this conversation). Whether it can also reach `archive.ubuntu.com` and `pypi.org` through the same network is uncertain.
- Colima's VM may be configured with different network routing than the Mac host.
- Corporate TLS inspection may be applied differently to known hosts (GitHub) vs unknown ones (arbitrary apt/pip mirrors).

**Plan:** attempt the Dockerfile build first. If it succeeds, we have a reproducible Dockerfile for future rebuilds. If apt or pip steps fail, fall back to the interactive-build pattern documented in `vit/docker/README.md` — start a running container, install packages manually (possibly from a different network such as home or VPN), and `docker commit` to produce the `vit-dev-openfast` image. This is the same pattern that produced `vit-dev` originally and is the known-good fallback for this environment.

Either path produces the same end-state image (`vit-dev-openfast`). The Dockerfile's value, even if the build fails, is that it documents the precise setup as a committed, version-controlled artifact — a reference that can be replayed when network access improves.

## Files created

Three files, all committed to the VIT repo in the OpenFAST workspace:

1. **`vit/docker/Dockerfile.openfast`** — the Dockerfile itself. Heavily commented with purpose, build instructions, network caveat, and fallback procedure. `FROM vit-dev-image`, installs LAPACK/BLAS/yaml-cpp via apt, installs fparser + OpenFAST Python deps via pip, sets WORKDIR to /workspace, defaults to `sleep infinity` so the container stays alive for `docker exec` attachment.

2. **`vit/docker/README.md`** — explains the `docker/` directory, current Dockerfile inventory, historical note on why `vit-dev` is not represented by a Dockerfile, why we FROM `vit-dev-image`, build and run instructions, network-failure fallback procedure, future additions.

3. **This dev note** (`vit/dev/202604140204-vit-dev-openfast-docker-plan.md`) — full decision record, dependency audit, base image strategy, network access discussion, Dockerfile content summary, and links to related context.

## Build executed and verified (2026-04-14)

After the plan above was drafted, the user authorized Colima restart and I executed the full build + smoke test in this same session. Everything worked. Results:

**Colima started successfully.** `colima start` from a stopped state took ~15 seconds. Docker daemon accessible afterward.

**`vit-dev-image` was already loaded** in the local Docker image store (2.7 GB disk, 728 MB content size). No need to `docker load` from the tarball.

**Pre-build inspection of `vit-dev-image`:**
- `cmake --version` → 3.28.3 (comfortably exceeds OpenFAST's 3.15 minimum; TODO resolved)
- `gfortran --version` → 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) — supports Fortran 2003+ features required by OpenFAST
- `python3 --version` → Python 3.12.3
- `dpkg -l` confirmed libopenblas/liblapack/libyaml-cpp/libhdf5/libnetcdf all absent (as expected; these were added by the Dockerfile)
- `pip3 list` confirmed numpy (2.4.2), pandas (3.0.1), pytest (9.0.2), scipy (1.17.1) already present from the ROSCO Python environment; vtk, Bokeh, nptdms, fparser all absent (added by the Dockerfile)

This let me trim the Dockerfile's pip install list — numpy, pandas, pytest are inherited from `vit-dev-image`, so the Dockerfile only installs `vtk`, `Bokeh`, `nptdms` for OpenFAST reg_tests. The original draft had all six; saved three redundant installs.

**Discovered issue: `/usr/local/bin/python2.7` is a dangling symlink in `vit-dev-image`.** The symlink points at `/opt/python27/bin/python2.7`, but `/opt/python27/` does not exist in the committed image. Either Python 2.7 was built into the running `vit-dev` container *after* the image was committed, or `/opt/` was excluded from the commit somehow. Either way, the effect is that the vit-dev-image base has a broken Python 2.7 setup, and any container derived from it inherits that broken state.

This meant the new OpenFAST container would not be able to run KGen (which needs Python 2.7). **Resolution: added two new RUN blocks to `Dockerfile.openfast`** — one that installs Python 2.7 build dependencies (`wget`, `build-essential`, `libssl-dev`, `zlib1g-dev`, `libffi-dev`, `libbz2-dev`, `libreadline-dev`, `libsqlite3-dev`, `libncurses-dev`), and one that downloads `Python-2.7.18.tar.xz` from python.org, configures with `--prefix=/opt/python27`, `make -j$(nproc)`, `make install`, and recreates the symlink at `/usr/local/bin/python2.7`. Adds ~5 minutes to the total docker build time. Makes the Dockerfile fully self-contained — no Python 2.7 tarball dependency on the host filesystem.

**Network access during `docker build` works cleanly.** The dev-note-documented concern from `202603231054` (corporate TLS inspection blocking apt/pip) does not apply to `docker build` in the current environment. All three RUN steps that need network succeeded:
1. `apt-get update && apt-get install` (OpenFAST system deps + Python 2.7 build deps): succeeded
2. `wget https://www.python.org/ftp/python/2.7.18/Python-2.7.18.tar.xz`: succeeded
3. `pip3 install fparser vtk Bokeh nptdms`: succeeded (all packages downloaded and installed cleanly, including transitive deps like tornado, xyzservices, markupsafe, narwhals, etc.)

This is a significant finding: it means future containers can be built from Dockerfiles instead of interactively, which is what we want for reproducibility. It may not have been true in 2026-03 when the vit-dev decision was made, but it is true now.

**Build produced image `vit-dev-openfast:latest` (id `e39dfd1dc1d5`)** successfully.

**End-to-end smoke test (all passing):**
- `docker run -d --name vit-dev-openfast -v ~/vit_translation_openfast:/workspace vit-dev-openfast:latest` → container starts cleanly with `sleep infinity` as the main process
- `docker exec vit-dev-openfast ls /workspace` → shows `vit/`, `KGen/`, `openfast/` (the three repos in the OpenFAST workspace, all visible through the mount)
- `docker exec vit-dev-openfast cmake --version` → 3.28.3
- `docker exec vit-dev-openfast gfortran --version` → 13.3.0
- `docker exec vit-dev-openfast python3 --version` → 3.12.3
- `docker exec vit-dev-openfast python2.7 --version` → **2.7.18** (fixed, no longer dangling)
- `docker exec vit-dev-openfast pip3 install -e /workspace/vit --break-system-packages` → `Successfully installed vit-0.1.0`
- `docker exec vit-dev-openfast vit --help` → shows all 12 subcommands (init, find, parse, extract, translate, interface, verify, integrate, sim-compare, run, status, analyze-types)
- `docker exec vit-dev-openfast python3 -c "import fparser; from fparser.two.parser import ParserFactory"` → `fparser 0.2.2 OK`
- `docker exec vit-dev-openfast python2.7 /workspace/KGen/bin/kgen --help` → KGen help text prints successfully, confirming Python 2.7 → KGen toolchain is operational
- `docker exec vit-dev-openfast dpkg -l libopenblas-dev liblapack-dev libyaml-cpp-dev` → all three `ii` (installed and correctly configured)

**The container is ready for work.** Phase 0 structural inventory (fparser-based analysis of AeroDyn and NWTC Library) can begin immediately. When the plan moves to KGen extraction, that will also work — Python 2.7 + KGen + VIT are all wired up.

## CLAUDE.md Docker Container section updated

Along with this dev note, CLAUDE.md's "Docker Container" section was rewritten to reflect the resolved state. The section now documents two containers (`vit-dev` for ROSCO, `vit-dev-openfast` for OpenFAST) in a two-row table, with mount paths, build origin, and a note that each container only sees one workspace. The "blocker" framing from the earlier version is replaced with a "running commands in the OpenFAST container" howto that includes the necessary `pip install -e` step for the editable VIT install.

## Deferred / future work

The following are nice-to-haves but not blockers for current AeroDyn work:

1. **Retroactive `Dockerfile.rosco`.** The ROSCO container is still represented only by the committed tarball at `vit-dev-image.tar.gz`. Now that we've proven network access works during `docker build`, we could write a retroactive `Dockerfile.rosco` that would reproduce the ROSCO container from scratch. Low priority — the tarball works fine for current needs.

2. **Save `vit-dev-openfast-image.tar.gz`** for sharing with collaborators (parallel to the existing `vit-dev-image.tar.gz`). Would let someone working on AeroDyn get a working container without waiting for the 10-15 minute build. Defer until a collaborator actually needs it.

3. **Verify `vit analyze-types` and `vit extract` work end-to-end against OpenFAST source.** The smoke test confirmed the toolchain components are wired up, but has not yet run actual VIT commands against actual OpenFAST source. This is the real "can VIT see OpenFAST?" milestone from the kickoff dev note (`202604140013`), and it is the first substantive AeroDyn work. When the user directs, the next step is: start the `vit analyze-types` run against `openfast/modules/aerodyn/AeroDyn_Types.f90` and see what blockers come up.

## Related dev notes

- `202603231054-collaborator-onboarding.md` — original Docker image sharing vs Dockerfile decision for the ROSCO container. Essential context for understanding why vit-dev was built interactively instead of from a Dockerfile in the first place.
- `202604140013-openfast-aerodyn-phase-start.md` — the AeroDyn kickoff note. Flagged the docker mount situation as a blocker and listed it in the "deferred" section; this dev note is the resolution plan for that deferred item.
- `202602252006-rosco-guide.md` — the original VIT user guide creation, which documents the container environment setup and establishes the "hand-assembled, documented in prose" pattern.

## Related files

- `CREATING_PROJECT_PLANS.md` — the methodology document that motivated adding fparser as a dependency. The structural inventory step (Phase 0 of the methodology) requires a Fortran AST tool, and fparser was chosen as the recommended one.
- `CLAUDE.md` (current reframed version) — the "Docker Container" section describes the current mount situation, flags it as a blocker for OpenFAST/AeroDyn work, and references this dev note for the decision record.
- `GETTING_STARTED.md` — the VIT user guide with the "Building from scratch" section that documents the original manual vit-dev build procedure. Useful as a reference if the OpenFAST Dockerfile build fails and we need to use the interactive fallback.
