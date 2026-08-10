"""Prove a kernel can fail before believing that it passed.

`vit verify` reports 20/20 IDENTICAL and that is read as evidence the
translation is right. It is evidence of that only if the kernel could have said
otherwise, and the reference campaign found repeatedly that it could not:

- a kernel printed `Verification PASSED` for a case whose
  `Number of output variables: 0` — it compared nothing at all;
- `ForeAftDamping` verified 41/41 with `kp` and `ki` **swapped**, because both
  gains are zero in every scenario;
- `VariableSpeedControl` printed 52/52 either way, because no single extraction
  captures both arms of its mode flag.

Eight of the campaign's twenty-three tool fixes were defects of exactly this
shape — a check reporting success while doing the opposite — and its own
summary named the habit that caught them: **break every new harness on purpose,
once, and confirm it says so.** This module makes that a step rather than a
habit, because a habit is not performed by a tired person at 2am.

## What a valid perturbation is

The campaign discarded two deliberate defects as invalid, and both lessons are
encoded here:

- **It must be well-defined.** One read past the end of an allocation, which is
  undefined behaviour, so its red meant nothing. Every perturbation here acts on
  a by-value local copy or a returned expression; none can move a pointer, index
  an array or change control flow.
- **It must have a direction that survives.** `__reachfix__` established
  multiplicative before additive, because a downstream clamp eats the additive
  direction. The converse is equally true and cost the campaign a separate
  finding: **a zero value eats the multiplicative direction** — scaling 0.0 by
  anything is still 0.0, which is the `ForeAftDamping` blindness exactly. So
  both are tried, scale first, and only failure of *both* is a verdict.

## What it cannot do, and says so

A function whose inputs are all pointers and derived types — most orchestrators
— has nothing here that can be perturbed safely, and the honest answer is
`NOT_CONSTRUCTED`. That is **not** a pass and must never be rendered as one; it
means the kernel's discriminating power is unmeasured, which is the state
`vit verify` was in for every function before this module existed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .interface_gen import build_c_params

# Small enough that a correct translation cannot drift into it, large enough to
# survive a narrowing to float (which carries ~1.2e-7 relative precision -- the
# campaign measured a 1e-10 probe vanishing into an `avrSWAP` cast and reading
# as "nothing moved").
SCALE = 1.0 + 1.0e-5
OFFSET = 1.0e-5

_FLOAT_C_TYPES = ("double", "float")


@dataclass(frozen=True)
class Perturbation:
    """One deliberate defect, and the C++ that carries it."""
    direction: str        # "scale" | "offset"
    target: str           # what it acts on, for the message
    text: str             # the perturbed translation

    @property
    def description(self) -> str:
        how = (f"scaled by {SCALE}" if self.direction == "scale"
               else f"offset by {OFFSET}")
        return f"{self.target} {how}"


def _body_open(cpp_text: str, func: str) -> Optional[int]:
    """Index just past the opening brace of `func`'s definition."""
    m = re.search(rf"\b{re.escape(func)}\s*\([^;{{}}]*\)\s*\{{", cpp_text, re.DOTALL)
    return m.end() if m else None


def _by_value_float_params(sig, kind_aliases, derived_types) -> list[str]:
    """Scalar float/double parameters passed BY VALUE.

    By value only, deliberately. Writing through a pointer parameter would
    mutate a buffer the caller owns -- in a kernel, state the Fortran side may
    also read -- so a perturbation there could change both implementations and
    prove nothing. A by-value copy is local by construction.
    """
    out = []
    for c_type, name in build_c_params(sig, kind_aliases, derived_types):
        t = c_type.strip()
        if t in _FLOAT_C_TYPES:          # no '*', no '&'
            out.append(name)
    return out


def _returns_float(sig, kind_aliases) -> bool:
    if not getattr(sig, "is_function", False):
        return False
    from .type_map import get_type_mapping
    try:
        return get_type_mapping(sig.result_type, kind_aliases).c_type in _FLOAT_C_TYPES
    except Exception:
        return False


def candidates(sig, cpp_text: str, kind_aliases: Optional[dict] = None,
               derived_types: Optional[dict] = None) -> list[Perturbation]:
    """Every perturbation that can be applied to this translation, scale first.

    Order is the verdict's order: a downstream clamp eats the additive
    direction, so multiplicative is tried first; a zero value eats the
    multiplicative one, so additive follows rather than being skipped.
    """
    func = getattr(sig, "cpp_impl_name", None) or sig.name
    at = _body_open(cpp_text, func)
    params = _by_value_float_params(sig, kind_aliases, derived_types)
    out: list[Perturbation] = []

    for direction in ("scale", "offset"):
        if at is not None and params:
            p = params[0]
            expr = (f"{p} = {p} * {SCALE!r};" if direction == "scale"
                    else f"{p} = {p} + {OFFSET!r};")
            out.append(Perturbation(
                direction, f"input `{p}`",
                cpp_text[:at] + f"\n    {expr}  // VIT red test\n" + cpp_text[at:]))
        elif _returns_float(sig, kind_aliases):
            # No by-value input to move, but the result is a float we can scale
            # on the way out. Same properties: local, well-defined, no control
            # flow touched.
            op = f"* {SCALE!r}" if direction == "scale" else f"+ {OFFSET!r}"
            text, n = re.subn(r"\breturn\s+([^;]+);",
                              lambda m: f"return ({m.group(1)}) {op};", cpp_text)
            if n:
                out.append(Perturbation(direction, "the return value", text))
    return out


def why_not(sig, cpp_text: str, kind_aliases: Optional[dict] = None,
            derived_types: Optional[dict] = None) -> str:
    """Why no perturbation could be built — reported, never silently skipped."""
    func = getattr(sig, "cpp_impl_name", None) or sig.name
    if _body_open(cpp_text, func) is None:
        return (f"could not find the definition of `{func}` in the translation, so "
                f"nothing could be perturbed")
    return ("no by-value floating-point parameter and no floating-point result: "
            "every input arrives behind a pointer or inside a derived type, and "
            "perturbing one of those could change what BOTH implementations "
            "compute. The kernel's discriminating power is therefore UNMEASURED "
            "for this function")
