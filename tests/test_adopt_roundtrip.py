"""Compile and RUN the adopt path: C++ that ALLOCATES view components.

A translated routine whose Fortran ALLOCATEs components of a derived-type
argument (every INTENT(OUT) initializer does) used to need a hand-written
two-pass wrapper. `vit_adopt_<type>` (view_populator._adopt_routines) makes the
generated wrapper handle it: the C++ allocates with `vit_adopt_alloc_lb`,
stating extents and lower bounds exactly as the Fortran ALLOCATE does, and
publishes the buffer through the view; the wrapper ALLOCATEs the Fortran
component to match, copies, and frees.

The fixture has AeroDyn's AFI_ParameterType shape: rank-1 arrays, an
ALLOCATABLE array of table types, and in each table rank-1, rank-2 and a
rank-3 component allocated `(n, m, 0:3)`. The original Fortran and the
generated-wrapper-plus-C++ run on the same inputs, and every allocation status,
bound and value is compared bit-for-bit, including an error branch that
returns part-allocated and an INOUT call that replaces one component, writes
another in place and must leave a third untouched.

Each green here is paired with a red: a pass counts only if the test can fail.
Skipped, not silently passed, without gfortran and a C++ compiler.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "adopt"
sys.path.insert(0, str(FIX))

needs_toolchain = pytest.mark.skipif(
    shutil.which("gfortran") is None or shutil.which("g++") is None,
    reason="needs gfortran and g++ to compile the generated wrapper",
)

F90 = ["aor_types.f90", "aor_ref.f90", "vit_tabt_view.f90", "vit_part_view.f90",
       "aor_wrap.f90", "compare.f90"]


def _build_and_run(d: Path) -> subprocess.CompletedProcess:
    def run(*cmd):
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
        assert r.returncode == 0, f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}"
    run("gfortran", "-c", *F90)
    run("g++", "-std=c++17", "-c", "aor_shim.cpp")
    cxxlib = "-lc++" if platform.system() == "Darwin" else "-lstdc++"
    run("gfortran", "-o", "compare", *[f[:-4] + ".o" for f in F90], "aor_shim.o", cxxlib)
    return subprocess.run(["./compare"], cwd=d, capture_output=True, text=True)


def _generate(d: Path, **kw):
    import generate
    generate.main(d, **kw)


# ---------------------------------------------------------------- round trip

@needs_toolchain
def test_generated_wrapper_matches_original(tmp_path):
    _generate(tmp_path)
    r = _build_and_run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ADOPT ROUNDTRIP: ALL IDENTICAL" in r.stdout


@needs_toolchain
def test_red_without_the_adopt_call(tmp_path):
    """The wrapper minus its adopt call: nothing the C++ allocated arrives."""
    _generate(tmp_path)
    w = tmp_path / "aor_wrap.f90"
    w.write_text(w.read_text().replace(
        "        CALL vit_adopt_part(p_view, p_snap, p)\n", "", 1))
    r = _build_and_run(tmp_path)
    assert r.returncode != 0
    assert "ALL IDENTICAL" not in r.stdout


SPL = "vit_adopt_alloc_lb((na - 1) * 2 * 4 * sizeof(double), 3, 1, 1, 0)"


@needs_toolchain
def test_red_when_the_translation_drops_a_lower_bound(tmp_path):
    """Spl stated 1-based instead of the ALLOCATE's 0:3 is a BOUNDS mismatch."""
    _generate(tmp_path, impl_source=_impl_with(
        SPL, "vit_adopt_alloc((na - 1) * 2 * 4 * sizeof(double))"))
    r = _build_and_run(tmp_path)
    assert r.returncode != 0
    assert "Table%Spl bounds" in r.stdout


# ---------------------------------------------------------------- refusals

def _impl_with(old: str, new: str) -> str:
    src = (FIX / "aor_impl.cpp").read_text()
    assert old in src
    return src.replace(old, new)


REPLACE = "    p->secondVals = g;          // REPLACED: the wrapper adopts it\n"


@needs_toolchain
def test_refuses_fortran_storage_as_a_new_buffer(tmp_path):
    _generate(tmp_path, impl_source=_impl_with(
        REPLACE, "    p->secondVals = p->keep;\n"))
    r = _build_and_run(tmp_path)
    assert r.returncode != 0
    assert "secondVals was set to the pre-call storage of ParT%keep" in r.stderr


@needs_toolchain
def test_refuses_extent_change_in_place(tmp_path):
    _generate(tmp_path, impl_source=_impl_with(REPLACE, "    (void)g;\n"))
    r = _build_and_run(tmp_path)
    assert r.returncode != 0
    assert "changed extent without a new buffer" in r.stderr


@needs_toolchain
def test_refuses_a_buffer_without_the_header(tmp_path):
    """Plain malloc: no lower bounds and no base to free -- refused, not guessed."""
    _generate(tmp_path, impl_source=_impl_with(
        "    double* g = alloc_d(n + 1);\n",
        "    double* g = static_cast<double*>(malloc((n + 1) * sizeof(double)));\n"))
    r = _build_and_run(tmp_path)
    assert r.returncode != 0
    assert "secondVals was not allocated with vit_adopt_alloc" in r.stderr


@needs_toolchain
def test_refuses_a_header_of_the_wrong_rank(tmp_path):
    _generate(tmp_path, impl_source=_impl_with(
        SPL, "vit_adopt_alloc_lb((na - 1) * 2 * 4 * sizeof(double), 2, 1, 1, 0)"))
    r = _build_and_run(tmp_path)
    assert r.returncode != 0
    assert "Spl was allocated with vit_adopt_alloc_lb for a different rank" in r.stderr
