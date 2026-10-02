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


def test_the_work_order_names_the_case_extraction_chooses(tmp_path):
    """The work order named the case that calls a procedure most; extraction
    chooses the one that runs the most of its body. One rule, so the two
    cannot disagree."""
    from vit.work_order import _verification

    config, data = _project(tmp_path)
    drag = [p for p in cov.procedure_coverage(data, tmp_path) if p["name"] == "drag"][0]
    assert drag["lines"] == {"long": 3, "short": 2}

    item = {"status": "reached", "basis": "calls", "calls": {"busy": 900, "broad": 10},
            "lines": {"busy": 2, "broad": 3}}
    index = {("physics.f90", "drag", ""): item}
    route = _verification({"file": "physics.f90", "name": "drag"}, index, True)
    assert (route["case"], route["note"]) == ("broad", "extract with broad (10 calls)")


# --------------------------------------------------------------------------
# The call site, when --line is not given (cli._extraction_site)

PHYSICS = SOURCE.replace("end module physics", textwrap.dedent("""\

      subroutine warmup(v)
        real(8), intent(inout) :: v
        v = v - drag(v)
      end subroutine warmup
    end module physics"""))
# step calls drag on line 14; warmup has the same call on line 19.

DRIVER = textwrap.dedent("""\
    module driver
      use physics
    contains
      subroutine spin(v)
        real(8), intent(inout) :: v
        v = drag(v) * 2d0
      end subroutine spin
    end module driver
""")
# spin calls drag on line 6.

# `long` runs both branches of drag, mostly from step; `short` one branch,
# mostly from spin; `other` never calls drag.
SITE_CASES = {"long": {"physics.f90": {5: 500, 6: 99, 8: 401, 14: 480, 19: 20}},
              "short": {"physics.f90": {5: 950, 6: 950, 19: 50}, "driver.f90": {6: 900}},
              "other": {}}


def _sites_project(tmp_path, cases=SITE_CASES):
    src = tmp_path / "src"
    src.mkdir()
    (src / "physics.f90").write_text(PHYSICS)
    (src / "driver.f90").write_text(DRIVER)
    for case, files in cases.items():
        d = tmp_path / "gcov" / case
        d.mkdir(parents=True)
        with gzip.open(d / "p.gcov.json.gz", "wt") as fh:
            json.dump({"format_version": "1", "files": [
                {"file": str(src / name),
                 "lines": [{"line_number": n, "count": c} for n, c in lines.items()],
                 "functions": []} for name, lines in files.items()]}, fh)
    data, _ = cov.import_gcov(tmp_path / "gcov", tmp_path)
    cov.write(data, tmp_path / "coverage.json")
    return VitConfig(config_dir=str(tmp_path), source_dir="src", build_dir="build",
                     run_cmd="./run_cmd",
                     coverage_cases={name: f"{{build_dir}}/simulate --{name}" for name in cases})


def _extract_at(tmp_path, monkeypatch, config, file=None, line=None, case=None):
    """cmd_extract with KGen stubbed; returns (rc, the kwargs KGen was given)."""
    seen = {}

    def fake_extract(**kwargs):
        seen.update(kwargs)
        seen["marked"] = Path(kwargs["source_file"]).read_text()
        kernel = tmp_path / "kernel"
        kernel.mkdir(exist_ok=True)
        (kernel / "drag.0.0.1").write_text("")
        return {"success": True, "kernel_dir": str(kernel), "state_dir": None, "cmd": "",
                "output": "", "include_ini": None, "cleaned": [], "state_cases": 1,
                "warnings": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vit.cli, "run_kgen_extract", fake_extract)
    args = argparse.Namespace(function_name="drag", file=file and str(file), line=line,
                              run_args=None, assumed_size=None, dry_run=False, case=case,
                              invocation=None)
    return vit.cli.cmd_extract(args, config), seen


def _pragma_line(marked):
    """The line, before marking, of the call KGen's pragma marks: the pragma
    is inserted above it, on its line."""
    lines = marked.split("\n")
    return next(i + 1 for i, l in enumerate(lines) if "begin_callsite" in l)


def test_choose_call_site_agrees_with_choose_case():
    sites = [{"path": "a.f90", "line": 9, "hits": {"busy": 900, "broad": 3}},
             {"path": "b.f90", "line": 4, "hits": {"broad": 10}}]
    lines = {"busy": 2, "broad": 3}
    site, case = cov.choose_call_site(sites, lines, {"busy", "broad"})
    assert (site["path"], case) == ("b.f90", "broad")
    assert cov.choose_case(site["hits"], lines, {"busy", "broad"}) == case
    # Without a command for broad, busy at the call it runs most.
    assert cov.choose_call_site(sites, lines, {"busy"})[0]["path"] == "a.f90"
    assert cov.choose_call_site(sites, lines, set()) is None


