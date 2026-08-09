"""Fortran→C type mapping.

**This module had no tests, and it is the most general component in VIT.**
Every translation, every bridge and every view struct goes through it, and it is
the one place where a mistake is a *silent* corruption rather than a build
failure: a type that maps to something plausible-but-wrong produces code that
compiles, links, runs and computes the wrong answer.

That is not hypothetical. `COMPLEX(DbKi)` was rewritten to `REAL(8)` because the
kind alias carried a base type of its own and the resolver returned the alias
whole. The view struct then held the real part and dropped the imaginary one,
with no error anywhere (found at ActiveWakeControl, function 58 of the ROSCO
replication). The regression for that is `test_a_kind_alias_supplies_the_width`.
"""

import pytest

from vit.type_map import get_type_mapping

ROSCO = {"DbKi": "REAL(8)", "ReKi": "REAL(4)", "SiKi": "REAL(4)", "IntKi": "INTEGER(4)"}


# --------------------------------------------------------------------------
# Kind aliases

def test_a_kind_alias_supplies_the_width_not_the_base_type():
    """The regression that motivates this file.

    An alias like `DbKi -> REAL(8)` says how WIDE a kind is. It does not say
    what the declared type is. Returning it whole turned every
    `COMPLEX(DbKi)` into a `REAL(8)`: half the value, silently.
    """
    assert get_type_mapping("COMPLEX(DbKi)", ROSCO).c_type == "vit_complex_double"
    assert get_type_mapping("REAL(DbKi)", ROSCO).c_type == "double"
    assert get_type_mapping("REAL(ReKi)", ROSCO).c_type == "float"
    assert get_type_mapping("INTEGER(IntKi)", ROSCO).c_type in ("int", "int32_t")


def test_a_complex_is_twice_as_wide_as_its_real():
    """A COMPLEX(k) is two contiguous REAL(k). If the sizes do not stand in that
    relation, something has narrowed."""
    c8 = get_type_mapping("COMPLEX(8)", ROSCO)
    r8 = get_type_mapping("REAL(8)", ROSCO)
    assert c8.byte_size == 2 * r8.byte_size
    c4 = get_type_mapping("COMPLEX(4)", ROSCO)
    assert c4.byte_size == 2 * get_type_mapping("REAL(4)", ROSCO).byte_size
    assert c8.c_type != r8.c_type, "a complex that maps to its own real part is the bug"


def test_alias_lookup_is_case_insensitive_because_fortran_is():
    a = get_type_mapping("REAL(dbki)", ROSCO)
    b = get_type_mapping("REAL(DBKI)", ROSCO)
    assert a.c_type == b.c_type == "double"


def test_an_unaliased_kind_is_not_silently_defaulted():
    """A kind the config never declared must not quietly become the default
    width. Whatever this does, it must not return REAL(8) as though it knew."""
    try:
        m = get_type_mapping("REAL(QuadKi)", ROSCO)
    except Exception:
        return                                  # refusing is a fine answer
    assert m.c_type != "double" or m.kind not in ("", "8"), \
        "an unknown kind resolved to double without being told its width"


# --------------------------------------------------------------------------
# The base types

@pytest.mark.parametrize("ftype,expect", [
    ("REAL(4)", "float"),
    ("REAL(8)", "double"),
    ("INTEGER(4)", "int"),
    ("LOGICAL", "int"),
])
def test_base_types_map_to_their_c_equivalents(ftype, expect):
    got = get_type_mapping(ftype, ROSCO).c_type
    assert got == expect or got.startswith(expect[:3]), f"{ftype} -> {got}"


def test_logical_does_not_cross_as_a_one_byte_bool():
    """A Fortran default LOGICAL is 4 bytes. `LOGICAL(C_BOOL)` is 1, and mixing
    them is a hard type mismatch at the bridge -- which is why VIT crosses
    LOGICAL as INTEGER(C_INT) everywhere and converts at the boundary."""
    m = get_type_mapping("LOGICAL", ROSCO)
    assert "BOOL" not in (m.iso_c_type or "").upper()


def test_an_undeclared_derived_type_is_marked_not_guessed():
    """An undeclared derived type is NOT an error -- it is a passthrough, and
    `build_c_params` drops it from the C signature.

    What matters is that it says so. `is_derived=True, is_c_compatible=False`
    is what lets a caller report "this argument never reaches C++" instead of
    discovering it as a missing parameter later. A mapping that guessed a
    plausible c_type here would be the silent kind of wrong.
    """
    m = get_type_mapping("TYPE(SomethingNobodyDeclared)", ROSCO)
    assert m.is_derived and not m.is_c_compatible
    assert m.byte_size == 0, "a size it cannot know must not be a number it made up"


def test_a_scalar_type_that_is_not_a_type_at_all_raises():
    """Silence reads as success: nonsense must stop the translation."""
    with pytest.raises(Exception):
        get_type_mapping("NOT_A_FORTRAN_TYPE", ROSCO)
