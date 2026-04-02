"""Tests for simulation array comparison."""

import os
import tempfile

import numpy as np
import pytest

from vit.sim_compare import compare_arrays, format_result


@pytest.fixture
def identical_dirs():
    """Create two directories with identical .npz files."""
    with tempfile.TemporaryDirectory() as base_dir, \
         tempfile.TemporaryDirectory() as int_dir:
        # Scenario 1: 3 arrays
        data = {
            "gen_torque": np.array([1.0, 2.0, 3.0]),
            "bld_pitch": np.array([0.1, 0.2, 0.3]),
            "gen_speed": np.array([100.0, 200.0, 300.0]),
        }
        np.savez(os.path.join(base_dir, "scenario_1.npz"), **data)
        np.savez(os.path.join(int_dir, "scenario_1.npz"), **data)

        # Scenario 2: 2 arrays
        data2 = {
            "gen_torque": np.array([4.0, 5.0]),
            "bld_pitch": np.array([0.4, 0.5]),
        }
        np.savez(os.path.join(base_dir, "scenario_2.npz"), **data2)
        np.savez(os.path.join(int_dir, "scenario_2.npz"), **data2)

        yield base_dir, int_dir


@pytest.fixture
def mismatching_dirs():
    """Create two directories with one mismatching array."""
    with tempfile.TemporaryDirectory() as base_dir, \
         tempfile.TemporaryDirectory() as int_dir:
        base_data = {
            "gen_torque": np.array([1.0, 2.0, 3.0]),
            "bld_pitch": np.array([0.1, 0.2, 0.3]),
        }
        int_data = {
            "gen_torque": np.array([1.0, 2.0, 3.0]),  # identical
            "bld_pitch": np.array([0.1, 0.2, 0.4]),   # differs at index 2
        }
        np.savez(os.path.join(base_dir, "scenario_1.npz"), **base_data)
        np.savez(os.path.join(int_dir, "scenario_1.npz"), **int_data)

        yield base_dir, int_dir


def test_identical_comparison(identical_dirs):
    """All arrays match → pass."""
    base_dir, int_dir = identical_dirs
    result = compare_arrays(base_dir, int_dir)

    assert result.passed
    assert result.total_values == 13  # scenario_1: 3+3+3=9, scenario_2: 2+2=4
    assert len(result.files) == 2
    assert result.identical_arrays == result.total_arrays


def test_mismatching_comparison(mismatching_dirs):
    """One array differs → fail."""
    base_dir, int_dir = mismatching_dirs
    result = compare_arrays(base_dir, int_dir)

    assert not result.passed
    assert len(result.files) == 1

    fr = result.files[0]
    assert not fr.passed

    # Find the mismatching array
    mismatch = [a for a in fr.arrays if not a.identical]
    assert len(mismatch) == 1
    assert mismatch[0].name == "bld_pitch"
    assert mismatch[0].max_diff == pytest.approx(0.1)
    assert mismatch[0].max_diff_index == 2


def test_tolerance_comparison(mismatching_dirs):
    """Within tolerance → pass."""
    base_dir, int_dir = mismatching_dirs
    result = compare_arrays(base_dir, int_dir, tolerance=0.2)
    assert result.passed


def test_missing_integrated_file(identical_dirs):
    """Missing integrated file → fail."""
    base_dir, int_dir = identical_dirs
    # Add an extra baseline file with no integrated counterpart
    extra = {"data": np.array([1.0])}
    np.savez(os.path.join(base_dir, "scenario_99.npz"), **extra)

    result = compare_arrays(base_dir, int_dir)
    assert not result.passed


def test_missing_baseline_dir():
    """Non-existent baseline dir → error."""
    with pytest.raises(FileNotFoundError):
        compare_arrays("/nonexistent/baseline", "/nonexistent/integrated")


def test_empty_baseline_dir():
    """Empty baseline dir → error."""
    with tempfile.TemporaryDirectory() as empty_dir, \
         tempfile.TemporaryDirectory() as int_dir:
        with pytest.raises(FileNotFoundError):
            compare_arrays(empty_dir, int_dir)


def test_format_result_identical(identical_dirs):
    """Format output for identical comparison."""
    base_dir, int_dir = identical_dirs
    result = compare_arrays(base_dir, int_dir)
    text = format_result(result)

    assert "IDENTICAL" in text
    assert "✓" in text
    assert "MISMATCH" not in text


def test_format_result_mismatch(mismatching_dirs):
    """Format output for mismatching comparison."""
    base_dir, int_dir = mismatching_dirs
    result = compare_arrays(base_dir, int_dir)
    text = format_result(result)

    assert "MISMATCH" in text
    assert "✗" in text
    assert "bld_pitch" in text
