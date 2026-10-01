"""
Every step of an extraction says how it ended, and `vit extract` names the
first one that failed.

Before, a failure anywhere reached VIT as KGen's output and nothing else. On
the quickstart, with one step broken at a time: a failed install was reported
as "✓ Extraction successful"; a failing kgen.add_source_cmd, a failing CMake
reconfigure and a failing plain build all read "make: [build] Error 1"; and a
failed run showed make's "Error 127" without the run's own message. These
tests run the generated scripts with bash and read what they recorded.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vit import extract_steps
from vit.extract import (generate_state_capture_build_script,
                         generate_state_capture_run_script, run_kgen_extract)


def _gnu_sed():
    r = subprocess.run(["sed", "--version"], capture_output=True, text=True)
    return r.returncode == 0 and "GNU" in r.stdout


needs_gnu_sed = pytest.mark.skipif(not _gnu_sed(), reason="the build script uses GNU sed -i")


def _project(tmp_path, cmake_lines="add_library(p STATIC\n    src/p.f90\n)\n"):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "p.f90").write_text("module p\nend module p\n")
    (tmp_path / "CMakeLists.txt").write_text(cmake_lines)
    (tmp_path / "kernel").mkdir()
    (tmp_path / "kernel" / "kgen_utils.f90").write_text("module kgen_utils_mod\nend module\n")
    return tmp_path


def _build_script(tmp_path, monkeypatch, **kw):
    monkeypatch.chdir(tmp_path)
    directory = extract_steps.steps_dir()
    extract_steps.reset(directory)
    kw.setdefault("cmake_file", str(tmp_path / "CMakeLists.txt"))
    script = generate_state_capture_build_script(steps_directory=directory, **kw)
    return directory, script


def _run(script):
    return subprocess.run(["bash", script], capture_output=True, text=True)


def _statuses(directory):
    return [(r["step"], r["status"], r["rc"]) for r in extract_steps.read_records(directory)
            if r["status"] != "started"]


def test_a_failed_install_fails_the_build_script(tmp_path, monkeypatch):
    """It was `install_cmd || true`. A program that loads the installed library,
    as ROSCO's harness does, then ran the old library and captured nothing,
    with no sign of why."""
    _project(tmp_path)
    directory, script = _build_script(tmp_path, monkeypatch, build_cmd="echo built",
                                      install_cmd="cp /nonexistent/lib.so /tmp/x",
                                      add_source_cmd="echo '    src/kgen_utils.f90' >> \"$CMAKE_FILE\"")
    r = _run(script)
    assert r.returncode != 0
    assert _statuses(directory)[-1] == ("install", "failed", 1)
    assert "step install failed" in r.stderr
    assert "/nonexistent/lib.so" in Path(directory, "install.log").read_text()


def test_a_step_that_fails_quietly_is_named(tmp_path, monkeypatch):
    """`( add_source_cmd )` under `set -e` stopped the script without a word;
    the only symptom was make's "[build] Error 1"."""
    _project(tmp_path)
    directory, script = _build_script(tmp_path, monkeypatch, build_cmd="echo built",
                                      add_source_cmd="false")
    r = _run(script)
    assert r.returncode == 1
    assert ("add_kgen_utils", "failed", 1) in _statuses(directory)
    assert not any(s == "build" for s, _, _ in _statuses(directory)), "the build must not run"
    assert "kgen.add_source_cmd failed" in Path(directory, "add_kgen_utils.log").read_text()


def test_an_unmatched_build_file_is_named(tmp_path, monkeypatch):
    _project(tmp_path)
    directory, script = _build_script(tmp_path, monkeypatch, build_cmd="echo built",
                                      add_source_cmd="true")
    assert _run(script).returncode == 1
    log = Path(directory, "add_kgen_utils.log").read_text()
    assert "could not add kgen_utils.f90" in log
    # The example it prints is a command that can be pasted into vit.yaml.
    assert """sed -i '/^set(SOURCES/a\\    src/kgen_utils.f90' "$CMAKE_FILE\"""" in log


def test_the_build_command_keeps_its_own_exit_status(tmp_path, monkeypatch):
    """vit.yaml's commands are not run under `set -e`: `false; true` succeeds,
    as it does when typed, and an explicit exit code comes through."""
    _project(tmp_path)
    add = "echo '    src/kgen_utils.f90' >> \"$CMAKE_FILE\""
    directory, script = _build_script(tmp_path, monkeypatch, build_cmd="false; true",
                                      add_source_cmd=add)
    assert _run(script).returncode == 0
    assert _statuses(directory)[-1] == ("build", "ok", 0)

    directory, script = _build_script(tmp_path, monkeypatch, build_cmd="echo compiling; exit 3",
                                      add_source_cmd=add)
    assert _run(script).returncode == 3
    assert _statuses(directory)[-1] == ("build", "failed", 3)
    assert "compiling" in Path(directory, "build.log").read_text()


