"""
Tests for derived type parsing, classification, and pipeline integration.

Tests TYPE(...) parsing in fortran_parser, DerivedTypeInfo from
derived_type_parser, and TypeMapping handling for derived types.
"""

import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.fortran_parser import parse_signature_from_source, IntentKind
from vit.derived_type_parser import parse_derived_type, DerivedTypeInfo
from vit.type_map import get_type_mapping, normalize_fortran_type, TypeMapping
from vit.interface_gen import (
    generate_cpp_wrapper,
    generate_fortran_interface_block,
    generate_fortran_wrapper,
    build_c_params,
)
from vit.integrate import _generate_standalone_cpp


# === Test data ===

# Source with TYPE(...) arguments including passthrough (ErrorVariables)
INTERP1D_SOURCE = """\
MODULE Functions

USE ROSCO_Types, ONLY: ErrorVariables

IMPLICIT NONE

CONTAINS

    FUNCTION interp1d(xData, yData, xq, ErrVar) RESULT(yq)
        USE ROSCO_Types, ONLY: ErrorVariables
        IMPLICIT NONE

        REAL(8), DIMENSION(:), INTENT(IN)    :: xData
        REAL(8), DIMENSION(:), INTENT(IN)    :: yData
        REAL(8), INTENT(IN)                  :: xq
        TYPE(ErrorVariables), INTENT(INOUT)  :: ErrVar
        REAL(8)                              :: yq

        yq = 0.0

    END FUNCTION interp1d

END MODULE Functions
"""

# ErrorVariables has ALLOCATABLE and nested TYPE fields — NOT C-compatible
ERROR_VARIABLES_SOURCE = """\
MODULE ROSCO_Types

IMPLICIT NONE

TYPE :: ErrorVariables
    INTEGER    :: ErrStat
    CHARACTER(1024) :: ErrMsg
    REAL(8), ALLOCATABLE :: ErrData(:)
    TYPE(SomeNested) :: Nested
END TYPE ErrorVariables

END MODULE ROSCO_Types
"""

# Simple BIND(C) compatible type (all primitives, no ALLOCATABLE/POINTER)
SIMPLE_TYPE_SOURCE = """\
MODULE SimpleTypes

IMPLICIT NONE

TYPE :: Point3D
    REAL(8) :: x
    REAL(8) :: y
    REAL(8) :: z
END TYPE Point3D

END MODULE SimpleTypes
"""

# Type with BIND(C) declaration
BIND_C_TYPE_SOURCE = """\
MODULE CTypes

IMPLICIT NONE

TYPE, BIND(C) :: CPoint
    REAL(8) :: x
    REAL(8) :: y
END TYPE CPoint

END MODULE CTypes
"""

# Type with POINTER and ALLOCATABLE fields
COMPLEX_TYPE_SOURCE = """\
MODULE ComplexTypes

IMPLICIT NONE

TYPE :: DataBuffer
    REAL(8), ALLOCATABLE :: values(:)
    INTEGER, POINTER :: indices(:)
    REAL(8) :: scale
END TYPE DataBuffer

END MODULE ComplexTypes
"""

ROSCO_KIND_ALIASES = {
    "ReKi": "REAL(8)",
    "DbKi": "REAL(8)",
    "SiKi": "REAL(4)",
    "IntKi": "INTEGER(4)",
}

# ErrorVariables AFTER Phase 4A fix — all primitives, C-compatible
ERRVAR_FIXED_SOURCE = """\
MODULE ROSCO_Types

USE ISO_C_BINDING

IMPLICIT NONE

TYPE, PUBLIC :: ErrorVariables
    INTEGER(4)       :: size_avcMSG
    INTEGER(C_INT)   :: aviFAIL
    INTEGER(C_INT)   :: ErrStat
    CHARACTER(1024)  :: ErrMsg
END TYPE ErrorVariables

END MODULE ROSCO_Types
"""

# sigma function — scalars + ErrorVariables
SIGMA_SOURCE = """\
MODULE Functions

USE ROSCO_Types, ONLY: ErrorVariables

IMPLICIT NONE

CONTAINS

    REAL(8) FUNCTION sigma(x, x0, x1, y0, y1, ErrVar)
        USE ROSCO_Types, ONLY : ErrorVariables
        IMPLICIT NONE
        REAL(8), INTENT(IN)  :: x, x0, x1
        REAL(8), INTENT(IN)  :: y0, y1
        TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
        sigma = 0.0
    END FUNCTION sigma

END MODULE Functions
"""


