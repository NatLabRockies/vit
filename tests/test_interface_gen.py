"""
Tests for VIT interface_gen module.

Tests the mechanical Fortran-to-C interface generation:
- build_c_params: converts FortranArg list to C parameter tuples
- generate_cpp_wrapper: generates extern "C" wrapper source
- generate_fortran_interface_block: generates BIND(C) interface block
- generate_fortran_wrapper: generates Fortran wrapper function/subroutine

Uses synthetic FortranSignature/FortranArg objects constructed directly
(not parsed from source) to isolate interface_gen from the parser.
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.interface_gen import (
    generate_cpp_wrapper,
    generate_fortran_interface_block,
    generate_fortran_wrapper,
    build_c_params,
)
from vit.fortran_parser import FortranSignature, FortranArg, SubprogramKind, IntentKind
from vit.derived_type_parser import DerivedTypeInfo, DerivedTypeField


# Standard kind aliases matching ROSCO project
KIND_ALIASES = {
    "ReKi": "REAL(8)",
    "DbKi": "REAL(8)",
    "SiKi": "REAL(4)",
    "IntKi": "INTEGER(4)",
}


# --- Derived type fixtures for reuse across tests ---

def _make_bind_c_type_info(name, fields=None):
    """Create a DerivedTypeInfo that is C-compatible (bind_c strategy)."""
    return DerivedTypeInfo(
        name=name,
        fields=fields or [
            DerivedTypeField(name="ErrStat", fortran_type="INTEGER(IntKi)"),
        ],
        is_bind_c=True,
        source_module="ROSCO_Types",
    )


def _make_view_type_info(name, fields=None):
    """Create a DerivedTypeInfo that uses the view strategy."""
    info = DerivedTypeInfo(
        name=name,
        fields=fields or [
            DerivedTypeField(
                name="PC_GS_angles",
                fortran_type="REAL(DbKi)",
                is_allocatable=True,
                is_array=True,
                array_dims=":",
            ),
        ],
        source_module="ROSCO_Types",
    )
    info.view_strategy = "view"
    return info


ERRVAR_INFO = _make_bind_c_type_info("ErrorVariables")
ERRVAR_REGISTRY = {"ErrorVariables": ERRVAR_INFO}

CNTRPAR_INFO = _make_view_type_info("ControlParameters")
CNTRPAR_REGISTRY = {"ControlParameters": CNTRPAR_INFO}


# === TestBuildCParams ===

class TestBuildCParams:
    """Test build_c_params() which converts Fortran args to C parameter list."""

    def test_scalar_intent_in(self):
        """REAL(DbKi) INTENT(IN) scalar maps to ("double", "val")."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 1
        assert params[0] == ("double", "val")

    def test_scalar_intent_inout(self):
        """REAL(DbKi) INTENT(INOUT) scalar maps to ("double*", "val")."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.INOUT),
            ],
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 1
        assert params[0] == ("double*", "val")

    def test_scalar_intent_out(self):
        """INTEGER(IntKi) INTENT(OUT) scalar maps to ("int*", "n")."""
        sig = FortranSignature(
            name="mysub",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="n", fortran_type="INTEGER(IntKi)",
                           intent=IntentKind.OUT),
            ],
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 1
        assert params[0] == ("int*", "n")

    def test_assumed_shape_array(self):
        """REAL(DbKi) DIMENSION(:) INTENT(IN) produces pointer + size param."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="arr", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN,
                           is_array=True, array_dims=":"),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 2
        assert params[0] == ("double*", "arr")
        assert params[1] == ("int", "n_arr")

    def test_fixed_size_array(self):
        """REAL(DbKi) DIMENSION(3) INTENT(INOUT) produces pointer, NO n_ param."""
        sig = FortranSignature(
            name="mysub",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="arr", fortran_type="REAL(DbKi)",
                           intent=IntentKind.INOUT,
                           is_array=True, array_dims="3"),
            ],
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 1
        assert params[0] == ("double*", "arr")

    def test_derived_type_bind_c(self):
        """TYPE(ErrorVariables) INTENT(INOUT) with bind_c maps to struct pointer."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="ErrVar", fortran_type="TYPE(ErrorVariables)",
                           intent=IntentKind.INOUT),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES, ERRVAR_REGISTRY)
        assert len(params) == 1
        assert params[0] == ("errorvariables_t*", "ErrVar")

    def test_derived_type_view(self):
        """TYPE(ControlParameters) INTENT(INOUT) with view strategy maps to view struct pointer."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="CntrPar", fortran_type="TYPE(ControlParameters)",
                           intent=IntentKind.INOUT),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES, CNTRPAR_REGISTRY)
        assert len(params) == 1
        assert params[0] == ("controlparameters_view_t*", "CntrPar")

    def test_optional_scalar(self):
        """REAL(DbKi) OPTIONAL INTENT(IN) produces has_ flag + value pair."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN, is_optional=True),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 2
        assert params[0] == ("int", "has_x")
        assert params[1] == ("double", "x")

    def test_optional_array(self):
        """REAL(DbKi) DIMENSION(:) OPTIONAL INTENT(IN) produces flag + pointer + size."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN, is_optional=True,
                           is_array=True, array_dims=":"),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES)
        assert len(params) == 3
        assert params[0] == ("int", "has_x")
        assert params[1] == ("double*", "x")
        assert params[2] == ("int", "n_x")

    def test_passthrough_skipped(self):
        """TYPE(UnknownType) with no derived_types entry is skipped (passthrough)."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="unk", fortran_type="TYPE(UnknownType)",
                           intent=IntentKind.INOUT),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        params = build_c_params(sig, KIND_ALIASES)
        param_names = [name for _, name in params]
        assert "val" in param_names
        assert "unk" not in param_names


# === TestGenerateCppWrapper ===

class TestGenerateCppWrapper:
    """Test generate_cpp_wrapper() for C++ extern "C" wrapper generation."""

    def test_scalar_function(self):
        """Function returning REAL(DbKi) with 3 scalar args produces correct wrapper."""
        sig = FortranSignature(
            name="saturate",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="minval", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="maxval", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="saturate_result",
        )
        cpp = generate_cpp_wrapper(sig, "saturate.cpp", KIND_ALIASES)
        assert 'extern "C"' in cpp
        assert "double saturate_c(double val, double minval, double maxval)" in cpp
        assert "return saturate(" in cpp

    def test_subroutine(self):
        """Subroutine with INOUT args produces void wrapper with no return."""
        sig = FortranSignature(
            name="update",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.INOUT),
                FortranArg(name="y", fortran_type="REAL(DbKi)",
                           intent=IntentKind.OUT),
            ],
        )
        cpp = generate_cpp_wrapper(sig, "update.cpp", KIND_ALIASES)
        assert "void update_c(" in cpp
        assert "return " not in cpp.split("update(")[0].split("{")[-1] or "return" not in cpp.replace("return update", "")
        # More precise: void function body should not have 'return'
        # The body calls update(...); without a return statement
        lines = cpp.split("\n")
        body_lines = [l for l in lines if "update(" in l and "return" not in l.lower()
                      or "update(" in l]
        assert any("update(x, y);" in l for l in lines)

    def test_includes_impl_file(self):
        """Verify the user's implementation file is #included."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        cpp = generate_cpp_wrapper(sig, "path/to/myimpl.cpp", KIND_ALIASES)
        assert '#include "myimpl.cpp"' in cpp

    def test_includes_struct_headers(self):
        """Struct headers passed via struct_headers are included."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        cpp = generate_cpp_wrapper(
            sig, "myfunc.cpp", KIND_ALIASES,
            struct_headers=["types.h"],
        )
        assert '#include "types.h"' in cpp

    def test_c_func_name_suffix(self):
        """The extern C function uses _c suffix from sig.c_func_name."""
        sig = FortranSignature(
            name="MyFunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        cpp = generate_cpp_wrapper(sig, "myfunc.cpp", KIND_ALIASES)
        assert "myfunc_c(" in cpp
        assert sig.c_func_name == "myfunc_c"


# === TestGenerateFortranInterfaceBlock ===

class TestGenerateFortranInterfaceBlock:
    """Test generate_fortran_interface_block() for BIND(C) interface generation."""

    def test_function_interface(self):
        """Scalar function interface has INTERFACE, BIND(C), FUNCTION, and END INTERFACE."""
        sig = FortranSignature(
            name="saturate",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="minval", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="maxval", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="saturate_result",
        )
        iface = generate_fortran_interface_block(sig, KIND_ALIASES)
        assert "INTERFACE" in iface
        assert "BIND(C, NAME='saturate_c')" in iface
        assert "FUNCTION" in iface
        assert "END FUNCTION saturate_c" in iface
        assert "END INTERFACE" in iface

    def test_subroutine_interface(self):
        """Subroutine interface has SUBROUTINE and no RESULT."""
        sig = FortranSignature(
            name="update",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.INOUT),
            ],
        )
        iface = generate_fortran_interface_block(sig, KIND_ALIASES)
        assert "SUBROUTINE" in iface
        assert "RESULT" not in iface

    def test_array_param_explicit_size(self):
        """Assumed-shape array arg generates integer size param + array with that size."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="arr", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN,
                           is_array=True, array_dims=":"),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        iface = generate_fortran_interface_block(sig, KIND_ALIASES)
        assert "n_arr" in iface
        assert "INTEGER(C_INT), VALUE :: n_arr" in iface
        # Array should be assumed-size (*) in the BIND(C) interface
        assert "arr(*)" in iface

    def test_contains_use_iso_c_binding(self):
        """Interface block must contain USE ISO_C_BINDING."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        iface = generate_fortran_interface_block(sig, KIND_ALIASES)
        assert "USE ISO_C_BINDING" in iface


# === TestGenerateFortranWrapper ===

class TestGenerateFortranWrapper:
    """Test generate_fortran_wrapper() for Fortran wrapper generation."""

    def test_function_wrapper_calls_c(self):
        """Scalar function wrapper contains call to _c function and assigns result."""
        sig = FortranSignature(
            name="saturate",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="minval", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="maxval", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="saturate_result",
        )
        wrapper = generate_fortran_wrapper(sig, KIND_ALIASES)
        assert "saturate_c(" in wrapper
        assert "saturate_result" in wrapper
        assert "FUNCTION saturate(" in wrapper
        assert "END FUNCTION saturate" in wrapper

    def test_subroutine_wrapper_calls_c(self):
        """Subroutine wrapper contains CALL to _c subroutine."""
        sig = FortranSignature(
            name="update",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.INOUT),
                FortranArg(name="y", fortran_type="REAL(DbKi)",
                           intent=IntentKind.OUT),
            ],
        )
        wrapper = generate_fortran_wrapper(sig, KIND_ALIASES)
        assert "CALL update_c(" in wrapper
        assert "SUBROUTINE update(" in wrapper
        assert "END SUBROUTINE update" in wrapper

    def test_wrapper_with_optional_args(self):
        """OPTIONAL arg produces PRESENT(x) check and has_ flag computation."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="val", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN),
                FortranArg(name="x", fortran_type="REAL(DbKi)",
                           intent=IntentKind.IN, is_optional=True),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
        )
        wrapper = generate_fortran_wrapper(sig, KIND_ALIASES)
        assert "PRESENT(x)" in wrapper
        assert "has_x_flag" in wrapper
        # The arg declaration should have OPTIONAL (kind may be resolved: REAL(8) instead of REAL(DbKi))
        found_optional = False
        for line in wrapper.split("\n"):
            if ":: x" in line and "REAL" in line:
                if "OPTIONAL" in line.upper():
                    found_optional = True
        assert found_optional, "x arg declaration should have OPTIONAL attribute"

    def test_wrapper_with_view_type(self):
        """View-type arg produces populate_ call and C_LOC usage."""
        sig = FortranSignature(
            name="myfunc",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg(name="CntrPar", fortran_type="TYPE(ControlParameters)",
                           intent=IntentKind.IN),
            ],
            result_type="REAL(DbKi)",
            result_name="myfunc_result",
            use_statements=["USE ROSCO_Types, ONLY: ControlParameters"],
        )
        wrapper = generate_fortran_wrapper(sig, KIND_ALIASES, CNTRPAR_REGISTRY)
        assert "vit_populate_controlparameters" in wrapper.lower()
        assert "C_LOC(" in wrapper


# === Run tests ===

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
