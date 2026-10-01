"""
What a kernel's captured calls cover: which lines of the function they run.

A kernel replays the calls KGen captured, so a verification is evidence only
for what those calls exercise. Every kernel used to sample a function's first
twenty calls, the least representative stretch of a run, and nothing said
what they covered (dev note 202608080640); the red test shows the comparison
can fail, not that the calls reach the function's branches.

`measure` builds the kernel's Fortran reference, the original Fortran body
with VIT's repairs (kernel_verify.fortran_reference_files), with gcov
instrumentation, in a copy of the kernel directory, replays the captured
calls, and reads which of the function's lines ran. It is a build of its
own, never the verification's: instrumentation and optimisation level change
gfortran's code generation, and the reference results must not move. It is
built at -O0, so that gcov attributes counts to the lines that ran.

`compare` maps those lines to the source by their text, which KGen keeps
verbatim, and, given the project's coverage file, sets them against the lines
of the function the full run executes.
"""

import difflib
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from . import coverage as cov
from .kernel_verify import _extract_function_body, fortran_reference_files

RESULT_FILE = "kernel_coverage.json"

_STATE_FILE = re.compile(r".+\.\d+\.\d+\.\d+$")
_SKIP = re.compile(r".+\.(o|mod|gcda|gcno|exe)$")
_FLAGS = re.compile(r"^(FC_FLAGS_SET_0\s*\??:?=)(.*)$", re.MULTILINE)
_OPT = re.compile(r"(?<!\S)-O\S*")


