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
               attribute() reconstruct per-function cost, and join() writes
               it as one CSV row per function:

                   python3 -m vit.telemetry join --cycle-log .vit/cycle_log.jsonl \
                       --transcripts ~/.claude/projects/<key>/ --out per_function.csv

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

import csv
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
    """Coalesce a function's CONSECUTIVE intervals into one run.

    Attribution uses spans, not individual commands, because the agent writes
    the C++ *between* `vit translate` and `vit verify` and that work belongs to
    the function. So a run absorbs the gaps inside it — but only until another
    function's interval begins, which is where this used to go wrong.

    **It kept one span per function, from its first start to its last end.**
    `__precompact__` ran 09:11:58–09:14:19 and again 11:37:53–11:40:41, and
    min/max turned those 309 seconds into a single 8,923-second window spanning
    almost the whole session between two compactions. Every record inside it was
    then charged to `__precompact__` by `attribute`, and the four functions
    nested within priced at zero with 0 requests — absence rendered as a value,
    since a row of zeros reads as free rather than as missing.

    A function that appears twice therefore gets two runs. Callers that report
    per function must sum them; `join()` does.

    **Interleaving is still detected on the function's whole EXTENT**, first
    start to last end, and not run by run. Splitting runs is what fixes
    attribution, and it would have destroyed this signal if the overlap scan
    had moved with it: two functions whose cycles alternate produce runs that
    never overlap pairwise, so a run-level scan reports no interleaving at all
    for the very case interleaving means. Overlap means work interleaved, which
    breaks attribution and makes those functions unusable as data points, so
    the two questions are answered on the two different shapes.
    """
    runs = []
    for interval in intervals:
        name = interval["function"]
        if not name:
            continue
        end = interval["end"] or interval["start"]
        if runs and runs[-1]["function"] == name:
            runs[-1]["end"] = max(runs[-1]["end"], end)
        else:
            runs.append({"function": name, "start": interval["start"], "end": end})
    ordered = sorted(runs, key=lambda s: s["start"])

    extents = OrderedDict()
    for span in ordered:
        name = span["function"]
        if name in extents:
            extents[name]["start"] = min(extents[name]["start"], span["start"])
            extents[name]["end"] = max(extents[name]["end"], span["end"])
        else:
            extents[name] = {"start": span["start"], "end": span["end"]}

    interleaved = {name: set() for name in extents}
    names = list(extents)
    for i, name in enumerate(names):
        for other in names[i + 1:]:
            a, b = extents[name], extents[other]
            if b["start"] < a["end"] and a["start"] < b["end"]:
                interleaved[name].add(other)
                interleaved[other].add(name)
    for span in ordered:
        span["overlaps"] = sorted(interleaved[span["function"]])
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
        # The INNERMOST containing run, not the first by start time. A nested
        # phase is more specific than whatever encloses it, so a record inside
        # `__alloc_return__` inside `Read_OL_Input` belongs to
        # `__alloc_return__`. Taking the first match charged everything to the
        # outermost span, which is how one phantom window swallowed four
        # functions whole. Shortest wins; ties break to the later start.
        holder = None
        hits = [s for s in spans if s["start"] <= timestamp <= s["end"]]
        if hits:
            holder = min(hits, key=lambda s: ((s["end"] - s["start"]).total_seconds(),
                                              -s["start"].timestamp()))["function"]
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
# Report
# --------------------------------------------------------------------------
#
# How a function's spans become one CSV row: its status, its time per step,
# and two different counts of command invocations. `python3 -m vit.telemetry
# join` writes the table.

STEP_COMMANDS = ("extract", "translate", "verify", "integrate", "rebuild", "regress")

COLUMNS = (
    ["function", "status"]
    + [f"t_{step}_s" for step in STEP_COMMANDS]
    + [
        "t_total_s",
        "verify_attempts",
        "integrate_attempts",
        "verify_runs",
        "integrate_runs",
        "tok_in",
        "tok_out",
        "tok_cache_read",
        "tok_cache_write",
        "tok_total_uncached",
        "usage_records",
        "model",
        "interleaved_with",
    ]
)


VALID_DISPOSITIONS = {"integrated", "out_of_scope", "deferred", "blocked",
                      "integrated_unexercised", "uncalled", "abandoned"}


