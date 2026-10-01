"""
Line coverage per test case, for choosing a call site and test case to extract.

KGen captures nothing from a call site the run never reaches, and little from
one it reaches rarely. With line coverage per test case, which case reaches a
line, and how often, is a lookup rather than a guess.

VIT does not run the project's test cases. The project builds with gcc's
`--coverage`, runs each case, and runs `gcov -j` (gcc 9 or later), leaving one
directory of `.gcov.json.gz` files per case:

    gcov_dir/
      case_a/  *.gcov.json.gz
      case_b/  *.gcov.json.gz

`vit coverage import` merges them into one JSON file. `vit coverage run` does all
of it, from the `coverage` section of vit.yaml: it builds an instrumented copy,
runs each case, collects the gcov output and imports it. `vit find FUNC
--coverage` lists each call site with the cases that reach it, and `vit
coverage line FILE:LINE` answers for one line.

The file holds, per source file relative to the source root, per test case, the
execution count of every line that ran:

    {"metadata": {"generated", "test_cases", "source_root", "source_commit",
                  "file_count"},
     "files": {"<path>": {"blob": "<git blob id>", "source": "<text, if needed>",
                          "test_cases": {"<case>": {"lines": {"<line>": count}}}}}}

Line numbers belong to the source the coverage was measured on, which is not
necessarily any commit: a project that measures on pre-integration source
restores old versions of some files into a newer checkout. So import records,
per file, the git blob id of the content it measured, and the text itself when
that content is not in the checked-out commit, or when there is no git
repository to recover it from. Lookups read that content, so later edits do not
shift the lines. A file without a blob id (an older coverage file) is read at
`source_commit`; failing that, the working tree is read, and the lookup says
whether it still matches what was measured.
"""

import datetime
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .extract import line_calls
from .fortran_parser import enclosing_procedure


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def read_gcov_json_gz(path) -> list:
    """(source path, {line: count}) for each file in one gcov JSON file.

    Only lines that ran are kept. A relative source path is resolved against
    the directory gcov recorded as its working directory.
    """
    with gzip.open(path, "rt") as f:
        data = json.load(f)
    cwd = data.get("current_working_directory")
    results = []
    for entry in data.get("files", []):
        source = entry["file"]
        if not os.path.isabs(source) and cwd:
            source = os.path.join(cwd, source)
        lines = {}
        for line in entry.get("lines", []):
            if line["count"] > 0:
                lines[str(line["line_number"])] = line["count"]
        if lines:
            results.append((source, lines))
    return results


def git_head(root) -> Optional[str]:
    """The commit checked out in `root`, or None if it is not a git repository."""
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def import_gcov(gcov_dir, source_root, prefix=None) -> tuple:
    """Merge per-test-case gcov JSON into one coverage document.

    Each subdirectory of `gcov_dir` is a test case. Paths are stored relative to
    `source_root`; files outside it are left out, as are files not under
    `prefix` when one is given. Counts for a file that appears in several gcov
    files of one case (one per compilation unit) are summed.

    Returns (coverage, report), where report counts what was read and what was
    left out, so that nothing is dropped without saying so.
    """
    root = os.path.realpath(source_root)
    gcov_dir = Path(gcov_dir)
    cases = sorted(d.name for d in gcov_dir.iterdir() if d.is_dir())
    report = {"gcov_files": 0, "unreadable": [], "outside_root": set(),
              "filtered": set(), "cases_without_data": []}

    merged: dict = {}
    for case in cases:
        gz_files = sorted((gcov_dir / case).glob("*.gcov.json.gz"))
        if not gz_files:
            report["cases_without_data"].append(case)
        for gz in gz_files:
            report["gcov_files"] += 1
            try:
                entries = read_gcov_json_gz(gz)
            except (OSError, ValueError, KeyError) as e:
                report["unreadable"].append(f"{gz}: {e}")
                continue
            for source, lines in entries:
                rel = os.path.relpath(os.path.realpath(source), root)
                if rel == ".." or rel.startswith(".." + os.sep):
                    report["outside_root"].add(source)
                    continue
                if prefix and not rel.startswith(prefix):
                    report["filtered"].add(rel)
                    continue
                counts = merged.setdefault(rel, {}).setdefault(case, {})
                for line, count in lines.items():
                    counts[line] = counts.get(line, 0) + count

    files = {
        rel: {"test_cases": {case: {"lines": merged[rel][case]}
                             for case in sorted(merged[rel])}}
        for rel in sorted(merged)
    }
    commit = git_head(root)
    report["stored_text"] = _record_measured_content(root, commit, files)
    coverage = {
        "metadata": {
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "test_cases": cases,
            "source_root": root,
            "source_commit": commit,
            "file_count": len(files),
        },
        "files": files,
    }
    return coverage, report