def test_extraction_chooses_the_call_site_from_coverage(tmp_path, monkeypatch, capsys):
    config = _sites_project(tmp_path)
    rc, seen = _extract_at(tmp_path, monkeypatch, config)
    out = capsys.readouterr().out
    assert rc == 0
    assert seen["source_file"].endswith("src/physics.f90")
    assert _pragma_line(seen["marked"]) == 14
    assert seen["run_cmd"].endswith("/simulate --long")
    assert ("Call site: src/physics.f90:14 in step, chosen from coverage.json: of the 3 calls "
            "of drag in src, the one test case long runs most often.") in out
    assert ("  Others, as measured: src/driver.f90:6 in spin (short: 900); "
            "src/physics.f90:19 in warmup (short: 50).") in out
    assert "Test case: long, chosen from coverage.json: it runs this call 480 time(s)" in out
    record = json.loads((tmp_path / "kernel" / "drag" / "extraction.json").read_text())
    assert (record["call_site_chosen"], record["case"]) == ("coverage", "long")


@pytest.mark.parametrize("file, case, site, run", [
    (None, "short", "src/driver.f90:6 in spin", "short"),
    ("driver.f90", None, "src/driver.f90:6 in spin", "short"),
    # Two identical calls in one file: the one short runs is the second.
    ("physics.f90", "short", "src/physics.f90:19 in warmup", "short"),
])
def test_file_and_case_limit_the_choice(tmp_path, monkeypatch, capsys, file, case, site, run):
    config = _sites_project(tmp_path)
    rc, seen = _extract_at(tmp_path, monkeypatch, config,
                           file=file and tmp_path / "src" / file, case=case)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"Call site: {site}" in out
    assert _pragma_line(seen["marked"]) == int(site.split(":")[1].split()[0])
    assert seen["run_cmd"].endswith(f"/simulate --{run}")


def test_a_call_moved_since_coverage_was_measured_is_found_by_its_text(
        tmp_path, monkeypatch, capsys):
    """Integrating a translation edits the source after it was measured."""
    config = _sites_project(tmp_path)
    physics = tmp_path / "src" / "physics.f90"
    physics.write_text("! one\n! two\n! three\n" + PHYSICS)
    rc, seen = _extract_at(tmp_path, monkeypatch, config, case="short",
                           file=physics)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Call site: src/physics.f90:22 in warmup (line 19 as measured)" in out
    assert _pragma_line(seen["marked"]) == 22


def test_a_chosen_call_gone_from_the_source_stops_before_kgen(tmp_path, monkeypatch, capsys):
    """Integrating a translation of step replaces its body, and its call of drag
    with it. Found on AeroDyn: 12 of the calls chosen from its coverage are in
    callers since integrated."""
    config = _sites_project(tmp_path)
    physics = tmp_path / "src" / "physics.f90"
    physics.write_text(PHYSICS.replace("    v = v - drag(v)\n  end subroutine step",
                                       "    call step_c(v)\n  end subroutine step"))
    rc, seen = _extract_at(tmp_path, monkeypatch, config)
    assert rc == 1 and not seen
    assert ("ERROR: the call chosen from coverage.json, src/physics.f90:14 as measured, "
            "`v = v - drag(v)`, is not in the source now, or not once.") in capsys.readouterr().out


@pytest.mark.parametrize("kw, message", [
    ({"case": "other"}, "ERROR: coverage.json says test case other runs none of the 3 call(s) "
                        "of drag in src, so the run would capture nothing."),
    ({"case": "nope"}, "ERROR: no test case nope in coverage.cases in vit.yaml"),
    ({"line": 14}, "ERROR: --line needs --file, the file the line is in."),
])
def test_a_call_site_that_cannot_capture_stops_before_kgen(tmp_path, monkeypatch, capsys,
                                                           kw, message):
    config = _sites_project(tmp_path)
    rc, seen = _extract_at(tmp_path, monkeypatch, config, **kw)
    assert rc == 1
    assert not seen, "KGen must not run"
    assert message in capsys.readouterr().out
    assert (tmp_path / "src" / "physics.f90").read_text() == PHYSICS


def test_without_coverage_the_only_call_is_used_and_several_are_listed(
        tmp_path, monkeypatch, capsys):
    config = _sites_project(tmp_path)
    (tmp_path / "coverage.json").unlink()
    rc, seen = _extract_at(tmp_path, monkeypatch, config)
    out = capsys.readouterr().out
    assert rc == 1 and not seen
    assert ("ERROR: 3 calls of drag in src; give one with --file and --line. With coverage of "
            "the test cases (`vit coverage run`), vit extract chooses one.") in out
    assert "src/driver.f90:6  v = drag(v) * 2d0" in out

    driver = tmp_path / "src" / "driver.f90"
    rc, seen = _extract_at(tmp_path, monkeypatch, config, file=driver)
    assert rc == 0
    assert "Call site: src/driver.f90:6, the only call of drag in src/driver.f90." in \
        capsys.readouterr().out
    assert seen["run_cmd"] == "./run_cmd"