def load_dispositions(path):
    """Read a {function: {status, reason}} map, or {} if no path was given.

    A disposition is a judgement about a function that no exit code can carry.
    Recognised statuses:

    `out_of_scope`  the function should not be mirrored at all -- its only
                    purpose is to work around a limitation the target language
                    does not have. A hand-rolled growable array in Fortran is
                    std::vector in C++; mirroring it would faithfully reproduce
                    a workaround for a problem that no longer exists.

    `deferred`      the function should be mirrored, but not yet, and not on
                    its own. The case this exists for is Fortran unit I/O: a
                    routine whose body is `READ(Un, ...)` operates on state
                    owned by the Fortran runtime, and a unit number has no
                    meaning on the C++ side. It becomes translatable only
                    together with whoever owns the file. Call-graph depth puts
                    such leaves early; their real dependency runs the other way.

    `blocked`       the function should be mirrored and the tooling cannot. A
                    gap in the method.

    `integrated_unexercised`
                    translated, integrated, and the gate is green -- but no
                    scenario executes the function, so the green says the build
                    works and nothing about the translation. This is NOT the
                    same outcome as `integrated` and must not share a column
                    with it: a function whose 27/27 is backed by bit-identical
                    channel output and one whose 27/27 is backed by nothing
                    both read as "passed" otherwise. Anything reachable only
                    behind a mode flag that is off in every input file, or
                    behind a preprocessor macro this build does not define,
                    belongs here.

    `uncalled`      the function has ZERO call sites in the codebase. Not
                    "no scenario executes it" -- nothing refers to it at all,
                    generically or directly. The test is destructive and
                    decisive: delete the procedure and rebuild. If the link
                    succeeds, nothing called it; if anything did, the compiler
                    says so by name. A pure-C++ module has no counterpart
                    because there is no call to translate, so the procedure is
                    deleted rather than mirrored.

                    Distinct from `integrated_unexercised`, which HAS call
                    sites that the gate never reaches -- a fact about the
                    scenarios, which a different input file could change.
                    `uncalled` is a fact about the source, and no configuration
                    can change it. Distinct from `out_of_scope`, where the call
                    exists and survives in a different C++ form.

    `abandoned`     attempted and given up on.

    `integrated`    a CONFIRMING no-op. It is what the joiner derives anyway,
                    so it changes no number; it exists so that a corrected
                    judgement can carry its reasoning next to the data instead
                    of only in a prose file. Written when a function was
                    previously given a different disposition and measurement
                    overturned it -- `PIIController` was `integrated_unexercised`
                    on the strength of mode values read out of the DISCON*.IN
                    files, which are outputs, until a perturbation probe showed
                    scenario 4 does reach it.

                    Because it is the one override that could HIDE something,
                    it is checked rather than trusted: if the derived status is
                    not already `integrated`, the joiner raises. An override may
                    document a judgement the derivation cannot make; it may not
                    contradict the exit codes.

    All but `integrated_unexercised` would otherwise be derived as `failed`,
    which understates the method and overstates the failure rate -- and, for
    `deferred`, would also misreport remaining work as work that will never
    happen. `integrated_unexercised` is the opposite error: it would otherwise
    be derived as `integrated`, and silently inflate the evidence.
    """
    if not path:
        return {}
    with open(path) as fh:
        raw = json.load(fh)
    out = {}
    for name, entry in raw.items():
        if isinstance(entry, str):
            entry = {"status": entry}
        status = entry.get("status")
        if status not in VALID_DISPOSITIONS:
            raise ValueError(
                f"{path}: disposition for '{name}' is {status!r}; "
                f"expected one of {sorted(VALID_DISPOSITIONS)}"
            )
        if not entry.get("reason"):
            raise ValueError(
                f"{path}: disposition for '{name}' has no reason. "
                f"A disposition without a stated reason is not reviewable."
            )
        out[name] = entry
    return out


def _resolve_status(name, dispositions, derived):
    """Apply a disposition override, refusing one that contradicts the data.

    Every override but `integrated` replaces a derived status the joiner cannot
    reach on its own -- a judgement about the function that exit codes do not
    encode. `integrated` is different: it is what the joiner already derives, so
    it must AGREE. Allowing it to disagree would let a failed cycle be relabelled
    as a pass by editing a JSON file, which is precisely the property this whole
    table exists to not have.
    """
    if name not in dispositions:
        return derived
    status = dispositions[name]["status"]
    if status == "integrated" and derived != "integrated":
        raise ValueError(
            f"dispositions: '{name}' is recorded as 'integrated' but the cycle "
            f"log derives {derived!r}. A confirming disposition may not "
            f"contradict the exit codes -- fix the data or use a different label."
        )
    return status


