"""
Tests for the telemetry joiner's row derivation.

The join primitives are tested in test_telemetry.py. These cover how a span
becomes a CSV row (vit/telemetry.py, "Report"): its status, and the two
different ways of counting command invocations. Both produced wrong numbers in
the replication run before these tests existed, which is why they exist.
"""

import csv
import json

import pytest

from vit import telemetry


def interval(command, exit_code=0, unclosed=False):
    return {"command": command, "exit_code": exit_code, "unclosed": unclosed}


class TestStatus:

    def test_phase_bracket_is_not_a_failure(self):
        """__analysis__ and __plan__ have no verify or integrate step and can
        never reach one. Calling them 'failed' puts phantom failures into any
        failure rate computed from the table."""
        assert telemetry.status_for([interval("analyze")]) == "phase"
        assert telemetry.status_for([interval("plan")]) == "phase"

    def test_integrated_beats_verified(self):
        assert telemetry.status_for(
            [interval("verify"), interval("integrate")]) == "integrated"

    def test_verified_without_integrate(self):
        assert telemetry.status_for([interval("extract"), interval("verify")]) == "verified"

    def test_unclosed_cycle_is_in_progress_not_failed(self):
        """Mid-run joins are routine; a function still running is not a failure."""
        assert telemetry.status_for(
            [interval("extract"), interval("verify", exit_code=None, unclosed=True)]
        ) == "in_progress"

    def test_real_failure(self):
        assert telemetry.status_for(
            [interval("extract"), interval("verify", exit_code=1)]) == "failed"

    def test_no_intervals(self):
        assert telemetry.status_for([]) == "unknown"


class TestCounts:

    def test_attempts_stops_at_first_pass(self):
        rows = [interval("verify", 1), interval("verify", 1), interval("verify", 0)]
        assert telemetry.attempts_to_first_pass(rows, "verify") == 3

    def test_attempts_ignores_reruns_after_a_pass(self):
        """A command re-run after it already succeeded is not an attempt at
        passing. This is the saturate case: integrate passed, then ran again to
        regenerate an artifact once a tool bug was fixed."""
        rows = [interval("integrate", 0), interval("integrate", 0)]
        assert telemetry.attempts_to_first_pass(rows, "integrate") == 1
        assert telemetry.total_runs(rows, "integrate") == 2

    def test_attempts_counts_all_when_none_pass(self):
        rows = [interval("verify", 1), interval("verify", 1)]
        assert telemetry.attempts_to_first_pass(rows, "verify") == 2
        assert telemetry.total_runs(rows, "verify") == 2

    def test_counts_are_per_command(self):
        rows = [interval("verify", 0), interval("integrate", 0), interval("regress", 0)]
        assert telemetry.total_runs(rows, "verify") == 1
        assert telemetry.total_runs(rows, "extract") == 0


class TestColumns:

    def test_both_count_columns_present(self):
        for column in ("verify_attempts", "verify_runs",
                       "integrate_attempts", "integrate_runs"):
            assert column in telemetry.COLUMNS

    def test_every_step_has_a_time_column(self):
        for step in telemetry.STEP_COMMANDS:
            assert f"t_{step}_s" in telemetry.COLUMNS


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
        load_dispositions = telemetry.load_dispositions
        assert load_dispositions(None) == {}

    def test_loads_status_and_reason(self, tmp_path):
        load_dispositions = telemetry.load_dispositions
        path = self._write(tmp_path, {
            "AddToList": {"status": "out_of_scope", "reason": "std::vector"}
        })
        loaded = load_dispositions(path)
        assert loaded["AddToList"]["status"] == "out_of_scope"
        assert loaded["AddToList"]["reason"] == "std::vector"

    def test_rejects_unknown_status(self, tmp_path):
        load_dispositions = telemetry.load_dispositions
        path = self._write(tmp_path, {
            "AddToList": {"status": "skipped", "reason": "because"}
        })
        with pytest.raises(ValueError, match="expected one of"):
            telemetry.load_dispositions(path)

    def test_requires_a_reason(self, tmp_path):
        """A disposition without a stated reason is not reviewable."""
        load_dispositions = telemetry.load_dispositions
        path = self._write(tmp_path, {"AddToList": {"status": "out_of_scope"}})
        with pytest.raises(ValueError, match="no reason"):
            telemetry.load_dispositions(path)

    def test_deferred_is_a_valid_status(self, tmp_path):
        """`deferred` and `out_of_scope` must not be conflated: one is remaining
        work, the other never will be. See DECISIONS.md, ReadEmptyLine (#27)."""
        load_dispositions = telemetry.load_dispositions
        path = self._write(tmp_path, {
            "ReadEmptyLine": {"status": "deferred",
                              "reason": "Fortran unit I/O; translate with its caller"}
        })
        assert load_dispositions(path)["ReadEmptyLine"]["status"] == "deferred"


