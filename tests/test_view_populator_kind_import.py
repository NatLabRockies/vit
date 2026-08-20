"""A view populator must import the KIND its own declarations name.

`_direct_alloc_decls` writes the field's declared type straight into a pointer
declaration -- `REAL(R8), POINTER :: vit_ap_v(:)` -- while the module header
emits `USE <source_module>, ONLY: <TypeName>`: the derived type, and nothing
else. `R8` is a kind parameter of that module, so the declaration cannot
resolve and gfortran reports `Symbol 'r8' has no IMPLICIT type`.

It went unnoticed because the ONE field that had taken this path in rosco-r2 is
`LOCALVARIABLES%ACC_INFILE`, a CHARACTER -- and `_direct_alloc_decls` rewrites
CHARACTER to `CHARACTER(KIND=C_CHAR)`, whose kind resolves through the
UNQUALIFIED `USE ISO_C_BINDING`. Every other kind is the field's own.

The exposure is not hypothetical: ROSCO_Types declares 74 ALLOCATABLE fields
with a named kind (DbKi, ReKi, IntKi), nearly all in ControlParameters.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from vit.derived_type_parser import DerivedTypeField, DerivedTypeInfo
from vit.view_populator import generate_view_populator


def _ty(fortran_type: str, name: str = "Kindy") -> DerivedTypeInfo:
    return DerivedTypeInfo(name=name, fields=[
        DerivedTypeField(name="v", fortran_type=fortran_type,
                         is_allocatable=True, is_array=True, array_dims=":"),
        DerivedTypeField(name="n", fortran_type="INTEGER(4)"),
    ])


def _use_only(text: str, module: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith(f"USE {module.upper()},"):
            return s
    return ""


def test_a_named_kind_is_imported_alongside_the_type():
    """THE RED TEST."""
    out = generate_view_populator(_ty("REAL(R8)"), [], None,
                                  source_module="kindy_types")
    assert "REAL(R8), POINTER :: vit_ap_v(:)" in out, "precondition: the decl uses R8"
    line = _use_only(out, "kindy_types")
    assert "R8" in line, f"kind not imported; got {line!r}"


def test_an_integer_named_kind_is_imported_too():
    out = generate_view_populator(_ty("INTEGER(IntKi)"), [], None,
                                  source_module="kindy_types")
    assert "IntKi" in _use_only(out, "kindy_types")


def test_a_numeric_literal_kind_imports_nothing():
    """`REAL(8)` names no symbol, so there is nothing to import and adding one
    would generate `ONLY: Kindy, 8`."""
    line = _use_only(generate_view_populator(_ty("REAL(8)"), [], None,
                                             source_module="kindy_types"),
                     "kindy_types")
    assert line.endswith("Kindy"), f"nothing should be added; got {line!r}"


def test_an_iso_c_binding_kind_is_not_imported_from_the_source_module():
    """C_DOUBLE resolves through the unqualified `USE ISO_C_BINDING` already in
    the header. Importing it from the source module would be wrong AND would
    usually not exist there."""
    line = _use_only(generate_view_populator(_ty("REAL(C_DOUBLE)"), [], None,
                                             source_module="kindy_types"),
                     "kindy_types")
    assert "C_DOUBLE" not in line, f"got {line!r}"


def test_a_character_field_still_imports_nothing():
    """The CHARACTER path is rewritten to CHARACTER(KIND=C_CHAR), so its own
    kind never reaches a declaration -- the case that masked this for weeks."""
    t = DerivedTypeInfo(name="Chr", fields=[
        DerivedTypeField(name="s", fortran_type="CHARACTER(LEN=:)",
                         is_allocatable=True, is_array=False),
    ])
    out = generate_view_populator(t, [], None, source_module="chr_types")
    assert "C_CHAR" not in _use_only(out, "chr_types")


@pytest.mark.skipif(shutil.which("gfortran") is None, reason="needs gfortran")
def test_the_generated_populator_actually_COMPILES(tmp_path):
    """The end-to-end check. Every assertion above is about text; this one is
    the compiler agreeing, which is what the campaign build does."""
    (tmp_path / "kindy_types.f90").write_text(
        "MODULE kindy_types\n"
        "    USE ISO_FORTRAN_ENV, ONLY: real64\n"
        "    IMPLICIT NONE\n"
        "    INTEGER, PARAMETER :: R8 = real64\n"
        "    TYPE, PUBLIC :: Kindy\n"
        "        REAL(R8), DIMENSION(:), ALLOCATABLE :: v\n"
        "        INTEGER :: n\n"
        "    END TYPE Kindy\n"
        "END MODULE kindy_types\n")
    (tmp_path / "vit_kindy_view.f90").write_text(
        generate_view_populator(_ty("REAL(R8)"), [], None,
                                source_module="kindy_types"))

    for src in ("kindy_types.f90", "vit_kindy_view.f90"):
        r = subprocess.run(["gfortran", "-fsyntax-only", "-J", ".", src],
                           cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 0, f"{src} did not compile:\n{r.stderr}"