def status_for(intervals):
    """Derive a function's outcome from its command exit codes.

    Three cases exist beyond pass/fail, and conflating any of them with
    "failed" corrupts a failure rate computed from this table:

    `phase`       the span brackets a measured phase (analysis, planning)
                  rather than a translation cycle. It has no verify or
                  integrate step and can never reach a passing one.
    `in_progress` a cycle command was started and has not ended. Reading a
                  mid-run join is now routine, so this must be visible.
    `failed`      a real cycle that did not reach a passing verify or
                  integrate.

    A fourth case, `out_of_scope`, cannot be derived from exit codes at all --
    it is a judgement about the function, not an outcome of running it -- so it
    is supplied externally via --dispositions. See _resolve_status().
    """
    by_command = {}
    unclosed = False
    for interval in intervals:
        by_command.setdefault(interval["command"], []).append(interval)
        if interval.get("unclosed"):
            unclosed = True

    def passed(command):
        return any(i["exit_code"] == 0 for i in by_command.get(command, []))

    if not by_command:
        return "unknown"
    # A __name__ bracket is a phase by convention, whatever
    # commands it ran. __gate_repair__ re-ran `regress` across every completed
    # function, which made it look like a cycle that never reached a passing
    # verify -- i.e. `failed` -- and would have put a phase bracket into any
    # failure rate computed from this column.
    name = next((i.get("function") for i in intervals if i.get("function")), "")
    if name.startswith("__") and name.endswith("__"):
        return "phase"
    if not any(c in by_command for c in STEP_COMMANDS):
        return "phase"
    if passed("integrate"):
        return "integrated"
    if passed("verify"):
        return "verified"
    if unclosed:
        return "in_progress"
    if by_command:
        return "failed"
    return "unknown"


def attempts_to_first_pass(intervals, command):
    """Invocations of `command` up to and including the first success.

    Counts every invocation if none succeeded, which is the honest answer
    for an abandoned function.

    This is deliberately not the raw invocation count. A command re-run
    *after* it already succeeded — regenerating an artifact once a tool bug
    was fixed, say — is not an attempt at passing. The two numbers differ
    exactly when that happens, and both are reported so the difference is
    recoverable rather than a matter of recollection.
    """
    count = 0
    for interval in intervals:
        if interval["command"] != command:
            continue
        count += 1
        if interval["exit_code"] == 0:
            return count
    return count


def total_runs(intervals, command):
    """Raw invocations of `command`, successful or not."""
    return sum(1 for i in intervals if i["command"] == command)


