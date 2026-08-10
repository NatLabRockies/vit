"""The deletion probe must be decidable, honest about scope, and non-destructive.

A sweep says *no input I tried reached it*; a probe says *nothing refers to it*.
The second is worth having only if three things hold: it actually rebuilds, it
reports the assumptions its verdict rests on, and it never leaves the source
modified. The third is the one that would be catastrophic to get wrong, so it
is tested against a build that fails, a build that times out, and a build that
succeeds.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.reachability import (_comment_out, _find_definition,        # noqa: E402
                              probe, undefined_symbols)


SRC = """\
MODULE Controllers
CONTAINS

    SUBROUTINE Untouched(x)
        REAL(8) :: x
    END SUBROUTINE Untouched

    REAL(DbKi) FUNCTION PIController(error, kp)
        REAL(8), INTENT(IN) :: error, kp
        PIController = error * kp
    END FUNCTION PIController

    SUBROUTINE AfterIt(y)
        REAL(8) :: y
    END SUBROUTINE AfterIt

END MODULE Controllers
"""


# --------------------------------------------------------------- finding it

def test_a_typed_function_definition_is_located():
    """`REAL(DbKi) FUNCTION Foo(...)` -- the type prefix must not defeat it."""
    span = _find_definition(SRC, "PIController")
    assert span
    body = SRC[span[0]:span[1]]
    assert body.startswith("    REAL(DbKi) FUNCTION PIController")
    assert body.rstrip().endswith("END FUNCTION PIController")


def test_only_the_named_procedure_is_taken():
    span = _find_definition(SRC, "PIController")
    body = SRC[span[0]:span[1]]
    assert "Untouched" not in body and "AfterIt" not in body


def test_a_missing_procedure_is_reported_not_guessed():
    assert _find_definition(SRC, "NoSuchThing") is None


# ------------------------------------------------------- line numbers survive

def test_commenting_out_preserves_the_line_count():
    """A probe that shifts line numbers cannot run against a kernel keyed on
    them, and every VIT kernel is."""
    block = SRC[slice(*_find_definition(SRC, "PIController"))]
    assert _comment_out(block).count("\n") == block.count("\n")


def test_the_probe_leaves_the_file_byte_identical(tmp_path):
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    probe("PIController", str(f), "true", cwd=str(tmp_path))
    assert f.read_text() == SRC


def test_the_file_is_restored_even_when_the_build_fails(tmp_path):
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    probe("PIController", str(f), "exit 1", cwd=str(tmp_path))
    assert f.read_text() == SRC


def test_the_file_is_restored_even_when_the_build_times_out(tmp_path):
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    r = probe("PIController", str(f), "sleep 5", cwd=str(tmp_path), timeout=1)
    assert r.verdict == "inconclusive"
    assert f.read_text() == SRC


# ------------------------------------------------------------------ verdicts

def test_a_clean_link_reads_as_unreferenced(tmp_path):
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    r = probe("PIController", str(f), "true", cwd=str(tmp_path))
    assert r.verdict == "unreferenced"
    assert r.ok


def test_an_undefined_symbol_reads_as_referenced(tmp_path):
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    # GNU ld writes ``undefined reference to `sym'`` -- a backtick AND a
    # trailing single quote, which is hostile to both shell quoting styles.
    # Writing it to a file sidesteps the quoting entirely; echoing it does not.
    log = tmp_path / "link.err"
    log.write_text("undefined reference to `__controllers_MOD_picontroller'\n")
    r = probe("PIController", str(f), f"cat {log} >&2; exit 1", cwd=str(tmp_path))
    assert r.verdict == "referenced"
    assert r.symbols == ["__controllers_MOD_picontroller"]


def test_an_unrelated_build_failure_is_inconclusive_not_unreferenced(tmp_path):
    """A build that broke for its own reasons says nothing about reachability,
    and calling it either way would be inventing evidence."""
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    r = probe("PIController", str(f), "echo 'fatal: disk full' >&2; exit 1",
              cwd=str(tmp_path))
    assert r.verdict == "inconclusive"
    assert not r.ok


def test_a_missing_definition_is_inconclusive(tmp_path):
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    r = probe("Nonexistent", str(f), "true", cwd=str(tmp_path))
    assert r.verdict == "inconclusive"


# -------------------------------------------------------------- the caveats

def test_an_unreferenced_verdict_states_what_it_does_not_cover(tmp_path):
    """`uncalled` that hides its own assumptions is the failure mode this
    project is named after. A PROCEDURE pointer links cleanly."""
    f = tmp_path / "Controllers.f90"
    f.write_text(SRC)
    r = probe("PIController", str(f), "true", cwd=str(tmp_path))
    joined = " ".join(r.caveats).lower()
    assert "procedure pointer" in joined
    assert "this build" in joined


def test_static_call_sites_contradicting_a_clean_link_are_surfaced(tmp_path):
    """If text search finds callers but the link succeeded, one of the two is
    wrong and the human must reconcile them -- silently preferring the
    flattering one is how a bad `uncalled` gets recorded."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "Controllers.f90"
    f.write_text(SRC)
    (src_dir / "Caller.f90").write_text(
        "SUBROUTINE Drive()\n    y = PIController(e, k)\nEND SUBROUTINE Drive\n")
    r = probe("PIController", str(f), "true", cwd=str(tmp_path),
              source_dir=str(src_dir))
    assert r.static_call_sites
    assert "reconcile" in " ".join(r.caveats)


# ------------------------------------------------------------ symbol parsing

@pytest.mark.parametrize("line,want", [
    ("undefined reference to `picontroller_'", "picontroller_"),
    ("undefined reference to `__controllers_MOD_picontroller'",
     "__controllers_MOD_picontroller"),
    ("ld: undefined symbol: _picontroller_", "_picontroller_"),
])
def test_mangled_fortran_symbols_are_recognised(line, want):
    assert want in undefined_symbols(line, "PIController")


def test_an_unrelated_symbol_is_not_claimed():
    out = "undefined reference to `some_other_function_'"
    assert undefined_symbols(out, "PIController") == []
