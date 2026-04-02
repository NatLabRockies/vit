"""
Simulation-level array comparison for non-KGen verification.

Compares .npz files between a baseline directory (pure Fortran build)
and an integrated directory (Fortran + C++ build). Every array in every
matching file must be bit-identical for the comparison to pass.

This is the Stage B equivalent of KGen kernel verification — instead of
comparing function-level I/O, it compares full simulation output arrays.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class ArrayResult:
    """Result for a single array comparison."""
    name: str
    n_values: int
    identical: bool
    max_diff: float = 0.0
    max_diff_index: int = 0


@dataclass
class FileResult:
    """Result for a single .npz file comparison."""
    filename: str
    arrays: list[ArrayResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(a.identical for a in self.arrays)

    @property
    def total_values(self) -> int:
        return sum(a.n_values for a in self.arrays)


@dataclass
class CompareResult:
    """Overall comparison result."""
    files: list[FileResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(f.passed for f in self.files)

    @property
    def total_values(self) -> int:
        return sum(f.total_values for f in self.files)

    @property
    def total_arrays(self) -> int:
        return sum(len(f.arrays) for f in self.files)

    @property
    def identical_arrays(self) -> int:
        return sum(1 for f in self.files for a in f.arrays if a.identical)


def compare_arrays(
    baseline_dir: str,
    integrated_dir: str,
    tolerance: float = 0.0,
) -> CompareResult:
    """
    Compare .npz simulation output files between baseline and integrated builds.

    Args:
        baseline_dir: Directory containing baseline .npz files
        integrated_dir: Directory containing integrated .npz files
        tolerance: Maximum allowed absolute difference (0.0 = exact match)

    Returns:
        CompareResult with per-file, per-array details
    """
    baseline_path = Path(baseline_dir)
    integrated_path = Path(integrated_dir)

    if not baseline_path.is_dir():
        raise FileNotFoundError(f"Baseline directory not found: {baseline_dir}")
    if not integrated_path.is_dir():
        raise FileNotFoundError(f"Integrated directory not found: {integrated_dir}")

    # Find matching .npz files
    baseline_files = sorted(baseline_path.glob("*.npz"))
    if not baseline_files:
        raise FileNotFoundError(f"No .npz files found in {baseline_dir}")

    result = CompareResult()

    for baseline_file in baseline_files:
        integrated_file = integrated_path / baseline_file.name
        if not integrated_file.exists():
            # Missing integrated file — report as failure
            fr = FileResult(filename=baseline_file.name)
            fr.arrays.append(ArrayResult(
                name="<file missing>", n_values=0, identical=False
            ))
            result.files.append(fr)
            continue

        baseline_data = np.load(str(baseline_file))
        integrated_data = np.load(str(integrated_file))

        fr = FileResult(filename=baseline_file.name)

        for key in sorted(baseline_data.files):
            if key not in integrated_data.files:
                fr.arrays.append(ArrayResult(
                    name=key, n_values=0, identical=False
                ))
                continue

            b = baseline_data[key]
            i = integrated_data[key]
            n = len(b)

            if tolerance == 0.0:
                is_identical = np.array_equal(b, i)
            else:
                diff = np.abs(b.astype(np.float64) - i.astype(np.float64))
                is_identical = diff.max() <= tolerance if n > 0 else True

            ar = ArrayResult(name=key, n_values=n, identical=is_identical)
            if not is_identical and n > 0:
                diff = np.abs(b.astype(np.float64) - i.astype(np.float64))
                ar.max_diff = float(diff.max())
                ar.max_diff_index = int(diff.argmax())

            fr.arrays.append(ar)

        result.files.append(fr)

    return result


def format_result(result: CompareResult) -> str:
    """Format comparison result as human-readable text."""
    lines = []

    for fr in result.files:
        name = fr.filename.replace(".npz", "")
        n_pass = sum(1 for a in fr.arrays if a.identical)
        n_total = len(fr.arrays)

        if fr.passed:
            lines.append(
                f"  {name}: {n_pass}/{n_total} IDENTICAL "
                f"({fr.total_values:,} values)"
            )
        else:
            for a in fr.arrays:
                if not a.identical:
                    lines.append(
                        f"  MISMATCH {name} {a.name}: "
                        f"max_diff={a.max_diff:.2e} at index {a.max_diff_index}"
                    )
                else:
                    lines.append(
                        f"  {name} {a.name}: IDENTICAL ({a.n_values:,} values)"
                    )

    lines.append("")
    if result.passed:
        lines.append(
            f"✓ ALL IDENTICAL — {result.total_values:,} total values compared"
        )
    else:
        lines.append(
            f"✗ DIFFERENCES FOUND — "
            f"{result.identical_arrays}/{result.total_arrays} arrays identical, "
            f"{result.total_values:,} total values"
        )

    return "\n".join(lines)
