"""A kernel that cannot fail is not evidence, and this is what proves it can.

The reference campaign's own summary: *"break every new harness on purpose,
once, and confirm it says so."* Eight of its twenty-three tool fixes were
defects where a check reported success while doing the opposite. These tests
pin the two properties that make a deliberate defect worth anything — that it
is well-defined, and that it has a direction that survives.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.fortran_parser import parse_signature_from_source          # noqa: E402
from vit.redtest import SCALE, candidates, why_not                  # noqa: E402


FUNC_SRC = """
FUNCTION PIController(error, kp, ki, DT)
    REAL(8), INTENT(IN) :: error, kp, ki, DT
    REAL(8) :: PIController
END FUNCTION PIController
"""

SUB_SRC = """
SUBROUTINE Scale(x, y)
    REAL(8), INTENT(IN) :: x
    REAL(8), INTENT(OUT) :: y
END SUBROUTINE Scale
"""

OPAQUE_SRC = """
SUBROUTINE Orchestrate(CntrPar, LocalVar)
    TYPE(ControlParameters), INTENT(INOUT) :: CntrPar
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar
END SUBROUTINE Orchestrate
"""

FUNC_CPP = """#include "vit_types.h"

double PIController(double error, double kp, double ki, double DT) {
    double out = error * kp + ki * DT;
    return out;
}
"""

SUB_CPP = """void Scale(double x, double* y) {
    *y = x * 2.0;
}
"""


def sig(src, name):
    return parse_signature_from_source(src, name)


# ------------------------------------------------------------ both directions

def test_both_directions_are_offered_scale_first():
    """A downstream clamp eats the additive direction; a ZERO value eats the
    multiplicative one -- which is `ForeAftDamping` exactly, 41/41 with the
    gains swapped. Trying only one is how a blind kernel passes a red test."""
    ps = candidates(sig(FUNC_SRC, "PIController"), FUNC_CPP)
    assert [p.direction for p in ps] == ["scale", "offset"]


def test_a_perturbation_actually_changes_the_source():
    for p in candidates(sig(FUNC_SRC, "PIController"), FUNC_CPP):
        assert p.text != FUNC_CPP, f"{p.direction} changed nothing"


def test_the_perturbation_is_reported_in_words():
    p = candidates(sig(FUNC_SRC, "PIController"), FUNC_CPP)[0]
    assert "error" in p.description and str(SCALE) in p.description


# ---------------------------------------------------------- well-definedness

def test_only_by_value_parameters_are_touched():
    """Writing through a pointer parameter mutates a buffer the caller owns --
    in a kernel, state the Fortran side may also read -- so the perturbation
    could change BOTH implementations and prove nothing. `y` is `double*`."""
    ps = candidates(sig(SUB_SRC, "Scale"), SUB_CPP)
    assert ps, "x is by value and should be perturbable"
    for p in ps:
        assert "*y = *y" not in p.text
        assert p.target == "input `x`"


def test_no_perturbation_moves_a_pointer_or_an_index():
    """The campaign discarded a deliberate defect that read past the end of an
    allocation: undefined behaviour, so its red meant nothing."""
    for p in candidates(sig(FUNC_SRC, "PIController"), FUNC_CPP):
        added = set(p.text.split()) - set(FUNC_CPP.split())
        assert not any(tok.startswith(("[", "&", "->")) for tok in added), added


def test_control_flow_is_never_altered():
    """A defect that inverts a branch changes which arm runs on the LIVE path,
    so it demonstrates nothing about the dead one."""
    for p in candidates(sig(FUNC_SRC, "PIController"), FUNC_CPP):
        for kw in ("if", "else", "for", "while", "return"):
            assert p.text.count(f"{kw} ") >= FUNC_CPP.count(f"{kw} ")


# ------------------------------------------------------------- the honest gap

def test_an_all_opaque_signature_yields_no_perturbation():
    """Most orchestrators take only derived types. That must not read as a
    pass -- it means the kernel's discriminating power is UNMEASURED."""
    cpp = "void Orchestrate(void* CntrPar, void* LocalVar) {\n    (void)CntrPar;\n}\n"
    assert candidates(sig(OPAQUE_SRC, "Orchestrate"), cpp) == []


def test_the_reason_names_the_consequence_not_just_the_cause():
    cpp = "void Orchestrate(void* CntrPar, void* LocalVar) {\n}\n"
    why = why_not(sig(OPAQUE_SRC, "Orchestrate"), cpp)
    assert "UNMEASURED" in why


def test_a_translation_whose_definition_cannot_be_found_says_so():
    why = why_not(sig(FUNC_SRC, "PIController"), "// no definition here\n")
    assert "could not find the definition" in why
    assert candidates(sig(FUNC_SRC, "PIController"), "// nothing\n") == []


# ------------------------------------------------------------- return scaling

def test_a_function_with_no_by_value_input_scales_its_return():
    src = """
FUNCTION CurValue(p)
    TYPE(ControlParameters), INTENT(IN) :: p
    REAL(8) :: CurValue
END FUNCTION CurValue
"""
    cpp = "double CurValue(controlparameters_t* p) {\n    return p->x;\n}\n"
    ps = candidates(sig(src, "CurValue"), cpp)
    assert ps and all(p.target == "the return value" for p in ps)
    assert "return (p->x)" in ps[0].text
