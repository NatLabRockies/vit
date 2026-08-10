"""Known-shape translation defects, as checks rather than as prose.

`TRANSLATION_TIPS.md` reached 2,314 lines and was pasted whole into every
`vit translate` prompt. Telling a model about a defect is the weakest rung on
the ladder — *impossible ▸ detected ▸ measured ▸ told* — and the only rung whose
failure is silent: the guidance is read or it is not, and no artifact records
which. This module moves what can be moved to **detected**.

## One registry, three consumers

- `vit check` runs it against (reference Fortran, translation C++) and reports.
- `vit translate` **generates** its guidance section from it — one line per
  active check, nothing hand-written.
- the cycle runs `vit check` before `vit verify`, so a known shape is caught
  before a kernel is built.

The property that earns this its keep: when a check moves from `detected` to
`impossible` — because the tool stopped allowing the defect — its prompt line
disappears **on its own**. The prompt shrinks as the tool improves, nobody
maintains it, and it cannot drift from what is enforced, because it is derived
from what is enforced. Retired entries stay in `RETIRED` with the reason, so the
knowledge is kept without costing prompt tokens.

## Every check ships with its own red test

A check registry with no known-bad is a fails-green surface, which is the
disease being treated. So each `Check` carries a `known_bad` it must flag and a
`known_good` it must not, and `tests/test_checks.py` runs **every** entry
against both. A check added without examples fails the suite; it cannot be
merged silently.

## Precision over coverage, deliberately

A noisy check gets disabled, and then you have neither the check nor the tip.
Where precision is not reachable — `**`-grouping that must not fire on a bare
`x**2`, a `NINT` written as a cast — this module does **not** ship a lossy
approximation. That is left to mutation scoring, which measures rather than
guesses. `PUSHED_TO_MUTATION` records those with the reason, so the gap is
visible instead of merely absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


# --------------------------------------------------------------------- source

def strip_fortran_comments(src: str) -> str:
    """Drop `!` comments, honouring quoted strings so `'a!b'` survives."""
    out = []
    for line in src.splitlines():
        quote = None
        cut = len(line)
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "!":
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def strip_cpp_comments(src: str) -> str:
    """Drop `//` and `/* */`, honouring quoted strings and escapes."""
    out, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "'\"":
            q = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\":
                    i += 2
                    if i - 1 < n:
                        out.append(src[i - 1])
                    continue
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class Context:
    """What a check gets to look at."""
    fortran: str = ""
    cpp: str = ""

    @property
    def f(self) -> str:
        return strip_fortran_comments(self.fortran)

    @property
    def c(self) -> str:
        return strip_cpp_comments(self.cpp)


@dataclass(frozen=True)
class Finding:
    check_id: str
    message: str
    evidence: str = ""

    def __str__(self) -> str:
        tail = f"  [{self.evidence}]" if self.evidence else ""
        return f"{self.check_id}: {self.message}{tail}"


@dataclass(frozen=True)
class Check:
    """One detectable defect shape, its prompt line, and its own red test."""
    id: str
    prompt_line: str                       # what `vit translate` shows, verbatim
    detect: Callable[[Context], list]
    known_bad: Context                     # MUST produce at least one finding
    known_good: Context                    # MUST produce none
    needs_fortran: bool = False            # cross-source: cannot run without a reference


@dataclass(frozen=True)
class Report:
    """What ran, what fired, and — the part that matters — what did NOT run.

    A check that could not run must never be indistinguishable from a check
    that ran and found nothing. That is the whole failure mode this project is
    named after, so `skipped` is reported beside `findings` rather than being
    dropped on the floor.
    """
    findings: list
    ran: list
    skipped: list                          # (check_id, why)

    @property
    def clean(self) -> bool:
        return not self.findings


def _find(cid: str, msg: str, ev: str = "") -> list:
    return [Finding(cid, msg, ev)]


# --------------------------------------------------------------- the checks
#
# Each is grounded in a defect that actually occurred; the comment names it.

def _forbidden_include(ctx: Context) -> list:
    # VIT injects callee declarations itself, and injects a DIFFERENT header
    # depending on the layer -- `vit_kernel_callees.h` while verifying,
    # `vit_translated.h` after integration. A translation naming either one
    # compiles in one layer and fails in the other.
    for header in ("vit_translated.h", "vit_kernel_callees.h"):
        if re.search(rf'#\s*include\s*"{re.escape(header)}"', ctx.c):
            return _find("forbidden-include",
                         f'translation includes "{header}"; VIT injects callee '
                         f"declarations itself, and injects a different header "
                         f"per layer, so this builds in one and fails in the other",
                         header)
    return []


def _toupper_locale(ctx: Context) -> list:
    # Fortran's CHAR(ICHAR(c)-32) guarded by 'a'..'z' is exactly ASCII, always.
    # std::toupper is locale-dependent and undefined on negative char.
    m = re.search(r"\b(?:std::)?(toupper|tolower)\s*\(", ctx.c)
    if m:
        return _find("toupper-locale",
                     f"`{m.group(1)}` is locale-dependent and undefined for negative "
                     f"`char`; Fortran's case conversion is plain ASCII. Compare "
                     f"through `unsigned char` and shift by 32 explicitly",
                     m.group(1))
    return []


def _strftime_locale(ctx: Context) -> list:
    # CurDate builds 'dd-mmm-yyyy' from a hardcoded English month table, and the
    # day is zero-padded from two raw characters. %b is locale-dependent.
    m = re.search(r"\bstrftime\s*\(|%b\b", ctx.c)
    if m:
        return _find("strftime-locale",
                     "`strftime`/`%b` is locale-dependent; the Fortran builds its "
                     "date from a hardcoded English month table with a zero-padded "
                     "day. Reproduce the table",
                     m.group(0).strip("(").strip())
    return []


def _nint_rounding(ctx: Context) -> list:
    # NINT is round-half-away-from-zero. rint/nearbyint are half-to-even under
    # the default rounding mode. They agree on values exact in the source type,
    # which is why this passes a kernel sample and is still wrong.
    if not re.search(r"\bNINT\s*\(", ctx.f, re.IGNORECASE):
        return []
    m = re.search(r"\b(?:std::)?(rint|nearbyint|lrint|llrint)\s*\(", ctx.c)
    if m:
        return _find("nint-rounding",
                     f"Fortran `NINT` is round-half-away-from-zero; `{m.group(1)}` is "
                     f"half-to-even under the default rounding mode. Use `std::lround` "
                     f"(or `std::round`)",
                     m.group(1))
    return []


def _minval_endpoints(ctx: Context) -> list:
    # interp1d bounds-checks with MINVAL/MAXVAL BEFORE it checks that xData is
    # increasing, and independently of it. Substituting the endpoints is
    # identical for well-formed input and different for exactly the malformed
    # input the error path exists to report.
    if not re.search(r"\b(MINVAL|MAXVAL)\s*\(", ctx.f, re.IGNORECASE):
        return []
    # Evidence of an actual scan: a library reduction, or a loop. Deliberately
    # NOT "a comparison against a subscript" -- that pattern matches
    # `x < xData[0]`, which is the endpoint substitution this check exists to
    # catch, so it would silence the check on its own defect.
    scans = (r"min_element|max_element|\bfmin\b|\bfmax\b|"
             r"\bstd::min\b|\bstd::max\b|\bfor\s*\(|\bwhile\s*\(")
    if not re.search(scans, ctx.c):
        return _find("minval-endpoints",
                     "the Fortran scans with `MINVAL`/`MAXVAL` but the translation "
                     "contains no scan — using the first/last element instead is "
                     "identical for sorted input and wrong for exactly the malformed "
                     "input the check exists to catch",
                     "no min/max scan")
    return []


def _save_static(ctx: Context) -> list:
    # A procedure-level SAVE and a local WITH AN INITIALISER are both implicitly
    # saved: initialised once, value survives across calls. The C++ equivalent
    # is a function-local `static`.
    decl = re.compile(
        r"^\s*(?:REAL|INTEGER|LOGICAL|CHARACTER|COMPLEX|TYPE)\b[^:\n]*::\s*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE)
    saved = []
    for m in decl.finditer(ctx.f):
        head = ctx.f[m.start():m.end()]
        if re.search(r"\b(PARAMETER|INTENT)\b", head, re.IGNORECASE):
            continue          # a named constant, or a dummy -- neither is SAVE
        if "=" in m.group(1) and "=>" not in m.group(1):
            saved.append(m.group(1).split("=")[0].strip())
    if re.search(r"^\s*SAVE\b", ctx.f, re.IGNORECASE | re.MULTILINE):
        saved.append("SAVE")
    if saved and not re.search(r"\bstatic\b", ctx.c):
        return _find("save-static",
                     "the Fortran has an implicitly-SAVEd local (a declaration with "
                     "an initialiser, or an explicit `SAVE`) but the translation "
                     "declares no `static`; the value must survive across calls",
                     ", ".join(sorted(set(saved))[:3]))
    return []


def _delimiter_set(ctx: Context) -> list:
    # SCAN/INDEX/VERIFY take a SET of characters, and the set is load-bearing:
    # GetWords leans on >1 / ==1 / ==0 to mean word / skip / stop.
    for m in re.finditer(r"\b(?:SCAN|INDEX|VERIFY)\s*\([^)\n]*?(['\"])(.*?)\1",
                         ctx.f, re.IGNORECASE):
        want = m.group(2)
        if not want or len(want) > 8:
            continue          # a message or a path, not a delimiter set
        literals = re.findall(r'"((?:[^"\\]|\\.)*)"', ctx.c)
        literals += re.findall(r"'((?:[^'\\]|\\.)*)'", ctx.c)
        decoded = [lit.replace("\\\\", "\\").replace('\\"', '"').replace("\\'", "'")
                   for lit in literals]
        if not any(set(want) <= set(d) for d in decoded):
            return _find("delimiter-set",
                         f"the Fortran matches against the character set {want!r}, "
                         f"which no literal in the translation contains; a delimiter "
                         f"set that differs changes which branch runs",
                         repr(want))
    return []


# ------------------------------------------------------------------- registry

REGISTRY: list = [
    Check(
        id="forbidden-include",
        prompt_line=('Do not `#include "vit_translated.h"` or `"vit_kernel_callees.h"`. '
                     'VIT injects callee declarations itself, and injects a different '
                     'header while verifying than after integration.'),
        detect=_forbidden_include,
        known_bad=Context(cpp='#include "vit_translated.h"\ndouble f(void) { return 1.0; }\n'),
        known_good=Context(cpp='#include "vit_types.h"\ndouble f(void) { return 1.0; }\n'),
    ),
    Check(
        id="toupper-locale",
        prompt_line=("Never use `toupper`/`tolower` for a Fortran case conversion — they are "
                     "locale-dependent and undefined on negative `char`. Fortran's "
                     "`CHAR(ICHAR(c)-32)` guarded by `'a'..'z'` is plain ASCII; compare "
                     "through `unsigned char`."),
        detect=_toupper_locale,
        known_bad=Context(cpp="char up(char c) { return std::toupper(c); }\n"),
        known_good=Context(
            cpp="char up(char c) {\n"
                "    unsigned char u = (unsigned char)c;\n"
                "    return (u >= 'a' && u <= 'z') ? (char)(u - 32) : c;\n}\n"),
    ),
    Check(
        id="strftime-locale",
        prompt_line=("Do not use `strftime` or `%b` for a Fortran date string — `%b` is "
                     "locale-dependent. Reproduce the hardcoded English month table, and "
                     "keep the day zero-padded."),
        detect=_strftime_locale,
        known_bad=Context(cpp='void d(char* o) { strftime(o, 12, "%d-%b-%Y", &tm); }\n'),
        known_good=Context(
            cpp='static const char* M[12] = {"Jan","Feb","Mar","Apr","May","Jun",\n'
                '                            "Jul","Aug","Sep","Oct","Nov","Dec"};\n'),
    ),
    Check(
        id="nint-rounding",
        prompt_line=("Fortran `NINT` is round-half-away-from-zero. `rint`/`nearbyint` are "
                     "half-to-even and a cast truncates; all three agree on values exact in "
                     "the source type, so this survives a kernel sample. Use `std::lround`."),
        detect=_nint_rounding,
        known_bad=Context(fortran="i = NINT(x)\n", cpp="int i = (int)std::rint(x);\n"),
        known_good=Context(fortran="i = NINT(x)\n", cpp="int i = (int)std::lround(x);\n"),
        needs_fortran=True,
    ),
    Check(
        id="minval-endpoints",
        prompt_line=("`MINVAL`/`MAXVAL` are not the first/last element. Substituting the "
                     "endpoints is identical for sorted input and differs for exactly the "
                     "malformed input the surrounding check exists to report."),
        detect=_minval_endpoints,
        known_bad=Context(fortran="IF (x < MINVAL(xData)) THEN\n",
                          cpp="if (x < xData[0]) { return 1; }\n"),
        known_good=Context(fortran="IF (x < MINVAL(xData)) THEN\n",
                           cpp="if (x < *std::min_element(xData, xData + n)) { return 1; }\n"),
        needs_fortran=True,
    ),
    Check(
        id="save-static",
        prompt_line=("A Fortran `SAVE` local — including the implicit form, a declaration "
                     "with an initialiser — becomes a function-local `static`. It is "
                     "load-bearing wherever a path reads it without having written it on "
                     "that call."),
        detect=_save_static,
        known_bad=Context(fortran="    REAL(DbKi) :: StartTime = 0.0\n",
                          cpp="void f(void) { double StartTime = 0.0; }\n"),
        known_good=Context(fortran="    REAL(DbKi) :: StartTime = 0.0\n",
                           cpp="void f(void) { static double StartTime = 0.0; }\n"),
        needs_fortran=True,
    ),
    Check(
        id="delimiter-set",
        prompt_line=("`SCAN`/`INDEX`/`VERIFY` take a character SET, and the set is "
                     "load-bearing — `>1`, `==1` and `==0` mean word, skip and stop. "
                     "Carry the Fortran literal across exactly."),
        detect=_delimiter_set,
        known_bad=Context(fortran="Ch = SCAN(Line, ' ,')\n",
                          cpp='int ch = scan_set(line, " ");\n'),
        known_good=Context(fortran="Ch = SCAN(Line, ' ,')\n",
                           cpp='int ch = scan_set(line, " ,");\n'),
        needs_fortran=True,
    ),
]


# Entries whose defect the tool made structurally impossible. They keep their
# reason and cost nothing: no check runs, no prompt line is emitted. This is
# what "the prompt shrinks as the tool improves" looks like in practice.
RETIRED: dict = {
    "shared-header-not-copied":
        "`shared_files` in vit.yaml is copied into the kernel and the source dir by "
        "VIT, and `vit translate` lists the available headers in the scaffold, so a "
        "translation can no longer fail by not knowing a header exists.",
    "callee-signature-guessed":
        "`vit translate` prints each already-translated callee's exact `_c` signature, "
        "including OPTIONAL handling, so there is nothing left to guess.",
}


# Shapes deliberately NOT shipped as checks, because no precise detector exists.
# Recorded so the gap is visible rather than merely absent -- these are rung 3
# (measured, by mutation) not rung 4 (told).
PUSHED_TO_MUTATION: dict = {
    "exponent-grouping":
        "`a*x**2` must become `a * (x*x)`, but a detector cannot tell a correct "
        "`a * x * x` (where the Fortran grouped that way) from an incorrect one "
        "without evaluating precedence against the reference expression.",
    "nint-as-cast":
        "`NINT` written as a bare cast is indistinguishable from a legitimate "
        "truncation of an already-rounded value. The `rint`/`nearbyint` half IS "
        "shipped, because that one is unambiguous.",
    "narrowing-local":
        "a CHARACTER local declared shorter than its argument is a semantic "
        "truncation, but the C++ side has no declaration to compare against — the "
        "buffer length lives in a parameter.",
}


# ---------------------------------------------------------------------- API

def is_unimplemented_scaffold(cpp: str) -> bool:
    """True for a `vit translate` scaffold nobody has written yet.

    Checking one is worse than useless: every finding is about code that is
    absent rather than wrong, and that is the noise that gets a checker turned
    off. Measured across ROSCO's 62 translation files, an untouched scaffold was
    the single source of false signal.
    """
    return bool(re.search(r"//\s*TODO:\s*implement\b", cpp))


def run(fortran: str = "", cpp: str = "", only: Optional[list] = None) -> list:
    """Every finding from every active check, in registry order."""
    return report(fortran, cpp, only).findings


def report(fortran: str = "", cpp: str = "", only: Optional[list] = None) -> Report:
    """Findings, plus an explicit account of which checks could not run."""
    ctx = Context(fortran=fortran, cpp=cpp)
    findings, ran, skipped = [], [], []
    have_fortran = bool(fortran.strip())
    for check in REGISTRY:
        if only and check.id not in only:
            continue
        if check.needs_fortran and not have_fortran:
            skipped.append((check.id, "no reference Fortran supplied (-f)"))
            continue
        findings.extend(check.detect(ctx))
        ran.append(check.id)
    return Report(findings=findings, ran=ran, skipped=skipped)


def prompt_section() -> str:
    """The translate prompt's guidance, DERIVED — never hand-written.

    One line per active check and nothing else. A retired check contributes
    nothing here, which is the whole point: the prompt cannot describe a rule
    that is no longer enforced, and it shrinks without anyone editing it.
    """
    lines = [f"- {c.prompt_line}" for c in REGISTRY]
    return ("## Known-shape defects (generated from VIT's check registry)\n\n"
            "`vit check` enforces each of these mechanically; they are listed so you "
            "can avoid them, not so you can be trusted to.\n\n" + "\n".join(lines))