# === Parser: TYPE(...) declaration tests ===

class TestParseTypeDeclaration:

    def test_type_arg_parsed(self):
        """TYPE(ErrorVariables) → fortran_type='TYPE(ErrorVariables)'."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        errvar = sig.args[3]
        assert errvar.fortran_type == "TYPE(ErrorVariables)"

    def test_type_arg_intent(self):
        """TYPE arg intent is parsed correctly."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        errvar = sig.args[3]
        assert errvar.intent == IntentKind.INOUT

    def test_type_arg_name(self):
        """TYPE arg name preserved."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        errvar = sig.args[3]
        assert errvar.name == "ErrVar"

    def test_non_type_args_still_work(self):
        """Primitive args are still parsed correctly alongside TYPE args."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        assert sig.args[0].fortran_type == "REAL(8)"
        assert sig.args[0].is_array
        assert sig.args[2].fortran_type == "REAL(8)"
        assert not sig.args[2].is_array

    def test_result_type_with_type_args(self):
        """Result type still parsed when TYPE args are present."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        assert sig.result_type == "REAL(8)"
        assert sig.result_name.upper() == "YQ"


# === FortranArg properties ===

class TestFortranArgProperties:

    def test_is_derived_type_true(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        errvar = sig.args[3]
        assert errvar.is_derived_type is True

    def test_is_derived_type_false(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        xdata = sig.args[0]
        assert xdata.is_derived_type is False

    def test_derived_type_name(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        errvar = sig.args[3]
        assert errvar.derived_type_name == "ErrorVariables"

    def test_derived_type_name_none_for_primitive(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        xdata = sig.args[0]
        assert xdata.derived_type_name is None

    def test_is_assumed_shape_true(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        xdata = sig.args[0]
        assert xdata.is_assumed_shape is True

    def test_is_assumed_shape_false_for_scalar(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        xq = sig.args[2]
        assert xq.is_assumed_shape is False


# === USE statement capture ===

class TestUseStatementCapture:

    def test_use_statements_captured(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        assert len(sig.use_statements) > 0
        assert any("ROSCO_Types" in s for s in sig.use_statements)

    def test_use_statements_include_only_clause(self):
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        rosco_use = [s for s in sig.use_statements if "ROSCO_Types" in s]
        assert len(rosco_use) >= 1
        assert "ErrorVariables" in rosco_use[0]


# === DerivedTypeInfo tests ===

class TestDerivedTypeInfo:

    def test_parse_simple_type(self):
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "Point3D")
        assert info is not None
        assert info.name == "Point3D"
        assert len(info.fields) == 3
        assert info.fields[0].name == "x"
        assert info.fields[0].fortran_type.upper().startswith("REAL")

    def test_simple_type_is_c_compatible(self):
        """All-primitive type without ALLOCATABLE/POINTER is C-compatible."""
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "Point3D")
        assert info.is_c_compatible is True

    def test_simple_type_not_bind_c(self):
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "Point3D")
        assert info.is_bind_c is False

    def test_bind_c_type(self):
        info = parse_derived_type(BIND_C_TYPE_SOURCE, "CPoint")
        assert info is not None
        assert info.is_bind_c is True
        assert info.is_c_compatible is True

    def test_error_variables_not_c_compatible(self):
        """ErrorVariables has ALLOCATABLE and nested TYPE → not C-compatible."""
        info = parse_derived_type(ERROR_VARIABLES_SOURCE, "ErrorVariables")
        assert info is not None
        assert info.is_c_compatible is False

    def test_allocatable_field_detected(self):
        info = parse_derived_type(ERROR_VARIABLES_SOURCE, "ErrorVariables")
        alloc_fields = [f for f in info.fields if f.is_allocatable]
        assert len(alloc_fields) >= 1

    def test_nested_type_field_detected(self):
        info = parse_derived_type(ERROR_VARIABLES_SOURCE, "ErrorVariables")
        type_fields = [f for f in info.fields
                       if f.fortran_type.upper().startswith("TYPE(")]
        assert len(type_fields) >= 1

    def test_pointer_field_detected(self):
        info = parse_derived_type(COMPLEX_TYPE_SOURCE, "DataBuffer")
        pointer_fields = [f for f in info.fields if f.is_pointer]
        assert len(pointer_fields) >= 1

    def test_complex_type_not_c_compatible(self):
        info = parse_derived_type(COMPLEX_TYPE_SOURCE, "DataBuffer")
        assert info.is_c_compatible is False

    def test_source_module_detected(self):
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "Point3D")
        assert info.source_module == "SimpleTypes"

    def test_type_not_found_returns_none(self):
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "NonExistent")
        assert info is None

    def test_case_insensitive_search(self):
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "point3d")
        assert info is not None


# === TypeMapping for derived types ===

class TestDerivedTypeMapping:

    def test_unknown_derived_type_is_passthrough(self):
        """TYPE(ErrorVariables) without registry → passthrough."""
        m = get_type_mapping("TYPE(ErrorVariables)")
        assert m.is_derived is True
        assert m.is_c_compatible is False

    def test_known_c_compatible_type(self):
        """TYPE(Point3D) with registry → C-compatible."""
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "Point3D")
        m = get_type_mapping("TYPE(Point3D)", derived_types={"Point3D": info})
        assert m.is_derived is True
        assert m.is_c_compatible is True

    def test_known_non_c_compatible_type(self):
        """TYPE(ErrorVariables) with registry → passthrough."""
        info = parse_derived_type(ERROR_VARIABLES_SOURCE, "ErrorVariables")
        m = get_type_mapping("TYPE(ErrorVariables)",
                             derived_types={"ErrorVariables": info})
        assert m.is_derived is True
        assert m.is_c_compatible is False

    def test_normalize_preserves_derived_type(self):
        """normalize_fortran_type passes TYPE(...) through unchanged."""
        assert normalize_fortran_type("TYPE(ErrorVariables)") == "TYPE(ERRORVARIABLES)"

    def test_primitive_type_not_derived(self):
        m = get_type_mapping("REAL(8)")
        assert m.is_derived is False
        assert m.is_c_compatible is True

    def test_unknown_primitive_still_raises(self):
        """Non-derived unknown types still raise ValueError."""
        with pytest.raises(ValueError):
            get_type_mapping("REAL(UnknownKind)")


# === Interface generation with passthrough and arrays ===

class TestPassthroughInterface:

    def _get_interp1d_sig(self):
        return parse_signature_from_source(INTERP1D_SOURCE, "interp1d")

    def test_c_params_exclude_passthrough(self):
        """Passthrough args (TYPE(ErrorVariables)) not in C param list."""
        sig = self._get_interp1d_sig()
        params = build_c_params(sig)
        param_names = [p[1] for p in params]
        assert "ErrVar" not in param_names

    def test_c_params_include_non_passthrough(self):
        """Primitive and array args are in C param list."""
        sig = self._get_interp1d_sig()
        params = build_c_params(sig)
        param_names = [p[1] for p in params]
        assert "xData" in param_names
        assert "yData" in param_names
        assert "xq" in param_names

    def test_c_params_have_size_for_assumed_shape(self):
        """Assumed-shape arrays get n_<name> size parameters."""
        sig = self._get_interp1d_sig()
        params = build_c_params(sig)
        param_names = [p[1] for p in params]
        assert "n_xData" in param_names
        assert "n_yData" in param_names

    def test_size_params_are_int(self):
        """Size parameters are int type."""
        sig = self._get_interp1d_sig()
        params = build_c_params(sig)
        size_params = [(t, n) for t, n in params if n.startswith("n_")]
        for c_type, _ in size_params:
            assert c_type == "int"

    def test_interface_excludes_passthrough(self):
        """BIND(C) interface does not include passthrough args."""
        sig = self._get_interp1d_sig()
        iface = generate_fortran_interface_block(sig)
        assert "ErrVar" not in iface

    def test_interface_has_assumed_size(self):
        """BIND(C) interface uses (*) for assumed-shape arrays."""
        sig = self._get_interp1d_sig()
        iface = generate_fortran_interface_block(sig)
        assert "(*)" in iface

    def test_interface_has_size_params(self):
        """BIND(C) interface declares size parameters."""
        sig = self._get_interp1d_sig()
        iface = generate_fortran_interface_block(sig)
        assert "n_xData" in iface
        assert "n_yData" in iface
        assert "INTEGER(C_INT)" in iface

    def test_wrapper_includes_all_args(self):
        """Fortran wrapper accepts ALL args including passthrough."""
        sig = self._get_interp1d_sig()
        wrapper = generate_fortran_wrapper(sig)
        assert "ErrVar" in wrapper
        assert "xData" in wrapper
        assert "yData" in wrapper
        assert "xq" in wrapper

    def test_wrapper_declares_passthrough_type(self):
        """Wrapper declares the passthrough arg with its original TYPE."""
        sig = self._get_interp1d_sig()
        wrapper = generate_fortran_wrapper(sig)
        assert "TYPE(ErrorVariables)" in wrapper

    def test_wrapper_call_excludes_passthrough(self):
        """Wrapper's C function call does not include passthrough args."""
        sig = self._get_interp1d_sig()
        wrapper = generate_fortran_wrapper(sig)
        # The C call line should have interp1d_c(...) without ErrVar
        lines = wrapper.split("\n")
        call_lines = [l for l in lines if "interp1d_c(" in l]
        assert len(call_lines) == 1
        call_line = call_lines[0]
        # ErrVar should NOT be in the C function call
        call_args_part = call_line[call_line.index("interp1d_c("):]
        assert "ErrVar" not in call_args_part

    def test_wrapper_call_has_size_args(self):
        """Wrapper passes SIZE(array) for assumed-shape arrays."""
        sig = self._get_interp1d_sig()
        wrapper = generate_fortran_wrapper(sig)
        assert "SIZE(xData)" in wrapper
        assert "SIZE(yData)" in wrapper

    def test_wrapper_has_use_for_derived_type(self):
        """Wrapper includes USE statement for module providing derived type."""
        sig = self._get_interp1d_sig()
        wrapper = generate_fortran_wrapper(sig)
        assert "ROSCO_Types" in wrapper

    def test_cpp_wrapper_excludes_passthrough(self):
        """C++ wrapper doesn't include passthrough args."""
        sig = self._get_interp1d_sig()
        cpp = generate_cpp_wrapper(sig, "interp1d_impl.hpp")
        assert "ErrVar" not in cpp

    def test_cpp_wrapper_has_size_params(self):
        """C++ wrapper has size params for assumed-shape arrays."""
        sig = self._get_interp1d_sig()
        cpp = generate_cpp_wrapper(sig, "interp1d_impl.hpp")
        assert "n_xData" in cpp
        assert "n_yData" in cpp

    def test_standalone_cpp_excludes_passthrough(self):
        """Standalone C++ file skips passthrough args."""
        sig = self._get_interp1d_sig()
        result = _generate_standalone_cpp(sig, "double interp1d() { return 0; }")
        assert "ErrVar" not in result
        assert "n_xData" in result


