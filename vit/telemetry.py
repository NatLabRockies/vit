"""
Per-function cycle telemetry.

VIT records when it starts and stops working on a function. The agent's token
usage is recorded separately by Claude Code in its session transcript. Neither
log alone answers "what did translating this function cost"; joined on
timestamps, they do.

    VIT cycle log     [ start saturate ]·····························[ end saturate ]
    transcript             usage   usage   usage   usage   usage
                           └──────── attributed to saturate ────────┘

Two halves live here:

  Recording  — log_event() appends one JSON object per line to the cycle log.
               Called from vit/cli.py around command dispatch, and from shell
               scripts via `python3 -m vit.telemetry` for the two cycle steps
               that are not VIT commands (rebuild, regress).

  Joining    — read_cycle_log(), read_usage_records(), build_spans() and
               attribute() reconstruct per-function cost. scripts/join_telemetry.py
               is a thin CLI over them; they live here so they are importable
               and testable.

Three things that would silently corrupt the numbers, and what is done about
them:

  Duplicate usage records.  The transcript emits one record per content block
  (text, thinking, tool_use), each carrying the *same* message-level usage.
  Measured on a real session, naive summation more than doubles every
  token count. read_usage_records() deduplicates by
  requestId (falling back to message.id).

  Session overhead.  Session startup and context compaction burn large token
  counts belonging to no function. Folded into whichever function was in
  flight, they make that function look arbitrarily expensive. They are counted
  as `session_overhead` and reported on their own line.

  Silent loss.  Usage that falls outside every function span is reported as
  `unattributed`, never dropped. A large unattributed fraction means the
  dataset is not trustworthy, and that has to be visible rather than hidden.

Telemetry never raises. A failure to record must not fail a translation.
"""

import json
import os
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(".vit") / "cycle_log.jsonl"

#: Cycle steps that are not `vit` subcommands. Shell scripts emit these via
#: `python3 -m vit.telemetry`. Without them, rebuild and regression time is
#: missing and every per-function total is quietly too low.
SHELL_COMMANDS = ("rebuild", "regress", "analyze")


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def enabled():
    """Telemetry is on unless VIT_TELEMETRY=0."""
    return os.environ.get("VIT_TELEMETRY", "1") != "0"


def log_path():
    """Cycle log location: $VIT_TELEMETRY_LOG, else .vit/cycle_log.jsonl."""
    override = os.environ.get("VIT_TELEMETRY_LOG")
    return Path(override) if override else Path.cwd() / DEFAULT_LOG


def utcnow():
    """ISO-8601 UTC with microseconds, 'Z'-suffixed to match transcript stamps."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log_event(function, command, phase, **fields):
    """Append one cycle event.

    phase is "start" or "end". On "end", callers pass duration_s and exit_code.
    Extra keyword fields are recorded as given.

    Never raises. A telemetry failure must not fail a translation cycle, so
    every error here is swallowed deliberately.
    """
    if not enabled():
        return
    try:
        record = {
            "ts": utcnow(),
            "function": function,
            "command": command,
            "phase": phase,
            "pid": os.getpid(),
        }
        record.update(fields)
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Joining
# --------------------------------------------------------------------------


def parse_ts(value):
    """Parse an ISO-8601 timestamp to an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_cycle_log(path):
    """Read cycle events, skipping malformed lines rather than failing."""
    events = []
    path = Path(path)
    if not path.exists():
        return events
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            timestamp = parse_ts(record.get("ts"))
            if timestamp is None:
                continue
            record["_ts"] = timestamp
            events.append(record)
    events.sort(key=lambda r: r["_ts"])
    return events


