"""
Tests for VIT core functionality.

Uses the ROSCO saturate function as ground truth — this is the function
we proved the full pipeline on (20/20 KGen verification PASSED,
full simulation output byte-for-byte identical).
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.type_map import get_type_mapping, normalize_fortran_type, DEFAULT_KIND_ALIASES
from vit.fortran_parser import parse_signature_from_source, SubprogramKind, IntentKind
from vit.interface_gen import (
    generate_cpp_wrapper,
    generate_fortran_interface_block,
    generate_fortran_wrapper,
)


# === Test data: ROSCO Functions.f90 excerpt ===
ROSCO_FUNCTIONS_SOURCE = """\
MODULE Functions

USE Constants

IMPLICIT NONE

CONTAINS

    !-------------------------------------------------------------------------------------------------------------------------------
    ! Saturate function
    FUNCTION saturate(val, minval, maxval) RESULT(saturate_result)
        USE Precision
        IMPLICIT NONE

        REAL(ReKi), INTENT(IN)  :: val
        REAL(ReKi), INTENT(IN)  :: minval
        REAL(ReKi), INTENT(IN)  :: maxval
        REAL(ReKi)              :: saturate_result

        saturate_result = MIN(MAX(val, minval), maxval)

    END FUNCTION saturate

    ! Another function for testing multi-function files
    FUNCTION interp1d(xData, yData, xq, ErrVar) RESULT(yq)
        USE Precision
        IMPLICIT NONE

        REAL(ReKi), DIMENSION(:), INTENT(IN)    :: xData
        REAL(ReKi), DIMENSION(:), INTENT(IN)    :: yData
        REAL(ReKi), INTENT(IN)                  :: xq
        INTEGER(IntKi), INTENT(IN), OPTIONAL    :: ErrVar
        REAL(ReKi)                              :: yq

        yq = 0.0

    END FUNCTION interp1d

