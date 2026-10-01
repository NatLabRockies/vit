"""Line coverage per test case (vit/coverage.py): import, lookup, and the CLI.

Coverage line numbers belong to the source it was measured on, which need not
be any commit: a project that measures pre-integration source restores old
versions of some files into a newer checkout. The tests that matter most edit
the tree after measuring, and require the lookup to read the measured lines,
or every count it reports belongs to some other line.
"""

import gzip
import json
import os
import shutil
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


def gcov_file(path, files, cwd=None, functions=None):
    """Write a .gcov.json.gz in the shape `gcov -j` produces. `functions` maps
    a file to [(symbol, start line, end line, calls)]."""
    functions = functions or {}
    data = {"format_version": "1", "gcc_version": "13.2.0", "files": [
        {"file": name,
         "lines": [{"line_number": n, "count": c} for n, c in lines.items()],
         "functions": [{"name": sym, "demangled_name": sym, "start_line": a, "end_line": b,
                        "execution_count": n} for sym, a, b, n in functions.get(name, [])]}
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


def test_import_records_the_content_measured(project, gcov_dir, tmp_path):
    data, report = cov.import_gcov(gcov_dir, project)
    head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    blob = subprocess.run(["git", "-C", str(project), "hash-object", "src/physics.f90"],
                          capture_output=True, text=True).stdout.strip()
    assert data["metadata"]["source_commit"] == head
    entry = data["files"]["src/physics.f90"]
    assert entry["blob"] == blob
    assert cov._blob_id((project / "src" / "physics.f90").read_bytes()) == blob
    assert "source" not in entry, "the commit holds this content; no need to store it"
    assert report["stored_text"] == []

    plain = tmp_path / "plain"
    plain.mkdir()
    assert cov.git_head(plain) is None


def test_outside_git_the_measured_text_is_stored(tmp_path):
    """No commit can recover the content later, so import keeps it."""
    root = tmp_path / "plain"
    (root / "src").mkdir(parents=True)
    (root / "src" / "physics.f90").write_text(PHYSICS)
    gcov = tmp_path / "gcov"
    (gcov / "long").mkdir(parents=True)
    gcov_file(gcov / "long" / "p.gcov.json.gz",
              {str(root / "src" / "physics.f90"): {10: 500, 15: 2}})

    data, report = cov.import_gcov(gcov, root)
    assert data["metadata"]["source_commit"] is None
    assert report["stored_text"] == ["src/physics.f90"]

    sites = cov.call_sites(data, root / "src", "drag")
    assert [(s["line"], s["read"]) for s in sites] == [(10, "measured"), (15, "measured")]

    (root / "src" / "physics.f90").write_text("! later\n" + PHYSICS)
    sites = cov.call_sites(data, root / "src", "drag")
    assert [(s["line"], s["read"]) for s in sites] == [(10, "measured"), (15, "measured")]


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


def test_call_sites_are_read_as_measured(project, gcov_dir):
    """Lines inserted after measuring must not shift the lookup."""
    data, _ = cov.import_gcov(gcov_dir, project)
    edited = project / "src" / "physics.f90"
    edited.write_text("! added after the coverage run\n" * 4 + PHYSICS)

    sites = cov.call_sites(data, project / "src", "drag")

    assert [(s["path"], s["line"], s["caller"], s["read"]) for s in sites] == [
        ("src/physics.f90", 10, "step", "measured"),
        ("src/physics.f90", 15, "settle", "measured")]
    assert sites[0]["hits"] == {"long": 500, "short": 3}
    assert sites[1]["hits"] == {"short": 1}


def test_measured_on_a_tree_that_differs_from_its_commit(project, gcov_dir):
    """Measured on old source restored into a newer checkout, then the newer
    version restored: the commit never held the measured content, so import
    stores it, and the lookup still reads the measured lines."""
    integrated = "! wrapper\n" * 4 + PHYSICS
    path = project / "src" / "physics.f90"
    path.write_text(integrated)
    git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "integrate")
    path.write_text(PHYSICS)                       # the reset, before the coverage run

    data, report = cov.import_gcov(gcov_dir, project)
    assert report["stored_text"] == ["src/physics.f90"]
    assert data["files"]["src/physics.f90"]["source"] == PHYSICS

    git(project, "checkout", "-q", "--", "src/physics.f90")   # back to integrated
    sites = cov.call_sites(data, project / "src", "drag")
    assert [(s["line"], s["read"]) for s in sites] == [(10, "measured"), (15, "measured")]
    assert sites[0]["hits"] == {"long": 500, "short": 3}


def test_an_entry_without_a_blob_is_read_at_the_recorded_commit(project, gcov_dir):
    """Coverage files written before per-file records name only a commit."""
    data, _ = cov.import_gcov(gcov_dir, project)
    for entry in data["files"].values():
        entry.pop("blob", None)
    (project / "src" / "physics.f90").write_text("! later\n" * 3 + PHYSICS)

    sites = cov.call_sites(data, project / "src", "drag")
    assert [(s["line"], s["read"]) for s in sites] == [(10, "commit"), (15, "commit")]


def test_without_any_record_the_working_tree_is_read_and_marked(project, gcov_dir):
    data, _ = cov.import_gcov(gcov_dir, project)
    for entry in data["files"].values():
        entry.pop("blob", None)
    data["metadata"]["source_commit"] = "0" * 40

    sites = cov.call_sites(data, project / "src", "drag")
    assert [(s["line"], s["read"]) for s in sites] == [(10, "working_tree"),
                                                      (15, "working_tree")]


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
    assert "(measured source not available" not in r.stdout
    assert "Most executions: long runs src/physics.f90:10 500 times." in r.stdout

    r = vit(tmp_path, "coverage", "line", "src/physics.f90:15", "--coverage", str(out))
    assert r.returncode == 0
    assert "v = drag(v)" in r.stdout
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


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def test_fill_quotes_paths_and_leaves_shell_braces(tmp_path):
    cmd = cov.fill("cd {build_dir} && ./run ${HOME} {project_dir}/in", "/p q", "/b")
    assert cmd == "cd /b && ./run ${HOME} '/p q'/in"


def _fake(path, text):
    path.write_text("#!/bin/sh\n" + text)
    path.chmod(0o755)
    return path


def test_find_gcov_matches_the_compiler_version(tmp_path, monkeypatch):
    """The gcov must come from the compiler's GCC release. A bare `gcov` may be
    LLVM's (macOS) or another release's, and neither can read the data."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    compiler = _fake(bin_dir / "gfortran", 'echo 77\n')
    monkeypatch.setenv("PATH", str(bin_dir))

    _fake(bin_dir / "gcov", 'echo "Apple LLVM version 17.0.0 (clang-1700)"\n')
    path, how = cov.find_gcov(str(compiler))
    assert path is None
    assert "GCC 77" in how and "Set coverage.gcov" in how

    _fake(bin_dir / "gcov", 'echo "gcov (GCC) 76.1.0"\n')
    assert cov.find_gcov(str(compiler))[0] is None, "another release's gcov"

    _fake(bin_dir / "gcov-77", 'echo "gcov (GCC) 77.2.0"\n')
    assert cov.find_gcov(str(compiler))[0] == str(bin_dir / "gcov-77")

    path, _ = cov.find_gcov(str(compiler), configured="gcov")
    assert path == str(bin_dir / "gcov"), "an explicit setting is taken as given"


def test_cmake_fortran_compiler(tmp_path):
    (tmp_path / "CMakeCache.txt").write_text(
        "CMAKE_C_COMPILER:FILEPATH=/usr/bin/cc\n"
        "CMAKE_Fortran_COMPILER:FILEPATH=/opt/gcc/bin/gfortran-13\n")
    assert cov.cmake_fortran_compiler(tmp_path) == "/opt/gcc/bin/gfortran-13"
    assert cov.cmake_fortran_compiler(tmp_path / "none") is None


def test_run_needs_cases(tmp_path):
    from vit.config import VitConfig

    with pytest.raises(cov.CoverageRunError, match="coverage.cases"):
        cov.run_coverage(VitConfig(config_dir=str(tmp_path)), log=lambda m: None)
    config = VitConfig(config_dir=str(tmp_path), coverage_cases={"a": "true"})
    with pytest.raises(cov.CoverageRunError, match="unknown case"):
        cov.run_coverage(config, case_names=["b"], log=lambda m: None)


def _toolchain_for_coverage():
    """Skip unless CMake, gfortran and a matching gcov are available."""
    if not (shutil.which("cmake") and shutil.which("gfortran")):
        pytest.skip("needs cmake and gfortran")
    if cov.find_gcov("gfortran")[0] is None:
        pytest.skip("no gcov matching gfortran")


def test_coverage_run_end_to_end(tmp_path):
    """The quickstart, two cases: one run of the program and two. Each run
    calls drag 500 times."""
    _toolchain_for_coverage()
    project = tmp_path / "qs"
    shutil.copytree(ROOT / "examples" / "quickstart", project)
    (project / "vit.yaml").write_text(textwrap.dedent("""\
        project:
          source_dir: src
        coverage:
          cases:
            once: cd {build_dir} && ./simulate
            twice: cd {build_dir} && ./simulate && ./simulate
            broken: cd {build_dir} && ./simulate && false
    """))

    r = vit(project, "coverage", "run")
    assert r.returncode == 1, "a failing case must fail the run"
    assert "WARNING: case broken exited 1" in r.stdout
    assert "3 test cases" in r.stdout

    r = vit(project, "coverage", "run", "--case", "once", "--case", "twice")
    assert r.returncode == 0, r.stdout + r.stderr
    r = vit(project, "find", "drag", "--coverage")
    assert r.returncode == 0, r.stderr
    assert "src/physics.f90:17 in step" in r.stdout
    assert "twice: 1000" in r.stdout and "once: 500" in r.stdout


def test_coverage_run_reports_procedures(tmp_path):
    """Every run writes which procedures the cases call and which none does."""
    _toolchain_for_coverage()
    project = tmp_path / "qs"
    shutil.copytree(ROOT / "examples" / "quickstart", project)
    physics = project / "src" / "physics.f90"
    physics.write_text(physics.read_text().replace("end module physics", textwrap.dedent("""\
            subroutine unused_helper(v)
                real(dp), intent(inout) :: v
                v = 0.0_dp
            end subroutine unused_helper
        end module physics""")))
    (project / "vit.yaml").write_text(textwrap.dedent("""\
        project:
          source_dir: src
        coverage:
          cases:
            once: cd {build_dir} && ./simulate
    """))
    r = vit(project, "coverage", "run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "procedures: 3 reached, 1 not reached by any test case, 0 not built" in r.stdout
    report = (project / "coverage_procedures.md").read_text()
    assert "| unused_helper | src/physics.f90 |" in report
    assert "| drag | src/physics.f90 | 500 (once) | 1 |" in report

    r = vit(project, "coverage", "procedures", "--not-reached")
    assert r.stdout.strip().endswith("unused_helper")


MODULE_SRC = textwrap.dedent("""\
    module m
    contains
      subroutine host(x)
        real(8), intent(inout) :: x
        call inner()
      contains
        subroutine inner()
          x = x + 1d0
        end subroutine inner
      end subroutine host
      subroutine never()
        integer :: i
        i = 1
      end subroutine never
    #ifdef NOT_SET
      subroutine excluded()
      end subroutine excluded
    #endif
    end module m
""")


def _module_project(tmp_path, functions=True):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "m.f90").write_text(MODULE_SRC)
    gcov = tmp_path / "gcov"
    (gcov / "case_a").mkdir(parents=True)
    src = str(root / "m.f90")
    # never's END line (14) has a count: gcov does this for procedures that
    # never ran, so it must not count as a call.
    lines = {3: 4, 5: 4, 7: 4, 8: 4, 14: 2}
    funcs = {src: [("__m_MOD_host", 3, 10, 4), ("inner.0", 7, 9, 4),
                   ("__m_MOD_never", 11, 14, 0), ("__m_MOD___copy_x", 19, 19, 0)]}
    gcov_file(gcov / "case_a" / "m.gcov.json.gz", {src: lines},
              functions=funcs if functions else None)
    data, _ = cov.import_gcov(gcov, root)
    return data, root


def test_procedures_from_call_records(tmp_path):
    data, root = _module_project(tmp_path)
    assert data["files"]["m.f90"]["functions"]["11"]["calls"] == {}, "built, never called"
    procs = {p["name"]: p for p in cov.procedure_coverage(data, root)}
    assert procs["host"]["status"] == "reached" and procs["host"]["calls"] == {"case_a": 4}
    assert procs["inner"]["status"] == "reached" and procs["inner"]["host"] == "host"
    assert procs["never"]["status"] == "not reached"
    assert procs["excluded"]["status"] == "not built"
    assert set(procs) == {"host", "inner", "never", "excluded"}, \
        "compiler-generated functions have no definition in the source"


def test_procedures_from_line_counts_ignore_end_lines(tmp_path):
    """An older coverage file has only line counts."""
    data, root = _module_project(tmp_path, functions=False)
    procs = {p["name"]: p for p in cov.procedure_coverage(data, root)}
    assert procs["host"]["status"] == "reached" and procs["host"]["basis"] == "lines"
    assert procs["never"]["status"] == "not reached", "a count on its END line only"
    assert procs["excluded"]["status"] == "not reached", "not built is indistinguishable here"