def _tail(text: str, n: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def _normalise(line: str) -> str:
    code = line.split("!", 1)[0]
    return re.sub(r"\s+", " ", code).strip().lower()


def measure(kernel_dir: str, sig, fortran_compiler: str = "gfortran", gcov: str = "",
            env: Optional[dict] = None) -> dict:
    """Run the captured calls through the instrumented Fortran reference.

    Returns {"ok": True, "lines": [{"line", "text", "count"}], "calls": n}
    for the executable lines of the function's body in the kernel, or
    {"ok": False, "reason": ...}. The kernel directory is not modified.
    """
    kernel = Path(kernel_dir)
    gcov_path, gcov_note = cov.find_gcov(fortran_compiler, gcov)
    if not gcov_path:
        return {"ok": False, "reason": gcov_note}

    work = Path(tempfile.mkdtemp(prefix="vit_kernel_coverage_"))
    try:
        calls = 0
        for item in kernel.iterdir():
            if not item.is_file() or _SKIP.match(item.name):
                continue
            if _STATE_FILE.match(item.name):
                (work / item.name).symlink_to(item.resolve())
                calls += 1
            else:
                shutil.copy2(item, work / item.name)

        reference = fortran_reference_files(work, sig)
        if reference is None:
            return {"ok": False, "reason": f"the kernel holds no Fortran body of {sig.name} to restore"}
        target, content, makefile = reference
        target.write_text(content)

        def instrument(m):
            flags = _OPT.sub("", m.group(2)).strip()
            return f"{m.group(1)} {flags} -O0 --coverage"
        makefile, n = _FLAGS.subn(instrument, makefile)
        if not n:
            return {"ok": False, "reason": "the kernel Makefile sets no FC_FLAGS_SET_0"}
        (work / "Makefile").write_text(makefile)

        run_env = {**os.environ, **(env or {})}
        build = subprocess.run("make build", shell=True, cwd=work, capture_output=True,
                               text=True, env=run_env)
        if build.returncode:
            return {"ok": False, "reason": "the Fortran reference did not build with coverage:\n"
                    + _tail(build.stdout + build.stderr)}
        run = subprocess.run("./kernel.exe", shell=True, cwd=work, capture_output=True,
                             env=run_env)
        if run.returncode:
            out = (run.stdout + run.stderr).decode("utf-8", errors="replace")
            return {"ok": False, "reason": f"the instrumented kernel exited with code "
                    f"{run.returncode}:\n" + _tail(out)}

        g = subprocess.run([gcov_path, "-j", "-o", ".", target.name], cwd=work,
                           capture_output=True, text=True)
        counts = {}
        # gcov names its output after the source or after the object, by
        # release; the entry naming the target file is what counts.
        for out in work.glob("*.gcov.json.gz"):
            with gzip.open(out, "rt") as f:
                data = json.load(f)
            for entry in data.get("files", []):
                if os.path.basename(entry.get("file", "")) == target.name:
                    for line in entry.get("lines", []):
                        n = line["line_number"]
                        counts[n] = counts.get(n, 0) + line["count"]
        if g.returncode or not counts:
            return {"ok": False, "reason": f"gcov wrote no data for {target.name}:\n"
                    + _tail(g.stdout + g.stderr)}

        body = _extract_function_body(content, sig.name, sig.is_function)
        if body is None:
            return {"ok": False, "reason": f"{sig.name} not found in {target.name}"}
        start, end, _ = body
        text = content.split("\n")
        # The SUBROUTINE/FUNCTION and END statements are left out: gcov counts
        # an END line whether or not the body ran (dev note 202610011349).
        lines = [{"line": n, "text": text[n - 1], "count": counts[n]}
                 for n in range(start + 2, end + 1) if n in counts]
        return {"ok": True, "lines": lines, "calls": calls, "file": target.name}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compare(measured: dict, sig, coverage_file: Optional[str] = None,
            source_dir: Optional[str] = None, case: Optional[str] = None) -> dict:
    """Set the lines the captured calls ran against the function's source.

    With a coverage file, the comparison is with the lines of the function the
    full run executes (in `case`, or in any case), numbered as measured.
    Without one, it is with the function's executable lines in the kernel.
    Returns a summary dict; `report` turns it into lines.
    """
    function_name, source_file = sig.name, sig.source_file
    lines = measured["lines"]
    run = [l for l in lines if l["count"] > 0]
    result = {"function": function_name, "calls": measured["calls"],
              "run": len(run), "executable": len(lines), "basis": "kernel",
              "missed": [(None, l["text"].strip()) for l in lines if l["count"] == 0]}
    if not coverage_file or not os.path.exists(coverage_file) or not source_file:
        return result

    data = cov.load(coverage_file)
    root = cov.coverage_root(data, source_dir or os.path.dirname(source_file))
    rel = os.path.relpath(os.path.realpath(source_file), os.path.realpath(root))
    if rel not in data["files"]:
        result["note"] = f"{os.path.relpath(coverage_file)} does not cover {rel}"
        return result
    text, how = cov.measured_text(data, root, rel)
    if text is None:
        result["note"] = f"the measured text of {rel} is not available"
        return result
    source = text.split("\n")
    body = _extract_function_body(text, function_name, sig.is_function)
    if body is None:
        result["note"] = f"{function_name} not found in {rel} as measured"
        return result
    start, end, _ = body
    span = range(start + 2, end + 1)  # 1-based lines between the statement and END

    cases = data["files"][rel]["test_cases"]
    if case and case not in cases:
        result["note"] = f"{os.path.relpath(coverage_file)} has no case {case} for {rel}"
        return result
    chosen = [case] if case else list(cases)
    full = {n for n in span for c in chosen if cases[c]["lines"].get(str(n), 0) > 0}

    # Kernel lines to source lines, by their text.
    kernel_lines = [l["text"] for l in lines]
    source_lines = [source[n - 1] for n in span]
    matcher = difflib.SequenceMatcher(None, [_normalise(l) for l in kernel_lines],
                                      [_normalise(l) for l in source_lines], autojunk=False)
    to_source = {}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            to_source[a + k] = span[b + k]
    ran = {to_source[i] for i, l in enumerate(lines) if l["count"] > 0 and i in to_source}
    unmatched = sorted(full - set(to_source.values()))

    result.update({
        "basis": "case" if case else "any case",
        "case": case,
        "coverage_file": os.path.relpath(coverage_file),
        "read": how,
        "full_run": len(full - set(unmatched)),
        "run": len((full - set(unmatched)) & ran),
        "missed": [(n, source[n - 1].strip()) for n in sorted(full - ran - set(unmatched))],
        "unmatched": [(n, source[n - 1].strip()) for n in unmatched],
    })
    return result


def report(result: dict) -> list:
    """The lines `vit verify` prints."""
    name, calls = result["function"], result["calls"]
    if result["basis"] == "kernel":
        lines = [f"Kernel coverage: the {calls} captured call(s) run {result['run']} of the "
                 f"{result['executable']} executable lines of {name}."]
        if result.get("note"):
            lines.append(f"  Not compared with the full run: {result['note']}.")
        else:
            lines.append("  No coverage file to compare with the full run (coverage.file).")
        if result["missed"]:
            lines.append("  Lines the captured calls do not run:")
            lines.extend(f"    {text}" for _, text in result["missed"])
        return lines
    where = f"in case {result['case']}" if result.get("case") else "in any case"
    lines = [f"Kernel coverage: the {calls} captured call(s) run {result['run']} of the "
             f"{result['full_run']} lines of {name} that the full run executes {where} "
             f"({result['coverage_file']})."]
    if result["missed"]:
        lines.append("  Lines the full run executes and the captured calls do not "
                     "(numbered as measured):")
        lines.extend(f"    {n}: {text}" for n, text in result["missed"])
    if result.get("unmatched"):
        lines.append(f"  {len(result['unmatched'])} line(s) the full run executes could not be "
                     "matched to the kernel's copy and are not counted.")
    if result.get("read") == "working_tree":
        lines.append("  The measured source was not available; line numbers are the working "
                     "tree's and may not match the coverage.")
    return lines


def summary(result: dict) -> str:
    """One line for the translation record."""
    if result["basis"] == "kernel":
        return f"{result['run']}/{result['executable']} executable lines"
    return f"{result['run']}/{result['full_run']} lines the full run executes"


def save(kernel_dir: str, result: dict) -> None:
    Path(kernel_dir, RESULT_FILE).write_text(json.dumps(result, indent=2) + "\n")