# === Scalar-only function still works (regression) ===

SCALAR_SOURCE = """\
MODULE Functions
CONTAINS
    FUNCTION saturate(val, minval, maxval) RESULT(saturate_result)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: val
        REAL(8), INTENT(IN) :: minval
        REAL(8), INTENT(IN) :: maxval
        REAL(8) :: saturate_result
        saturate_result = MIN(MAX(val, minval), maxval)
    END FUNCTION saturate
END MODULE Functions
"""


class TestScalarRegression:

    def test_scalar_function_c_params_unchanged(self):
        """Scalar-only function generates same params as before."""
        sig = parse_signature_from_source(SCALAR_SOURCE, "saturate")
        params = build_c_params(sig)
        assert len(params) == 3
        assert all(t == "double" for t, _ in params)

    def test_scalar_function_interface_unchanged(self):
        """Scalar function interface still uses VALUE for IN args."""
        sig = parse_signature_from_source(SCALAR_SOURCE, "saturate")
        iface = generate_fortran_interface_block(sig)
        assert "VALUE" in iface
        assert "BIND(C" in iface

    def test_scalar_function_wrapper_unchanged(self):
        """Scalar function wrapper still works correctly."""
        sig = parse_signature_from_source(SCALAR_SOURCE, "saturate")
        wrapper = generate_fortran_wrapper(sig)
        assert "saturate_c" in wrapper
        assert "IMPLICIT NONE" in wrapper


