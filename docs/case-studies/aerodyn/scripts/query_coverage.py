#!/usr/bin/env python3
"""Query gcov line coverage data for AeroDyn extraction planning.

Loads line_coverage.json (produced by parse_gcov.py) and answers questions
like "which test cases hit AirfoilInfo.f90:1482?" or "for function X, which
test cases exercise each call site?"

Usage:
    # Which test cases hit a specific line?
    python3 query_coverage.py --line modules/aerodyn/src/AirfoilInfo.f90:974

    # Find all call sites for a function and which tests reach each
    python3 query_coverage.py --call-sites ComputeUA360_CnOffset

    # Which test case gives the most hits for a line? (best for extraction)
    python3 query_coverage.py --best modules/aerodyn/src/AirfoilInfo.f90:974

    # Per-file coverage summary across all test cases
    python3 query_coverage.py --summary

    # Use a custom coverage file
    python3 query_coverage.py --coverage path/to/line_coverage.json --line ...
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_COVERAGE = os.path.join(
    os.path.dirname(__file__), "..", "data", "line_coverage.json"
)
DEFAULT_SOURCE_DIR = None  # resolved at runtime


def load_coverage(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def query_line(coverage: dict, file_path: str, line_no: int) -> dict[str, int]:
    """Return {test_case: hit_count} for a specific file:line."""
    file_data = coverage["files"].get(file_path)
    if not file_data:
        # Try matching by basename
        basename = os.path.basename(file_path)
        for key in coverage["files"]:
            if os.path.basename(key) == basename:
                file_data = coverage["files"][key]
                break
    if not file_data:
        return {}

    result = {}
    line_str = str(line_no)
    for tc, tc_data in file_data["test_cases"].items():
        count = tc_data["lines"].get(line_str, 0)
        if count > 0:
            result[tc] = count
    return result


def find_call_sites(
    source_root: str, func_name: str, source_commit: str = None
) -> list[tuple[str, int, str]]:
    """Find all call sites for a function in AeroDyn source files.

    Returns list of (relative_file_path, line_number, line_text) tuples.
    Searches for both CALL statements and function-call expressions.

    If source_commit is provided, reads source from that git commit
    (via `git show`) to match the line numbers in coverage data.
    """
    aerodyn_src = os.path.join(source_root, "modules", "aerodyn", "src")
    if not os.path.isdir(aerodyn_src):
        return []

    pattern = re.compile(
        rf"(?:call\s+{re.escape(func_name)}\b|=\s*{re.escape(func_name)}\s*\()",
        re.IGNORECASE,
    )
    def_pattern = re.compile(
        rf"^\s*(SUBROUTINE|FUNCTION)\s+{re.escape(func_name)}\b", re.IGNORECASE
    )

    def _read_lines(rel_path: str) -> list[str]:
        """Read file lines, using git show if source_commit is set."""
        if source_commit:
            try:
                result = subprocess.run(
                    ["git", "-C", source_root, "show", f"{source_commit}:{rel_path}"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout.splitlines()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        abs_path = os.path.join(source_root, rel_path)
        if os.path.isfile(abs_path):
            with open(abs_path) as f:
                return f.read().splitlines()
        return []

    results = []
    for f90 in sorted(Path(aerodyn_src).glob("*.f90")):
        rel_path = os.path.relpath(str(f90), source_root)
        lines = _read_lines(rel_path)
        for i, line in enumerate(lines, 1):
            if pattern.search(line) and not def_pattern.search(line):
                results.append((rel_path, i, line.strip()))
    return results


def find_enclosing_function(
    source_file: str, line_no: int,
    source_root: str = None, source_commit: str = None, rel_path: str = None,
) -> str:
    """Find the function/subroutine that contains a given line number."""
    lines = None
    if source_commit and source_root and rel_path:
        try:
            result = subprocess.run(
                ["git", "-C", source_root, "show", f"{source_commit}:{rel_path}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.splitlines()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    if lines is None:
        if not os.path.isfile(source_file):
            return "unknown"
        with open(source_file) as f:
            lines = f.read().splitlines()

    current_func = "module-level"
    func_pattern = re.compile(
        r"^\s*(?:RECURSIVE\s+)?(?:PURE\s+)?(?:ELEMENTAL\s+)?"
        r"(?:(?:REAL|INTEGER|LOGICAL|CHARACTER|DOUBLE\s+PRECISION|TYPE)\s*(?:\([^)]*\))?\s+)?"
        r"(?:SUBROUTINE|FUNCTION)\s+(\w+)",
        re.IGNORECASE,
    )
    end_pattern = re.compile(
        r"^\s*END\s+(?:SUBROUTINE|FUNCTION)\b", re.IGNORECASE
    )

    for i, line in enumerate(lines, 1):
        m = func_pattern.match(line)
        if m:
            current_func = m.group(1)
        if end_pattern.match(line):
            if i >= line_no:
                return current_func
            current_func = "module-level"
        if i == line_no:
            return current_func
    return current_func


def cmd_line(args, coverage):
    """Handle --line FILE:LINE query."""
    parts = args.line.rsplit(":", 1)
    if len(parts) != 2:
        print(f"ERROR: Expected FILE:LINE format, got: {args.line}", file=sys.stderr)
        sys.exit(1)

    file_path, line_no = parts[0], int(parts[1])
    hits = query_line(coverage, file_path, line_no)

    if not hits:
        print(f"No test case hits {file_path}:{line_no}")
    else:
        print(f"{file_path}:{line_no}")
        for tc in sorted(hits, key=lambda t: -hits[t]):
            print(f"  {tc}: {hits[tc]} hits")


def cmd_best(args, coverage):
    """Handle --best FILE:LINE query — which test case gives the most hits."""
    parts = args.best.rsplit(":", 1)
    if len(parts) != 2:
        print(f"ERROR: Expected FILE:LINE format, got: {args.best}", file=sys.stderr)
        sys.exit(1)

    file_path, line_no = parts[0], int(parts[1])
    hits = query_line(coverage, file_path, line_no)

    if not hits:
        print(f"No test case hits {file_path}:{line_no}")
    else:
        best_tc = max(hits, key=hits.get)
        print(f"{best_tc} ({hits[best_tc]} hits)")


def cmd_call_sites(args, coverage):
    """Handle --call-sites FUNC_NAME query."""
    source_root = args.source_root
    if not source_root:
        source_root = coverage.get("metadata", {}).get("source_root", ".")

    source_commit = coverage.get("metadata", {}).get("source_commit")

    sites = find_call_sites(source_root, args.call_sites, source_commit=source_commit)
    if not sites:
        print(f"No call sites found for {args.call_sites} in AeroDyn source")
        return

    print(f"Call sites for {args.call_sites}:")
    for file_path, line_no, line_text in sites:
        abs_path = os.path.join(source_root, file_path)
        caller = find_enclosing_function(
            abs_path, line_no,
            source_root=source_root, source_commit=source_commit, rel_path=file_path,
        )

        hits = query_line(coverage, file_path, line_no)
        if hits:
            tc_list = ", ".join(
                f"{tc}: {c}" for tc, c in sorted(hits.items(), key=lambda x: -x[1])
            )
            print(f"  {file_path}:{line_no} ({caller}) -> {tc_list}")
        else:
            print(f"  {file_path}:{line_no} ({caller}) -> NO COVERAGE")


def cmd_summary(args, coverage):
    """Handle --summary — per-file coverage overview."""
    test_cases = coverage["metadata"]["test_cases"]

    # Header
    print(f"{'File':<50s} ", end="")
    for tc in test_cases:
        # Abbreviate test case names
        short = tc.replace("ad_", "").replace("_OLAF", "/O").replace("_Fixed", "/F")
        print(f"{short:>10s} ", end="")
    print()
    print("-" * (50 + 11 * len(test_cases)))

    for file_path in sorted(coverage["files"].keys()):
        # Only show AeroDyn files by default
        if "aerodyn" not in file_path.lower():
            continue

        basename = os.path.basename(file_path)
        print(f"{basename:<50s} ", end="")

        file_data = coverage["files"][file_path]
        for tc in test_cases:
            tc_data = file_data["test_cases"].get(tc, {})
            n_lines = len(tc_data.get("lines", {}))
            if n_lines > 0:
                print(f"{n_lines:>10d} ", end="")
            else:
                print(f"{'.':>10s} ", end="")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Query gcov line coverage for AeroDyn extraction planning"
    )
    parser.add_argument(
        "--coverage",
        default=DEFAULT_COVERAGE,
        help="Path to line_coverage.json",
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help="OpenFAST source root (for finding call sites)",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--line", help="Query FILE:LINE — which test cases hit it")
    group.add_argument("--best", help="Query FILE:LINE — best test case for extraction")
    group.add_argument("--call-sites", help="Find all call sites for FUNC_NAME")
    group.add_argument("--summary", action="store_true", help="Per-file coverage summary")

    args = parser.parse_args()

    coverage_path = os.path.realpath(args.coverage)
    if not os.path.isfile(coverage_path):
        print(f"ERROR: Coverage data not found at {coverage_path}", file=sys.stderr)
        print("Run generate_coverage.sh first.", file=sys.stderr)
        sys.exit(1)

    coverage = load_coverage(coverage_path)

    if args.line:
        cmd_line(args, coverage)
    elif args.best:
        cmd_best(args, coverage)
    elif args.call_sites:
        cmd_call_sites(args, coverage)
    elif args.summary:
        cmd_summary(args, coverage)


if __name__ == "__main__":
    main()
