"""
Which step of an extraction failed.

Extraction runs in three kinds of process: VIT itself, KGen, and the two
scripts KGen runs through its state Makefile, the build script and the run
script. A failure in any of them used to reach VIT only as KGen's exit status
and whatever text KGen printed, so a failed install read as success, a failed
reconfigure printed nothing, and every other failure looked like a failed
`make` target.

Every step now records how it ended, wherever it runs. Each appends a record
to `.vit/extract/steps.jsonl` in the directory `vit extract` runs in, one
when it starts and one when it ends, and keeps its own output in
`.vit/extract/<step>.log`; KGen's is `kgen_output.log`. The directory is
emptied at the start of each extraction and kept afterwards. `diagnose`
reads the records and names the first step that failed.
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STEPS_DIR = os.path.join(".vit", "extract")
RECORDS = "steps.jsonl"
# KGen's stdout and stderr, as VIT captured them. Not `kgen.log`: KGen writes
# its own log by that name in the directory it runs in.
KGEN_OUTPUT = "kgen_output.log"

# Every step, in the order an extraction runs them.
STEPS = {
    "clean": "kgen.clean_cmd, before KGen",
    "prebuild": "kgen.build_cmd, before KGen, on the uninstrumented source",
    "kgen": "KGen: parse the sources, generate kernel/ and state/, run the capture",
    "copy_kgen_utils": "copy kgen_utils.f90 into the source tree",
    "add_kgen_utils": "add kgen_utils.f90 to the build file",
    "assumed_size": "rewrite assumed-size arrays in the instrumented code",
    "persistence_resets": "apply the kgen.dll_persistence resets",
    "touch_sources": "touch the Fortran sources so the build recompiles them",
    "cross_module": "move cross-module instrumentation into the build target",
    "reconfigure": "re-run CMake's configure step",
    "build": "kgen.build_cmd, on the instrumented source",
    "install": "kgen.install_cmd",
    "remove_resets": "remove the kgen.dll_persistence resets",
    "run": "kgen.run_cmd, with the instrumented program",
    "restore": "restore the build file and re-run CMake's configure step",
}

# The steps of the build script, which KGen's state Makefile runs as its
# build command after copying the instrumented sources into place.
BUILD_SCRIPT_STEPS = ("copy_kgen_utils", "add_kgen_utils", "assumed_size",
                      "persistence_resets", "touch_sources", "cross_module",
                      "reconfigure", "build", "install", "remove_resets")

TAIL_LINES = 25


def steps_dir(working_dir: str = ".") -> str:
    return os.path.abspath(os.path.join(working_dir, STEPS_DIR))


def reset(directory: str) -> None:
    """Empty the record directory, so nothing from an earlier extraction is read."""
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)


def record(directory: str, step: str, status: str, rc: Optional[int] = None,
           log: Optional[str] = None) -> None:
    entry = {"step": step, "status": status, "rc": rc, "log": log or f"{step}.log",
             "time": int(time.time())}
    with open(os.path.join(directory, RECORDS), "a") as f:
        f.write(json.dumps(entry) + "\n")


def run_step(directory: str, step: str, cmd: str) -> int:
    """Run a shell command as one recorded step, its output kept in <step>.log."""
    record(directory, step, "started")
    with open(os.path.join(directory, f"{step}.log"), "w") as out:
        rc = subprocess.run(cmd, shell=True, stdout=out, stderr=subprocess.STDOUT).returncode
    record(directory, step, "ok" if rc == 0 else "failed", rc)
    return rc


def shell_prelude(directory: str) -> list[str]:
    """Bash that defines `vit_step`, the scripts' equivalent of `run_step`.

    `vit_step NAME FUNCTION` runs FUNCTION in a subshell that stops at its
    first failing command, appends its output to NAME.log, records how it
    ended, and stops the script with the step's exit code if it failed. A
    function that runs a command from vit.yaml starts with `set +e`, so the
    command keeps its own exit status, as it did when run directly.
    """
    return [
        f'VIT_STEPS="{directory}"',
        "vit_record() {",
        "    printf '{\"step\": \"%s\", \"status\": \"%s\", \"rc\": %s, \"log\": \"%s.log\", \"time\": %s}\\n' \\",
        '        "$1" "$2" "$3" "$1" "$(date +%s)" >> "$VIT_STEPS/steps.jsonl"',
        "}",
        "vit_step() {",
        '    vit_record "$1" started null',
        '    ( set -e; "$2" ) >> "$VIT_STEPS/$1.log" 2>&1',
        "    local rc=$?",
        '    if [ "$rc" -eq 0 ]; then',
        '        vit_record "$1" ok 0',
        "    else",
        '        vit_record "$1" failed "$rc"',
        '        echo "VIT: step $1 failed with exit code $rc; its output is in $VIT_STEPS/$1.log" >&2',
        '        exit "$rc"',
        "    fi",
        "}",
    ]


def read_records(directory: str) -> list[dict]:
    path = os.path.join(directory, RECORDS)
    if not os.path.exists(path):
        return []
    records = []
    for line in Path(path).read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut short by a killed process
    return records


# make's directory bookkeeping, which can fill a build's last lines.
_MAKE_DIRECTORY = re.compile(r"^g?make(\[\d+\])?: (Entering|Leaving) directory")


def tail(path: str, n: int = TAIL_LINES) -> list[str]:
    if not os.path.exists(path):
        return []
    lines = [l.rstrip() for l in Path(path).read_text(errors="replace").splitlines()
             if not _MAKE_DIRECTORY.match(l)]
    while lines and not lines[-1]:
        lines.pop()
    return lines[-n:]


@dataclass
class Failure:
    """The step an extraction failed at, and what it printed."""
    step: str
    summary: str
    rc: Optional[int] = None
    log: Optional[str] = None
    tail: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return STEPS.get(self.step, self.step)

    def report(self) -> list[str]:
        lines = [f"✗ Extraction failed at step '{self.step}' ({self.description}): {self.summary}"]
        if self.tail:
            lines.append(f"  Last lines of its output ({self.log}):")
            lines.extend(f"    {l}" for l in self.tail)
        elif self.log:
            lines.append(f"  It printed nothing ({self.log}).")
        return lines


def _failure(directory: str, rec: dict, summary: str) -> Failure:
    log = os.path.join(directory, rec.get("log") or f"{rec['step']}.log")
    return Failure(rec["step"], summary, rec.get("rc"), _relative(log), tail(log))


def _relative(path: str) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def diagnose(directory: str, *, kgen_ran: bool = True, kgen_rc: Optional[int] = None,
             generated: bool = True, timeout: Optional[int] = None) -> Optional[Failure]:
    """The first step that failed, or None when every step succeeded.

    Args:
        directory: the record directory
        kgen_ran: whether KGen was started at all
        kgen_rc: KGen's exit status, None if it did not finish
        generated: whether KGen wrote kernel/ and state/Makefile
        timeout: the limit, in seconds, if KGen was stopped for exceeding it
    """
    records = read_records(directory)
    status: dict[str, dict] = {}
    for rec in records:
        status[rec["step"]] = rec

    def is_failed(rec):
        return rec.get("status") == "failed"

    # VIT's own steps before KGen.
    for step in ("clean", "prebuild"):
        rec = status.get(step)
        if rec and is_failed(rec):
            return _failure(directory, rec, f"exit code {rec['rc']}; KGen was not run")
    if not kgen_ran:
        return None

    kgen_log = os.path.join(directory, KGEN_OUTPUT)
    if timeout is not None:
        running = [r for r in status.values()
                   if r.get("status") == "started" and r["step"] != "kgen"]
        if running:
            return _failure(directory, running[-1],
                            f"still running when extraction stopped at its {timeout} s limit "
                            f"(kgen.timeout)")
        return Failure("kgen", f"still running when extraction stopped at its {timeout} s limit "
                       f"(kgen.timeout)",
                       None, _relative(kgen_log), tail(kgen_log))

    if not generated:
        return Failure("kgen", f"exit code {kgen_rc}; KGen did not generate kernel/ and state/",
                       kgen_rc, _relative(kgen_log), tail(kgen_log))

    for rec in records:
        if rec["step"] not in ("kgen", "restore") and is_failed(rec):
            return _failure(directory, rec, f"exit code {rec['rc']}")

    run = status.get("run")
    if run is None or run.get("status") != "ok":
        done = [r["step"] for r in records
                if r.get("status") == "ok" and r["step"] in BUILD_SCRIPT_STEPS]
        where = (f"after step '{done[-1]}'" if done
                 else "before the build script started, in its save or replace target")
        return Failure("kgen", f"KGen's state Makefile stopped {where}; the run did not start",
                       kgen_rc, _relative(kgen_log), tail(kgen_log))

    if kgen_rc not in (0, None):
        return Failure("kgen", f"exit code {kgen_rc} after the run",
                       kgen_rc, _relative(kgen_log), tail(kgen_log))
    return None


def failed(directory: str, step: str) -> Optional[Failure]:
    """The failure of one step, if its last record says it failed."""
    for rec in reversed(read_records(directory)):
        if rec["step"] == step:
            if rec.get("status") == "failed":
                return _failure(directory, rec, f"exit code {rec['rc']}")
            return None
    return None


# --------------------------------------------------------------------------
# Every step succeeded and nothing was captured
# --------------------------------------------------------------------------

# What KGen's instrumentation prints after writing each captured call (KGen's
# spelling). It is a string constant in the instrumented call site, so it is
# also how a build product that holds the instrumentation is recognised.
CAPTURE_MESSAGE = "Collected Kernel Input/Ouput state"

_STATE_PATH = re.compile(r'kgen_filepath\([^)]*\),\s*FMT="\(A,I0,A,I0,A,I0\)"\)\s*"([^"]+)"',
                         re.IGNORECASE)


def _started(directory: str, step: str) -> Optional[int]:
    times = [r.get("time") for r in read_records(directory)
             if r["step"] == step and r.get("status") == "started"]
    return times[-1] if times and times[-1] is not None else None


def captures_reported(directory: str) -> int:
    """How many captured calls the instrumented program said it wrote."""
    path = os.path.join(directory, "run.log")
    if not os.path.exists(path):
        return 0
    return Path(path).read_text(errors="replace").count(CAPTURE_MESSAGE)


def state_file_prefix(state_dir: str) -> Optional[str]:
    """The path the instrumented code writes captured calls to, as compiled in."""
    if not state_dir or not os.path.isdir(state_dir):
        return None
    for path in sorted(Path(state_dir).glob("*")):
        if path.suffix.lower() not in (".f90", ".f", ".f03", ".f08"):
            continue
        m = _STATE_PATH.search(path.read_text(errors="replace"))
        if m:
            return m.group(1)
    return None


def instrumented_build_products(directory: str, build_dir: str) -> Optional[list[str]]:
    """Files the instrumented build wrote under build_dir that hold the call site.

    None when it cannot be told: no build directory, or no record of when the
    build started. An empty list means the build wrote nothing that contains
    the instrumentation: it did not recompile the instrumented source.
    """
    start = _started(directory, "build")
    if not build_dir or start is None or not os.path.isdir(build_dir):
        return None
    marker = CAPTURE_MESSAGE.encode()
    found = []
    for root, _, files in os.walk(build_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.stat(path).st_mtime < start:
                    continue
                with open(path, "rb") as f:
                    if marker in f.read():
                        found.append(path)
            except OSError:
                continue
    return found


def _reach_markers(kernel_dir: str, function_name: str) -> list[Path]:
    if not kernel_dir or not os.path.isdir(kernel_dir):
        return []
    name = f"{function_name}.reached".lower()
    return [p for p in Path(kernel_dir).iterdir() if p.name.lower() == name]


def clear_reach_marker(kernel_dir: str, function_name: str) -> None:
    """Remove a marker an earlier extraction left, so it cannot be read as this one's."""
    for path in _reach_markers(kernel_dir, function_name):
        path.unlink()


