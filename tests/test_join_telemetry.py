"""
Tests for the telemetry joiner's row derivation.

The join primitives live in vit/telemetry.py and are tested there. What lives
only in scripts/join_telemetry.py is how a span becomes a CSV row: its status,
and the two different ways of counting command invocations. Both produced wrong
numbers in the replication run before these tests existed, which is why they
exist.
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "join_telemetry", os.path.join(ROOT, "scripts", "join_telemetry.py"))
join = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(join)


def interval(command, exit_code=0, unclosed=False):
    return {"command": command, "exit_code": exit_code, "unclosed": unclosed}


class TestStatus:

    def test_phase_bracket_is_not_a_failure(self):
        """__analysis__ and __plan__ have no verify or integrate step and can
        never reach one. Calling them 'failed' puts phantom failures into any
        failure rate computed from the table."""
        assert join.status_for([interval("analyze")]) == "phase"
        assert join.status_for([interval("plan")]) == "phase"

    def test_integrated_beats_verified(self):
        assert join.status_for(
            [interval("verify"), interval("integrate")]) == "integrated"

    def test_verified_without_integrate(self):
        assert join.status_for([interval("extract"), interval("verify")]) == "verified"

    def test_unclosed_cycle_is_in_progress_not_failed(self):
        """Mid-run joins are routine; a function still running is not a failure."""
        assert join.status_for(
            [interval("extract"), interval("verify", exit_code=None, unclosed=True)]
        ) == "in_progress"

    def test_real_failure(self):
        assert join.status_for(
            [interval("extract"), interval("verify", exit_code=1)]) == "failed"

    def test_no_intervals(self):
        assert join.status_for([]) == "unknown"


class TestCounts:

    def test_attempts_stops_at_first_pass(self):
        rows = [interval("verify", 1), interval("verify", 1), interval("verify", 0)]
        assert join.attempts_to_first_pass(rows, "verify") == 3

    def test_attempts_ignores_reruns_after_a_pass(self):
        """A command re-run after it already succeeded is not an attempt at
        passing. This is the saturate case: integrate passed, then ran again to
        regenerate an artifact once a tool bug was fixed."""
        rows = [interval("integrate", 0), interval("integrate", 0)]
        assert join.attempts_to_first_pass(rows, "integrate") == 1
        assert join.total_runs(rows, "integrate") == 2

    def test_attempts_counts_all_when_none_pass(self):
        rows = [interval("verify", 1), interval("verify", 1)]
        assert join.attempts_to_first_pass(rows, "verify") == 2
        assert join.total_runs(rows, "verify") == 2

    def test_counts_are_per_command(self):
        rows = [interval("verify", 0), interval("integrate", 0), interval("regress", 0)]
        assert join.total_runs(rows, "verify") == 1
        assert join.total_runs(rows, "extract") == 0


class TestColumns:

    def test_both_count_columns_present(self):
        for column in ("verify_attempts", "verify_runs",
                       "integrate_attempts", "integrate_runs"):
            assert column in join.COLUMNS

    def test_every_step_has_a_time_column(self):
        for step in join.STEP_COMMANDS:
            assert f"t_{step}_s" in join.COLUMNS


class TestDispositions:
    """A disposition is a judgement no exit code can carry.

    The case it exists for: a function whose only purpose is to work around a
    limitation the target language does not have. Counting it as `failed`
    understates the method and overstates the failure rate. See DECISIONS.md,
    AddToList (#25).
    """

    def _write(self, tmp_path, payload):
        import json
        p = tmp_path / "dispositions.json"
        p.write_text(json.dumps(payload))
        return str(p)

    def test_no_path_returns_empty(self):
        load_dispositions = join.load_dispositions
        assert load_dispositions(None) == {}

    def test_loads_status_and_reason(self, tmp_path):
        load_dispositions = join.load_dispositions
        path = self._write(tmp_path, {
            "AddToList": {"status": "out_of_scope", "reason": "std::vector"}
        })
        loaded = load_dispositions(path)
        assert loaded["AddToList"]["status"] == "out_of_scope"
        assert loaded["AddToList"]["reason"] == "std::vector"

    def test_rejects_unknown_status(self, tmp_path):
        load_dispositions = join.load_dispositions
        path = self._write(tmp_path, {
            "AddToList": {"status": "skipped", "reason": "because"}
        })
        with pytest.raises(ValueError, match="expected one of"):
            load_dispositions(path)

    def test_requires_a_reason(self, tmp_path):
        """A disposition without a stated reason is not reviewable."""
        load_dispositions = join.load_dispositions
        path = self._write(tmp_path, {"AddToList": {"status": "out_of_scope"}})
        with pytest.raises(ValueError, match="no reason"):
            load_dispositions(path)

    def test_deferred_is_a_valid_status(self, tmp_path):
        """`deferred` and `out_of_scope` must not be conflated: one is remaining
        work, the other never will be. See DECISIONS.md, ReadEmptyLine (#27)."""
        load_dispositions = join.load_dispositions
        path = self._write(tmp_path, {
            "ReadEmptyLine": {"status": "deferred",
                              "reason": "Fortran unit I/O; translate with its caller"}
        })
        assert load_dispositions(path)["ReadEmptyLine"]["status"] == "deferred"
