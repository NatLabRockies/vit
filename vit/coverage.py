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
     "files": {"<path>": {"blob": "<git blob id>", "source": "<text, if needed>",
                          "test_cases": {"<case>": {"lines": {"<line>": count}}}}}}

Line numbers belong to the source the coverage was measured on, which is not
necessarily any commit: a project that measures on pre-integration source
restores old versions of some files into a newer checkout. So import records,
per file, the git blob id of the content it measured, and the text itself when
that content is not in the checked-out commit. Lookups read that content, so
later edits do not shift the lines. A file without a blob id (an older coverage
file, or a source root outside git) is read at `source_commit`, failing that
from the working tree, and the lookup says so.
"""

import datetime
import gzip
import json
import os
import subprocess
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


def _record_measured_content(root, commit, files) -> list:
    """Give each file its blob id, and its text when `commit` does not hold it.

    Returns the paths whose text was stored. Outside a git repository nothing
    is recorded, and lookups read the working tree.
    """
    if commit is None:
        return []
    rels = [rel for rel in files if (Path(root) / rel).is_file()]
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
        return path.read_text(errors="replace"), "working_tree"
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