def call_site_reached(kernel_dir: str, state_dir: str, function_name: str) -> Optional[bool]:
    """Whether the run reached the call site, from KGen's first-reach marker.

    The KGen fork's instrumentation writes `<kernel>/<name>.reached` the first
    time the call site runs, whether or not any call falls in the capture
    range. None when the instrumentation in state/ writes no marker (a KGen
    without that change), so its absence is never read as "not reached". The
    marker is removed once read.
    """
    if not state_dir or not os.path.isdir(state_dir):
        return None
    needle = f"/{function_name}.reached\"".lower()
    writes_marker = any(needle in p.read_text(errors="replace").lower()
                        for p in Path(state_dir).iterdir()
                        if p.is_file() and p.suffix.lower() in (".f90", ".f", ".f03", ".f08"))
    markers = _reach_markers(kernel_dir, function_name)
    for path in markers:
        path.unlink()
    return bool(markers) if writes_marker else None


def explain_empty_capture(directory: str, *, build_dir: str = "", state_dir: str = "",
                          invocation: str = "", reached: Optional[bool] = None) -> list[str]:
    """What the evidence says when every step succeeded and nothing was captured.

    In order: the program said it wrote calls (they went somewhere else); no
    file the build wrote holds the instrumented call site (it was not
    recompiled); otherwise the call was not captured by this run, and
    `reached`, when known, says whether the call site ran at all.
    """
    reported = captures_reported(directory)
    if reported:
        prefix = state_file_prefix(state_dir)
        where = f"{prefix}<process>.<thread>.<call>" if prefix else "the path compiled into it"
        return [f"The run reported {reported} captured call(s), and none is in kernel/.",
                f"The instrumented code writes them to {where}.",
                "Look where the program ran, or for a command in kgen.run_cmd that moved them."]

    products = instrumented_build_products(directory, build_dir)
    if products is not None and not products:
        return [f"No file the build wrote under {build_dir} contains the instrumented call site:",
                "the build did not recompile the instrumented source, or the target it builds",
                "does not include it."]

    if invocation and "," in invocation:
        requested = f"among the {invocation.count(',') + 1} calls requested"
    else:
        requested = f"in the capture range {invocation or 'requested'}"
    lines = [f"The instrumented call site was built ({products[0]}), and the run finished."
             if products else "The run finished."]
    if reached is False:
        return lines + ["The run never reached the call site. If the program loads its library from",
                        "somewhere else, it may have run another build: see kgen.install_cmd."]
    if reached is True:
        return lines + [f"The run reached the call site, and none of its calls was {requested}."]
    return lines + ["No call was captured: the run never reached the call site, or none of its",
                    f"calls was {requested}."]
