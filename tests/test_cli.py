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
