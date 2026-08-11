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
Where precision is not reachable — currently only a `NINT` written as a bare
cast — this module does **not** ship a lossy approximation. That is left to
mutation scoring, which measures rather than guesses. `PUSHED_TO_MUTATION`
records those with the reason, so the gap is visible instead of merely absent.

## Precision is earned by measuring, not by asserting

Three shapes here were written off as undetectable before anyone ran a detector
over accepted code, and all three ship:

- `exponent-grouping` fired 4 times on 49 accepted translations. The false
  positives fell into exactly two classes — `x*x*x` (an integer power written
  out, which is what gfortran emits anyway) and `2.0*x*x` (scaling by an exact
  power of two, where both associations are bit-identical). Excluding those left
  only genuine regroupings.
- `narrowing-local` fired 4 times, all ordinary scratch buffers, because the
  first detector asked "is there a `CHARACTER(n)` local?" The defect is
  narrower: a local assigned **from a `CHARACTER(*)` dummy**. With that
  condition it is silent on all 89.
- `real-exponent-pow`, `reciprocal-multiply` and `array-section-row` were silent
  on the corpus at the first attempt — but silence had to be distinguished from
  a **dead** detector, so each was also run against the defect as the campaign
  recorded it, and each fired.

That last step is the one worth keeping. *Zero findings* and *cannot produce a
finding* look identical in a report, and only one of them is good news.
"""

from __future__ import annotations

import math
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
    """Blank out `//` and `/* */`, honouring quoted strings and escapes.

    Blanked rather than removed: a finding reports a LINE NUMBER, and deleting
    characters shifts every offset after them. Replacing with spaces keeps the
    text the same length and the newlines in place, so an offset in the stripped
    text is an offset in the original.
    """
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
                out.append(" ")
                i += 1
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            stop = n if end < 0 else end + 2
            out.extend("\n" if src[j] == "\n" else " " for j in range(i, stop))
            i = stop
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_HANDOFF = "--- User-provided C++ implementation ---"


def translator_region(cpp: str) -> str:
    """Only the part a human wrote, not VIT's own integration preamble.

    `vit integrate` prepends a header to the translation — including
    `#include "vit_translated.h"`, which is exactly what a *translator* must not
    write. Checking the whole file therefore flags VIT's output as a translator
    error. Measured: this was 5 of 5 `forbidden-include` hits across AeroDyn's
    40 translations, every one of them VIT's own preamble on a verified,
    integrated function.

    VIT already marks the boundary, so use its marker rather than guessing.
    """
    at = cpp.find(_HANDOFF)
    if at < 0:
        return cpp
    head = cpp[:at + len(_HANDOFF)]
    blanked = "".join("\n" if c == "\n" else " " for c in head)
    return blanked + cpp[at + len(_HANDOFF):]


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
        return strip_cpp_comments(translator_region(self.cpp))


@dataclass(frozen=True)
class Finding:
    check_id: str
    message: str
    evidence: str = ""
    line: int = 0          # 1-based; 0 when the check is about the file as a whole

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        tail = f"  [{self.evidence}]" if self.evidence else ""
        return f"{self.check_id}: {where}{self.message}{tail}"


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


def _find(cid: str, msg: str, ev: str = "", line: int = 0) -> list:
    return [Finding(cid, msg, ev, line)]


def _line_of(text: str, pos: int) -> int:
    """1-based line for an offset. Exact, because stripping preserves offsets."""
    return text.count("\n", 0, pos) + 1


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
    out = []
    for m in re.finditer(r"\b(?:std::)?(toupper|tolower)\s*\(", ctx.c):
        out.extend(_find("toupper-locale",
                     f"`{m.group(1)}` is locale-dependent and undefined for negative "
                     f"`char`; Fortran's case conversion is plain ASCII. Compare "
                          f"through `unsigned char` and shift by 32 explicitly",
                          m.group(1), _line_of(ctx.c, m.start())))
    return out


def _strftime_locale(ctx: Context) -> list:
    # CurDate builds 'dd-mmm-yyyy' from a hardcoded English month table, and the
    # day is zero-padded from two raw characters. %b is locale-dependent.
    #
    # Only LOCALE-DEPENDENT conversion specifiers are flagged. `strftime` itself
    # is not the defect: `"%H:%M:%S"` reproduces Fortran's
    # `CTime(1:2)//':'//CTime(3:4)//':'//CTime(5:6)` exactly and is identical in
    # every locale. Flagging it made one real defect read as two, and a check
    # that cries wolf on correct code is the kind that gets switched off.
    out = []
    for m in re.finditer(r"%([aAbBcpxXZ])\b|%E[cxX]|%O[a-z]", ctx.c):
        spec = m.group(0)
        out.extend(_find("strftime-locale",
                         f"`{spec}` is locale-dependent; the Fortran builds its date "
                         f"from a hardcoded English month table with a zero-padded "
                         f"day. Reproduce the table",
                         spec, _line_of(ctx.c, m.start())))
    return out


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


def _nint_as_cast(ctx: Context) -> list:
    # The other half of `nint-rounding`. That one catches `rint`/`nearbyint`;
    # this catches a bare cast, which TRUNCATES where NINT rounds half away
    # from zero.
    #
    # Deferred once as "indistinguishable from a legitimate truncation of an
    # already-rounded value" -- which was a claim about a ONE-SIDED detector.
    # Anchored on both sides it is precise: the reference must use NINT, and the
    # translation must contain NO rounding call at all. A function mixing NINT
    # and INT has an `lround` somewhere, so it is exempt, and that is the case
    # the ambiguity was really about.
    if not re.search(r"\bNINT\s*\(", ctx.f, re.IGNORECASE):
        return []
    if re.search(r"\b(?:std::)?l?l?round\b|\brint\b|\bnearbyint\b", ctx.c):
        return []
    m = re.search(r"\(\s*(?:int|long|int64_t|long long)\s*\)\s*[A-Za-z_(]", ctx.c)
    if m:
        return _find("nint-as-cast",
                     "the reference uses `NINT` but the translation contains no "
                     "rounding call and casts to an integer type; a cast TRUNCATES "
                     "and `NINT` is round-half-away-from-zero. Use `std::lround`",
                     m.group(0).strip(), _line_of(ctx.c, m.start()))
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


def _exponent_grouping(ctx: Context) -> list:
    # The most expensive defect in this project's history. Fortran `a*x**2` is
    # `a * (x*x)`; C++ `a * x * x` is `(a*x) * x`, and the different intermediate
    # rounding is 1-3 ULP. ComputeTau2 verified 20/20 IDENTICAL in the kernel
    # and diverged catastrophically in the baselines on exactly this.
    #
    # Precise because it is anchored on BOTH sides: the reference must actually
    # use `**`, and the translation must repeat a factor with no parentheses
    # around the pair. A bare `x * x` (no leading `*`) is the correct rendering
    # of `x**2` standing alone and is not matched.
    #
    # EVERY instance is reported, not the first. Measured the hard way: fixing
    # the three flagged lines in ROSCO left three more of the same defect on
    # lines the report never named, including a second one on the very line it
    # did name. A count that understates the work reads as "one line to fix".
    if not re.search(r"\*\*\s*\d", ctx.f):
        return []
    c = ctx.c
    out = []
    # Find each REPEATED FACTOR first, then look back for what multiplies it.
    #
    # Not one combined pattern: `re.finditer` is non-overlapping, so a benign
    # leading match consumes the text and hides a real one behind it. Measured
    # on `2.0 * DT * DT * CornerFreq * CornerFreq` -- the `2.0 * DT * DT` prefix
    # is exempt (scaling by a power of two is exact), and matching it swallowed
    # the `CornerFreq` pair, which is a genuine regrouping.
    for m in re.finditer(r"([A-Za-z_]\w*)\s*\*\s*\1\b", c):
        var = m.group(1)
        # A closing paren counts: `(x0 - 3*x1) * x0 * x0` regroups exactly like
        # `a * x0 * x0`, and requiring an identifier missed it. Found by running
        # the check over the very code it had just been used to fix.
        lead = re.search(r"(\)|[A-Za-z_]\w*|[\d.]+)\s*\*\s*$", c[:m.start()])
        if lead is None:
            continue                      # `x * x` standing alone is correct
        head = lead.group(1)
        if head == var:
            continue                      # `x * x * x` is an integer power
        if _is_power_of_two_literal(head):
            continue                      # exact scaling; both associations agree
        out.extend(_find("exponent-grouping",
                         f"the reference uses `**` and the translation multiplies "
                         f"`{var}` twice without grouping them; Fortran's `a*x**2` "
                         f"is `a * (x*x)` but C++ `a * x * x` is `(a*x) * x`. "
                         f"Parenthesise the power",
                         f"{head} * {var} * {var}", _line_of(c, m.start())))
    return out


def _is_power_of_two_literal(tok: str) -> bool:
    try:
        v = abs(float(tok))
    except ValueError:
        return False
    if v == 0.0:
        return False
    m, _ = math.frexp(v)
    return m == 0.5          # exactly representable as 2**k


def _column_major_2d(ctx: Context) -> list:
    # Fortran stores 2D arrays column-major, so `X(row, col)` flattens to
    # `X[(col-1)*n_rows + (row-1)]` -- the stride is the ROW count. Using the
    # column count as the stride is the row-major habit, and it is silently
    # wrong for every non-square array.
    #
    # Anchored inside a subscript on purpose: `rows * cols` in a size or a
    # malloc is legitimate and lives outside brackets.
    out = []
    for m in re.finditer(r"\[[^\]]*\*\s*(?:\w+(?:->|\.))?n_(\w+)_cols\b[^\]]*\]", ctx.c):
        out.extend(_find("column-major-2d",
                     f"a 2D subscript strides by `n_{m.group(1)}_cols`; Fortran is "
                     f"column-major, so `X(row, col)` is "
                     f"`X[(col-1) * n_{m.group(1)}_rows + (row-1)]` — the stride is the "
                          f"ROW count",
                          f"n_{m.group(1)}_cols used as stride",
                          _line_of(ctx.c, m.start())))
    return out


def _character_padding(ctx: Context) -> list:
    # Fortran `X = ""` fills the WHOLE declared width with spaces. `x[0]='\0'`
    # sets one byte and leaves the rest as it was. It passes baselines, because
    # Fortran callers trim at the first space, and fails KGen, which compares
    # the buffer byte for byte. Written for ErrMsg, true of every CHARACTER
    # out-parameter.
    if re.search(r"\bmemset\s*\(", ctx.c):
        return []
    out = []
    for m in re.finditer(r"\b(\w+)\s*\[\s*0\s*\]\s*=\s*'\\0'", ctx.c):
        out.extend(_find("character-padding",
                     f"`{m.group(1)}[0] = '\\0'` sets one byte; a Fortran CHARACTER "
                     f"assignment blank-fills the whole declared width. This passes "
                     f"baselines (callers trim at the first space) and fails a "
                     f"byte-for-byte kernel compare. Use "
                          f"`std::memset({m.group(1)}, ' ', <width>)`",
                          m.group(1), _line_of(ctx.c, m.start())))
    return out


def _real_exponent_pow(ctx: Context) -> list:
    # gfortran compiles `x**N` by repeated multiplication for an INTEGER N, and
    # for the REAL literal 2.0 as a special case -- but for a real exponent of
    # 3.0 or more it emits a libm `pow` call, and `pow(x,3.0)` differs from
    # `x*x*x` by 1 ULP on some inputs. The WSE covariance loop amplified exactly
    # that into visible divergence.
    #
    # Complementary to `exponent-grouping`, which is about association and
    # deliberately ignores `x*x*x`. Here `x*x*x` is the defect.
    for m in re.finditer(r"(\w+)\s*\*\*\s*(\d+\.\d*)", ctx.f):
        var, exp = m.group(1), float(m.group(2))
        if exp < 3.0:
            continue                        # **2.0 IS optimised to x*x
        chain = rf"\b{re.escape(var)}\s*\*\s*{re.escape(var)}\s*\*\s*{re.escape(var)}"
        if re.search(chain, ctx.c) and not re.search(r"\bpow\s*\(", ctx.c):
            return _find("real-exponent-pow",
                         f"the reference raises `{var}` to the REAL exponent {exp}; "
                         f"gfortran emits a libm `pow` call for real exponents >= 3.0, "
                         f"and it differs from repeated multiplication by 1 ULP. Use "
                         f"`std::pow({var}, {exp})`",
                         f"{var}**{m.group(2)}")
    return []


def _reciprocal_multiply(ctx: Context) -> list:
    # `/` and `*` have equal precedence and associate left to right, so
    # `1.0/a * b` is a reciprocal followed by a multiply. That rounds
    # differently from the single division `b/a`. Transcribe the shape, not the
    # algebra.
    if not re.search(r"1\.(?:0*)\s*/\s*[\w%()]+\s*\*", ctx.f):
        return []
    if not re.search(r"1\.0*\s*/", ctx.c):
        return _find("reciprocal-multiply",
                     "the reference computes a reciprocal and then multiplies "
                     "(`1.0/a * b`); that rounds differently from the single division "
                     "`b/a`, which is what the translation appears to do. Transcribe "
                     "the shape, not the algebra",
                     "1.0/a * b")
    return []


def _array_section_row(ctx: Context) -> list:
    # `A(i, :)` is a STRIDED row of a column-major array. Fortran packs a
    # contiguous temporary wherever one is required -- including at the BIND(C)
    # boundary -- and C++ does not. Two things go wrong silently: the slice
    # length is the OTHER dimension, and for i == 1 the wrong contiguous run
    # STARTS at the right value, so the first group looks plausible.
    m = re.search(r"\b(\w+)\s*\(\s*[^,()]+\s*,\s*:\s*\)", ctx.f)
    if not m:
        return []
    if not re.search(r"\bfor\s*\(|std::vector", ctx.c):
        return _find("array-section-row",
                     f"the reference passes the row section `{m.group(1)}(i, :)` of a "
                     f"column-major array; a row is strided, so it cannot cross as a "
                     f"bare pointer and must be gathered element by element. The slice "
                     f"length is the other dimension",
                     f"{m.group(1)}(i, :)")
    return []


def _narrowing_local(ctx: Context) -> list:
    # The discriminator is NOT "a CHARACTER(n) local exists" -- that flagged four
    # ordinary scratch buffers across the corpus. The defect is a local narrower
    # than what is ASSIGNED INTO it: `ChkParseData` assigns CHARACTER(*) dummies
    # into CHARACTER(20) locals, so two names agreeing to character 20 compare
    # EQUAL. `F_VSRefSpdCornerFreq` is exactly 20 characters -- live, not
    # theoretical.
    names = set()
    for group in re.findall(r"^\s*CHARACTER\(\s*\*\s*\)[^:]*::\s*([^\n!]+)$",
                            ctx.f, re.IGNORECASE | re.MULTILINE):
        for piece in group.split(","):
            m = re.match(r"\s*([A-Za-z_]\w*)", piece)
            if m:
                names.add(m.group(1).lower())
    if not names:
        return []
    for m in re.finditer(r"^\s*CHARACTER\(\s*(\d+)\s*\)\s*(?:,[^:]*)?::\s*([^\n!]+)$",
                         ctx.f, re.IGNORECASE | re.MULTILINE):
        if re.search(r"\bINTENT\b|\bPARAMETER\b", ctx.f[m.start():m.end()], re.IGNORECASE):
            continue
        width = m.group(1)
        for piece in m.group(2).split(","):
            local = re.match(r"\s*([A-Za-z_]\w*)", piece)
            if not local:
                continue
            lname = local.group(1)
            for asg in re.finditer(rf"^\s*{re.escape(lname)}\s*=\s*([A-Za-z_]\w*)",
                                   ctx.f, re.IGNORECASE | re.MULTILINE):
                if asg.group(1).lower() in names and width not in ctx.c:
                    return _find("narrowing-local",
                                 f"`{lname}` is CHARACTER({width}) but is assigned from "
                                 f"`{asg.group(1)}`, which is CHARACTER(*); the "
                                 f"assignment TRUNCATES to {width} characters and any "
                                 f"later comparison runs on the truncated value. The "
                                 f"width does not appear in the translation",
                                 f"{lname} = {asg.group(1)}")
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
        # A character set written as an ARRAY -- `{ '\\', '/' }` -- is the same
        # set in C++'s other syntax for one, and reading only string literals
        # calls it missing. This campaign's own "name a size once" rule pushes
        # translations INTO that form: an array states its length once via
        # `sizeof`, where a string literal needs the length written beside it and
        # carries a NUL terminator the Fortran set does not have. So the check
        # would fire on every translation that followed the rule. Measured on
        # rosco-r2 GetRoot, whose `INDEX( '\/', ... )` became
        # `index_first(Separators, (int)sizeof Separators, ...)`.
        for braced in re.finditer(r"\{([^{}]*)\}", ctx.c):
            chars = re.findall(r"'((?:[^'\\]|\\.)*)'", braced.group(1))
            if chars:
                decoded.append("".join(
                    ch.replace("\\\\", "\\").replace('\\"', '"').replace("\\'", "'")
                    for ch in chars))
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
            # A locale-FREE strftime is correct and must not be flagged: this is
            # `CurTime`, which "%H:%M:%S" reproduces exactly in every locale.
            cpp='void t(char* o) { strftime(o, 9, "%H:%M:%S", &tm); }\n'),
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
        id="nint-as-cast",
        prompt_line=("A bare cast TRUNCATES; Fortran `NINT` rounds half away from zero. "
                     "If the reference uses `NINT`, the translation needs "
                     "`std::lround` — a cast is only right where the Fortran says `INT`."),
        detect=_nint_as_cast,
        known_bad=Context(fortran="i = NINT(x)\n", cpp="int i = (int)x;\n"),
        known_good=Context(fortran="i = NINT(x)\n",
                           cpp="int i = (int)std::lround(x);\n"),
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
        id="exponent-grouping",
        prompt_line=("Fortran `a*x**2` means `a * (x*x)`; C++ `a * x * x` means `(a*x) * x`, "
                     "and the different intermediate rounding is 1-3 ULP. Parenthesise the "
                     "power: `a * (x * x)`."),
        detect=_exponent_grouping,
        known_bad=Context(fortran="k_tau = 0.39 - 0.26*spanRatio**2\n",
                          cpp="double k = 0.39 - 0.26 * spanRatio * spanRatio;\n"),
        known_good=Context(fortran="k_tau = 0.39 - 0.26*spanRatio**2\n",
                           cpp="double k = 0.39 - 0.26 * (spanRatio * spanRatio);\n"),
        needs_fortran=True,
    ),
    Check(
        id="column-major-2d",
        prompt_line=("Fortran is column-major: `X(row, col)` flattens to "
                     "`X[(col-1) * n_X_rows + (row-1)]` — the stride is the ROW count. "
                     "Striding by the column count is silently wrong for any "
                     "non-square array."),
        detect=_column_major_2d,
        known_bad=Context(cpp="double v = p->Coefs[(row - 1) * p->n_Coefs_cols + (col - 1)];\n"),
        known_good=Context(cpp="double v = p->Coefs[(col - 1) * p->n_Coefs_rows + (row - 1)];\n"),
    ),
    Check(
        id="character-padding",
        prompt_line=("A Fortran CHARACTER assignment blank-fills the entire declared "
                     "width. `buf[0] = '\\0'` sets one byte, passes baselines because "
                     "callers trim at the first space, and fails a byte-for-byte kernel "
                     "compare. Use `std::memset(buf, ' ', <width>)`."),
        detect=_character_padding,
        known_bad=Context(cpp="void f(char* errMsg) { errMsg[0] = '\\0'; }\n"),
        known_good=Context(cpp="void f(char* errMsg) { std::memset(errMsg, ' ', ErrMsgLen); }\n"),
    ),
    Check(
        id="real-exponent-pow",
        prompt_line=("gfortran expands `x**N` by repeated multiplication for an INTEGER "
                     "exponent and for the real literal `2.0`, but emits a libm `pow` "
                     "call for a REAL exponent of 3.0 or more — and the two differ by "
                     "1 ULP. Match the reference: `std::pow(x, 3.0)`, not `x*x*x`."),
        detect=_real_exponent_pow,
        known_bad=Context(fortran="Q(2,2) = v_m**3.0*dt\n",
                          cpp="double q = v_m * v_m * v_m * dt;\n"),
        known_good=Context(fortran="Q(2,2) = v_m**3.0*dt\n",
                           cpp="double q = std::pow(v_m, 3.0) * dt;\n"),
        needs_fortran=True,
    ),
    Check(
        id="reciprocal-multiply",
        prompt_line=("`/` and `*` have equal precedence and associate left to right, so "
                     "`1.0/a * b` is a reciprocal then a multiply — which rounds "
                     "differently from `b/a`. Transcribe the shape, not the algebra."),
        detect=_reciprocal_multiply,
        known_bad=Context(fortran="y = 1.0/FP%nfs_a2(inst) * (b0*x)\n",
                          cpp="double y = (b0 * x) / a2;\n"),
        known_good=Context(fortran="y = 1.0/FP%nfs_a2(inst) * (b0*x)\n",
                           cpp="double y = 1.0 / a2 * (b0 * x);\n"),
        needs_fortran=True,
    ),
    Check(
        id="array-section-row",
        prompt_line=("A Fortran array section is not a pointer. `A(i, :)` is a STRIDED "
                     "row of a column-major array and must be gathered element by "
                     "element; its length is the other dimension. (`A(:, j)` is "
                     "contiguous and can be passed as `&buf[(j-1)*rows]`.)"),
        detect=_array_section_row,
        known_bad=Context(fortran="CALL interp1d(CntrPar%OL_StructControl(I_GROUP, :), y)\n",
                          cpp="interp1d_c(&p->OL_StructControl[i], n, &y);\n"),
        known_good=Context(
            fortran="CALL interp1d(CntrPar%OL_StructControl(I_GROUP, :), y)\n",
            cpp=("std::vector<double> row(cols);\n"
                 "for (int j = 0; j < cols; ++j) row[j] = b[j * rows + i];\n")),
        needs_fortran=True,
    ),
    Check(
        id="narrowing-local",
        prompt_line=("A CHARACTER local narrower than what is assigned into it truncates "
                     "silently — no cast, no warning — and every later comparison runs on "
                     "the truncated value. Read each CHARACTER local's declared length "
                     "before translating."),
        detect=_narrowing_local,
        known_bad=Context(
            fortran=("    CHARACTER(*), INTENT(IN) :: ExpVarName\n"
                     "    CHARACTER(20)            :: ExpUCVarName\n"
                     "    ExpUCVarName = ExpVarName\n"),
            cpp="std::string a = name;\n"),
        known_good=Context(
            fortran=("    CHARACTER(*), INTENT(IN) :: ExpVarName\n"
                     "    CHARACTER(20)            :: ExpUCVarName\n"
                     "    ExpUCVarName = ExpVarName\n"),
            cpp="char a[20]; std::strncpy(a, name, 20);\n"),
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
PUSHED_TO_MUTATION: dict = {}
"""Empty, and that is a fact rather than an omission.

Every shape this module deferred has since been closed by writing a better
detector -- `exponent-grouping`, `narrowing-local` and finally `nint-as-cast`,
each of which was recorded as undetectable and each of which was not. Three for
three is enough to state the rule plainly: **a deferral here is a claim about a
particular detector, and it expires the moment someone anchors the same shape on
both sides.**

If an entry is added, it needs a reason that survives that test.
"""


# Shapes whose right fix is in VIT itself, not in a check and certainly not in a
# tip. Recorded here so deleting the prose does not delete the knowledge, and so
# the work is visible as work rather than as guidance nobody enforces.
#
# The tell, from the Step 0 audit: when a tip explains the TOOL's behaviour
# rather than the language's, it is a bug report with a note attached.
TOOL_GAPS: dict = {
    "reverse-copy-detection":
        "`vit integrate` needs `--reverse-copy` when a function writes a SCALAR field "
        "of a view-type INOUT argument, and not when it writes only ALLOCATABLE arrays "
        "(those travel through C_LOC). VIT can determine which from the reference "
        "body it already parses, so the flag should be inferred and the human asked "
        "only to confirm — not documented.",
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
