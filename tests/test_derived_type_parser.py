"""Derived-type parsing and the strategy overrides.

**This module had no tests**, and it decides whether a derived-type argument
reaches C++ at all. When it gets that wrong the argument is simply absent from
the generated signature -- there is no error, no warning, and the first symptom
is a translation that verifies against a call it was never given.

`apply_type_strategies` had exactly that shape: handed the vit.yaml form
`{"strategy": "view"}` instead of the flattened `"view"`, it assigned a dict to
`view_strategy`, every downstream `== "view"` failed, and the type was treated
as passthrough. Nothing raised. It is a public function, so a caller reading
vit.yaml directly -- which is the obvious thing to do -- got the silent path.
"""

import pytest

from vit.derived_type_parser import apply_type_strategies, parse_derived_type


def parse_all(source: str, *names) -> dict:
    """A registry from source text. `parse_derived_type` handles one type at a
    time; the tests want the dict shape every caller downstream uses."""
    out = {}
    for n in names:
        info = parse_derived_type(source, n)
        assert info is not None, f"{n} not parsed"
        out[n] = info
    return out

SRC = """
MODULE Types
    IMPLICIT NONE
    TYPE, PUBLIC :: Simple
        REAL(DbKi) :: a
        INTEGER(IntKi) :: n
    END TYPE Simple

    TYPE, PUBLIC :: HasAlloc
        REAL(DbKi), DIMENSION(:), ALLOCATABLE :: v
        REAL(DbKi), DIMENSION(:,:), ALLOCATABLE :: m
        REAL(DbKi) :: fixed(12)
        CHARACTER(1024) :: name
        REAL(DbKi), POINTER :: p => NULL()
    END TYPE HasAlloc
END MODULE Types
"""


@pytest.fixture
def registry():
    return parse_all(SRC, "Simple", "HasAlloc")


# --------------------------------------------------------------------------
# Field classification -- what every downstream generator branches on

def test_allocatable_pointer_and_fixed_arrays_are_distinguished(registry):
    """Three different things that all look like "an array field", and each one
    crosses the C boundary differently: an ALLOCATABLE becomes pointer+extent,
    a fixed-size array crosses by reference, a POINTER cannot be reconstructed
    from a C view at all. Conflating any two produces a bridge that compiles."""
    f = {x.name: x for x in registry["HasAlloc"].fields}
    assert f["v"].is_allocatable and f["v"].array_dims == ":"
    assert f["m"].is_allocatable and f["m"].array_dims.count(",") == 1, "rank must survive"
    assert f["fixed"].is_array and not f["fixed"].is_allocatable
    assert f["fixed"].array_dims == "12", "a fixed extent is the number, not a colon"
    assert f["p"].is_pointer


def test_a_type_with_no_allocatables_is_not_marked_as_having_them(registry):
    assert not any(x.is_allocatable for x in registry["Simple"].fields)


def test_character_fields_keep_their_declared_length(registry):
    """The length is what lets a CHARACTER field cross as a C_CHAR buffer.
    Losing it is the difference between a comparable field and a dropped one."""
    name = {x.name: x for x in registry["HasAlloc"].fields}["name"]
    assert "1024" in name.fortran_type


# --------------------------------------------------------------------------
# apply_type_strategies -- the silent no-op

def test_the_vit_yaml_shape_is_accepted(registry):
    """`types: {HasAlloc: {strategy: view}}` is what vit.yaml actually contains.
    Passing it straight through used to set `view_strategy` to a DICT: truthy,
    never equal to "view", so the type was dropped from the C signature with no
    error anywhere."""
    apply_type_strategies(registry, {"HasAlloc": {"strategy": "view"}})
    assert registry["HasAlloc"].view_strategy == "view"


def test_the_flattened_shape_is_accepted(registry):
    apply_type_strategies(registry, {"HasAlloc": "view"})
    assert registry["HasAlloc"].view_strategy == "view"


def test_lookup_is_case_insensitive_because_fortran_is(registry):
    apply_type_strategies(registry, {"hasalloc": "view"})
    assert registry["HasAlloc"].view_strategy == "view"


def test_a_strategy_it_cannot_read_raises(registry):
    """A strategy the code cannot interpret must not look like one it declined
    to apply. Both are 'nothing happened'; only one is a mistake."""
    for bad in ({"HasAlloc": {"strategy": "viwe"}}, {"HasAlloc": 7},
                {"HasAlloc": None}, {"HasAlloc": ["view"]}):
        with pytest.raises(ValueError):
            apply_type_strategies(registry, bad)


def test_a_type_absent_from_this_registry_is_reported_not_ignored(registry):
    """vit.yaml declares strategies for a whole campaign, and any one function
    sees only some of them -- so this is not an error. But the caller has to be
    able to say which ones did nothing, or a typo'd type name is indisting-
    uishable from one this function does not use."""
    unmatched = apply_type_strategies(registry, {"HasAlloc": "view", "Typoed": "view"})
    assert unmatched == ["Typoed"]
    assert registry["HasAlloc"].view_strategy == "view", "one bad name must not stop the rest"


# --------------------------------------------------------------------------

def test_a_nested_type_field_keeps_its_type_name():
    """A field that is itself a derived type must record which one, or the
    registry cannot walk to it and the C++ gets an opaque pointer instead of
    the expanded fields."""
    src = SRC + """
MODULE Outer
    USE Types
    TYPE, PUBLIC :: Wrapper
        TYPE(Simple) :: inner
    END TYPE Wrapper
END MODULE Outer
"""
    reg = parse_all(src, "Wrapper", "Simple")
    inner = {x.name: x for x in reg["Wrapper"].fields}["inner"]
    assert "Simple" in inner.fortran_type