def read_usage_records(paths):
    """Read token-usage records from Claude Code session transcripts.

    Deduplicates by requestId (falling back to message.id). The transcript
    writes one record per content block, each repeating the whole message's
    usage, so without this every count is roughly doubled.

    Returns records sorted by timestamp, each with _ts, _usage, _model,
    _session and _is_overhead.
    """
    by_request = OrderedDict()
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                timestamp = parse_ts(record.get("timestamp"))
                if timestamp is None:
                    continue
                key = (
                    str(path),
                    record.get("requestId")
                    or message.get("id")
                    or record.get("uuid"),
                )
                if key in by_request:
                    continue
                by_request[key] = {
                    "_ts": timestamp,
                    "_usage": usage,
                    "_model": message.get("model"),
                    "_session": record.get("sessionId") or path.stem,
                    "_is_overhead": bool(
                        record.get("isCompactSummary") or record.get("compactMetadata")
                    ),
                }
    records = list(by_request.values())
    records.sort(key=lambda r: r["_ts"])
    return records


def build_intervals(events):
    """Pair start/end events into command intervals.

    Matched first on (pid, function, command) so concurrent VIT invocations do
    not cross-pair, then on (function, command) alone. The fallback is required,
    not cosmetic: shell scripts emit start and end through two separate
    `python3 -m vit.telemetry` processes, so their PIDs never agree and a
    PID-only match silently drops every rebuild and regress interval.

    A start with no end is returned with end=None and is reported rather than
    dropped.
    """
    open_starts = {}
    intervals = []

    def take_start(event):
        exact = (event.get("pid"), event.get("function"), event.get("command"))
        pending = open_starts.get(exact)
        if pending:
            return pending.pop(0)
        for key, waiting in open_starts.items():
            if waiting and key[1:] == exact[1:]:
                return waiting.pop(0)
        return None

    for event in events:
        key = (event.get("pid"), event.get("function"), event.get("command"))
        if event.get("phase") == "start":
            open_starts.setdefault(key, []).append(event)
        elif event.get("phase") == "end":
            start = take_start(event)
            intervals.append(
                {
                    "function": event.get("function"),
                    "command": event.get("command"),
                    "start": start["_ts"] if start else event["_ts"],
                    "end": event["_ts"],
                    "duration_s": event.get(
                        "duration_s",
                        (event["_ts"] - start["_ts"]).total_seconds() if start else None,
                    ),
                    "exit_code": event.get("exit_code"),
                    "unclosed": start is None,
                }
            )
    for pending in open_starts.values():
        for start in pending:
            intervals.append(
                {
                    "function": start.get("function"),
                    "command": start.get("command"),
                    "start": start["_ts"],
                    "end": None,
                    "duration_s": None,
                    "exit_code": None,
                    "unclosed": True,
                }
            )
    intervals.sort(key=lambda i: i["start"])
    return intervals


def build_spans(intervals):
    """Collapse command intervals into one span per function.

    A function's span runs from its first command start to its last command
    end. Attribution uses spans, not individual commands, because the agent
    writes the C++ *between* `vit translate` and `vit verify` and that work
    belongs to the function.

    Functions whose spans overlap are marked; overlap means work interleaved,
    which breaks attribution and makes those functions unusable as data points.
    """
    spans = OrderedDict()
    for interval in intervals:
        name = interval["function"]
        if not name:
            continue
        end = interval["end"] or interval["start"]
        if name not in spans:
            spans[name] = {"function": name, "start": interval["start"], "end": end}
        else:
            spans[name]["start"] = min(spans[name]["start"], interval["start"])
            spans[name]["end"] = max(spans[name]["end"], end)
    ordered = sorted(spans.values(), key=lambda s: s["start"])
    for span in ordered:
        span["overlaps"] = []
    for i, span in enumerate(ordered):
        for other in ordered[i + 1:]:
            if other["start"] < span["end"] and span["start"] < other["end"]:
                span["overlaps"].append(other["function"])
                other["overlaps"].append(span["function"])
    return ordered


USAGE_FIELDS = (
    ("tok_in", "input_tokens"),
    ("tok_out", "output_tokens"),
    ("tok_cache_read", "cache_read_input_tokens"),
    ("tok_cache_write", "cache_creation_input_tokens"),
)


def _blank_totals():
    totals = {name: 0 for name, _ in USAGE_FIELDS}
    totals["records"] = 0
    return totals


