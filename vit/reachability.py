"""Settle "is this called?" by deleting it and rebuilding.

A scenario sweep answers *no input I tried reached it*. That is a statement
about the inputs, and a different configuration file can overturn it. Deleting
the procedure and rebuilding answers *nothing in this program refers to it* —
a statement no configuration can overturn, because it is decided by the linker
rather than sampled by a run.

The reference campaign spent two cycles establishing an `uncalled` claim by
sweeping counters, then got the stronger claim from two rebuilds. Post-mortem
§7.2 calls it the run's most transferable idea, and the same move shows up
elsewhere disguised: *"can this rule set generate the distinguishing input?"*
is decidable offline in seconds, where *"run it and see whether it catches the
defect"* is a sampled proxy for it.

## What a probe can and cannot conclude

- **link fails** → something refers to it. Definitive, and the complaining
  symbols name the callers.
- **link succeeds** → nothing in this build refers to it. Strong, but scoped to
  *this build*: a procedure reached only through a `PROCEDURE` pointer, a
  `BIND(C)` entry point called from outside, or a target excluded from this
  configuration will link cleanly and still be live. Those are reported as
  caveats rather than hidden, because an `uncalled` verdict that quietly ignores
  its own assumptions is the failure mode this project exists to remove.

The probe never leaves the source modified: the original text is restored in a
`finally`, and the restoration is verified before returning.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Probe:
    """The outcome of one deletion probe."""
    function: str
    verdict: str                       # "referenced" | "unreferenced" | "inconclusive"
    detail: str
    symbols: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    static_call_sites: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict in ("referenced", "unreferenced")


# Undefined-symbol diagnostics differ per linker; these cover GNU ld, lld and
# macOS ld. A symbol we cannot parse is still a failed link, so the verdict
# holds even when the name extraction misses.
_UNDEF = [
    re.compile(r"undefined reference to [`'\"]([^'\"`]+)", re.IGNORECASE),
    re.compile(r"undefined symbol:?\s+(\S+)", re.IGNORECASE),
    re.compile(r'symbol not found for architecture.*?[`\'"]([^\'"`]+)', re.IGNORECASE),
    re.compile(r"^\s+_?(\w+)\s*$", re.MULTILINE),          # macOS symbol list
]


def undefined_symbols(build_output: str, function: str) -> list:
    """Undefined symbols the linker complained about, best effort.

    Fortran symbols are mangled — gfortran lowercases and appends `_`, and a
    module procedure becomes `__<module>_MOD_<name>` — so the match is on the
    lowercased name appearing anywhere in the symbol.
    """
    want = function.lower()
    found = []
    for pat in _UNDEF:
        for m in pat.finditer(build_output):
            sym = m.group(1)
            if want in sym.lower() and sym not in found:
                found.append(sym)
    return found


def _find_definition(source: str, function: str) -> Optional[tuple]:
    """(start, end) character offsets of the procedure definition, or None."""
    name = re.escape(function)
    start_pat = re.compile(
        rf"^[ \t]*(?:(?:RECURSIVE|PURE|ELEMENTAL|IMPURE|MODULE)\s+)*"
        rf"(?:[\w()*: ,]*?\s+)?(FUNCTION|SUBROUTINE)\s+{name}\s*(?:\(|$)",
        re.IGNORECASE | re.MULTILINE)
    m = start_pat.search(source)
    if not m:
        return None
    kind = m.group(1).upper()
    end_pat = re.compile(rf"^[ \t]*END\s*{kind}\b[^\n]*$", re.IGNORECASE | re.MULTILINE)
    e = end_pat.search(source, m.end())
    if not e:
        return None
    return m.start(), e.end()


def _comment_out(block: str) -> str:
    """Comment a procedure out rather than cutting it.

    Deleting text shifts every line number after it, and a probe that changes
    line numbers cannot be run mid-cycle against a kernel keyed on them.
    Commenting preserves the line count exactly.
    """
    return "\n".join("!VIT_PROBE " + line for line in block.split("\n"))


def probe(function: str, source_file: str, build_cmd: str, cwd: str = ".",
          source_dir: Optional[str] = None, timeout: int = 1800) -> Probe:
    """Comment the procedure out, rebuild, and report what the linker said."""
    path = Path(source_file)
    if not path.is_file():
        return Probe(function, "inconclusive", f"no such source file: {source_file}")

    original = path.read_text()
    span = _find_definition(original, function)
    if span is None:
        return Probe(function, "inconclusive",
                     f"could not locate a definition of `{function}` in {path.name}")

    # Static call sites are gathered BEFORE the probe, so a link that succeeds
    # can be cross-examined rather than believed on its own.
    sites = []
    if source_dir:
        try:
            from .extract import find_call_sites
            sites = [f"{Path(f).name}:{n}" for f, n, _ in
                     find_call_sites(source_dir, function)]
        except Exception:
            sites = []

    start, end = span
    patched = original[:start] + _comment_out(original[start:end]) + original[end:]

    try:
        path.write_text(patched)
        run = subprocess.run(build_cmd, shell=True, cwd=cwd, timeout=timeout,
                             capture_output=True, text=True)
        output = (run.stdout or "") + (run.stderr or "")
    except subprocess.TimeoutExpired:
        return Probe(function, "inconclusive",
                     f"the build did not finish within {timeout}s")
    finally:
        path.write_text(original)
        # Verified, not assumed: a probe that leaves a procedure commented out
        # would be a catastrophic silent failure.
        if path.read_text() != original:
            raise RuntimeError(
                f"FAILED TO RESTORE {path}. The original text is lost from this "
                f"process; recover it with `git checkout -- {path}` before doing "
                f"anything else.")

    syms = undefined_symbols(output, function)
    if run.returncode != 0:
        if syms:
            return Probe(function, "referenced",
                         f"the link failed with {len(syms)} undefined symbol(s) naming "
                         f"`{function}`, so something refers to it",
                         symbols=syms, static_call_sites=sites)
        return Probe(function, "inconclusive",
                     "the build failed, but no undefined symbol named this procedure — "
                     "the failure is probably unrelated (a syntax error from the probe, "
                     "or a pre-existing break). Check the build output",
                     static_call_sites=sites)

    caveats = [
        "scoped to THIS build: a target excluded from this configuration would "
        "not have complained",
        "a procedure reached only through a PROCEDURE pointer or a generic "
        "interface can link cleanly and still be called",
    ]
    if sites:
        caveats.insert(0,
                       f"{len(sites)} static call site(s) were found by text search "
                       f"({', '.join(sites[:4])}{'...' if len(sites) > 4 else ''}) — "
                       f"if these are real calls, the link should have failed, so "
                       f"reconcile before trusting this verdict")
    return Probe(function, "unreferenced",
                 "the build linked cleanly without it, so nothing in this build "
                 "refers to it",
                 caveats=caveats, static_call_sites=sites)
