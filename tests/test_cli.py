"""The CLI surface.

**`vit/cli.py` had no test file**, and it is the only entry point users have.
This was written after a SyntaxError in it -- a repeated keyword argument --
survived a full run of 748 tests. Two telemetry tests caught it, by accident,
because they happened to invoke the CLI in a subprocess.

Nothing here checks behaviour deeply. It checks the two things that make every
other test meaningless when they break: that the module imports, and that every
subcommand's argument wiring is consistent with the function it calls.
"""

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_cli_module_imports():
    """A SyntaxError here makes the tool unusable while the unit tests pass."""
    import vit.cli  # noqa: F401


def test_every_subcommand_offers_help():
    """`--help` walks the parser, so a subcommand wired to a missing function or
    a broken default fails here rather than in front of a user."""
    import vit.cli

    # The parser is built inside main(), so drive it the way a user does.
    names = _subcommands()
    assert len(names) >= 8, f"expected the full command set, got {names}"
    for name in names:
        r = _run(name, "--help")
        assert r.returncode == 0, f"`vit {name} --help` exited {r.returncode}: {r.stderr[-400:]}"


def _run(*argv):
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); from vit.cli import main; "
         "sys.argv=['vit']+sys.argv[1:]; sys.exit(main())" % str(ROOT), *argv],
        capture_output=True, text=True)


def _subcommands() -> list:
    """The command list, read off `vit --help` -- the same surface a user sees."""
    out = _run("--help").stdout
    import re
    m = re.search(r"\{([a-z0-9,\-]+)\}", out)
    assert m, f"could not find the subcommand list in:\n{out[:400]}"
    return m.group(1).split(",")


def test_the_installed_entry_point_runs():
    r = _run("--help")
    assert r.returncode == 0, r.stderr[-800:]
    assert "test-validate" in r.stdout


@pytest.mark.parametrize("fn_name", ["generate_test_makefile", "run_kgen_extract"])
def test_call_sites_pass_only_arguments_the_callee_accepts(fn_name):
    """Catches the class of mistake that produced this file.

    A keyword inserted into the wrong call is a TypeError at runtime and a
    SyntaxError only if it duplicates one. Comparing the keywords each call site
    passes against the callee's signature finds both without running anything.
    """
    import ast

    import vit.extract
    import vit.test_validate

    mod = vit.test_validate if fn_name == "generate_test_makefile" else vit.extract
    accepted = set(inspect.signature(getattr(mod, fn_name)).parameters)

    tree = ast.parse((ROOT / "vit" / "cli.py").read_text())
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if getattr(f, "id", None) != fn_name and getattr(f, "attr", None) != fn_name:
            continue
        found += 1
        passed = {k.arg for k in node.keywords if k.arg}
        extra = passed - accepted
        assert not extra, f"{fn_name} called with {sorted(extra)}, which it does not accept"
        dupes = [k.arg for k in node.keywords if k.arg]
        assert len(dupes) == len(set(dupes)), f"{fn_name} called with a repeated keyword"
    assert found, f"no call to {fn_name} found in cli.py"


def test_check_list_needs_no_translation(tmp_path):
    """`vit check --list` is how the documentation says to see the registry.

    It once required a translation path it never read, so the documented
    command failed with a usage error. Checking a file still needs one.
    """
    import os

    env = dict(os.environ, VIT_TELEMETRY="0")

    def run(*argv):
        return subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); from vit.cli import main; "
             "sys.argv=['vit']+sys.argv[1:]; sys.exit(main())" % str(ROOT), *argv],
            capture_output=True, text=True, cwd=tmp_path, env=env)

    listed = run("check", "--list")
    assert listed.returncode == 0, listed.stderr[-800:]
    assert "active check(s)" in listed.stdout
    assert "exponent-grouping" in listed.stdout

    missing = run("check")
    assert missing.returncode == 2
    assert "required" in missing.stdout


_EXTRACT_SOURCE = """\
module physics
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
end module physics
"""