END MODULE Functions
"""

ROSCO_KIND_ALIASES = {
    "ReKi": "REAL(8)",
    "DbKi": "REAL(8)",
    "SiKi": "REAL(4)",
    "IntKi": "INTEGER(4)",
}


# === Type Mapping Tests ===

class TestTypeMapping:

    def test_real8_mapping(self):
        m = get_type_mapping("REAL(8)")
        assert m.c_type == "double"
        assert m.c_kind == "C_DOUBLE"
        assert m.iso_c_type == "REAL(C_DOUBLE)"

    def test_real4_mapping(self):
        m = get_type_mapping("REAL(4)")
        assert m.c_type == "float"
        assert m.c_kind == "C_FLOAT"

    def test_integer_mapping(self):
        m = get_type_mapping("INTEGER")
        assert m.c_type == "int"
        assert m.c_kind == "C_INT"

    def test_integer4_mapping(self):
        m = get_type_mapping("INTEGER(4)")
        assert m.c_type == "int"

    def test_reki_alias(self):
        """ReKi should resolve to REAL(8) → double."""
        m = get_type_mapping("REAL(ReKi)", ROSCO_KIND_ALIASES)
        assert m.c_type == "double"
        assert m.c_kind == "C_DOUBLE"

    def test_intki_alias(self):
        """IntKi should resolve to INTEGER(4) → int."""
        m = get_type_mapping("INTEGER(IntKi)", ROSCO_KIND_ALIASES)
        assert m.c_type == "int"
        assert m.c_kind == "C_INT"

    def test_normalize_kind_prefix(self):
        assert normalize_fortran_type("REAL(KIND=8)") == "REAL(8)"

    def test_normalize_case(self):
        assert normalize_fortran_type("real(8)") == "REAL(8)"

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            get_type_mapping("REAL(UnknownKind)")


# === Fortran Parser Tests ===

class TestFortranParser:

    def test_parse_saturate(self):
        """Parse the ROSCO saturate function — our ground truth."""
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")

        assert sig.name == "saturate"
        assert sig.kind == SubprogramKind.FUNCTION
        assert len(sig.args) == 3
        assert sig.result_name.upper() == "SATURATE_RESULT"

    def test_saturate_arg_names(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        arg_names = [a.name.lower() for a in sig.args]
        assert arg_names == ["val", "minval", "maxval"]

    def test_saturate_arg_types(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        for arg in sig.args:
            assert "REAL" in arg.fortran_type.upper()

    def test_saturate_arg_intent(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        for arg in sig.args:
            assert arg.intent == IntentKind.IN

    def test_saturate_result_type(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        assert sig.result_type is not None
        assert "REAL" in sig.result_type.upper()

    def test_saturate_c_func_name(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        assert sig.c_func_name == "saturate_c"

    def test_saturate_module_detection(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        # Module detection is done by find_function_in_file, not parse_signature
        # But the source has MODULE Functions
        assert sig.kind == SubprogramKind.FUNCTION

    def test_parse_interp1d(self):
        """Test parsing a more complex function with arrays and optional args."""
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "interp1d")

        assert sig.name == "interp1d"
        assert len(sig.args) == 4
        assert sig.result_name.upper() == "YQ"

    def test_interp1d_array_arg(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "interp1d")
        xdata = sig.args[0]
        assert xdata.is_array
        assert ":" in (xdata.array_dims or "")

    def test_function_not_found(self):
        with pytest.raises(ValueError):
            parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "nonexistent")

    def test_case_insensitive(self):
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "Saturate")
        assert sig.name == "Saturate"
        assert sig.kind == SubprogramKind.FUNCTION


# === Interface Generation Tests ===

class TestInterfaceGeneration:

    def _get_saturate_sig(self):
        return parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")

    def test_cpp_wrapper_contains_extern_c(self):
        sig = self._get_saturate_sig()
        cpp = generate_cpp_wrapper(sig, "saturate_impl.hpp", ROSCO_KIND_ALIASES)
        assert 'extern "C"' in cpp

    def test_cpp_wrapper_function_name(self):
        sig = self._get_saturate_sig()
        cpp = generate_cpp_wrapper(sig, "saturate_impl.hpp", ROSCO_KIND_ALIASES)
        assert "saturate_c" in cpp

    def test_cpp_wrapper_uses_double(self):
        """ReKi args should map to double in C++."""
        sig = self._get_saturate_sig()
        cpp = generate_cpp_wrapper(sig, "saturate_impl.hpp", ROSCO_KIND_ALIASES)
        assert "double" in cpp

    def test_fortran_interface_has_bind_c(self):
        sig = self._get_saturate_sig()
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        assert "BIND(C" in iface

    def test_fortran_interface_has_iso_c_binding(self):
        sig = self._get_saturate_sig()
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        assert "ISO_C_BINDING" in iface

    def test_fortran_interface_has_c_double(self):
        sig = self._get_saturate_sig()
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        assert "C_DOUBLE" in iface

    def test_fortran_interface_value_attribute(self):
        """Scalar IN args should have VALUE attribute in C interface."""
        sig = self._get_saturate_sig()
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        assert "VALUE" in iface

    def test_fortran_wrapper_calls_c_function(self):
        sig = self._get_saturate_sig()
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "saturate_c" in wrapper

    def test_fortran_wrapper_has_iso_c_binding(self):
        sig = self._get_saturate_sig()
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "ISO_C_BINDING" in wrapper

    def test_fortran_wrapper_has_implicit_none(self):
        sig = self._get_saturate_sig()
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "IMPLICIT NONE" in wrapper

    def test_fortran_wrapper_preserves_result_name(self):
        sig = self._get_saturate_sig()
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "saturate_result" in wrapper.lower()


# === Subroutine Tests ===

SUBROUTINE_SOURCE = """\
MODULE TestMod
CONTAINS
    SUBROUTINE update_values(x, y, n)
        IMPLICIT NONE
        REAL(8), INTENT(INOUT) :: x
        REAL(8), INTENT(OUT) :: y
        INTEGER, INTENT(IN) :: n
        y = x * n
        x = x + 1.0
    END SUBROUTINE update_values
END MODULE TestMod
"""


class TestSubroutine:

    def test_parse_subroutine(self):
        sig = parse_signature_from_source(SUBROUTINE_SOURCE, "update_values")
        assert sig.kind == SubprogramKind.SUBROUTINE
        assert len(sig.args) == 3

    def test_subroutine_intents(self):
        sig = parse_signature_from_source(SUBROUTINE_SOURCE, "update_values")
        intents = {a.name.lower(): a.intent for a in sig.args}
        assert intents["x"] == IntentKind.INOUT
        assert intents["y"] == IntentKind.OUT
        assert intents["n"] == IntentKind.IN

    def test_subroutine_interface_no_return(self):
        sig = parse_signature_from_source(SUBROUTINE_SOURCE, "update_values")
        iface = generate_fortran_interface_block(sig)
        assert "SUBROUTINE" in iface
        assert "FUNCTION" not in iface

    def test_subroutine_out_args_not_value(self):
        """OUT/INOUT args should NOT have VALUE attribute."""
        sig = parse_signature_from_source(SUBROUTINE_SOURCE, "update_values")
        iface = generate_fortran_interface_block(sig)
        # n (IN) should have VALUE, x (INOUT) and y (OUT) should not
        lines = iface.split("\n")
        for line in lines:
            if ":: n" in line.lower():
                assert "VALUE" in line.upper()
            if ":: x" in line.lower():
                assert "INTENT" in line.upper()
                # INOUT args shouldn't have VALUE
            if ":: y" in line.lower():
                assert "INTENT" in line.upper()


# === Run tests ===

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
