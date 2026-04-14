# vit/docker/

Dockerfiles for VIT development containers, one per translation target.

VIT itself is project-agnostic, but each translation target has different build dependencies that warrant a dedicated container. This directory holds the Dockerfile for each project's container so the setup is reproducible and version-controlled.

## Current Dockerfiles

| File | Container | Project | Status |
|------|-----------|---------|--------|
| `Dockerfile.openfast` | `vit-dev-openfast` | OpenFAST / AeroDyn translation | **Built and verified 2026-04-14.** End-to-end smoke test passed: cmake 3.28.3, gfortran 13.3.0, Python 3.12.3, Python 2.7.18, VIT 0.1.0 editable install, fparser 0.2.2, KGen executable, LAPACK/BLAS/yaml-cpp present. See dev note `202604140204-vit-dev-openfast-docker-plan.md` for the full build + smoke test record. |

## Historical note on `vit-dev` (ROSCO container)

The `vit-dev` container used for the ROSCO project is **not** represented by a Dockerfile in this directory. It was built interactively on `ubuntu:24.04`, with Python 2.7 compiled from source inside the container and VIT/KGen installed as editable packages after mounting the workspace. The decision was documented in dev note `202603231054-collaborator-onboarding.md`: a Dockerfile approach was rejected at the time because corporate TLS inspection blocks network access during `docker build` in some environments, and the manual setup was too fragile to codify without network-dependent steps that may or may not work.

The committed vit-dev image is shared as a tarball at `vit-dev-image.tar.gz` (689 MB compressed) — that is the authoritative "how vit-dev is assembled" artifact. Build-from-scratch instructions (apt packages, Python 2.7 source build, pip installs) are documented in `GETTING_STARTED.md` under "Building from scratch" as a fallback.

## Why `Dockerfile.openfast` uses `vit-dev-image` as its base

Rather than rebuilding from `ubuntu:24.04`, the OpenFAST Dockerfile starts from the committed `vit-dev-image` and adds OpenFAST-specific dependencies on top. This minimizes the network footprint during `docker build` (fewer packages to install) and reuses everything that already works for ROSCO (gfortran, g++, Python 2.7, KGen, VIT's Python deps).

The tradeoff is that `vit-dev-image` must be loaded as a Docker image before the build works:

```bash
docker load < vit-dev-image.tar.gz
# verify:
docker images | grep vit-dev-image
```

If `vit-dev-image` is not available as a local Docker image, `docker build -f Dockerfile.openfast` will fail with "pull access denied" or similar.

## Building

```bash
cd vit_translation_openfast/vit/docker
docker build -f Dockerfile.openfast -t vit-dev-openfast ..
```

The `..` at the end sets the build context to the parent `vit/` directory, not the `docker/` subdirectory itself.

## Running

```bash
docker run -d --name vit-dev-openfast \
    -v ~/vit_translation_openfast:/workspace \
    vit-dev-openfast

# Attach with:
docker exec -it vit-dev-openfast bash

# Once attached, re-install VIT as an editable package pointing at the new workspace:
pip3 install -e /workspace/vit --break-system-packages
```

The mount maps `vit_translation_openfast/` on the Mac to `/workspace/` inside the container. From inside the container:

- `/workspace/vit/` — VIT tool (independent clone, same `main` branch as ROSCO workspace)
- `/workspace/KGen/` — KGen (independent clone, same `master` branch)
- `/workspace/openfast/` — OpenFAST source tree (upstream NREL clone)

## When network access breaks during build

The Dockerfile has `apt-get update && apt-get install` and `pip3 install` steps that require network access. If `docker build` fails on any of these, the fallback is the same interactive-build pattern used for the original `vit-dev`:

1. Start an interactive container from the base image:
   ```bash
   docker run --name vit-dev-openfast-setup -it vit-dev-image bash
   ```
2. Inside the container, run each `RUN` step from `Dockerfile.openfast` manually (possibly from a different network — home, VPN, or a network without TLS inspection)
3. Commit the resulting container state to a new image:
   ```bash
   docker commit vit-dev-openfast-setup vit-dev-openfast
   docker rm vit-dev-openfast-setup
   ```
4. Start the real container from the committed image as shown in "Running" above.

This is not ideal (it breaks the reproducibility promise of a Dockerfile), but it matches the documented pattern for this environment and produces the same result.

## Future additions

- `Dockerfile.rosco` — retroactive Dockerfile for the ROSCO container, if we ever want to make that environment reproducible from source instead of from the committed tarball. Not a priority while the tarball works.
- Dockerfiles for any subsequent translation targets beyond OpenFAST.
