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

`vit coverage import` merges them into one JSON file. `vit find FUNC --coverage
FILE` lists each call site with the cases that reach it, and `vit coverage line
FILE:LINE` answers for one line.

The file holds, per source file relative to the source root, per test case, the
execution count of every line that ran:

    {"metadata": {"generated", "test_cases", "source_root", "source_commit",
                  "file_count"},
     "files": {"<path>": {"test_cases": {"<case>": {"lines": {"<line>": count}}}}}}

Line numbers belong to the source the coverage was measured on. When the source
root is a git repository, import records its commit, and call sites are then
looked up in the source at that commit, so later edits, such as integration
wrappers, do not shift the lines.
"""

import datetime
import gzip
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from .extract import find_call_sites, line_calls
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
    coverage = {
        "metadata": {
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "test_cases": cases,
            "source_root": root,
            "source_commit": git_head(root),
            "file_count": len(files),
        },
        "files": files,
    }
    return coverage, report


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


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=60)


def _commit_available(root, commit) -> bool:
    if not commit:
        return False
    try:
        return _git(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _sites_at_commit(root, rel_dir, commit, function_name) -> list:
    """(rel path, line, text) for each call, in the source at `commit`."""
    r = _git(root, "grep", "-z", "-n", "-I", "-i", "-F", function_name,
             commit, "--", rel_dir)
    if r.returncode not in (0, 1):  # 1: no match
        raise RuntimeError(f"git grep failed in {root}: {r.stderr.strip()}")
    sites = []
    for record in r.stdout.splitlines():
        try:
            location, line_no, text = record.split("\0", 2)
        except ValueError:
            continue
        path = location[len(commit) + 1:]
        if path.endswith(".f90") and line_calls(text, function_name):
            sites.append((path, int(line_no), text.rstrip()))
    return sites


def coverage_root(coverage, source_dir) -> Path:
    """The directory the coverage paths are relative to.

    The recorded source root when it exists here; otherwise the git repository
    holding `source_dir` (the recorded root is often a container path), and
    failing that `source_dir` itself.
    """
    recorded = coverage.get("metadata", {}).get("source_root")
    if recorded and os.path.isdir(recorded):
        return Path(recorded)
    try:
        r = _git(source_dir, "rev-parse", "--show-toplevel")
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path(source_dir)


def call_sites(coverage, source_dir, function_name) -> tuple:
    """Each call site of `function_name` under `source_dir`, with its coverage.

    Returns (sites, source), where each site is a dict with `path` (relative to
    the coverage's root), `line`, `text`, `caller` and `hits`, and `source` says
    where the lines were read: the commit the coverage was measured on when the
    repository has it, otherwise the working tree, whose lines may have moved.
    """
    root = coverage_root(coverage, source_dir)
    commit = coverage.get("metadata", {}).get("source_commit")
    rel_dir = os.path.relpath(os.path.realpath(source_dir), os.path.realpath(root))
    inside = not (rel_dir == ".." or rel_dir.startswith(".." + os.sep))

    if inside and _commit_available(root, commit):
        found = _sites_at_commit(root, rel_dir, commit, function_name)
        source = {"kind": "commit", "commit": commit}

        def read(path):
            return _git(root, "show", f"{commit}:{path}").stdout
    else:
        found = [(os.path.relpath(os.path.realpath(p), os.path.realpath(root)), n, t)
                 for p, n, t in find_call_sites(str(source_dir), function_name)]
        source = {"kind": "working_tree", "commit": commit}

        def read(path):
            return (root / path).read_text(errors="replace")

    sites, texts = [], {}
    for path, line_no, text in sorted(found):
        if path not in texts:
            texts[path] = read(path)
        sites.append({
            "path": path, "line": line_no, "text": text.strip(),
            "caller": enclosing_procedure(texts[path], line_no),
            "hits": line_hits(coverage, path, line_no),
        })
    return sites, source
