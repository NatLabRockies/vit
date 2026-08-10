"""The callable inventory is read, not maintained.

`TRANSLATION_TIPS.md` listed the thirteen functions in `vit_nwtc.h` by hand.
A hand-written list of a header's contents is a copy, and a copy drifts —
silently, in the direction that matters least visibly: a translator who does not
see a function in the list reimplements it inline, and nothing goes red.

These tests pin the two properties that make reading better than copying —
that what the header declares is what the prompt shows, and that a header
gaining a function needs no edit anywhere else.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.header_api import constants, declarations, inventory      # noqa: E402


HEADER = """\
#ifndef VIT_NWTC_H
#define VIT_NWTC_H

constexpr double Pi = 3.141592653589793;
constexpr int ErrMsgLen = 1024;

// Keeps highest severity, concatenates messages with RoutineName prefix.
inline void SetErrStat(int errStatLcl, const char* errMsgLcl,
                       int* errStat, char* errMsg, const char* routineName) {
    int len = ErrMsgLen;          // a local, not a declaration
    double shadowed(int x);       // indented: not top level
}

bool EqualRealNos(double a, double b);

double InterpStp(double XVal, const double* XAry,
                 const double* YAry, int& Ind, int AryLen);

#endif
"""


def names(src):
    import re
    return [re.search(r"([A-Za-z_]\w*)\s*\(", d).group(1) for d, _ in declarations(src)]


def test_top_level_declarations_are_found():
    assert names(HEADER) == ["SetErrStat", "EqualRealNos", "InterpStp"]


def test_indented_lines_are_not_declarations():
    """Locals and nested declarations live inside bodies. Reporting them would
    tell a translator to call something that does not exist."""
    assert "shadowed" not in names(HEADER)


def test_a_multi_line_declaration_is_normalised_to_one_line():
    decl = [d for d, _ in declarations(HEADER) if d.startswith("double InterpStp")][0]
    assert "\n" not in decl
    assert "int& Ind, int AryLen" in decl


def test_the_comment_above_a_declaration_is_carried():
    """The 1-based/0-based conventions live in those comments, and they are the
    part a translator most needs and most easily gets wrong."""
    note = [n for d, n in declarations(HEADER) if d.startswith("inline void SetErrStat")][0]
    assert "highest severity" in note


def test_constants_are_reported_and_include_guards_are_not():
    got = dict(constants(HEADER))
    assert got["Pi"].startswith("3.14")
    assert got["ErrMsgLen"] == "1024"
    assert "VIT_NWTC_H" not in got, "an include guard is not a constant anyone can use"


def test_inventory_names_the_header_it_read(tmp_path):
    h = tmp_path / "vit_nwtc.h"
    h.write_text(HEADER)
    text = inventory([str(h)])
    assert "vit_nwtc.h" in text
    assert "EqualRealNos" in text
    assert "do not reimplement them inline" in text


def test_a_function_added_to_the_header_appears_with_no_other_edit(tmp_path):
    """The property that makes this better than a maintained list."""
    h = tmp_path / "vit_nwtc.h"
    h.write_text(HEADER)
    assert "BrandNewThing" not in inventory([str(h)])
    h.write_text(HEADER.replace("#endif", "int BrandNewThing(double x);\n#endif"))
    assert "BrandNewThing" in inventory([str(h)])


def test_no_headers_yields_no_section(tmp_path):
    """A project without shared files must not get an empty heading."""
    assert inventory([]) == ""
    assert inventory([str(tmp_path / "missing.h")]) == ""


def test_a_shared_cpp_is_not_read_as_a_header(tmp_path):
    """`shared_files` carries .cpp too; those are linked, not included, and
    their internals are not an API."""
    c = tmp_path / "vit_nwtc.cpp"
    c.write_text("double helper(double x) { return x; }\n")
    assert inventory([str(c)]) == ""