# === Phase 5A: ErrorVariables struct pointer pattern ===

from vit.derived_type_parser import generate_c_struct_header


class TestErrorVariablesFixed:
    """ErrorVariables after Phase 4A fix is C-compatible and generates correct interfaces."""

    def test_errvar_fixed_is_c_compatible(self):
        """Phase 4A fix makes ErrorVariables C-compatible (no ALLOCATABLE/POINTER)."""
        info = parse_derived_type(ERRVAR_FIXED_SOURCE, "ErrorVariables")
        assert info is not None
        assert info.is_c_compatible is True

    def test_errvar_fixed_has_four_fields(self):
        info = parse_derived_type(ERRVAR_FIXED_SOURCE, "ErrorVariables")
        assert len(info.fields) == 4

    def test_errvar_fixed_errmsg_is_character_1024(self):
        info = parse_derived_type(ERRVAR_FIXED_SOURCE, "ErrorVariables")
        errmsg = [f for f in info.fields if f.name.upper() == "ERRMSG"]
        assert len(errmsg) == 1
        assert "1024" in errmsg[0].fortran_type

    def test_errvar_struct_header_has_char_1024(self):
        """C struct header maps CHARACTER(1024) to char[1024]."""
        info = parse_derived_type(ERRVAR_FIXED_SOURCE, "ErrorVariables")
        header = generate_c_struct_header(info, ROSCO_KIND_ALIASES)
        assert "char ErrMsg[1024]" in header
        assert "errorvariables_t" in header

    def test_errvar_struct_header_has_int_fields(self):
        info = parse_derived_type(ERRVAR_FIXED_SOURCE, "ErrorVariables")
        header = generate_c_struct_header(info, ROSCO_KIND_ALIASES)
        assert "int aviFAIL" in header
        assert "int ErrStat" in header