@needs_gnu_sed
def test_persistence_resets_are_removed_when_the_build_fails(tmp_path, monkeypatch):
    """The resets used to be removed after the build only; a step failing before
    then left them in the source, where they accumulate."""
    _project(tmp_path)
    source = tmp_path / "src" / "p.f90"
    source.write_text("module p\ncontains\nsubroutine s()\n  ! ANCHOR\nend subroutine\nend module p\n")
    directory, script = _build_script(
        tmp_path, monkeypatch, build_cmd="grep -c VIT_DLL_RESET src/p.f90; exit 2",
        source_dir=str(tmp_path / "src"),
        add_source_cmd="echo '    src/kgen_utils.f90' >> \"$CMAKE_FILE\"",
        dll_persistence={"resets": [{"anchor": "ANCHOR", "code": "x = 0"}]})
    r = _run(script)
    assert r.returncode == 2
    assert Path(directory, "build.log").read_text().startswith("1"), "the reset was in place"
    assert "VIT_DLL_RESET" not in source.read_text()
    assert ("remove_resets", "ok", 0) in _statuses(directory)


def test_the_run_script_records_the_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = extract_steps.steps_dir()
    extract_steps.reset(directory)
    script = generate_state_capture_run_script("echo 'no such input' >&2; exit 4", directory)
    assert _run(script).returncode == 4
    assert _statuses(directory) == [("run", "failed", 4)]
    failure = extract_steps.diagnose(directory, kgen_rc=0)
    assert failure.step == "run"
    assert failure.tail == ["no such input"]


def test_a_failing_build_stops_extraction_before_kgen(tmp_path, monkeypatch):
    """A build that fails without the instrumentation fails with it too, after
    KGen has spent minutes parsing."""
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "kgen_ran"
    result = run_kgen_extract(source_file=str(tmp_path / "x.f90"),
                              build_cmd="echo 'cannot find module' >&2; exit 2",
                              run_cmd="true", kgen_path=f"touch {marker}; true")
    assert not marker.exists()
    assert result["failure"].step == "prebuild"
    assert result["failure"].tail == ["cannot find module"]
    assert "KGen was not run" in result["failure"].summary


