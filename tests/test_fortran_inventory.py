"""scripts/fortran_inventory.py on a codebase it was not written for.

The inventory was written for one codebase and carried its assumptions: a
summary titled after it, its code generator's marker built in as the only way to
recognise generated files, its framework's interface names hardcoded as the
external API, and module-name conventions standing in for what the language
already says (`BIND(C)`). Each test below fails against that version.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fparser")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fortran_inventory.py"

PHYSICS = """\
module physics
    use iso_c_binding
    implicit none
contains
    real(8) function drag(v)
        real(8), intent(in) :: v
        drag = v * v
    end function drag

    subroutine step(v)
        real(8), intent(inout) :: v
        v = v - drag(v)
    end subroutine step

    subroutine physics_init_c(n) bind(c, name='physics_init_c')
        integer(c_int), value :: n
    end subroutine physics_init_c

    subroutine AD_Init(n)
        integer, intent(in) :: n
    end subroutine AD_Init

    subroutine unused_helper()
    end subroutine unused_helper
end module physics
"""

GENERATED = """\
! THIS FILE IS GENERATED -- do not edit
module physics_types
    implicit none
contains
    subroutine copy_state()
    end subroutine copy_state
end module physics_types
"""


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "physics.f90").write_text(PHYSICS)
    (d / "physics_types.f90").write_text(GENERATED)
    return d


def _run(src, out, *extra):
    r = subprocess.run([sys.executable, str(SCRIPT), "--src", str(src),
                        "--output", str(out), *extra],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    load = lambda name: json.loads((out / name).read_text())
    return {
        "procedures": {p["name"]: p for p in load("function_inventory.json")},
        "uncalled": {u["name"]: u["likely_reason"] for u in load("uncalled_procedures.json")},
        "boundary": {b["name"]: b["reason"] for b in load("framework_boundary.json")},
        "summary": (out / "inventory_summary.md").read_text(),
    }


def test_the_summary_names_no_codebase(src, tmp_path):
    summary = _run(src, tmp_path / "out")["summary"]
    assert summary.startswith("# Structural Inventory Summary")
    for word in ("AeroDyn", "OpenFAST", "Registry", "ROSCO"):
        assert word not in summary


def test_a_bind_c_procedure_is_an_entry_point(src, tmp_path):
    """`BIND(C)` is the language saying "called from C". It is not dead code,
    whatever its module is called."""
    inv = _run(src, tmp_path / "out")
    assert inv["procedures"]["physics_init_c"]["is_bind_c"] is True
    assert inv["procedures"]["drag"]["is_bind_c"] is False
    assert inv["uncalled"]["physics_init_c"] == "c_entry_point"
    assert inv["boundary"]["physics_init_c"] == "c_entry_point"


def test_entry_points_are_declared_not_built_in(src, tmp_path):
    """No framework's interface names are built in. Without a declaration,
    nothing in the source shows `AD_Init` is called, so it is reported as dead;
    declared, it is an entry point."""
    plain = _run(src, tmp_path / "plain")
    assert plain["uncalled"]["AD_Init"] == "genuinely_dead"
    assert "AD_Init" not in plain["boundary"]

    declared = _run(src, tmp_path / "declared", "--entry-point", "AD_Init")
    assert declared["uncalled"]["AD_Init"] == "declared_entry_point"
    assert declared["boundary"]["AD_Init"] == "declared_entry_point"

    names = tmp_path / "entry_points.txt"
    names.write_text("# called by the host framework\nad_init\n")
    from_file = _run(src, tmp_path / "file", "--entry-points-file", str(names))
    assert from_file["boundary"]["AD_Init"] == "declared_entry_point"

    assert plain["uncalled"]["unused_helper"] == "genuinely_dead"


def test_generated_files_are_recognised_only_by_a_given_marker(src, tmp_path):
    plain = _run(src, tmp_path / "plain")
    assert plain["procedures"]["copy_state"]["is_generated"] is False

    marked = _run(src, tmp_path / "marked", "--generated-marker", "THIS FILE IS GENERATED")
    assert marked["procedures"]["copy_state"]["is_generated"] is True
    assert marked["procedures"]["drag"]["is_generated"] is False
    assert "| Generated procedures | 1 |" in marked["summary"]