def join(cycle_log, transcripts, out, dispositions_path=None):
    """Write one CSV row per function and print where the tokens went.

    `python3 -m vit.telemetry join` calls this. Returns an exit code.
    """
    dispositions = load_dispositions(dispositions_path)

    events = read_cycle_log(cycle_log)
    if not events:
        print(f"No cycle events in {cycle_log}", file=sys.stderr)
        return 1

    transcripts = Path(transcripts)
    paths = sorted(transcripts.glob("*.jsonl")) if transcripts.is_dir() else [transcripts]
    usage = read_usage_records(paths)

    intervals = build_intervals(events)
    spans = build_spans(intervals)
    per_function, buckets = attribute(usage, spans)

    by_function = {}
    for interval in intervals:
        if interval["function"]:
            by_function.setdefault(interval["function"], []).append(interval)

    unseen = sorted(set(dispositions) - {s["function"] for s in spans})

    # `build_spans` returns one run per contiguous appearance, so a function
    # that was worked on twice has two. The CSV is one row per FUNCTION, and
    # its elapsed time is the sum of its runs -- not first-start to last-end,
    # which is what produced `__precompact__`'s phantom 8,923-second window
    # from 309 seconds of actual work.
    runs_by_function = OrderedDict()
    for span in spans:
        runs_by_function.setdefault(span["function"], []).append(span)

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for name, runs in runs_by_function.items():
            owned = by_function.get(name, [])
            totals = per_function[name]
            row = {
                "function": name,
                "status": _resolve_status(name, dispositions, status_for(owned)),
                "t_total_s": round(
                    sum((r["end"] - r["start"]).total_seconds() for r in runs), 3),
                "verify_attempts": attempts_to_first_pass(owned, "verify"),
                "integrate_attempts": attempts_to_first_pass(owned, "integrate"),
                "verify_runs": total_runs(owned, "verify"),
                "integrate_runs": total_runs(owned, "integrate"),
                "tok_total_uncached": total_uncached(totals),
                "usage_records": totals["records"],
                "model": ";".join(sorted(totals.get("models") or [])),
                "interleaved_with": ";".join(sorted(
                    {f for r in runs for f in r["overlaps"]})),
            }
            for step in STEP_COMMANDS:
                seconds = sum(i["duration_s"] or 0 for i in owned if i["command"] == step)
                row[f"t_{step}_s"] = round(seconds, 3)
            for key, _ in USAGE_FIELDS:
                row[key] = totals[key]
            writer.writerow(row)

    attributed = sum(total_uncached(t) for t in per_function.values())
    overhead = total_uncached(buckets["session_overhead"])
    boundary = total_uncached(buckets["boundary"])
    orphan = total_uncached(buckets["unattributed"])
    accounted = attributed + overhead + boundary + orphan

    def sessions(bucket):
        count = len(bucket.get("sessions") or ())
        return f", {count} session(s)" if count else ""

    print(f"Wrote {out_path} ({len(spans)} functions, {len(paths)} transcript(s))")
    if dispositions:
        applied = sorted(n for n in dispositions if n not in unseen)
        print(f"  dispositions applied: {len(applied)}"
              + (f" — {', '.join(applied)}" if applied else ""))
        for name in applied:
            print(f"      {name}: {dispositions[name]['status']} — "
                  f"{dispositions[name]['reason']}")
    if unseen:
        # A disposition for a function with no span is almost always a typo in
        # the name, and silently ignoring it would hide the function from the
        # dataset entirely.
        print(f"  WARNING: disposition names not found in the cycle log: "
              f"{', '.join(unseen)}", file=sys.stderr)
    print(f"  attributed        {attributed:>12,}")
    print(f"  session_overhead  {overhead:>12,}  "
          f"({buckets['session_overhead']['records']} records"
          f"{sessions(buckets['session_overhead'])})")
    print(f"  boundary          {boundary:>12,}  "
          f"({buckets['boundary']['records']} records"
          f"{sessions(buckets['boundary'])})   "
          f"inter-function: commit, findings, report")
    if orphan:
        print(f"  unattributed      {orphan:>12,}  "
              f"({buckets['unattributed']['records']} records"
              f"{sessions(buckets['unattributed'])})")
        print("  WARNING: unattributed is non-zero, which means no spans were "
              "found. Check --transcripts points at the right project.")
    if accounted:
        print(f"  boundary fraction: {100.0 * boundary / accounted:.1f}%"
              "   (reported, not a threshold: inter-function work is real "
              "project cost that belongs to no function)")

    interleaved = [s["function"] for s in spans if s["overlaps"]]
    if interleaved:
        print(f"  WARNING: overlapping spans, attribution unreliable for: "
              f"{', '.join(sorted(set(interleaved)))}")
    unclosed = [i for i in intervals if i.get("unclosed")]
    if unclosed:
        print(f"  WARNING: {len(unclosed)} unpaired start/end event(s)")
    return 0


# --------------------------------------------------------------------------
# Shell entry point
# --------------------------------------------------------------------------


def main(argv=None):
    """`python3 -m vit.telemetry start|end|join ...`

    start and end let shell scripts log the cycle steps that are not VIT
    commands. join writes the per-function table.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m vit.telemetry",
        description="Record VIT cycle telemetry from a shell script, or join "
                    "the cycle log against session transcripts",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for phase in ("start", "end"):
        p = sub.add_parser(phase, help=f"Log a cycle {phase} event")
        p.add_argument("--function", "-F", default=None)
        p.add_argument("--command", "-C", required=True,
                       help="Cycle step, e.g. rebuild or regress")
        p.add_argument("--exit-code", type=int, default=None)
        p.add_argument("--duration-s", type=float, default=None)
        p.add_argument("--note", default=None)
    p = sub.add_parser("join", help="Write per-function time and tokens as CSV")
    p.add_argument("--cycle-log", required=True)
    p.add_argument("--transcripts", required=True,
                   help="Directory of session .jsonl transcripts, or a single file")
    p.add_argument("--out", required=True)
    p.add_argument("--dispositions", default=None,
                   help="JSON map {function: {status, reason}} overriding "
                        "derived status for functions deliberately not "
                        "mirror-translated. See load_dispositions().")
    args = parser.parse_args(argv)

    if args.action == "join":
        return join(args.cycle_log, args.transcripts, args.out, args.dispositions)

    extra = {}
    if args.exit_code is not None:
        extra["exit_code"] = args.exit_code
    if args.duration_s is not None:
        extra["duration_s"] = args.duration_s
    if args.note:
        extra["note"] = args.note

    log_event(args.function, args.command, args.action, **extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