def _blob_id(data: bytes) -> str:
    """Git's blob id for `data`, computed without git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _record_measured_content(root, commit, files) -> list:
    """Give each file its blob id, and its text when `commit` does not hold it.

    Outside a git repository nothing else can recover the content later, so
    every file's text is stored. Returns the paths whose text was stored.
    """
    rels = [rel for rel in files if (Path(root) / rel).is_file()]
    if commit is None:
        for rel in rels:
            data = (Path(root) / rel).read_bytes()
            files[rel]["blob"] = _blob_id(data)
            files[rel]["source"] = data.decode("utf-8", errors="replace")
        return rels
    if not rels:
        return []
    measured = _git(root, "hash-object", "--stdin-paths", stdin="\n".join(rels) + "\n")
    in_commit = _git(root, "cat-file", "--batch-check=%(objectname)",
                     stdin="".join(f"{commit}:./{rel}\n" for rel in rels))
    if measured.returncode or in_commit.returncode:
        return []
    stored = []
    for rel, blob, committed in zip(rels, measured.stdout.split(),
                                    in_commit.stdout.splitlines()):
        files[rel]["blob"] = blob
        if committed.split()[0] != blob:  # "<object> missing" when untracked
            files[rel]["source"] = (Path(root) / rel).read_text(errors="replace")
            stored.append(rel)
    return stored


def write(coverage, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(coverage, f, indent=2, sort_keys=True)


def load(path) -> dict:
    with open(path) as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


def line_hits(coverage, rel_path, line_no) -> dict:
    """{test case: count} for one line, or {} if no case ran it.

    `rel_path` is relative to the source root. If no file has exactly that
    path, a file with the same name is used only when exactly one exists: with
    two candidates, picking either could report another file's coverage.
    """
    files = coverage["files"]
    data = files.get(rel_path)
    if data is None:
        name = os.path.basename(rel_path)
        matches = [k for k in files if os.path.basename(k) == name]
        if len(matches) != 1:
            return {}
        data = files[matches[0]]
    key = str(line_no)
    return {case: d["lines"][key] for case, d in data["test_cases"].items()
            if d["lines"].get(key, 0) > 0}


def _git(root, *args, stdin=None):
    try:
        return subprocess.run(["git", "-C", str(root), *args], input=stdin,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def coverage_root(coverage, source_dir) -> Path:
    """The directory the coverage paths are relative to.

    The recorded source root when it exists here; otherwise the git repository
    holding `source_dir` (the recorded root is often a container path), and
    failing that `source_dir` itself.
    """
    recorded = coverage.get("metadata", {}).get("source_root")
    if recorded and os.path.isdir(recorded):
        return Path(recorded)
    r = _git(source_dir, "rev-parse", "--show-toplevel")
    if r.returncode == 0:
        return Path(r.stdout.strip())
    return Path(source_dir)


def measured_text(coverage, root, rel) -> tuple:
    """(text, how) for a covered file, as it was when the coverage was measured.

    `how` is "measured" when the text is the measured content (stored, or
    found by blob id), "commit" when it was read at `source_commit` for an
    entry with no blob id, and "working_tree" when neither was available and
    the current file was read instead, whose lines may not match the counts.
    Text is None if the file cannot be read at all.
    """
    entry = coverage["files"].get(rel, {})
    if "source" in entry:
        return entry["source"], "measured"
    path = Path(root) / rel
    blob = entry.get("blob")
    if blob:
        r = _git(root, "cat-file", "blob", blob)
        if r.returncode == 0:
            return r.stdout, "measured"
    else:
        commit = coverage.get("metadata", {}).get("source_commit")
        if commit:
            r = _git(root, "show", f"{commit}:./{rel}")
            if r.returncode == 0:
                return r.stdout, "commit"
    if path.is_file():
        data = path.read_bytes()
        unchanged = blob is not None and _blob_id(data) == blob
        return data.decode("utf-8", errors="replace"), "measured" if unchanged else "working_tree"
    return None, "working_tree"


def call_sites(coverage, source_dir, function_name) -> list:
    """Each call site of `function_name` under `source_dir`, with its coverage.

    A file the coverage covers is searched as it was measured, so its line
    numbers match the counts; any other file is searched in the working tree,
    and has no counts. Each site is a dict with `path` (relative to the
    coverage's root), `line`, `text`, `caller`, `hits`, and `read`: "measured",
    "commit" or "working_tree" for a covered file (see measured_text), and
    "uncovered" otherwise.
    """
    root = coverage_root(coverage, source_dir)
    real_root = os.path.realpath(root)
    rel_dir = os.path.relpath(os.path.realpath(source_dir), real_root)
    inside = not (rel_dir == ".." or rel_dir.startswith(".." + os.sep))

    on_disk = {os.path.relpath(os.path.realpath(p), real_root): p
               for p in Path(source_dir).rglob("*.f90")}
    covered_here = {rel for rel in coverage["files"]
                    if inside and rel.endswith(".f90")
                    and (rel_dir == "." or rel.startswith(rel_dir + os.sep))}

    sites = []
    for rel in sorted(set(on_disk) | covered_here):
        if rel in coverage["files"]:
            text, read = measured_text(coverage, root, rel)
        else:
            try:
                text, read = on_disk[rel].read_text(errors="replace"), "uncovered"
            except (OSError, UnicodeDecodeError):
                continue
        if text is None:
            continue
        for line_no, line in enumerate(text.split("\n"), 1):
            if line_calls(line, function_name):
                sites.append({
                    "path": rel, "line": line_no, "text": line.strip(),
                    "caller": enclosing_procedure(text, line_no),
                    "hits": line_hits(coverage, rel, line_no),
                    "read": read,
                })
    return sites


# --------------------------------------------------------------------------
# Run: build, run each case, collect gcov output, import
# --------------------------------------------------------------------------

_COVERAGE_FLAGS = ("CMAKE_Fortran_FLAGS", "CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS",
                   "CMAKE_EXE_LINKER_FLAGS", "CMAKE_SHARED_LINKER_FLAGS")
DEFAULT_CONFIGURE = ("cmake -S {project_dir} -B {build_dir} "
                     + " ".join(f"-D{flag}=--coverage" for flag in _COVERAGE_FLAGS))
DEFAULT_BUILD = "cmake --build {build_dir}"


class CoverageRunError(RuntimeError):
    """A step of `vit coverage run` failed; the message says which and why."""


def fill(command, project_dir, build_dir) -> str:
    """Substitute {project_dir} and {build_dir}, quoted for the shell.

    Plain replacement rather than str.format, so a command's own braces, as
    in ${VAR}, are left alone.
    """
    return (command.replace("{build_dir}", shlex.quote(str(build_dir)))
                   .replace("{project_dir}", shlex.quote(str(project_dir))))


def cmake_fortran_compiler(build_dir) -> Optional[str]:
    """The Fortran compiler CMake chose for a configured build directory."""
    cache = Path(build_dir) / "CMakeCache.txt"
    if not cache.is_file():
        return None
    for line in cache.read_text(errors="replace").splitlines():
        if line.startswith("CMAKE_Fortran_COMPILER:"):
            return line.split("=", 1)[1].strip() or None
    return None


def _run(argv, **kwargs):
    try:
        return subprocess.run(argv, capture_output=True, text=True, **kwargs)
    except OSError as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))


def _gcov_major(gcov) -> Optional[str]:
    """The GCC major version of a gcov, or None if it is not GCC's gcov.

    macOS puts LLVM's gcov on PATH as `gcov`; it cannot read GCC's data files.
    """
    r = _run([gcov, "--version"], timeout=30)
    first = (r.stdout or "").splitlines()[:1]
    if r.returncode or not first or not first[0].startswith("gcov"):
        return None
    m = re.search(r"(\d+)\.\d+(?:\.\d+)?\s*$", first[0])
    return m.group(1) if m else None


def find_gcov(compiler, configured="") -> tuple:
    """(gcov path or None, description) for data written by `compiler`.

    gcov must come from the same GCC release as the compiler, and be GCC 9 or
    later for JSON output. Tries a version-suffixed gcov beside the compiler,
    then on PATH, then plain `gcov`, and takes the first whose version matches.
    """
    if configured:
        path = shutil.which(configured)
        if not path:
            return None, f"coverage.gcov is {configured}, which is not an executable on PATH"
        return path, f"{path} (coverage.gcov)"
    r = _run([compiler, "-dumpversion"], timeout=30)
    major = r.stdout.strip().split(".")[0] if r.returncode == 0 and r.stdout.strip() else None
    names = []
    suffix = re.search(r"-(\d+)$", Path(compiler).name)
    if suffix:
        names.append(f"gcov-{suffix.group(1)}")
    if major:
        names.append(f"gcov-{major}")
    names.append("gcov")
    found = []
    for name in dict.fromkeys(names):
        sibling = Path(compiler).parent / name
        for candidate in ([str(sibling)] if sibling.is_file() else []) + [shutil.which(name)]:
            if not candidate:
                continue
            version = _gcov_major(candidate)
            found.append(f"{candidate} ({version or 'not GCC gcov'})")
            if version and (major is None or version == major) and int(version) >= 9:
                return candidate, f"{candidate}, GCC {version}, matching {compiler}"
    want = f"GCC {major}" if major else "a GCC gcov"
    return None, (f"no gcov for {want} (the compiler is {compiler}); tried "
                  + (", ".join(found) or ", ".join(names)) + ". Set coverage.gcov.")


def _shell(command, cwd, timeout=None):
    return _run(command, shell=True, cwd=cwd, timeout=timeout)


def _tail(result, n=20) -> str:
    text = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()
    return "\n".join(text[-n:])


def collect_gcov(gcov, build_dir, out_dir) -> int:
    """Run gcov -j on every object directory with data, into `out_dir`.

    gcov names its output after the source, so two directories holding the
    same file name would overwrite each other; each file is prefixed with its
    directory's index. Returns the number of files written.
    """
    by_dir: dict = {}
    for gcda in Path(build_dir).rglob("*.gcda"):
        gcno = gcda.with_suffix(".gcno")
        if gcno.is_file():
            by_dir.setdefault(gcda.parent, []).append(str(gcno))
    written = 0
    for index, directory in enumerate(sorted(by_dir)):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run([gcov, "-j", "-o", str(directory), *sorted(by_dir[directory])], cwd=tmp)
            if r.returncode:
                raise CoverageRunError(f"gcov failed on {directory}:\n{_tail(r)}")
            for out in sorted(Path(tmp).glob("*.gcov.json.gz")):
                shutil.move(str(out), str(Path(out_dir) / f"{index:03d}-{out.name}"))
                written += 1
    return written


def run_coverage(config, case_names=None, log=print) -> tuple:
    """Build with coverage, run each case, collect gcov output, and import it.

    Returns (coverage, report, results), where results has one entry per case
    with its exit code and the number of gcov files it produced. Raises
    CoverageRunError when the build, gcov or the configuration is the problem.
    """
    project_dir = Path(config.config_dir).resolve()
    build_dir = Path(config.abs_path(config.coverage_build_dir)).resolve()
    cases = dict(config.coverage_cases)
    if not cases:
        raise CoverageRunError(
            "no test cases: list them in vit.yaml as coverage.cases, a name and a shell "
            "command each, for example\n  coverage:\n    cases:\n      default: "
            "{build_dir}/my_program input.txt")
    if case_names:
        unknown = [n for n in case_names if n not in cases]
        if unknown:
            raise CoverageRunError(f"unknown case(s) {', '.join(unknown)}; vit.yaml lists "
                                   f"{', '.join(cases)}")
        cases = {n: cases[n] for n in case_names}
    for name in cases:
        if not name or "/" in name or name in (".", ".."):
            raise CoverageRunError(f"case name {name!r} cannot be a directory name")

    for step, command in (("configure", config.coverage_configure_cmd or DEFAULT_CONFIGURE),
                          ("build", config.coverage_build_cmd or DEFAULT_BUILD)):
        command = fill(command, project_dir, build_dir)
        log(f"{step}: {command}")
        r = _shell(command, project_dir)
        if r.returncode:
            raise CoverageRunError(f"the coverage {step} failed (exit {r.returncode}):\n{_tail(r)}")

    compiler = cmake_fortran_compiler(build_dir) or config.fortran_compiler
    gcov, how = find_gcov(compiler, config.coverage_gcov)
    if not gcov:
        raise CoverageRunError(how)
    log(f"gcov: {how}")

    gcov_dir = build_dir / "vit-gcov"
    shutil.rmtree(gcov_dir, ignore_errors=True)
    results = []
    for name, command in cases.items():
        for stale in build_dir.rglob("*.gcda"):
            stale.unlink()
        command = fill(command, project_dir, build_dir)
        log(f"case {name}: {command}")
        r = _shell(command, project_dir)
        case_dir = gcov_dir / name
        case_dir.mkdir(parents=True)
        files = collect_gcov(gcov, build_dir, case_dir)
        results.append({"name": name, "exit_code": r.returncode, "gcov_files": files,
                        "output_tail": _tail(r, 8) if r.returncode else ""})

    coverage, report = import_gcov(gcov_dir, project_dir)
    return coverage, report, results