def _add_usage(totals, usage):
    for name, key in USAGE_FIELDS:
        try:
            totals[name] += int(usage.get(key) or 0)
        except (TypeError, ValueError):
            pass
    totals["records"] += 1


def attribute(usage_records, spans):
    """Assign each usage record to a function span, or to a bucket.

    Every record lands somewhere. Nothing is dropped:

      <function>        inside that function's span
      session_overhead  compaction records, and everything before the first
                        span in a session (session startup)
      boundary          between one function's last command and the next
                        function's first: committing, recording findings,
                        reporting, deciding what is next
      unattributed      no spans exist at all, so nothing can be placed

    `boundary` is derived, not bracketed. A record in the gap between two
    spans is inter-function work by construction, and the joiner already
    knows both spans, so nothing needs to log it. Calling it `unattributed`
    was wrong: that word means attribution failed, and here it did not.
    Boundary work is real project cost that belongs to no single function,
    which is a different thing from cost we cannot place.

    In practice `unattributed` is now non-empty only when the caller points
    at the wrong transcripts, which is worth a loud number.

    Returns (per_function, buckets). Both map name -> token totals plus a
    record count.
    """
    per_function = OrderedDict((s["function"], _blank_totals()) for s in spans)
    for span in spans:
        per_function[span["function"]]["models"] = set()
    buckets = {
        "session_overhead": _blank_totals(),
        "boundary": _blank_totals(),
        "unattributed": _blank_totals(),
    }
    for bucket in buckets.values():
        bucket["sessions"] = set()

    first_start = min((s["start"] for s in spans), default=None)
    for record in usage_records:
        timestamp = record["_ts"]
        if record["_is_overhead"]:
            _add_usage(buckets["session_overhead"], record["_usage"])
            buckets["session_overhead"]["sessions"].add(record["_session"])
            continue
        holder = None
        for span in spans:
            if span["start"] <= timestamp <= span["end"]:
                holder = span["function"]
                break
        if holder is not None:
            _add_usage(per_function[holder], record["_usage"])
            if record["_model"]:
                per_function[holder]["models"].add(record["_model"])
        elif first_start is None:
            _add_usage(buckets["unattributed"], record["_usage"])
            buckets["unattributed"]["sessions"].add(record["_session"])
        elif timestamp < first_start:
            _add_usage(buckets["session_overhead"], record["_usage"])
            buckets["session_overhead"]["sessions"].add(record["_session"])
        else:
            _add_usage(buckets["boundary"], record["_usage"])
            buckets["boundary"]["sessions"].add(record["_session"])
    return per_function, buckets


def total_uncached(totals):
    """Input + output + cache writes.

    Deliberately not called "billable": cache reads are billed too, at a
    reduced rate. A cost figure has to price all four components.
    """
    return totals["tok_in"] + totals["tok_out"] + totals["tok_cache_write"]


# --------------------------------------------------------------------------
# Shell entry point
# --------------------------------------------------------------------------


def main(argv=None):
    """`python3 -m vit.telemetry start|end --function F --command C [...]`

    Lets shell scripts log the cycle steps that are not VIT commands.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m vit.telemetry",
        description="Emit a VIT cycle telemetry event from a shell script",
    )
    parser.add_argument("phase", choices=["start", "end"])
    parser.add_argument("--function", "-F", default=None)
    parser.add_argument("--command", "-C", required=True,
                        help="Cycle step, e.g. rebuild or regress")
    parser.add_argument("--exit-code", type=int, default=None)
    parser.add_argument("--duration-s", type=float, default=None)
    parser.add_argument("--note", default=None)
    args = parser.parse_args(argv)

    extra = {}
    if args.exit_code is not None:
        extra["exit_code"] = args.exit_code
    if args.duration_s is not None:
        extra["duration_s"] = args.duration_s
    if args.note:
        extra["note"] = args.note

    log_event(args.function, args.command, args.phase, **extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
