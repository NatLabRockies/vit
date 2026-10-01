"""Line coverage per test case (vit/coverage.py): import, lookup, and the CLI.

Coverage line numbers belong to the source it was measured on. The test that
matters most here is the one that edits the working tree after measuring: the
lookup must still read the call site's line at the recorded commit, or every
count it reports belongs to some other line.
"""

import gzip
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from vit import coverage as cov

ROOT = Path(__file__).resolve().parents[1]

PHYSICS = textwrap.dedent("""\
    module physics
    contains
      real(8) function drag(v)
        real(8), intent(in) :: v
        drag = v * v
      end function drag

      subroutine step(v)
        real(8), intent(inout) :: v
        v = v - 0.01d0 * drag(v)
      end subroutine step

      subroutine settle(v)
        real(8), intent(inout) :: v
        v = drag(v)
      end subroutine settle
    end module physics
""")
# drag is called at lines 10 (in step) and 15 (in settle); line 3 defines it.


def gcov_file(path, files, cwd=None):
    """Write a .gcov.json.gz in the shape `gcov -j` produces."""
    data = {"format_version": "1", "gcc_version": "13.2.0", "files": [
        {"file": name, "lines": [{"line_number": n, "count": c} for n, c in lines.items()]}
        for name, lines in files.items()]}
    if cwd:
        data["current_working_directory"] = cwd
    with gzip.open(path, "wt") as fh:
        json.dump(data, fh)


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def project(tmp_path):
    """A git repository holding src/physics.f90, committed."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "physics.f90").write_text(PHYSICS)
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return root


@pytest.fixture
def gcov_dir(tmp_path, project):
    """Two test cases. `long` runs step 500 times, `short` runs step 3 times and
    settle once; `empty` produced no gcov files."""
    d = tmp_path / "gcov"
    src = str(project / "src" / "physics.f90")
    (d / "long").mkdir(parents=True)
    gcov_file(d / "long" / "physics.gcov.json.gz",
              {src: {3: 500, 5: 500, 10: 500, 15: 0}})
    (d / "short").mkdir()
    # The same file in two gcov files of one case (two compilation units),
    # once by a path relative to gcov's working directory: counts add up.
    gcov_file(d / "short" / "a.gcov.json.gz", {src: {10: 2}})
    gcov_file(d / "short" / "b.gcov.json.gz", {"src/physics.f90": {10: 1, 15: 1}},
              cwd=str(project))
    gcov_file(d / "short" / "c.gcov.json.gz", {"/usr/include/stdio.h": {1: 9}})
    (d / "empty").mkdir()
    return d


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def test_import_merges_cases_and_compilation_units(project, gcov_dir):
    data, report = cov.import_gcov(gcov_dir, project)
    lines = data["files"]["src/physics.f90"]["test_cases"]
    assert lines["long"]["lines"] == {"3": 500, "5": 500, "10": 500}
    assert lines["short"]["lines"] == {"10": 3, "15": 1}
    assert data["metadata"]["test_cases"] == ["empty", "long", "short"]
    assert data["metadata"]["file_count"] == 1


def test_import_reports_what_it_left_out(project, gcov_dir):
    data, report = cov.import_gcov(gcov_dir, project)
    assert report["outside_root"] == {"/usr/include/stdio.h"}
    assert report["cases_without_data"] == ["empty"]
    assert report["gcov_files"] == 4

    _, report = cov.import_gcov(gcov_dir, project, prefix="lib/")
    assert report["filtered"] == {"src/physics.f90"}


def test_import_records_the_commit_measured(project, gcov_dir, tmp_path):
    data, _ = cov.import_gcov(gcov_dir, project)
    head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert data["metadata"]["source_commit"] == head

    plain = tmp_path / "plain"
    plain.mkdir()
    assert cov.git_head(plain) is None


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------


def test_line_hits_needs_a_unique_file():
    data = {"files": {
        "a/types.f90": {"test_cases": {"x": {"lines": {"4": 2}}}},
        "b/types.f90": {"test_cases": {"x": {"lines": {"4": 7}}}},
        "c/solo.f90": {"test_cases": {"x": {"lines": {"9": 1}}}},
    }}
    assert cov.line_hits(data, "a/types.f90", 4) == {"x": 2}
    assert cov.line_hits(data, "other/solo.f90", 9) == {"x": 1}
    # Two files share the name: neither may answer for a third path.
    assert cov.line_hits(data, "other/types.f90", 4) == {}
    assert cov.line_hits(data, "a/types.f90", 5) == {}


def test_call_sites_are_read_at_the_measured_commit(project, gcov_dir):
    """Lines inserted after measuring must not shift the lookup."""
    data, _ = cov.import_gcov(gcov_dir, project)
    edited = project / "src" / "physics.f90"
    edited.write_text("! added after the coverage run\n" * 4 + PHYSICS)

    sites, source = cov.call_sites(data, project / "src", "drag")

    assert source["kind"] == "commit"
    assert [(s["path"], s["line"], s["caller"]) for s in sites] == [
        ("src/physics.f90", 10, "step"), ("src/physics.f90", 15, "settle")]
    assert sites[0]["hits"] == {"long": 500, "short": 3}
    assert sites[1]["hits"] == {"short": 1}


def test_call_sites_fall_back_to_the_working_tree(project, gcov_dir):
    """Without the measured commit, the working tree is read, and the result
    says so: its lines may have moved."""
    data, _ = cov.import_gcov(gcov_dir, project)
    data["metadata"]["source_commit"] = "0" * 40

    sites, source = cov.call_sites(data, project / "src", "drag")

    assert source["kind"] == "working_tree"
    assert [s["line"] for s in sites] == [10, 15]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def vit(cwd, *argv):
    env = dict(os.environ, VIT_TELEMETRY="0", PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "vit.cli", *argv],
                          capture_output=True, text=True, cwd=cwd, env=env)


def test_cli_import_find_and_line(project, gcov_dir, tmp_path):
    out = tmp_path / "coverage.json"
    r = vit(tmp_path, "coverage", "import", "--gcov-dir", str(gcov_dir),
            "--source-root", str(project), "--output", str(out))
    assert r.returncode == 0, r.stderr
    assert "1 source files, 3 test cases" in r.stdout
    assert "test cases with no gcov files: empty" in r.stdout
    assert "1 file(s) outside the source root" in r.stdout

    r = vit(tmp_path, "find", "drag", "--source-dir", str(project / "src"),
            "--coverage", str(out))
    assert r.returncode == 0, r.stderr
    assert "src/physics.f90:10 in step" in r.stdout
    assert "src/physics.f90:3" not in r.stdout, "the definition is not a call site"
    assert "Most executions: long runs src/physics.f90:10 500 times." in r.stdout

    r = vit(tmp_path, "coverage", "line", "src/physics.f90:15", "--coverage", str(out))
    assert r.returncode == 0
    assert "short: 1" in r.stdout
    r = vit(tmp_path, "coverage", "line", "src/physics.f90:4", "--coverage", str(out))
    assert r.returncode == 1


def test_cli_import_with_nothing_to_import_fails(project, tmp_path):
    empty = tmp_path / "gcov"
    (empty / "case").mkdir(parents=True)
    out = tmp_path / "coverage.json"
    r = vit(tmp_path, "coverage", "import", "--gcov-dir", str(empty),
            "--source-root", str(project), "--output", str(out))
    assert r.returncode == 1
    assert "No coverage imported" in r.stderr
    assert not out.exists()
