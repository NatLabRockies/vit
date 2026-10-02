"""Which calls a kernel captures (vit/extract.py capture_invocation) and the state
that keeps them consecutive (fortran_parser.save_state, coverage.state_in_call_tree).

Every kernel used to hold a function's first twenty calls: the warm-up window,
the least representative part of a run (dev note 202608080640). With the
number of calls known from coverage, `auto` spreads them over the run, unless
the call tree keeps state between calls, which KGen does not capture.
"""

import argparse
import gzip
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import vit.cli
from vit import coverage as cov
from vit.config import VitConfig
from vit.extract import capture_invocation, kernel_state
from vit.fortran_parser import save_state

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# save_state

@pytest.mark.parametrize("body, expected", [
    ("real :: y\n  y = x", []),
    ("integer :: n\n  save", ["SAVE statement"]),
    ("INTEGER, SAVE :: YawState", ["SAVE attribute"]),
    ("logical :: first = .true.  ! comment = x", ["initialised in its declaration"]),
    ("real, pointer :: p => null()", ["initialised in its declaration"]),
    ("integer :: k\n  data k /3/", ["DATA statement"]),
    ("real :: a(3)\n  DATA (a(i), i=1,3) / 1., 2., 3. /", ["DATA statement"]),
    ("integer :: a, &\n     b = 4", ["initialised in its declaration"]),
    # Constants, declarations with dimensions or kinds, and type defaults are not state.
    ("integer, parameter :: n = 3\n  character(len=*), parameter :: c = 'a=b'", []),
    ("real :: a(n), b(2,n)\n  real(kind=8) :: c", []),
    ("type :: t\n    real :: x = 0.0\n  end type t\n  type(t) :: v", []),
    ("interface\n    subroutine f(n)\n      integer, intent(in) :: n\n    end subroutine\n"
     "  end interface", []),
    # Found in OpenFAST's ModReg.f90: a variable named Data, assigned.
    ("real, pointer :: Data(:)\n  Data(1:) => Data\n  Data = Data / 2", []),
    ("integer :: a\n  if (a == 1) a = 2", []),
])
def test_save_state(body, expected):
    text = f"subroutine s(x, n)\n  {body}\nend subroutine s"
    assert [d.split(": ", 1)[1].split(" (")[0] for d in save_state(text)] == expected


def test_save_state_numbers_lines_in_the_file():
    assert save_state("subroutine s()\n  integer, save :: n\nend subroutine", first_line=40) == \
        ["line 41: SAVE attribute"]


def test_the_inventory_uses_the_same_rule(tmp_path):
    """The inventory looked for SAVE statements and attributes only; a local
    initialised in its declaration is SAVE too."""
    pytest.importorskip("fparser")
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.f90").write_text(textwrap.dedent("""\
        module m
        contains
          subroutine counted()
            integer :: calls = 0
            calls = calls + 1
          end subroutine counted
          subroutine plain(x)
            real, intent(inout) :: x
            x = 2 * x
          end subroutine plain
        end module m
    """))
    env = dict(os.environ, VIT_TELEMETRY="0", PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, "-m", "vit.cli", "inventory", "--src", str(src),
                        "--output", str(tmp_path / "out")], capture_output=True, text=True,
                       cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr[-1500:]
    procedures = {p["name"]: p for p in
                  json.loads((tmp_path / "out" / "function_inventory.json").read_text())}
    assert procedures["counted"]["has_save"] is True
    assert procedures["plain"]["has_save"] is False


# --------------------------------------------------------------------------
# capture_invocation

def test_auto_spreads_the_calls_over_the_run():
    invocation, numbers, why = capture_invocation("auto", 38400)
    assert len(numbers) == 20 and numbers[0] == 1 and numbers[-1] == 38400
    assert numbers == sorted(set(numbers))
    assert invocation.split(",")[:2] == ["0:0:1", f"0:0:{numbers[1]}"]
    assert why.startswith("20 calls spread over the 38,400 the run makes: 1, ")


@pytest.mark.parametrize("spec, calls, state, invocation, why", [
    ("auto", None, None, "0:0:1-20", "how many calls the run makes is unknown"),
    ("auto", 4, None, "0:0:1-4", "all 4 call(s) the run makes"),
    ("auto:5", 3, None, "0:0:1-3", "all 3 call(s)"),
    ("auto", 500, [("YawRateControl", "Controllers.f90", "line 386: SAVE attribute")],
     "0:0:1-20", "YawRateControl (Controllers.f90, line 386: SAVE attribute) keeps state"),
    ("0:0:1-20", 500, None, "0:0:1-20", "the first 20 of the 500 calls the run makes"),
    ("0:0:100-110", 500, None, "0:0:100-110", "kgen.invocation 0:0:100-110"),
])
def test_capture_invocation(spec, calls, state, invocation, why):
    got, numbers, reason = capture_invocation(spec, calls, state)
    assert got == invocation
    assert why in reason


def test_auto_with_a_count():
    _, numbers, _ = capture_invocation("auto:40", 1000)
    assert len(numbers) == 40 and numbers[-1] == 1000


# --------------------------------------------------------------------------
# state_in_call_tree

CALLER = textwrap.dedent("""\
    module m
      interface smooth
        module procedure smooth_r8
      end interface smooth
    contains
      subroutine step(x, y)
        real(8), intent(inout) :: x, y
        call smooth(x)
        y = helper(y)
      end subroutine step
      subroutine smooth_r8(x)
        real(8), intent(inout) :: x
        real(8), save :: last = 0d0
        x = 0.5d0 * (x + last)
        last = x
      end subroutine smooth_r8
      real(8) function helper(y)
        real(8), intent(in) :: y
        helper = y
      end function helper
    end module m