@pytest.mark.parametrize("cases, expected_rc, expected_mark", [
    (0, 1, "✗ Extraction captured no state data"),
    (20, 0, "✓ Extraction successful"),
])
def test_extract_fails_when_it_captured_nothing(tmp_path, monkeypatch, capsys,
                                                cases, expected_rc, expected_mark):
    """A kernel with no captured state verifies nothing.

    `vit extract` used to print "✓ Extraction successful" and exit 0 when KGen
    produced a kernel but the instrumented build or run captured no state, so a
    caller checking the exit code carried on with an empty kernel. KGen is
    stubbed here; what is tested is how the CLI reports its result.
    """
    import argparse

    import vit.cli
    from vit.config import VitConfig

    src = tmp_path / "src"
    src.mkdir()
    source = src / "physics.f90"
    source.write_text(_EXTRACT_SOURCE)
    kernel_dir = tmp_path / "kernel"

    def fake_extract(**kwargs):
        kernel_dir.mkdir(exist_ok=True)
        (kernel_dir / "kernel_driver.f90").write_text("")
        return {"success": True, "kernel_dir": str(kernel_dir), "state_dir": None,
                "cmd": "", "output": "", "include_ini": None, "cleaned": [],
                "state_cases": cases, "warnings": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vit.cli, "run_kgen_extract", fake_extract)
    config = VitConfig(config_dir=str(tmp_path), source_dir="src")
    args = argparse.Namespace(function_name="drag", file=str(source), line=11,
                              run_args=None, assumed_size=None, dry_run=False)

    rc = vit.cli.cmd_extract(args, config)
    out = capsys.readouterr().out

    assert rc == expected_rc
    assert expected_mark in out
    assert source.read_text() == _EXTRACT_SOURCE, "the source must be restored"


def test_status_reports_an_integrated_function_as_verified(capsys):
    """What `vit status` prints for a function verified and then integrated."""
    import vit.cli
    from vit.config import VitConfig

    config = VitConfig(project_name="p", translations={
        "drag": {"status": "integrated", "cpp_file": "drag.cpp",
                 "cases_passed": 20, "cases_total": 20},
        "lift": {"status": "integrated", "cpp_file": "lift.cpp"},
    })
    assert vit.cli.cmd_status(None, config) == 0
    out = capsys.readouterr().out
    assert "drag: integrated (verified 20/20 cases)" in out
    assert "lift: integrated (unverified)" in out


def test_shared_files_are_synced_beside_the_integrated_source(tmp_path):
    """`vit integrate --apply` copies shared files to where the C++ it writes
    is compiled: the integrated Fortran file's own directory. It used to copy
    them into vit.yaml's `source_dir`, which for AeroDyn is the broad
    `modules/`: five untracked duplicates appeared there, compiled by nothing,
    while the real files sat in `modules/aerodyn/src/`."""
    from vit.cli import _sync_shared_files
    from vit.config import VitConfig

    # AeroDyn's layout: the shared files already live beside the source.
    src = tmp_path / "modules" / "aerodyn" / "src"
    src.mkdir(parents=True)
    (src / "vit_nwtc.h").write_text("// shared\n")
    config = VitConfig(config_dir=str(tmp_path), source_dir="modules",
                       shared_files=["modules/aerodyn/src/vit_nwtc.h"])
    assert _sync_shared_files(config, src) == []
    assert not (tmp_path / "modules" / "vit_nwtc.h").exists()

    # ROSCO's layout: the shared header lives with the translations.
    rosco = tmp_path / "rosco"
    (rosco / "translations").mkdir(parents=True)
    (rosco / "translations" / "rosco_constants.h").write_text("// constants\n")
    rsrc = rosco / "rosco" / "controller" / "src"
    rsrc.mkdir(parents=True)
    config = VitConfig(config_dir=str(rosco), source_dir="rosco/controller/src",
                       shared_files=["translations/rosco_constants.h"])
    messages = _sync_shared_files(config, rsrc)
    assert (rsrc / "rosco_constants.h").read_text() == "// constants\n"
    assert len(messages) == 1