class TestStructPointerInterface:
    """C-compatible ErrorVariables generates struct pointer in interfaces."""

    def _errvar_registry(self):
        info = parse_derived_type(ERRVAR_FIXED_SOURCE, "ErrorVariables")
        return {"ErrorVariables": info}

    def test_sigma_c_params_include_errvar(self):
        """sigma with C-compatible ErrVar includes errorvariables_t* in C params."""
        sig = parse_signature_from_source(SIGMA_SOURCE, "sigma")
        registry = self._errvar_registry()
        params = build_c_params(sig, derived_types=registry)
        param_names = [p[1] for p in params]
        assert "ErrVar" in param_names
        errvar_param = [(t, n) for t, n in params if n == "ErrVar"]
        assert errvar_param[0][0] == "errorvariables_t*"

    def test_sigma_interface_has_c_ptr(self):
        """sigma interface uses TYPE(C_PTR), VALUE for C-compatible ErrVar."""
        sig = parse_signature_from_source(SIGMA_SOURCE, "sigma")
        registry = self._errvar_registry()
        iface = generate_fortran_interface_block(sig, derived_types=registry)
        assert "TYPE(C_PTR), VALUE :: ErrVar" in iface

    def test_sigma_wrapper_has_target(self):
        """Wrapper declares ErrVar with TARGET attribute for C_LOC."""
        sig = parse_signature_from_source(SIGMA_SOURCE, "sigma")
        registry = self._errvar_registry()
        wrapper = generate_fortran_wrapper(sig, derived_types=registry)
        assert "TARGET" in wrapper

    def test_sigma_wrapper_has_c_loc(self):
        """Wrapper passes C_LOC(ErrVar) to the C function."""
        sig = parse_signature_from_source(SIGMA_SOURCE, "sigma")
        registry = self._errvar_registry()
        wrapper = generate_fortran_wrapper(sig, derived_types=registry)
        assert "C_LOC(ErrVar)" in wrapper

    def test_interp1d_with_errvar_registry_includes_errvar(self):
        """interp1d with C-compatible ErrVar includes it in C params (not passthrough)."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        registry = self._errvar_registry()
        params = build_c_params(sig, derived_types=registry)
        param_names = [p[1] for p in params]
        assert "ErrVar" in param_names
        assert "xData" in param_names
        assert "n_xData" in param_names

    def test_interp1d_without_registry_excludes_errvar(self):
        """interp1d without registry treats ErrVar as passthrough (backward compat)."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        params = build_c_params(sig)
        param_names = [p[1] for p in params]
        assert "ErrVar" not in param_names

    def test_sigma_cpp_wrapper_has_errvar(self):
        """C++ wrapper for sigma includes errorvariables_t* ErrVar."""
        sig = parse_signature_from_source(SIGMA_SOURCE, "sigma")
        registry = self._errvar_registry()
        cpp = generate_cpp_wrapper(sig, "sigma.cpp", derived_types=registry)
        assert "errorvariables_t*" in cpp
        assert "ErrVar" in cpp


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