""")
# `step` calls smooth_r8 through the generic `smooth`, and helper directly.
# smooth_r8's body is lines 12-15, step's call is line 8.

# A second platform's copy of helper, with state, that the build does not compile.
OTHER_PLATFORM = textwrap.dedent("""\
    module m_other
    contains
      real(8) function helper(y)
        real(8), intent(in) :: y
        integer :: calls = 0
        calls = calls + 1
        helper = y
      end function helper
    end module m_other
""")


def _coverage(tmp_path, cases):
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.f90").write_text(CALLER)
    (src / "m_other.f90").write_text(OTHER_PLATFORM)
    for case, lines in cases.items():
        d = tmp_path / "gcov" / case
        d.mkdir(parents=True)
        with gzip.open(d / "p.gcov.json.gz", "wt") as fh:
            json.dump({"format_version": "1", "files": [{
                "file": str(src / "m.f90"),
                "lines": [{"line_number": n, "count": c} for n, c in lines.items()],
                "functions": []}]}, fh)
    data, _ = cov.import_gcov(tmp_path / "gcov", tmp_path)
    return data, str(src)


def test_state_is_found_through_generic_interfaces(tmp_path):
    data, src = _coverage(tmp_path, {"smoothing": {8: 9, 9: 9, 14: 9, 15: 9, 19: 9}})
    found = cov.state_in_call_tree(data, src, "step", "smoothing")
    assert [(p, d) for p, _, d in found] == [("smooth_r8", "line 13: SAVE attribute")]


def test_state_a_case_never_runs_is_not_counted(tmp_path):
    """Its value is the initial one in the run and in the kernel alike."""
    data, src = _coverage(tmp_path, {"no_smoothing": {8: 9, 9: 9, 19: 9}})
    assert cov.state_in_call_tree(data, src, "step", "no_smoothing") == []


def test_only_files_the_build_compiled_are_searched(tmp_path):
    """OpenFAST defines WrNR once per platform; only one is built. The
    uncovered m_other.f90's helper, with state, is not the one called."""
    data, src = _coverage(tmp_path, {"no_smoothing": {8: 9, 9: 9, 19: 9}})
    assert "src/m_other.f90" not in data["files"]
    assert cov.state_in_call_tree(data, src, "helper", "no_smoothing") == []


# --------------------------------------------------------------------------
# kernel_state

def test_kernel_state_skips_kgen_routines_inside_copied_procedures(tmp_path):
    """KGen puts verification routines inside the procedures it copies; one of
    them initialises a local in its declaration."""
    (tmp_path / "physics.f90").write_text(textwrap.dedent("""\
        module physics
        contains
          subroutine step(v)
            real(8) :: v
            v = 1d0
          contains
            subroutine kv_step_real__dp(var)
              real(8) :: var
              logical :: is_print = .false.
            end subroutine kv_step_real__dp
          end subroutine step
          subroutine filter(x)
            real(8) :: x
            real(8), save :: last
            last = x
          end subroutine filter
        end module physics
    """))
    assert kernel_state(str(tmp_path)) == [("filter", "physics.f90", "line 14: SAVE attribute")]


# --------------------------------------------------------------------------
# vit extract

