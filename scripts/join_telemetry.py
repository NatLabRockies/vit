#!/usr/bin/env python3
"""
Join VIT's cycle log against Claude Code session transcripts to produce
per-function cost.

    join_telemetry.py --cycle-log .vit/cycle_log.jsonl \
                      --transcripts ~/.claude/projects/<key>/ \
                      --out analysis/replication/per_function.csv

Writes the CSV and prints a summary including the unattributed fraction.
The join logic lives in vit/telemetry.py so it is importable and tested;
this is the command-line wrapper.

Read the module docstring of vit/telemetry.py before trusting any number
this produces. In particular: transcript usage records are deduplicated by
requestId, without which every count is roughly doubled.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vit import telemetry  # noqa: E402


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
                      "integrated_unexercised", "abandoned"}


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
    is supplied externally via --dispositions. See apply_dispositions().
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
    # A __name__ bracket is a phase by convention (see the runbook), whatever
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--cycle-log", required=True)
    parser.add_argument("--transcripts", required=True,
                        help="Directory of session .jsonl transcripts, or a single file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dispositions", default=None,
                        help="JSON map {function: {status, reason}} overriding "
                             "derived status for functions deliberately not "
                             "mirror-translated. See load_dispositions().")
    args = parser.parse_args(argv)

    dispositions = load_dispositions(args.dispositions)

    events = telemetry.read_cycle_log(args.cycle_log)
    if not events:
        print(f"No cycle events in {args.cycle_log}", file=sys.stderr)
        return 1

    transcripts = Path(args.transcripts)
    paths = sorted(transcripts.glob("*.jsonl")) if transcripts.is_dir() else [transcripts]
    usage = telemetry.read_usage_records(paths)

    intervals = telemetry.build_intervals(events)
    spans = telemetry.build_spans(intervals)
    per_function, buckets = telemetry.attribute(usage, spans)

    by_function = {}
    for interval in intervals:
        if interval["function"]:
            by_function.setdefault(interval["function"], []).append(interval)

    unseen = sorted(set(dispositions) - {s["function"] for s in spans})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for span in spans:
            name = span["function"]
            owned = by_function.get(name, [])
            totals = per_function[name]
            row = {
                "function": name,
                "status": _resolve_status(name, dispositions, status_for(owned)),
                "t_total_s": round(
                    (span["end"] - span["start"]).total_seconds(), 3),
                "verify_attempts": attempts_to_first_pass(owned, "verify"),
                "integrate_attempts": attempts_to_first_pass(owned, "integrate"),
                "verify_runs": total_runs(owned, "verify"),
                "integrate_runs": total_runs(owned, "integrate"),
                "tok_total_uncached": telemetry.total_uncached(totals),
                "usage_records": totals["records"],
                "model": ";".join(sorted(totals.get("models") or [])),
                "interleaved_with": ";".join(sorted(set(span["overlaps"]))),
            }
            for step in STEP_COMMANDS:
                seconds = sum(i["duration_s"] or 0 for i in owned if i["command"] == step)
                row[f"t_{step}_s"] = round(seconds, 3)
            for key, _ in telemetry.USAGE_FIELDS:
                row[key] = totals[key]
            writer.writerow(row)

    attributed = sum(telemetry.total_uncached(t) for t in per_function.values())
    overhead = telemetry.total_uncached(buckets["session_overhead"])
    boundary = telemetry.total_uncached(buckets["boundary"])
    orphan = telemetry.total_uncached(buckets["unattributed"])
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


if __name__ == "__main__":
    sys.exit(main())
