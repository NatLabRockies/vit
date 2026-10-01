#!/usr/bin/env python3
"""Parse gcov JSON output into consolidated line coverage data.

Reads *.gcov.json.gz files produced by `gcov -j` from per-test-case
subdirectories and produces a single line_coverage.json with per-line
execution counts for every source file, for every test case.

Usage:
    python3 parse_gcov.py \
        --gcov-dir /workspace/openfast/build-coverage/gcov_json \
        --output /workspace/vit/docs/case-studies/aerodyn/data/line_coverage.json \
        --source-root /workspace/openfast
"""

import argparse
import datetime
import gzip
import json
import os
import subprocess
from pathlib import Path


def parse_gcov_json_gz(path: str) -> list[tuple[str, dict[str, int]]]:
    """Parse a single *.gcov.json.gz file.

    Returns list of (source_file_path, {line_number_str: count}) tuples.
    Only includes lines with count > 0.
    """
    with gzip.open(path, "rt") as f:
        data = json.load(f)

    results = []
    for file_entry in data.get("files", []):
        source_file = file_entry["file"]
        lines = {}
        for line_entry in file_entry.get("lines", []):
            count = line_entry["count"]
            if count > 0:
                line_no = str(line_entry["line_number"])
                lines[line_no] = count
        if lines:
            results.append((source_file, lines))
    return results


def relativize_path(abs_path: str, source_root: str) -> str:
    """Convert absolute path to relative, falling back to the original."""
    try:
        return os.path.relpath(abs_path, source_root)
    except ValueError:
        return abs_path


def main():
    parser = argparse.ArgumentParser(
        description="Parse gcov JSON into consolidated line coverage"
    )
    parser.add_argument(
        "--gcov-dir",
        required=True,
        help="Directory with per-test-case subdirs of .gcov.json.gz files",
    )
    parser.add_argument(
        "--output", required=True, help="Output JSON path"
    )
    parser.add_argument(
        "--source-root",
        required=True,
        help="OpenFAST repo root for relativizing paths",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Only include source files matching this prefix (e.g., modules/aerodyn)",
    )
    args = parser.parse_args()

    source_root = os.path.realpath(args.source_root)
    gcov_dir = Path(args.gcov_dir)

    # Discover test cases from subdirectories
    test_cases = sorted([d.name for d in gcov_dir.iterdir() if d.is_dir()])

    # Structure: {rel_path: {test_case: {line_no_str: count}}}
    coverage: dict[str, dict[str, dict[str, int]]] = {}

    for tc in test_cases:
        tc_dir = gcov_dir / tc
        gz_files = list(tc_dir.glob("*.gcov.json.gz"))

        for gz_file in gz_files:
            try:
                entries = parse_gcov_json_gz(str(gz_file))
            except Exception as e:
                print(f"  WARNING: Failed to parse {gz_file}: {e}")
                continue

            for abs_path, line_data in entries:
                rel_path = relativize_path(abs_path, source_root)

                # Optional filter
                if args.filter and not rel_path.startswith(args.filter):
                    continue

                if rel_path not in coverage:
                    coverage[rel_path] = {}

                if tc not in coverage[rel_path]:
                    coverage[rel_path][tc] = {}

                # Merge line counts (gcov may produce multiple entries
                # for the same source from different compilation units)
                existing = coverage[rel_path][tc]
                for line_no, count in line_data.items():
                    existing[line_no] = existing.get(line_no, 0) + count

    # Build output structure
    files_output = {}
    for rel_path in sorted(coverage.keys()):
        tc_data = coverage[rel_path]
        files_output[rel_path] = {
            "test_cases": {
                tc: {"lines": tc_data[tc]}
                for tc in sorted(tc_data.keys())
            }
        }

    # Capture the git commit hash of the source used for coverage
    source_commit = None
    try:
        result = subprocess.run(
            ["git", "-C", source_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            source_commit = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    output = {
        "metadata": {
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "test_cases": test_cases,
            "source_root": source_root,
            "source_commit": source_commit,
            "file_count": len(files_output),
        },
        "files": files_output,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2, sort_keys=True)

    # Summary
    aerodyn_files = sum(
        1 for k in files_output if "aerodyn" in k.lower()
    )
    total_lines = sum(
        len(lines)
        for file_data in files_output.values()
        for tc_data in file_data["test_cases"].values()
        for lines in [tc_data["lines"]]
    )
    print(f"Written {args.output}")
    print(f"  {len(files_output)} source files ({aerodyn_files} AeroDyn)")
    print(f"  {len(test_cases)} test cases")
    print(f"  {total_lines} non-zero line entries total")


if __name__ == "__main__":
    main()
