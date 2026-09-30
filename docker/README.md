# VIT Docker image

VIT's extraction and verification steps run KGen, which needs Python 2.7 and a Linux Fortran
toolchain. This image provides both, with VIT installed, so every step of the workflow runs in one
place. It is not tied to any one translation target: a target's own build dependencies are added
when the image is built.

## What's in the image

| Component | Where |
|---|---|
| Ubuntu 24.04, with gfortran, gcc, g++, make, cmake, git and strace | system packages |
| Python 3.12 with VIT, numpy, pyyaml, pytest and fparser | virtual environment at `/opt/venv`, on `PATH` |
| VIT, installed in editable mode | `/opt/vit` |
| Python 2.7.18, built from source and checksum-verified | `/opt/python27`, with `python2.7` on `PATH` |
| KGen (the fork VIT depends on), at a pinned commit | `/opt/KGen`, with `kgen` on `PATH` |

Because `kgen` is on `PATH`, `kgen.path` in `vit.yaml` can stay at its default, `kgen`.

## Build

From the repository root:

```bash
docker build -f docker/Dockerfile -t vit .
```

The first build compiles Python 2.7, which takes several minutes. Docker builds the image for the
host's architecture; it is tested on ARM64.

| Build argument | Default | Purpose |
|---|---|---|
| `EXTRA_APT_PACKAGES` | none | System packages the translation target needs to build |
| `EXTRA_PIP_PACKAGES` | none | Python packages the target's build or test scripts need |
| `KGEN_REF` | a pinned commit | The KGen fork commit to install |

For example, OpenFAST needs LAPACK, BLAS and yaml-cpp to build, and its regression-test scripts
need a few Python packages:

```bash
docker build -f docker/Dockerfile -t vit-openfast \
    --build-arg EXTRA_APT_PACKAGES="liblapack-dev libopenblas-dev libyaml-cpp-dev" \
    --build-arg EXTRA_PIP_PACKAGES="vtk bokeh nptdms pandas" .
```

## Run

Mount the directory that holds the translation target, and anything its `vit.yaml` refers to, at
`/workspace`:

```bash
docker run -it --rm -v "$PWD":/workspace vit
```

Paths in `vit.yaml` (`build_cmd`, `run_cmd` and the rest) are then container paths under
`/workspace`.

To keep a container running and send commands to it, for example from a coding agent:

```bash
docker run -d --name vit -v "$PWD":/workspace vit sleep infinity
docker exec vit vit --help
```

Floating-point results can differ between x86_64 and ARM64. Compare a translation against
baselines produced on the same architecture.

## Test the image

```bash
docker run --rm vit bash -c "cd /opt/vit && python -m pytest tests -q"
```

## Develop VIT in the container

VIT is installed in editable mode from `/opt/vit`. To use a local checkout instead, mount it over
that path:

```bash
docker run -it --rm -v "$PWD":/opt/vit vit bash -c "cd /opt/vit && python -m pytest tests -q"
```

Python's and pytest's caches stay inside the container, so the mounted checkout is not written to.
