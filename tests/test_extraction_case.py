"""Which test case `vit extract` runs (vit/cli.py _extraction_case, vit/coverage.py).

Choosing a case used to mean reading `vit find --coverage`, editing
kgen.run_cmd, extracting, and restoring kgen.run_cmd. With coverage and the
cases' commands in vit.yaml, extraction runs the case that runs the most
lines of the function among those that run the call; `--case` names one.
"""

import argparse
import gzip
import json
import shlex
import textwrap
from pathlib import Path

import pytest

import vit.cli
from vit import coverage as cov
from vit.config import VitConfig

SOURCE = textwrap.dedent("""\
    module physics
    contains
      real(8) function drag(v)
        real(8), intent(in) :: v
        if (v > 30d0) then
          drag = 1.1d0 * v * v
        else
          drag = v * v
        end if
      end function drag

      subroutine step(v)
        real(8), intent(inout) :: v
        v = v - drag(v)
      end subroutine step
    end module physics
""")
# The call is line 14; the body of drag is lines 4-9, of which 5, 6 and 8 run.

# Lines each case runs: `long` takes both branches, `short` only the first,
# `other` never calls drag from step.
CASES = {"long": {5: 500, 6: 99, 8: 401, 14: 500},
         "short": {5: 50, 6: 50, 14: 50},
         "other": {}}


def _project(tmp_path, cases=CASES, commands=None):
    src = tmp_path / "src"
    src.mkdir()
    (src / "physics.f90").write_text(SOURCE)
    for case, lines in cases.items():
        d = tmp_path / "gcov" / case
        d.mkdir(parents=True)
        with gzip.open(d / "p.gcov.json.gz", "wt") as fh:
            json.dump({"format_version": "1", "files": [{
                "file": str(src / "physics.f90"),
                "lines": [{"line_number": n, "count": c} for n, c in lines.items()],
                "functions": []}]}, fh)
    data, _ = cov.import_gcov(tmp_path / "gcov", tmp_path)
    cov.write(data, tmp_path / "coverage.json")
    if commands is None:
        commands = {name: f"{{build_dir}}/simulate --{name}" for name in cases}
    return VitConfig(config_dir=str(tmp_path), source_dir="src", build_dir="build",
                     run_cmd="./run_cmd", coverage_cases=commands), data


def test_coverage_helpers(tmp_path):
    config, data = _project(tmp_path)
    source = str(tmp_path / "src" / "physics.f90")
    hits = cov.call_site_hits(data, str(tmp_path / "src"), "drag", source, "v = v - drag(v)")
    assert hits == {"long": 500, "short": 50}
    # `other` ran nothing in this file, so the file has no record for it.
    assert cov.function_lines_by_case(data, str(tmp_path / "src"), "drag") == \
        ({"long": 3, "short": 2}, 3)
    # Lines first, then calls, then name.
    assert cov.choose_case({"a": 10, "b": 900}, {"a": 3, "b": 2}, {"a", "b"}) == "a"
    assert cov.choose_case({"a": 10, "b": 900}, {"a": 2, "b": 2}, {"a", "b"}) == "b"
    assert cov.choose_case({"b": 5, "a": 5}, {}, {"a", "b"}) == "a"
    # A case without a command cannot be run, and one that never calls is no use.
    assert cov.choose_case({"a": 10, "b": 900}, {"a": 3}, {"b"}) == "b"
    assert cov.choose_case({"a": 0}, {"a": 3}, {"a"}) is None


def _extract(tmp_path, monkeypatch, config, **kw):
    """Run cmd_extract with KGen stubbed; returns (rc, output, run_cmd passed)."""
    seen = {}

    def fake_extract(**kwargs):
        seen.update(kwargs)
        kernel = tmp_path / "kernel"
        kernel.mkdir(exist_ok=True)
        (kernel / "drag.0.0.1").write_text("")
        return {"success": True, "kernel_dir": str(kernel), "state_dir": None, "cmd": "",
                "output": "", "include_ini": None, "cleaned": [], "state_cases": 1,
                "warnings": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vit.cli, "run_kgen_extract", fake_extract)
    args = argparse.Namespace(function_name="drag", file=str(tmp_path / "src" / "physics.f90"),
                              line=14, run_args=None, assumed_size=None, dry_run=False,
                              case=kw.get("case"))
    rc = vit.cli.cmd_extract(args, config)
    return rc, seen.get("run_cmd")


def test_extraction_runs_the_case_that_runs_the_most_of_the_function(tmp_path, monkeypatch, capsys):
    config, _ = _project(tmp_path)
    rc, run_cmd = _extract(tmp_path, monkeypatch, config)
    out = capsys.readouterr().out
    assert rc == 0
    project = shlex.quote(str(tmp_path.resolve()))
    build = shlex.quote(str((tmp_path / "build").resolve()))
    assert run_cmd == f"cd {project} && {build}/simulate --long"
    assert ("Test case: long, chosen from coverage.json: it runs this call 500 time(s), "
            "and 3 of the 3 lines of drag that any case runs.") in out
    record = json.loads((tmp_path / "kernel" / "drag" / "extraction.json").read_text())
    assert (record["case"], record["calls_in_case"]) == ("long", 500)


def test_a_named_case_is_run(tmp_path, monkeypatch, capsys):
    config, _ = _project(tmp_path)
    rc, run_cmd = _extract(tmp_path, monkeypatch, config, case="short")
    assert rc == 0
    assert run_cmd.endswith("/simulate --short")
    assert "Test case: short (--case): it runs this call 50 time(s)" in capsys.readouterr().out


@pytest.mark.parametrize("case, message", [
    ("nope", "ERROR: no test case nope in coverage.cases in vit.yaml; it lists long, short, other."),
    ("other", "ERROR: coverage.json says test case other never runs this call."),
])
def test_a_case_that_cannot_capture_stops_before_kgen(tmp_path, monkeypatch, capsys, case, message):
    config, _ = _project(tmp_path)
    rc, run_cmd = _extract(tmp_path, monkeypatch, config, case=case)
    assert rc == 1
    assert run_cmd is None, "KGen must not run"
    assert message in capsys.readouterr().out
    assert (tmp_path / "src" / "physics.f90").read_text() == SOURCE


def test_without_case_commands_kgen_run_cmd_is_used_and_the_best_case_named(
        tmp_path, monkeypatch, capsys):
    """Coverage without cases' commands, as a project with imported coverage
    has: the run is kgen.run_cmd, and the report says which case would be
    better and what to add."""
    config, _ = _project(tmp_path, commands={})
    rc, run_cmd = _extract(tmp_path, monkeypatch, config)
    assert (rc, run_cmd) == (0, "./run_cmd")
    assert ("Running kgen.run_cmd. In coverage.json, long runs the most lines of drag among the "
            "cases that run this call") in capsys.readouterr().out


def test_without_coverage_nothing_changes(tmp_path, monkeypatch, capsys):
    config, _ = _project(tmp_path)
    (tmp_path / "coverage.json").unlink()
    rc, run_cmd = _extract(tmp_path, monkeypatch, config)
    assert (rc, run_cmd) == (0, "./run_cmd")
    assert "Test case" not in capsys.readouterr().out