class TestPhaseBrackets:
    def test_dunder_name_is_a_phase_even_with_step_commands(self):
        """__gate_repair__ re-ran `regress` for every completed function. Without
        this it derived as `failed` and would have entered a failure rate."""
        intervals = [
            {"command": "regress", "exit_code": 0, "unclosed": False,
             "function": "__gate_repair__"},
        ]
        assert telemetry.status_for(intervals) == "phase"

    def test_ordinary_name_with_only_regress_is_still_failed(self):
        intervals = [
            {"command": "regress", "exit_code": 0, "unclosed": False,
             "function": "saturate"},
        ]
        assert telemetry.status_for(intervals) == "failed"


class TestIntegratedUnexercisedDisposition:
    """A green gate over code no scenario runs is not the same result as a
    green gate over code every scenario runs, and one column cannot mean both."""

    def _write(self, tmp_path, body):
        p = tmp_path / "dispositions.json"
        p.write_text(json.dumps(body))
        return str(p)

    def test_accepted(self, tmp_path):
        path = self._write(tmp_path, {
            "UpdateZeroMQ": {
                "status": "integrated_unexercised",
                "reason": "ZMQ_Mode is 0 in all 14 DISCON*.IN; both call sites are gated on it.",
            }
        })
        got = telemetry.load_dispositions(path)
        assert got["UpdateZeroMQ"]["status"] == "integrated_unexercised"

    def test_still_requires_a_reason(self, tmp_path):
        path = self._write(tmp_path, {"UpdateZeroMQ": {"status": "integrated_unexercised"}})
        with pytest.raises(ValueError, match="no reason"):
            telemetry.load_dispositions(path)

    def test_unknown_status_still_rejected(self, tmp_path):
        path = self._write(tmp_path, {"X": {"status": "unexercised", "reason": "typo"}})
        with pytest.raises(ValueError, match="expected one of"):
            telemetry.load_dispositions(path)


class TestJoinCommand:
    """`python3 -m vit.telemetry join`, end to end: argument parsing, the CSV
    row, and the deduplication the row's token counts depend on."""

    def test_writes_one_row_per_function(self, tmp_path, capsys):
        def event(ts, command, phase, exit_code=None):
            e = {"ts": ts, "function": "saturate", "command": command,
                 "phase": phase, "pid": 1}
            if exit_code is not None:
                e["exit_code"] = exit_code
            return e

        log = tmp_path / "cycle_log.jsonl"
        log.write_text("".join(json.dumps(e) + "\n" for e in [
            event("2026-08-07T10:00:00Z", "verify", "start"),
            event("2026-08-07T10:01:00Z", "verify", "end", exit_code=1),
            event("2026-08-07T10:02:00Z", "verify", "start"),
            event("2026-08-07T10:03:00Z", "verify", "end", exit_code=0),
            event("2026-08-07T10:04:00Z", "integrate", "start"),
            event("2026-08-07T10:05:00Z", "integrate", "end", exit_code=0),
        ]))

        def usage(ts, request_id):
            return {"type": "assistant", "timestamp": ts, "requestId": request_id,
                    "sessionId": "session-1",
                    "message": {"id": f"msg_{request_id}", "model": "m",
                                "usage": {"input_tokens": 10, "output_tokens": 100,
                                          "cache_read_input_tokens": 0,
                                          "cache_creation_input_tokens": 0}}}

        transcripts = tmp_path / "transcripts"
        transcripts.mkdir()
        # The same request twice: one record per content block, as Claude Code
        # writes them. Counted once.
        (transcripts / "s.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
            usage("2026-08-07T10:00:30Z", "r1"),
            usage("2026-08-07T10:00:31Z", "r1"),
            usage("2026-08-07T10:02:30Z", "r2"),
        ]))

        out = tmp_path / "per_function.csv"
        rc = telemetry.main(["join", "--cycle-log", str(log),
                             "--transcripts", str(transcripts), "--out", str(out)])
        assert rc == 0
        rows = list(csv.DictReader(out.open()))
        assert [r["function"] for r in rows] == ["saturate"]
        row = rows[0]
        assert row["status"] == "integrated"
        assert row["verify_attempts"] == "2"
        assert row["verify_runs"] == "2"
        assert row["integrate_attempts"] == "1"
        assert row["tok_out"] == "200"
        assert row["usage_records"] == "2"
        assert "Wrote" in capsys.readouterr().out

    def test_empty_cycle_log_fails(self, tmp_path):
        log = tmp_path / "cycle_log.jsonl"
        log.write_text("")
        rc = telemetry.main(["join", "--cycle-log", str(log),
                             "--transcripts", str(tmp_path), "--out",
                             str(tmp_path / "out.csv")])
        assert rc == 1
