"""`vit inventory` (vit/inventory.py) on a codebase it was not written for.

The inventory was written for one codebase and carried its assumptions: a
summary titled after it, its code generator's marker built in as the only way to
recognise generated files, its framework's interface names hardcoded as the
external API, and module-name conventions standing in for what the language
already says (`BIND(C)`). Each test below fails against that version.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fparser")

ROOT = Path(__file__).resolve().parents[1]

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


def _vit(cwd, *argv):
    """Run the `vit` CLI in `cwd`, with telemetry off so no cycle log is written."""
    env = dict(os.environ, VIT_TELEMETRY="0", PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "vit.cli", *argv],
                          capture_output=True, text=True, cwd=cwd, env=env)


def _run(src, out, *extra):
    r = _vit(out.parent, "inventory", "--src", str(src), "--output", str(out), *extra)
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


# ---------------------------------------------------------------------------
# Ordering exceptions
# ---------------------------------------------------------------------------

READER = """\
module reader
  use lib_io, only: open_out
  implicit none
  integer :: log_unit
  type :: report_t
    integer :: un_out
  end type report_t
  type :: summary_t
    integer :: un_out
  end type summary_t
  interface get
    module procedure get_int, get_real
  end interface get
contains
  subroutine load(fname, n, x)
    character(*), intent(in) :: fname
    integer, intent(out) :: n
    real(8), intent(out) :: x
    integer :: u
    open(newunit=u, file=fname, status='old')
    call read_header(u, n)
    call read_body(u)
    call get(u, x)
    call finish()
  contains
    subroutine finish()
      close(u)
    end subroutine finish
  end subroutine load
  subroutine read_header(un, n)
    integer, intent(in) :: un
    integer, intent(out) :: n
    read(un, *) n
  end subroutine read_header
  subroutine read_body(un)
    integer, intent(in) :: un
    call skip_line(un)
  end subroutine read_body
  subroutine skip_line(un)
    integer, intent(in) :: un
    character(len=80) :: line
    read(un, '(A)') line
  end subroutine skip_line
  subroutine get_int(un, v)
    integer, intent(in) :: un
    integer, intent(out) :: v
    read(un, *) v
  end subroutine get_int
  subroutine get_real(un, v)
    integer, intent(in) :: un
    real(8), intent(out) :: v
    read(un, *) v
  end subroutine get_real
  subroutine format_value(x, s)
    real(8), intent(in) :: x
    character(len=32), intent(out) :: s
    write(s, '(ES12.5)') x
  end subroutine format_value
  subroutine say(msg)
    character(*), intent(in) :: msg
    print *, msg
    write(*, *) msg
    write(6, *) msg
  end subroutine say
  subroutine log_msg(msg)
    character(*), intent(in) :: msg
    write(log_unit, '(A)') msg
  end subroutine log_msg
  subroutine open_log()
    open(unit=log_unit, file='run.log')
  end subroutine open_log
  subroutine debug_dump(x)
    real(8), intent(in) :: x
    write(401, *) x
  end subroutine debug_dump
  subroutine init_report(r)
    type(report_t), intent(inout) :: r
    call open_out(r%un_out, 'report.txt')
  end subroutine init_report
  subroutine init_summary(s)
    type(summary_t), intent(inout) :: s
    open(newunit=s%un_out, file='summary.txt')
  end subroutine init_summary
  subroutine write_report(r, x)
    type(report_t), intent(in) :: r
    real(8), intent(in) :: x
    write(r%un_out, *) x
  end subroutine write_report
  subroutine write_summary(s, x)
    type(summary_t), intent(in) :: s
    real(8), intent(in) :: x
    write(s%un_out, *) x
  end subroutine write_summary
  subroutine stamp(s)
    character(len=11), intent(out) :: s
    s = __DATE__
  end subroutine stamp
  real(8) function double_it(x)
    real(8), intent(in) :: x
    double_it = 2d0 * x
  end function double_it
  real(8) function twice_scaled(x)
    real(8), intent(in) :: x
    twice_scaled = double_it(double_it(x))
  end function twice_scaled
  subroutine compute(x)
    real(8), intent(inout) :: x
    x = twice_scaled(x)
  end subroutine compute