def test_extract_spreads_the_calls_and_records_them(tmp_path, monkeypatch, capsys):
    data, src = _coverage(tmp_path, {"run": {8: 500, 9: 500, 19: 500}})
    cov.write(data, tmp_path / "coverage.json")
    config = VitConfig(config_dir=str(tmp_path), source_dir="src", build_dir="build",
                       coverage_cases={"run": "{build_dir}/prog"})
    seen = {}

    def fake_extract(**kwargs):
        seen.update(kwargs)
        kernel = tmp_path / "kernel"
        kernel.mkdir(exist_ok=True)
        for n in kwargs["invocation"].split(",")[:12]:      # the run stops short
            (kernel / f"helper.0.0.{n.split(':')[-1]}").write_text("")
        return {"success": True, "kernel_dir": str(kernel), "state_dir": None, "cmd": "",
                "output": "", "include_ini": None, "cleaned": [], "state_cases": 12,
                "warnings": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vit.cli, "run_kgen_extract", fake_extract)
    args = argparse.Namespace(function_name="helper", file=str(tmp_path / "src" / "m.f90"),
                              line=9, run_args=None, assumed_size=None, dry_run=False,
                              case=None, invocation=None)
    assert vit.cli.cmd_extract(args, config) == 0
    out = capsys.readouterr().out

    assert seen["invocation"].startswith("0:0:1,0:0:")
    assert seen["invocation"].endswith("0:0:500")
    assert "Calls to capture: 20 calls spread over the 500 the run makes" in out
    assert "Captured 12 of the 20 calls requested" in out
    record = json.loads((tmp_path / "kernel" / "helper" / "extraction.json").read_text())
    assert record["invocation"] == seen["invocation"]


# --------------------------------------------------------------------------
# How long the capture run takes (cli._run_time)

def _timed(seconds, calls, name="long"):
    return {"case": name, "calls": calls,
            "coverage": {"metadata": {"case_seconds": {name: seconds}}}}


def test_calls_spread_to_the_last_run_the_case_to_its_end():
    """KGen stops the program after the last call requested: calls 1-20 stopped
    early, calls spread to the last one do not."""
    assert vit.cli._run_time(VitConfig(), _timed(42.0, 500), [1, 27, 500]) == [
        "Run time: test case long ran in 42.0 s when coverage was measured; the capture runs "
        "it to the end."]
    assert vit.cli._run_time(VitConfig(), _timed(600.0, 38400), list(range(1, 21))) == [
        "Run time: test case long ran in 600.0 s when coverage was measured; the capture stops "
        "after call 20 of 38,400, about 0.3 s if the calls are spread evenly over the run."]


def test_a_run_near_the_timeout_is_warned_of():
    long = _timed(700.0, 500)
    warned = vit.cli._run_time(VitConfig(), long, [1, 500])
    assert warned[1].startswith("⚠ That is more than half of kgen.timeout (1,200 s)")
    assert len(vit.cli._run_time(VitConfig(kgen_timeout=3600), long, [1, 500])) == 1


def test_without_a_recorded_time_nothing_is_said():
    """`vit coverage import` records no time."""
    assert vit.cli._run_time(VitConfig(), {"case": "long", "calls": 500,
                                           "coverage": {"metadata": {}}}, [1, 500]) == []


def test_extract_says_how_long_the_run_takes(tmp_path, monkeypatch, capsys):
    data, src = _coverage(tmp_path, {"run": {8: 500, 9: 500, 19: 500}})
    data["metadata"]["case_seconds"] = {"run": 900.0}
    cov.write(data, tmp_path / "coverage.json")
    config = VitConfig(config_dir=str(tmp_path), source_dir="src", build_dir="build",
                       coverage_cases={"run": "{build_dir}/prog"}, kgen_timeout=1500)
    seen = {}

    def fake_extract(**kwargs):
        seen.update(kwargs)
        return {"success": False, "kernel_dir": None, "state_dir": None, "cmd": "",
                "output": "", "include_ini": None, "cleaned": [], "state_cases": 0,
                "warnings": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vit.cli, "run_kgen_extract", fake_extract)
    args = argparse.Namespace(function_name="helper", file=str(tmp_path / "src" / "m.f90"),
                              line=9, run_args=None, assumed_size=None, dry_run=False,
                              case=None, invocation=None)
    vit.cli.cmd_extract(args, config)
    out = capsys.readouterr().out
    assert ("Run time: test case run ran in 900.0 s when coverage was measured; the capture "
            "runs it to the end.") in out
    assert "⚠ That is more than half of kgen.timeout (1,500 s)" in out
    assert seen["timeout"] == 1500


def test_a_partial_capture_is_listed_in_call_order(tmp_path):
    """Without KGen's list the kernel ran `ls`: drag.0.0.10 before drag.0.0.2."""
    from vit.extract import write_statefile_list
    for n in (1, 2, 10, 11):
        (tmp_path / f"drag.0.0.{n}").write_text("")
    (tmp_path / "drag.0.0.1.kgen").write_text("")
    write_statefile_list(str(tmp_path), "drag")
    assert (tmp_path / "kgen_statefile.lst").read_text().split() == \
        ["drag.0.0.1", "drag.0.0.2", "drag.0.0.10", "drag.0.0.11"]
