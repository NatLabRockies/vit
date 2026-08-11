"""Compile and RUN the generated deferred-length CHARACTER bridge.

`tests/test_view_types.py::TestDeferredLengthCharacter` pins the SHAPE of the
generated code. This pins its BEHAVIOUR, and the distinction earned its keep the
first time the generator was run: every shape assertion passed while the buffer
was silently reallocating back to the source length, because

    buf = src%ErrMsg

is a whole-variable assignment to a deferred-length ALLOCATABLE and Fortran 2003
automatic reallocation applies to it. The capacity published to the C++ side --
`n_<f>_cap`, the member that makes the field writable at all -- was therefore a
lie in the dangerous direction: 5 where 4101 had been allocated. No amount of
reading the generated source found it; compiling and running it found it in one
attempt.

Six behaviours, each a different wrong answer being ruled out:

  1. read       length and capacity reach the C side; contents are blank-padded
  2. grow       a LONGER string round-trips, which is the whole point of the
                capacity and the case `ErrMsg = RoutineName//':'//TRIM(ErrMsg)`
                needs
  3. shrink     a SHORTER string reallocates down, as Fortran assignment does
  4. overrun    a request past the capacity is REFUSED and the field is left
                alone -- never truncated, because a shortened error message is
                a plausible wrong answer no bit comparison can catch
  5. null       an unallocated field renders as a NULL pointer (P6: absence must
                not render as a value)
  6. empty      an allocated ZERO-LENGTH field is a DIFFERENT input, and is
                non-null; both report length 0, so the pointer is the only thing
                that separates them

Skipped, not silently passed, when gfortran or gcc is absent.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from vit.derived_type_parser import DerivedTypeField, DerivedTypeInfo
from vit.view_populator import generate_view_populator

FIXTURES = Path(__file__).parent / "fixtures" / "deferred_char"

pytestmark = pytest.mark.skipif(
    shutil.which("gfortran") is None or shutil.which("gcc") is None,
    reason="needs gfortran and gcc to compile the generated bridge",
)


def _errlike() -> DerivedTypeInfo:
    """The same type `fixtures/deferred_char/errlike_types.f90` declares.

    Declared twice on purpose -- once in Fortran for the compiler, once here for
    the generator -- because that is the situation the generator is always in.
    A mismatch between the two shows up as a compile or link failure, which is
    the loud outcome.
    """
    return DerivedTypeInfo(name="ErrLike", fields=[
        DerivedTypeField(name="size_avcMSG", fortran_type="INTEGER(4)"),
        DerivedTypeField(name="aviFAIL", fortran_type="INTEGER(4)"),
        DerivedTypeField(name="ErrStat", fortran_type="INTEGER(4)"),
        DerivedTypeField(name="ErrMsg", fortran_type="CHARACTER(:)",
                         is_allocatable=True),
    ])


def test_deferred_character_field_round_trips(tmp_path):
    populator = generate_view_populator(
        _errlike(), [], None, source_module="errlike_types")
    (tmp_path / "vit_errlike_view.f90").write_text(populator)
    for name in ("errlike_types.f90", "roundtrip.f90", "roundtrip.c"):
        shutil.copy(FIXTURES / name, tmp_path / name)

    def run(*cmd):
        r = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 0, f"{cmd[0]} failed:\n{r.stdout}\n{r.stderr}"
        return r

    run("gcc", "-c", "roundtrip.c")
    run("gfortran", "-c", "errlike_types.f90")
    run("gfortran", "-c", "vit_errlike_view.f90")
    run("gfortran", "-o", "rt", "roundtrip.f90",
        "errlike_types.o", "vit_errlike_view.o", "roundtrip.o")

    r = subprocess.run(["./rt"], cwd=tmp_path, capture_output=True, text=True)
    assert "ROUNDTRIP OK" in r.stdout, f"{r.stdout}\n{r.stderr}"
    assert r.returncode == 0
    # The refusal is not merely non-fatal; it must SAY something. A silent
    # refusal is the same defect one level down.
    assert "field left unchanged" in r.stderr
