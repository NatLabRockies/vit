"""Tests for callee detection and C++ source scanning in kernel_verify."""

import textwrap
import pytest

from vit.kernel_verify import detect_callees


# --- Fortran source fixtures ---

FORTRAN_WITH_CALLEES = textwrap.dedent("""\
    MODULE TestMod
    CONTAINS
    SUBROUTINE MainFunc(x, y)
        INTEGER, INTENT(IN) :: x
        REAL, INTENT(OUT) :: y
        CALL HelperSub(x)
        y = UtilityFunc(x)
        CALL AnotherSub(x, y)
    END SUBROUTINE

    SUBROUTINE HelperSub(x)
        INTEGER, INTENT(IN) :: x
    END SUBROUTINE

    FUNCTION UtilityFunc(x) RESULT(r)
        INTEGER, INTENT(IN) :: x
        REAL :: r
        r = REAL(x)
    END FUNCTION

    SUBROUTINE AnotherSub(x, y)
        INTEGER, INTENT(IN) :: x
        REAL, INTENT(INOUT) :: y
    END SUBROUTINE
    END MODULE
""")

FORTRAN_NO_CALLEES = textwrap.dedent("""\
    MODULE TestMod
    CONTAINS
    SUBROUTINE LeafFunc(x, y)
        INTEGER, INTENT(IN) :: x
        REAL, INTENT(OUT) :: y
        y = REAL(x) * 2.0
    END SUBROUTINE
    END MODULE
""")

FORTRAN_WITH_NESTED_CALLS = textwrap.dedent("""\
    MODULE TestMod
    CONTAINS
    SUBROUTINE OuterFunc(x)
        INTEGER, INTENT(IN) :: x
        CALL MiddleFunc(x)
    END SUBROUTINE

    SUBROUTINE MiddleFunc(x)
        INTEGER, INTENT(IN) :: x
        CALL InnerFunc(x)
    END SUBROUTINE

    SUBROUTINE InnerFunc(x)
        INTEGER, INTENT(IN) :: x
    END SUBROUTINE
    END MODULE
""")

# C++ source fixtures

CPP_WITH_FUNCTIONS = textwrap.dedent("""\
    #include "vit_types.h"
    #include "vit_nwtc.h"

    static void HelperFunc(double x, double* y) {
        *y = x * 2.0;
    }

    void MainFunc(double x, double* y, int* errStat) {
        HelperFunc(x, y);
        *errStat = 0;
    }
""")

SHARED_HEADER = textwrap.dedent("""\
    #ifndef VIT_NWTC_H
    #define VIT_NWTC_H

    inline double MPi2Pi(double angle) {
        return angle;
    }

    int LocateBin(double XVal, const double* XAry, int AryLen);

    void CubicSplineInterpM(double X, const double* XAry, const double* YAry,
                            const double* Coef, double* Res,
                            int NumPts, int nCols, int nCoefRows);

    #endif
""")


# === detect_callees tests ===

class TestDetectCallees:

    def test_detects_call_statements(self):
        """Finds CALL subroutine references in function body."""
        known = {"HelperSub", "UtilityFunc", "AnotherSub"}
        callees = detect_callees(FORTRAN_WITH_CALLEES, "MainFunc",
                                 known_functions=known)
        callee_upper = {c.upper() for c in callees}
        assert "HELPERSUB" in callee_upper
        assert "ANOTHERSUB" in callee_upper

    def test_detects_function_calls(self):
        """Finds function references (not just CALL subroutines)."""
        known = {"HelperSub", "UtilityFunc", "AnotherSub"}
        callees = detect_callees(FORTRAN_WITH_CALLEES, "MainFunc",
                                 known_functions=known)
        callee_upper = {c.upper() for c in callees}
        assert "UTILITYFUNC" in callee_upper

    def test_no_callees_returns_empty(self):
        """Function with no calls returns empty list."""
        callees = detect_callees(FORTRAN_NO_CALLEES, "LeafFunc")
        assert callees == []

    def test_only_direct_callees(self):
        """Only detects direct callees, not transitive ones."""
        known = {"MiddleFunc", "InnerFunc"}
        callees = detect_callees(FORTRAN_WITH_NESTED_CALLS, "OuterFunc",
                                 known_functions=known)
        callee_upper = {c.upper() for c in callees}
        assert "MIDDLEFUNC" in callee_upper
        # InnerFunc is called by MiddleFunc, not OuterFunc
        assert "INNERFUNC" not in callee_upper


# === C++ function scanning tests ===

class TestCppFunctionScanning:
    """Test the _scan_cpp_for_functions logic used in _generate_callee_bridges."""

    def _scan(self, content):
        """Extract function names from C++ source (mirrors kernel_verify logic)."""
        import re
        names = set()
        for m in re.finditer(
            r'(?:^|\n)\s*(?:static\s+)?(?:inline\s+)?'
            r'(?:void|int|double|float|bool|char)\s+'
            r'(\w+)\s*\(',
            content,
        ):
            names.add(m.group(1).upper())
        return names

    def test_finds_static_and_public_functions(self):
        """Detects both static and non-static function definitions."""
        funcs = self._scan(CPP_WITH_FUNCTIONS)
        assert "HELPERFUNC" in funcs
        assert "MAINFUNC" in funcs

    def test_finds_header_declarations(self):
        """Detects function declarations in shared headers."""
        funcs = self._scan(SHARED_HEADER)
        assert "MPI2PI" in funcs
        assert "LOCATEBIN" in funcs
        assert "CUBICSPLINEINTERPM" in funcs

    def test_case_insensitive(self):
        """All names are uppercased for case-insensitive matching."""
        funcs = self._scan(CPP_WITH_FUNCTIONS)
        # All entries should be uppercase
        for f in funcs:
            assert f == f.upper()
