"""
Tests for per-function cycle telemetry.

Covers recording (log_event, the CLI hook, the shell entry point) and joining
(deduplication, span attribution, the unattributed and session_overhead
buckets).

The deduplication test is the load-bearing one. Claude Code transcripts emit
one record per content block, each repeating the whole message's usage; on a
real session, summing them naively inflated every token count by 104%.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit import telemetry


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "cycle_log.jsonl"
    monkeypatch.setenv("VIT_TELEMETRY_LOG", str(path))
    monkeypatch.delenv("VIT_TELEMETRY", raising=False)
    return path


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def usage_record(ts, request_id, out=100, in_=10, cache_read=0, cache_write=0,
                 model="claude-opus-5", compact=False):
    """One transcript line, in the shape Claude Code actually writes."""
    record = {
        "type": "assistant",
        "timestamp": ts,
        "requestId": request_id,
        "uuid": f"{request_id}-{out}-{in_}",
        "sessionId": "session-1",
        "message": {
            "id": f"msg_{request_id}",
            "model": model,
            "usage": {
                "input_tokens": in_,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        },
    }
    if compact:
        record["isCompactSummary"] = True
    return record


def write_transcript(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


# === Recording ===

class TestLogEvent:

    def test_writes_valid_jsonl(self, log_file):
        telemetry.log_event("saturate", "translate", "start")
        telemetry.log_event("saturate", "translate", "end",
                            duration_s=1.5, exit_code=0)
        events = read_events(log_file)
        assert len(events) == 2
        assert events[0]["function"] == "saturate"
        assert events[0]["phase"] == "start"
        assert events[1]["duration_s"] == 1.5
        assert events[1]["exit_code"] == 0
        assert all("ts" in e and "pid" in e for e in events)

    def test_appends_rather_than_truncates(self, log_file):
        for i in range(3):
            telemetry.log_event(f"fn{i}", "verify", "start")
        assert len(read_events(log_file)) == 3

    def test_disabled_by_env_var_writes_nothing(self, log_file, monkeypatch):
        monkeypatch.setenv("VIT_TELEMETRY", "0")
        telemetry.log_event("saturate", "translate", "start")
        assert not log_file.exists()

    def test_never_raises_on_unwritable_path(self, tmp_path, monkeypatch):
        # A directory where the log file should be: open() will fail.
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        monkeypatch.setenv("VIT_TELEMETRY_LOG", str(blocked))
        telemetry.log_event("saturate", "translate", "start")  # must not raise

    def test_timestamp_is_utc_and_parseable(self, log_file):
        telemetry.log_event("saturate", "verify", "start")
        stamp = telemetry.parse_ts(read_events(log_file)[0]["ts"])
        assert stamp is not None and stamp.tzinfo is not None


class TestCliHook:
    """The hook must bracket every path, including failures."""

    def run_cli(self, args, log_path, cwd):
        env = dict(os.environ, VIT_TELEMETRY_LOG=str(log_path))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = root
        return subprocess.run(
            [sys.executable, "-c",
             "from vit.cli import main; import sys; sys.exit(main())"] + args,
            capture_output=True, text=True, env=env, cwd=str(cwd),
        )

    def test_failing_command_still_logs_end_event(self, tmp_path):
        log_path = tmp_path / "cycle_log.jsonl"
        # `parse` on a nonexistent function fails; the end event must exist.
        self.run_cli(["parse", "no_such_function", "-f", "no_such_file.f90"],
                     log_path, tmp_path)
        events = read_events(log_path)
        phases = [e["phase"] for e in events]
        assert "start" in phases and "end" in phases
        end = [e for e in events if e["phase"] == "end"][0]
        assert end["exit_code"] != 0
        assert end["function"] == "no_such_function"
        assert "duration_s" in end

    def test_records_command_and_argv(self, tmp_path):
        log_path = tmp_path / "cycle_log.jsonl"
        self.run_cli(["parse", "saturate", "-f", "missing.f90"], log_path, tmp_path)
        start = [e for e in read_events(log_path) if e["phase"] == "start"][0]
        assert start["command"] == "parse"
        assert "saturate" in start["argv"]


class TestShellEntryPoint:
    """Rebuild and regress are not vit commands; scripts log them this way."""

    def test_module_main_writes_event(self, log_file):
        rc = telemetry.main(["start", "--function", "saturate", "--command", "regress"])
        assert rc == 0
        event = read_events(log_file)[0]
        assert event["command"] == "regress"
        assert event["function"] == "saturate"

    def test_module_main_end_with_exit_code(self, log_file):
        telemetry.main(["end", "--function", "saturate", "--command", "rebuild",
                        "--exit-code", "0", "--duration-s", "2.5"])
        event = read_events(log_file)[0]
        assert event["exit_code"] == 0
        assert event["duration_s"] == 2.5

    def test_invocable_as_subprocess(self, tmp_path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = tmp_path / "cycle_log.jsonl"
        env = dict(os.environ, VIT_TELEMETRY_LOG=str(log_path), PYTHONPATH=root)
        result = subprocess.run(
            [sys.executable, "-m", "vit.telemetry", "start",
             "--function", "saturate", "--command", "regress"],
            capture_output=True, text=True, env=env, cwd=root,
        )
        assert result.returncode == 0, result.stderr
        assert read_events(log_path)[0]["command"] == "regress"


# === Joining ===

class TestReadUsageRecords:

    def test_deduplicates_by_request_id(self, tmp_path):
        """One request written as three content blocks must count once.

        Without this, real transcripts inflate token counts by ~104%.
        """
        transcript = write_transcript(tmp_path / "s.jsonl", [
            usage_record("2026-08-07T10:00:00.100Z", "req-1", out=500),
            usage_record("2026-08-07T10:00:00.200Z", "req-1", out=500),
            usage_record("2026-08-07T10:00:00.300Z", "req-1", out=500),
            usage_record("2026-08-07T10:00:05.000Z", "req-2", out=700),
        ])
        records = telemetry.read_usage_records([transcript])
        assert len(records) == 2
        assert sum(r["_usage"]["output_tokens"] for r in records) == 1200

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text(
            "not json\n"
            + json.dumps(usage_record("2026-08-07T10:00:00Z", "req-1")) + "\n"
            + json.dumps({"type": "user", "timestamp": "2026-08-07T10:00:01Z"}) + "\n"
        )
        assert len(telemetry.read_usage_records([path])) == 1

    def test_missing_transcript_is_not_an_error(self, tmp_path):
        assert telemetry.read_usage_records([tmp_path / "absent.jsonl"]) == []


class TestAttribution:

    def build(self, tmp_path, events, usage_records):
        log_path = tmp_path / "cycle_log.jsonl"
        log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
        transcript = write_transcript(tmp_path / "s.jsonl", usage_records)
        spans = telemetry.build_spans(
            telemetry.build_intervals(telemetry.read_cycle_log(log_path)))
        usage = telemetry.read_usage_records([transcript])
        return telemetry.attribute(usage, spans), spans

    def cycle(self, function, start, end):
        return [
            {"ts": start, "function": function, "command": "translate",
             "phase": "start", "pid": 1},
            {"ts": end, "function": function, "command": "verify",
             "phase": "end", "pid": 1, "exit_code": 0, "duration_s": 1.0},
        ]

    def test_record_inside_span_is_attributed(self, tmp_path):
        (per_function, buckets), _ = self.build(
            tmp_path,
            self.cycle("saturate", "2026-08-07T10:00:00Z", "2026-08-07T10:10:00Z"),
            [usage_record("2026-08-07T10:05:00Z", "req-1", out=500)],
        )
        assert per_function["saturate"]["tok_out"] == 500
        assert buckets["unattributed"]["records"] == 0

    def test_record_between_spans_is_boundary_not_dropped(self, tmp_path):
        """Inter-function work is derived from the gap, not bracketed.

        The joiner already knows both surrounding spans, so a record between
        them is boundary work by construction. Calling it `unattributed`
        implied attribution had failed when it had not.
        """
        events = (self.cycle("saturate", "2026-08-07T10:00:00Z", "2026-08-07T10:10:00Z")
                  + self.cycle("wrap_180", "2026-08-07T11:00:00Z", "2026-08-07T11:10:00Z"))
        (per_function, buckets), _ = self.build(
            tmp_path, events,
            [usage_record("2026-08-07T10:30:00Z", "req-gap", out=900)],
        )
        assert buckets["boundary"]["tok_out"] == 900
        assert buckets["unattributed"]["tok_out"] == 0
        assert per_function["saturate"]["tok_out"] == 0
        assert per_function["wrap_180"]["tok_out"] == 0

    def test_unattributed_only_when_no_spans_exist(self, tmp_path):
        """The remaining meaning of `unattributed`: the caller pointed at
        transcripts from a project with no cycle log, so nothing can be
        placed. That is worth a loud number."""
        (per_function, buckets), _ = self.build(
            tmp_path, [],
            [usage_record("2026-08-07T10:30:00Z", "req-lost", out=900)],
        )
        assert buckets["unattributed"]["tok_out"] == 900
        assert buckets["boundary"]["tok_out"] == 0

    def test_record_before_first_span_is_session_overhead(self, tmp_path):
        (per_function, buckets), _ = self.build(
            tmp_path,
            self.cycle("saturate", "2026-08-07T10:00:00Z", "2026-08-07T10:10:00Z"),
            [usage_record("2026-08-07T09:00:00Z", "req-boot", out=4000)],
        )
        assert buckets["session_overhead"]["tok_out"] == 4000
        assert per_function["saturate"]["tok_out"] == 0

    def test_compaction_is_overhead_even_inside_a_span(self, tmp_path):
        """Compaction burns tokens that belong to no function."""
        (per_function, buckets), _ = self.build(
            tmp_path,
            self.cycle("saturate", "2026-08-07T10:00:00Z", "2026-08-07T10:10:00Z"),
            [usage_record("2026-08-07T10:05:00Z", "req-compact", out=30000, compact=True)],
        )
        assert buckets["session_overhead"]["tok_out"] == 30000
        assert per_function["saturate"]["tok_out"] == 0

    def test_nothing_is_lost(self, tmp_path):
        """Every token lands in exactly one bucket."""
        events = (self.cycle("saturate", "2026-08-07T10:00:00Z", "2026-08-07T10:10:00Z")
                  + self.cycle("wrap_180", "2026-08-07T11:00:00Z", "2026-08-07T11:10:00Z"))
        records = [
            usage_record("2026-08-07T09:00:00Z", "a", out=1),
            usage_record("2026-08-07T10:05:00Z", "b", out=2),
            usage_record("2026-08-07T10:30:00Z", "c", out=4),
            usage_record("2026-08-07T11:05:00Z", "d", out=8),
            usage_record("2026-08-07T12:00:00Z", "e", out=16),
        ]
        (per_function, buckets), _ = self.build(tmp_path, events, records)
        total = (sum(t["tok_out"] for t in per_function.values())
                 + sum(b["tok_out"] for b in buckets.values()))
        assert total == 31
        # 1 before the first span, 2 and 8 inside spans, 4 and 16 in gaps.
        assert buckets["session_overhead"]["tok_out"] == 1
        assert buckets["boundary"]["tok_out"] == 20
        assert buckets["unattributed"]["tok_out"] == 0

    def test_model_is_read_from_the_transcript(self, tmp_path):
        (per_function, _), _ = self.build(
            tmp_path,
            self.cycle("saturate", "2026-08-07T10:00:00Z", "2026-08-07T10:10:00Z"),
            [usage_record("2026-08-07T10:05:00Z", "req-1", model="claude-opus-5")],
        )
        assert per_function["saturate"]["models"] == {"claude-opus-5"}


class TestSpans:

    def test_span_covers_the_gap_between_commands(self, tmp_path):
        """The agent writes C++ between translate and verify. That is the
        function's work and must fall inside its span."""
        events = [
            {"ts": "2026-08-07T10:00:00Z", "function": "saturate",
             "command": "translate", "phase": "start", "pid": 1},
            {"ts": "2026-08-07T10:00:05Z", "function": "saturate",
             "command": "translate", "phase": "end", "pid": 1,
             "exit_code": 0, "duration_s": 5.0},
            {"ts": "2026-08-07T10:20:00Z", "function": "saturate",
             "command": "verify", "phase": "start", "pid": 2},
            {"ts": "2026-08-07T10:21:00Z", "function": "saturate",
             "command": "verify", "phase": "end", "pid": 2,
             "exit_code": 0, "duration_s": 60.0},
        ]
        log_path = tmp_path / "cycle_log.jsonl"
        log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
        spans = telemetry.build_spans(
            telemetry.build_intervals(telemetry.read_cycle_log(log_path)))
        assert len(spans) == 1
        assert (spans[0]["end"] - spans[0]["start"]).total_seconds() == 21 * 60

    def test_overlapping_spans_are_flagged(self, tmp_path):
        """Interleaved work breaks attribution and must be visible."""
        events = [
            {"ts": "2026-08-07T10:00:00Z", "function": "a", "command": "translate",
             "phase": "start", "pid": 1},
            {"ts": "2026-08-07T10:30:00Z", "function": "a", "command": "verify",
             "phase": "end", "pid": 1, "exit_code": 0},
            {"ts": "2026-08-07T10:10:00Z", "function": "b", "command": "translate",
             "phase": "start", "pid": 2},
            {"ts": "2026-08-07T10:40:00Z", "function": "b", "command": "verify",
             "phase": "end", "pid": 2, "exit_code": 0},
        ]
        log_path = tmp_path / "cycle_log.jsonl"
        log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
        spans = telemetry.build_spans(
            telemetry.build_intervals(telemetry.read_cycle_log(log_path)))
        assert {s["function"] for s in spans if s["overlaps"]} == {"a", "b"}

    def test_shell_events_pair_across_processes(self, tmp_path):
        """`python3 -m vit.telemetry` runs start and end as separate processes,
        so their PIDs never agree. Matching on PID alone silently drops every
        rebuild and regress interval, and the per-function totals lose the
        step that dominates the cycle's mechanical cost."""
        events = [
            {"ts": "2026-08-07T10:00:00Z", "function": "saturate",
             "command": "regress", "phase": "start", "pid": 16892},
            {"ts": "2026-08-07T10:00:51Z", "function": "saturate",
             "command": "regress", "phase": "end", "pid": 16893,
             "exit_code": 0, "duration_s": 51.4},
        ]
        log_path = tmp_path / "cycle_log.jsonl"
        log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
        intervals = telemetry.build_intervals(telemetry.read_cycle_log(log_path))
        assert len(intervals) == 1
        assert intervals[0]["unclosed"] is False
        assert intervals[0]["duration_s"] == 51.4

    def test_unclosed_start_is_reported_not_dropped(self, tmp_path):
        events = [{"ts": "2026-08-07T10:00:00Z", "function": "a",
                   "command": "verify", "phase": "start", "pid": 1}]
        log_path = tmp_path / "cycle_log.jsonl"
        log_path.write_text("".join(json.dumps(e) + "\n" for e in events))
        intervals = telemetry.build_intervals(telemetry.read_cycle_log(log_path))
        assert len(intervals) == 1
        assert intervals[0]["unclosed"] is True


class TestTotals:

    def test_total_uncached_excludes_cache_reads(self):
        totals = {"tok_in": 10, "tok_out": 20, "tok_cache_read": 1000,
                  "tok_cache_write": 5, "records": 1}
        assert telemetry.total_uncached(totals) == 35