end module reader
"""

OTHER = """\
module other
  implicit none
contains
  subroutine read_header(un, n)
    integer, intent(in) :: un
    integer, intent(out) :: n
    read(un, *) n
  end subroutine read_header
  subroutine other_load(n)
    integer, intent(out) :: n
    integer :: v
    open(newunit=v, file='other.txt')
    call read_header(v, n)
  end subroutine other_load
end module other
"""

LIB_IO = """\
module lib_io
  implicit none
contains
  subroutine open_out(un, name)
    integer, intent(out) :: un
    character(*), intent(in) :: name
    open(newunit=un, file=name)
  end subroutine open_out
end module lib_io
"""


@pytest.fixture
def ordering(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "reader.f90").write_text(READER)
    (src / "other.f90").write_text(OTHER)
    (src / "broken.f90").write_text("module broken\n  this is not fortran\nend module broken\n")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "lib_io.f90").write_text(LIB_IO)
    out = tmp_path / "out"
    r = _vit(tmp_path, "inventory", "--src", str(src), "--deps", str(lib), "--output", str(out))
    assert r.returncode == 0, r.stderr[-1500:]
    data = json.loads((out / "ordering.json").read_text())
    return {
        "flagged": {(e["name"], e["module"]): e for e in data["unit_io_not_owned"]},
        "probes": [c["name"] for c in data["probe_candidates"]],
        "procedures": {(p["name"], p["module"]): p
                       for p in json.loads((out / "function_inventory.json").read_text())},
        "summary": (out / "inventory_summary.md").read_text(),
    }


def _owners(entry):
    return [(o["name"], o["distance"]) for o in entry["owners"]]


def test_a_unit_passed_in_is_owned_by_the_caller_that_holds_it(ordering):
    flagged = ordering["flagged"]
    assert _owners(flagged[("read_header", "reader")]) == [("load", 1)]
    assert _owners(flagged[("skip_line", "reader")]) == [("load", 2)], "via read_body"
    assert _owners(flagged[("get_int", "reader")]) == [("load", 1)], "via the generic"
    assert _owners(flagged[("get_real", "reader")]) == [("load", 1)]
    assert _owners(flagged[("read_header", "other")]) == [("other_load", 1)], \
        "a same-named procedure in another module has its own owner"


def test_stored_module_and_fixed_units(ordering):
    flagged = ordering["flagged"]
    assert _owners(flagged[("write_report", "reader")]) == [("init_report", None)], \
        "opened through a library routine that opens its argument"
    assert _owners(flagged[("write_summary", "reader")]) == [("init_summary", None)], \
        "the same component name in another type has another owner"
    assert _owners(flagged[("log_msg", "reader")]) == [("open_log", None)]
    assert flagged[("debug_dump", "reader")]["owners"] == []
    assert "unit 401 is opened nowhere" in flagged[("debug_dump", "reader")]["notes"][0]


def test_what_is_not_an_exception(ordering):
    flagged = {name for name, _ in ordering["flagged"]}
    for name in ("load", "finish", "format_value", "say", "open_log", "init_report",
                 "init_summary", "read_body", "other_load", "stamp"):
        assert name not in flagged, name


def test_depth_and_the_first_non_leaf_probe(ordering):
    procs = ordering["procedures"]
    assert procs[("double_it", "reader")]["depth"] == 0
    assert procs[("twice_scaled", "reader")]["depth"] == 1
    assert procs[("compute", "reader")]["depth"] == 2
    assert ordering["probes"] == ["twice_scaled"]


def test_files_the_parser_cannot_read_are_reported(ordering):
    assert ("stamp", "reader") in ordering["procedures"], "a predefined cpp macro parses"
    assert "## Files that could not be parsed: 1" in ordering["summary"]
    assert "- broken.f90" in ordering["summary"]