def test_kgen_that_does_not_start_is_named(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run_kgen_extract(source_file=str(tmp_path / "x.f90"), build_cmd="true",
                              run_cmd="true", kgen_path=str(tmp_path / "no-kgen"))
    assert result["failure"].step == "kgen"
    assert "did not generate kernel/ and state/" in result["failure"].summary
    assert any("no-kgen" in l for l in result["failure"].tail)


# --------------------------------------------------------------------------
# diagnose, from records alone

def _records(tmp_path, *entries):
    directory = str(tmp_path)
    for step, status, rc in entries:
        extract_steps.record(directory, step, status, rc)
    return directory


def test_the_first_failure_is_reported_not_the_last(tmp_path):
    d = _records(tmp_path, ("prebuild", "ok", 0), ("build", "failed", 2),
                 ("remove_resets", "failed", 1), ("kgen", "ok", 0))
    assert extract_steps.diagnose(d, kgen_rc=0).step == "build"


def test_a_makefile_that_stopped_between_steps_is_located(tmp_path):
    """KGen's state Makefile restores the sources after the build script and
    before the run; a failure there leaves no failed record, only a missing run."""
    d = _records(tmp_path, ("build", "ok", 0), ("install", "ok", 0), ("kgen", "ok", 0))
    failure = extract_steps.diagnose(d, kgen_rc=0)
    assert failure.step == "kgen"
    assert "after step 'install'" in failure.summary

    other = tmp_path / "other"
    other.mkdir()
    d = _records(other, ("kgen", "ok", 0))
    assert "save or replace" in extract_steps.diagnose(d, kgen_rc=0).summary


def test_a_timeout_names_the_step_that_was_running(tmp_path):
    d = _records(tmp_path, ("build", "ok", 0), ("run", "started", None))
    failure = extract_steps.diagnose(d, timeout=1200)
    assert failure.step == "run"
    assert "1200 s" in failure.summary


def test_every_step_succeeding_is_not_a_failure(tmp_path):
    d = _records(tmp_path, ("build", "ok", 0), ("run", "ok", 0), ("kgen", "ok", 0),
                 ("restore", "failed", 1))
    assert extract_steps.diagnose(d, kgen_rc=0) is None
    assert extract_steps.failed(d, "restore").step == "restore"


def test_every_recorded_step_has_a_description():
    """A step the report cannot describe would print its bare identifier."""
    import re
    import vit.extract as ex
    source = Path(ex.__file__).read_text()
    named = set(re.findall(r'steps\.append\(\("(\w+)"', source))
    named |= set(re.findall(r'run_step\(\w+, "(\w+)"', source))
    named |= {"remove_resets", "run", "kgen", "clean", "prebuild"}
    assert named <= set(extract_steps.STEPS), named - set(extract_steps.STEPS)


# --------------------------------------------------------------------------
# Every step succeeded and nothing was captured

def _empty_capture(tmp_path, run_output="", build_files=(), state_source=None):
    """A record directory whose build started at t=1000, a build directory
    holding (name, mtime, content) files, and a state/ directory."""
    directory = tmp_path / "steps"
    directory.mkdir()
    (directory / "steps.jsonl").write_text(
        '{"step": "build", "status": "started", "rc": null, "log": "build.log", "time": 1000}\n'
        '{"step": "build", "status": "ok", "rc": 0, "log": "build.log", "time": 1001}\n')
    (directory / "run.log").write_text(run_output)
    build = tmp_path / "build"
    build.mkdir()
    for name, mtime, content in build_files:
        (build / name).write_bytes(content)
        os.utime(build / name, (mtime, mtime))
    state = tmp_path / "state"
    state.mkdir()
    if state_source:
        (state / "physics.f90").write_text(state_source)
    return str(directory), str(build), str(state)


MARKER = extract_steps.CAPTURE_MESSAGE.encode()


def test_calls_reported_but_not_found_name_the_path(tmp_path):
    """The instrumented program prints a line for each call it writes; when the
    run printed them and kernel/ has none, the files went somewhere else."""
    d, build, state = _empty_capture(
        tmp_path, run_output=f" {extract_steps.CAPTURE_MESSAGE} from: 0 0 1\n" * 3,
        state_source='WRITE (kgen_filepath(kgen_mymid, 0), FMT="(A,I0,A,I0,A,I0)") '
                     '"/w/kernel/drag." , kgen_mymid\n')
    lines = extract_steps.explain_empty_capture(d, build_dir=build, state_dir=state)
    assert lines[0] == "The run reported 3 captured call(s), and none is in kernel/."
    assert "/w/kernel/drag.<process>.<thread>.<call>" in lines[1]


def test_a_build_that_did_not_recompile_is_named(tmp_path):
    """Only files the instrumented build wrote count: an object holding the
    instrumentation from before the build started is not this build's."""
    d, build, state = _empty_capture(tmp_path, build_files=[
        ("old.o", 900, b"x" + MARKER), ("new.o", 1005, b"no instrumentation")])
    assert extract_steps.instrumented_build_products(d, build) == []
    lines = extract_steps.explain_empty_capture(d, build_dir=build, state_dir=state)
    assert "did not recompile the instrumented source" in lines[1]


@pytest.mark.parametrize("reached, expected", [
    (None, "never reached the call site, or reached it only"),
    (False, "The run never reached the call site."),
    (True, "none of its calls fell in the capture range 0:0:600-620"),
])
def test_a_built_call_site_that_captured_nothing(tmp_path, reached, expected):
    d, build, state = _empty_capture(tmp_path, build_files=[("prog", 1003, b"x" + MARKER)])
    lines = extract_steps.explain_empty_capture(d, build_dir=build, state_dir=state,
                                                invocation="0:0:600-620", reached=reached)
    assert lines[0].startswith(f"The instrumented call site was built ({build}/prog)")
    assert expected in " ".join(lines)


def test_coverage_says_which_cases_run_the_call(tmp_path):
    """The first check when nothing is captured is whether the run reaches the
    call; with coverage per test case it is a lookup, so the report makes it."""
    import gzip
    import json
    from vit import coverage as cov
    from vit.cli import _coverage_of_call
    from vit.config import VitConfig

    source = ("module physics\ncontains\n  real(8) function drag(v)\n    real(8) :: v\n"
              "    drag = v\n  end function drag\n  subroutine step(v)\n    real(8) :: v\n"
              "    v = drag(v)\n  end subroutine step\nend module physics\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "physics.f90").write_text(source)
    for case, count in (("long", 500), ("short", 3)):
        (tmp_path / "gcov" / case).mkdir(parents=True)
        with gzip.open(tmp_path / "gcov" / case / "p.gcov.json.gz", "wt") as fh:
            json.dump({"format_version": "1", "files": [{
                "file": str(tmp_path / "src" / "physics.f90"),
                "lines": [{"line_number": 9, "count": count}], "functions": []}]}, fh)
    data, _ = cov.import_gcov(tmp_path / "gcov", tmp_path)
    cov.write(data, tmp_path / "coverage.json")
    config = VitConfig(config_dir=str(tmp_path), source_dir="src")

    lines = _coverage_of_call(config, str(tmp_path / "src" / "physics.f90"), "v = drag(v)", "drag")
    assert lines[1:] == ["  long: 500", "  short: 3"]
    assert _coverage_of_call(config, str(tmp_path / "src" / "physics.f90"),
                             "v = other(v)", "drag")[0].startswith("Coverage (")
    os.remove(tmp_path / "coverage.json")
    assert _coverage_of_call(config, str(tmp_path / "src" / "physics.f90"), "v = drag(v)", "drag") == []
