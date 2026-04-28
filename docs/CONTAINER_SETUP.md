# Container Setup Reference

All building, extraction, simulation, and testing happens inside Docker containers. **Never run VIT commands, ROSCO/OpenFAST builds, or KGen directly on the Mac** — KGen's Fortran parser does not work on macOS.

## Two Containers

| Container | Mac path mounted | Container path | Project |
|-----------|-----------------|----------------|---------|
| **`vit-dev`** | `vit_translation/` | `/workspace/` | ROSCO (completed) |
| **`vit-dev-openfast`** | `vit_translation_openfast/` | `/workspace/` | OpenFAST / AeroDyn (active) |

Files edited on the Mac are immediately visible inside the corresponding container. **Use the right container for the right project.**

## Running Commands (OpenFAST Container)

```bash
# Start if stopped:
docker start vit-dev-openfast

# Or recreate from the built image:
docker run -d --name vit-dev-openfast \
    -v ~/vit_translation_openfast:/workspace \
    vit-dev-openfast:latest

# After mounting a fresh workspace, re-install VIT:
docker exec vit-dev-openfast pip3 install -e /workspace/vit --break-system-packages

# Run VIT commands:
docker exec -it vit-dev-openfast bash -c "cd /workspace/openfast && vit --help"
```

## Running Commands (ROSCO Container)

```bash
docker start vit-dev

docker exec -it vit-dev bash -c "cd /workspace/ROSCO && <command>"

# Multi-command:
docker exec -it vit-dev bash -c "
  cd /workspace/ROSCO &&
  <command1> &&
  <command2>
"
```

**WARNING: Never chain build and simulation in one `docker exec` command.** The build step changes the working directory. Use separate `docker exec` calls.

## Rebuilding `vit-dev-openfast` from Scratch

```bash
# Load the base image if not already loaded:
docker load < vit-dev-image.tar.gz

# Build:
cd vit_translation_openfast/vit/docker
docker build -f Dockerfile.openfast -t vit-dev-openfast ..
```

Building from scratch takes ~10-15 minutes (most is the Python 2.7 source compile). The Dockerfile records all setup steps.

## How `vit-dev` Was Built

Interactively, on `ubuntu:24.04`, with Python 2.7 compiled from source. Committed and exported as `vit-dev-image.tar.gz`. See dev note `202603231054`.

`vit-dev-openfast` FROMs `vit-dev-image` and adds OpenFAST dependencies (LAPACK/BLAS, yaml-cpp, Python 2.7 rebuilt, fparser, vtk/Bokeh/nptdms). Built and verified 2026-04-14. See dev note `202604140204`.

## What Is Installed

- Ubuntu 24.04 (ARM, via Colima on Apple Silicon)
- gfortran, g++, cmake
- Python 3.12 (system), Python 2.7 (built from source at `/opt/python27`)
- VIT: `pip install -e /workspace/vit`
- ROSCO: `pip install -e /workspace/ROSCO` (vit-dev only)

## If the Container Is Destroyed

Python 2.7 must be rebuilt from source. The tarball is at `/workspace/Python-2.7.18.tar.xz`:

```bash
cd /workspace/Python-2.7.18
./configure --prefix=/opt/python27
make -j$(nproc)
make install
ln -s /opt/python27/bin/python2.7 /usr/local/bin/python2.7
```

Then reinstall VIT:
```bash
pip3 install -e /workspace/vit --break-system-packages
```

## Container Quirks

- **Clock skew warnings** during `make` are harmless (filesystem timestamp differences between Mac and container).
- **No network access** from inside the container due to corporate TLS inspection. All packages were pre-installed.
