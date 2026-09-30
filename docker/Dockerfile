# VIT image: a Fortran/C/C++ toolchain, Python 3 with VIT, and KGen with the Python 2.7 it
# requires. One image serves any translation target; a target's own build dependencies are
# added with the EXTRA_* build arguments. See docker/README.md.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile -t vit .

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# System and Python packages the translation target itself needs, e.g. for OpenFAST:
#   --build-arg EXTRA_APT_PACKAGES="liblapack-dev libopenblas-dev libyaml-cpp-dev"
ARG EXTRA_APT_PACKAGES=""
ARG EXTRA_PIP_PACKAGES=""

# KGen, VIT's state-capture engine. The fork carries capture fixes VIT depends on.
ARG KGEN_REPO=https://github.com/kevinmenear/KGen.git
ARG KGEN_REF=d3d651626c2d2005652c8b068fa7d56e4b98776a

# KGen runs only on Python 2.7, which Ubuntu 24.04 no longer packages; it is built from source.
ARG PY27_URL=https://www.python.org/ftp/python/2.7.18/Python-2.7.18.tar.xz
ARG PY27_SHA256=b62c0e7937551d0cc02b8fd5cb0f544f9405bafc9a54d3808ed4594812edef43

ENV LANG=C.UTF-8

# Toolchain, Python 3, and the libraries Python 2.7 builds against.
# strace: KGen reads the target's compiler flags by tracing its build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gfortran cmake git strace ca-certificates wget xz-utils \
        python3 python3-venv python3-dev \
        zlib1g-dev libffi-dev libbz2-dev libreadline-dev libsqlite3-dev libncurses-dev \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q -O /tmp/Python-2.7.18.tar.xz "${PY27_URL}" \
    && echo "${PY27_SHA256}  /tmp/Python-2.7.18.tar.xz" | sha256sum -c - \
    && mkdir /tmp/py27 \
    && tar -xf /tmp/Python-2.7.18.tar.xz -C /tmp/py27 --strip-components=1 \
    && cd /tmp/py27 \
    && ./configure --prefix=/opt/python27 \
    && make -j"$(nproc)" \
    && make install \
    && ln -s /opt/python27/bin/python2.7 /usr/local/bin/python2.7 \
    && cd / && rm -rf /tmp/py27 /tmp/Python-2.7.18.tar.xz

RUN git clone --quiet "${KGEN_REPO}" /opt/KGen \
    && git -C /opt/KGen checkout --quiet --detach "${KGEN_REF}"

RUN if [ -n "${EXTRA_APT_PACKAGES}" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ${EXTRA_APT_PACKAGES} \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# VIT runs in its own Python 3 environment, so pip never touches the system Python.
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=/opt/venv/bin:/opt/KGen/bin:$PATH

# Keep Python's and pytest's caches inside the container. A checkout mounted from the host is then
# never written to (the container runs as root), and caches compiled on the host, which carry host
# paths, are never read.
ENV PYTHONPYCACHEPREFIX=/tmp/pycache
ENV PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest_cache"
RUN python3 -m venv /opt/venv

# VIT is installed editable from /opt/vit: mount a checkout over /opt/vit to run that instead.
# pytest runs VIT's test suite; fparser is used by scripts/fortran_inventory.py.
# Bytecode caches and build metadata copied from the host are removed first: builders that ignore
# Dockerfile.dockerignore would otherwise bring them in, and a host-compiled .pyc carries the
# host's source path, which breaks tracebacks and inspect.getsource inside the container.
COPY . /opt/vit
RUN find /opt/vit \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} + \
    && pip install --no-cache-dir -e /opt/vit pytest fparser ${EXTRA_PIP_PACKAGES}

WORKDIR /workspace
CMD ["bash"]
