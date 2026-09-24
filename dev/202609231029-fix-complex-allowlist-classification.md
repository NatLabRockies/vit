# `--fix complex`: Allowlist Classification, No Silent Fallback
**Date:** 2026-09-23
**Status:** completed
**Scope:** `vit/analyze_types.py` (`fix_complex_field`, new `_classify_complex_reference`), `tests/test_analyze_types.py`

## Problem

`vit analyze-types --fix complex` splits a COMPLEX derived-type field `f` into `f_re`/`f_im` and
rewrites every Fortran line that references it. VIT writes these rewrites itself. Only lines it
declines go to the agent + human through EXIT_MANUAL (11) and `--rewrite` (dev note
`202603191921`).

Since `3b45dd4` the manual/auto decision was a **denylist**. A line went to manual only when it
contained `EXP|CMPLX|CONJG|ABS|SQRT|LOG`. Every other line was auto-rewritten, and anything that
matched no pattern hit a catch-all "rename every reference to `_re`". This had two silent-wrong-code
defects:

1. **Fallback drops the imaginary part.** `x%f(K) = x%f(K) * c` became
   `x%f_re(K) = x%f_re(K) * c`, and `_im` was never updated. It compiled with no warning.
2. **Constant init copies the constant into `_im`.** The pattern (`[0-9]+\.?[0-9]*\w*`, unanchored)
   turned `x%f = 1.5D0` into `f_re = 1.5D0; f_im = 1.5D0`. It also matched
   `x%f = 0.0 + y`. `2a1787f` had matched only `0\.0\w*`. `3b45dd4` broadened it.

Why this matters: the rewritten Fortran becomes the reference. Extraction runs on the post-fix
source (e.g. ROSCO `Controllers.f90` @ `fde0498`), so a wrong rewrite would be captured by KGen
and the C++ would verify against it. Only a full-simulation comparison against unmodified source
would catch it.

This contradicted the protocol's stated principle "VIT never writes incorrect code"
(`202603191921`:132). ROSCO never triggered either defect.

Found while gathering evidence (dev note `202609221200` §4).

## Fix

**Allowlist.** One function, `_classify_complex_reference(line, field_name)`, returns
`(label, replacement_lines)` for a line matching a known pattern, or `None`. There is no fallback.
Known patterns:

| Pattern | Rewrite |
|---|---|
| Whole statement `WRITE/READ(...) var%f(i)` | one statement per part (anchored to end of line; was unanchored) |
| Whole statement `var%f[(...)] = <real literal>` | `_re = literal`, `_im = 0` with the literal's precision marker (`D` exponent → `0.0D0`, `_kind` → `0.0_kind`, else `0.0`) |
| `REAL(var%f[(...)])`, `AIMAG(var%f[(...)])` anywhere | `_re` / `_im`, only if no other reference to the field remains on the line |

**One plan, two phases.** Phase 1 classifies every reference and builds the full edit plan
(`--rewrite` lines take precedence). If any line is unclassified, it returns manual edits (exit 11)
and writes nothing. Phase 2 only applies the plan, so scan and apply can no longer disagree. Before,
they used different logic.

**Ordering.** Reference edits are applied before the TYPE-definition rewrite, which is now built
from the file's current text. Plan indices come from the original files, and the TYPE rewrite turns
one line into two. A TYPE file that also references the field would otherwise have had its edits
shifted. This was latent: the old code re-scanned after writing, so it was not affected.

The EXIT_MANUAL reason still names the intrinsic when one is present (`complex intrinsic: EXP`).
Otherwise it reads `no known rewrite pattern`. The six-intrinsic list no longer affects which lines
are classified manual.

### Alternative considered, rejected

Keep auto-rewriting `<target> = var%f(...)` when the target is REAL (Fortran's complex→real
assignment takes the real part). VIT has no type information for the target. A wrong type lookup
would bring back the silent-truncation class. It costs one extra `--rewrite` per such line, and is
not worth it.

## New behaviour

VIT rewrites a line only when it matches a pattern whose meaning is fully known. Every other
reference stops the run with exit 11 before anything is written:
- complex arithmetic without intrinsics
- assignments to other variables
- literal-plus-expression
- continuation lines
- `REAL()` mixed with a bare reference

## Verification

- **New tests (8)** in `TestFixComplex`: arithmetic → manual; assignment to another variable →
  manual; `1.5D0` / `2.0_DbKi` / `-3` → `_im = 0.0D0` / `0.0_DbKi` / `0.0`; `0.0 + y` → manual;
  `REAL()` plus a bare reference → manual; `REAL()*AIMAG()` → rewritten; continuation → manual; and
  references in the TYPE file land on the right lines. Against the pre-change code, 7 of the 8 fail.
  The eighth passes, because it guards the new ordering.
- **Full suite:** 962 passed, 1 failed. The failure, `test_conformance.py::test_cell_matches_the_record[c_assumed_shape_2d]`,
  is pre-existing: it fails identically with this change stashed (954 passed, 1 failed).
- **ROSCO replay** (pure Python, no Docker), on `rosco/controller/src` from ROSCO `e8010f0` (pre-Phase 4C):
  - Run 1 exits with 2 manual edits:
    - `Controllers.f90:759` (`complex intrinsic: EXP`)
    - `ROSCO_IO.f90:928` (`no known rewrite pattern`, `LocalVarOutData(150) = LocalVar%AWC_complexangle(1)`)
  - Before this change, only 759 was manual.
  - Run 2, with the COS/SIN rewrite for 759 and `…_re(1)` for 928: all 24 lines matching
    `AWC_complexangle|complexI` in `Controllers.f90`, `ROSCO_IO.f90` and `ROSCO_Types.f90` are
    textually identical to the committed Phase 4C result (ROSCO `fde0498`), ignoring line numbers.
    The simulation was not re-run; identical source implies the recorded byte-identical output
    still holds.
