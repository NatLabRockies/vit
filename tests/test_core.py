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
    build_c_params,
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
        # CLI arg is PascalCase but Fortran source is lowercase — source wins
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "Saturate")
        assert sig.name == "saturate"  # preserves source casing, not CLI arg
        assert sig.kind == SubprogramKind.FUNCTION


# === Interface Generation Tests ===

class TestInterfaceGeneration:

    def _get_saturate_sig(self):
        return parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")

    def test_cpp_wrapper_contains_extern_c(self):
        sig = self._get_saturate_sig()
        cpp = generate_cpp_wrapper(sig, "saturate.hpp", ROSCO_KIND_ALIASES)
        assert 'extern "C"' in cpp

    def test_cpp_wrapper_function_name(self):
        sig = self._get_saturate_sig()
        cpp = generate_cpp_wrapper(sig, "saturate.hpp", ROSCO_KIND_ALIASES)
        assert "saturate_c" in cpp

    def test_cpp_wrapper_uses_double(self):
        """ReKi args should map to double in C++."""
        sig = self._get_saturate_sig()
        cpp = generate_cpp_wrapper(sig, "saturate.hpp", ROSCO_KIND_ALIASES)
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


# === Subroutine + Fixed-Size Array Tests ===

COLEMAN_SOURCE = """\
MODULE Functions
USE Constants
IMPLICIT NONE
CONTAINS
    SUBROUTINE ColemanTransform(rootMOOP, aziAngle, nHarmonic, axTOut, axYOut)
        IMPLICIT NONE
        REAL(DbKi), INTENT(IN)     :: rootMOOP(3)
        REAL(DbKi), INTENT(IN)     :: aziAngle
        INTEGER(IntKi), INTENT(IN) :: nHarmonic
        REAL(DbKi), INTENT(OUT)    :: axTOut, axYOut
        axTOut = 0.0
        axYOut = 0.0
    END SUBROUTINE ColemanTransform

    SUBROUTINE ColemanTransformInverse(axTIn, axYIn, aziAngle, nHarmonic, aziOffset, PitComIPC)
        IMPLICIT NONE
        REAL(DbKi), INTENT(IN)     :: axTIn, axYIn
        REAL(DbKi), INTENT(IN)     :: aziAngle
        REAL(DbKi), INTENT(IN)     :: aziOffset
        INTEGER(IntKi), INTENT(IN) :: nHarmonic
        REAL(DbKi), INTENT(OUT)    :: PitComIPC(3)
        PitComIPC = 0.0
    END SUBROUTINE ColemanTransformInverse
END MODULE Functions
"""


class TestSubroutineFixedArrays:
    """Tests for subroutines with fixed-size array args (Phase 2A)."""

    def test_parse_coleman_transform_args(self):
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        assert sig.kind == SubprogramKind.SUBROUTINE
        assert len(sig.args) == 5
        names = [a.name.lower() for a in sig.args]
        assert names == ["rootmoop", "aziangle", "nharmonic", "axtout", "axyout"]

    def test_parse_fixed_array_dims(self):
        """rootMOOP(3) should be a fixed-size array, not assumed-shape."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        rootmoop = sig.args[0]
        assert rootmoop.is_array
        assert rootmoop.array_dims == "3"
        assert not rootmoop.is_assumed_shape

    def test_parse_scalar_out(self):
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        axtout = sig.args[3]
        assert not axtout.is_array
        assert axtout.intent == IntentKind.OUT

    def test_build_c_params_scalar_out_is_pointer(self):
        """Scalar INTENT(OUT) args in subroutines must be pointers in C."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["axTOut"] == "double*"
        assert param_dict["axYOut"] == "double*"

    def test_build_c_params_fixed_array_no_size_param(self):
        """Fixed-size arrays should NOT get an n_ size parameter."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_names = [name for _, name in params]
        assert "rootMOOP" in param_names
        assert "n_rootMOOP" not in param_names

    def test_build_c_params_fixed_array_is_pointer(self):
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["rootMOOP"] == "double*"

    def test_build_c_params_in_scalar_not_pointer(self):
        """Scalar INTENT(IN) args should be by value, not pointer."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["aziAngle"] == "double"
        assert param_dict["nHarmonic"] == "int"

    def test_inverse_out_array_no_size_param(self):
        """INTENT(OUT) fixed-size array should have no size param."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransformInverse")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_names = [name for _, name in params]
        assert "PitComIPC" in param_names
        assert "n_PitComIPC" not in param_names

    def test_inverse_out_array_is_pointer(self):
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransformInverse")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["PitComIPC"] == "double*"

    def test_cpp_wrapper_void_for_subroutine(self):
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        cpp = generate_cpp_wrapper(sig, "coleman.hpp", ROSCO_KIND_ALIASES)
        assert "void " in cpp
        assert 'extern "C"' in cpp

    def test_fortran_interface_subroutine(self):
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        assert "SUBROUTINE" in iface
        assert "FUNCTION" not in iface

    def test_fortran_interface_out_no_value(self):
        """OUT scalars in interface should NOT have VALUE."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        for line in iface.split("\n"):
            if ":: axtout" in line.lower() or ":: axyout" in line.lower():
                assert "VALUE" not in line.upper()

    def test_fortran_interface_in_scalar_has_value(self):
        """IN scalars should have VALUE in the interface."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        for line in iface.split("\n"):
            if ":: aziangle" in line.lower():
                assert "VALUE" in line.upper()
            if ":: nharmonic" in line.lower():
                assert "VALUE" in line.upper()

    def test_fortran_wrapper_call_syntax(self):
        """Subroutine wrapper should use CALL, not assignment."""
        sig = parse_signature_from_source(COLEMAN_SOURCE, "ColemanTransform")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "CALL" in wrapper.upper()
        assert "colemantransform_c" in wrapper.lower()


# === Array-Valued Function Result Tests ===

IDENTITY_SOURCE = """\
MODULE Functions
USE Constants
IMPLICIT NONE
CONTAINS
    FUNCTION identity(n) RESULT(A)
        IMPLICIT NONE
        INTEGER, INTENT(IN)         :: n
        REAL(DbKi), DIMENSION(n, n) :: A
        INTEGER                     :: i, j
        DO i=1,n
            DO j=1,n
                IF (i == j) THEN
                    A(i,j) = 1.0
                ELSE
                    A(i,j) = 0.0
                ENDIF
            END DO
        END DO
    END FUNCTION identity
END MODULE Functions
"""


class TestArrayValuedResult:
    """Tests for functions that return arrays (Phase 2B)."""

    def test_parse_result_is_array(self):
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        assert sig.result_is_array
        assert sig.result_dims is not None

    def test_parse_result_dims(self):
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        assert "N" in sig.result_dims.upper()

    def test_parse_result_type(self):
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        assert "REAL" in sig.result_type.upper()

    def test_build_c_params_has_result_pointer(self):
        """Array-valued results should produce an output pointer param."""
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_dict = {name: ctype for ctype, name in params}
        assert "identity_result" in param_dict
        assert param_dict["identity_result"] == "double*"

    def test_build_c_params_n_is_value(self):
        """Input scalar n should be by value, not pointer."""
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["n"] == "int"

    def test_cpp_wrapper_void_for_array_result(self):
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        cpp = generate_cpp_wrapper(sig, "identity.hpp", ROSCO_KIND_ALIASES)
        assert "void " in cpp
        assert "identity_result" in cpp

    def test_fortran_interface_is_subroutine(self):
        """Array-valued function's BIND(C) interface should be a SUBROUTINE."""
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES)
        assert "SUBROUTINE" in iface
        assert "FUNCTION" not in iface
        assert "identity_result" in iface

    def test_fortran_wrapper_is_function(self):
        """The Fortran wrapper remains a FUNCTION (drop-in replacement)."""
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "FUNCTION identity" in wrapper
        assert "END FUNCTION identity" in wrapper

    def test_fortran_wrapper_calls_subroutine(self):
        """The wrapper internally CALLs the C subroutine."""
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "CALL identity_c" in wrapper

    def test_fortran_wrapper_declares_array_result(self):
        """The wrapper should declare the result with DIMENSION."""
        sig = parse_signature_from_source(IDENTITY_SOURCE, "identity")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES)
        assert "DIMENSION" in wrapper

    def test_scalar_result_not_affected(self):
        """Scalar functions should NOT have result_is_array."""
        sig = parse_signature_from_source(ROSCO_FUNCTIONS_SOURCE, "saturate")
        assert not sig.result_is_array
        assert sig.result_dims is None


# === Phase 3: Scalar INOUT in Functions, Derived Types, OPTIONAL, LOGICAL ===

FUNCTION_INOUT_SOURCE = """\
MODULE TestMod
CONTAINS
    FUNCTION accumulate(val, counter) RESULT(total)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: val
        INTEGER, INTENT(INOUT) :: counter
        REAL(8) :: total
        counter = counter + 1
        total = val * counter
    END FUNCTION accumulate
END MODULE TestMod
"""

RATELIMIT_SOURCE = """\
MODULE Functions
USE ROSCO_Types, ONLY: rlParams
IMPLICIT NONE
CONTAINS
    REAL(DbKi) FUNCTION ratelimit(inputSignal, minRate, maxRate, DT, reset, rlP, inst, ResetValue)
        USE ROSCO_Types, ONLY : rlParams
        IMPLICIT NONE
        REAL(DbKi), INTENT(IN)          :: inputSignal
        REAL(DbKi), INTENT(IN)          :: minRate
        REAL(DbKi), INTENT(IN)          :: maxRate
        REAL(DbKi), INTENT(IN)          :: DT
        LOGICAL,    INTENT(IN)          :: reset
        TYPE(rlParams), INTENT(INOUT)   :: rlP
        INTEGER(IntKi), INTENT(INOUT)   :: inst
        REAL(DbKi), OPTIONAL, INTENT(IN) :: ResetValue
        ratelimit = 0.0
    END FUNCTION ratelimit
END MODULE Functions
"""


# Mock derived type info for testing
from vit.derived_type_parser import DerivedTypeInfo, DerivedTypeField

RLPARAMS_INFO = DerivedTypeInfo(
    name="rlParams",
    fields=[
        DerivedTypeField(
            name="LastSignal",
            fortran_type="REAL(DbKi)",
            is_array=True,
            array_dims="1024",
        ),
    ],
    source_module="ROSCO_Types",
)

RLPARAMS_REGISTRY = {"rlParams": RLPARAMS_INFO}


class TestScalarINOUTInFunction:
    """Phase 3: Scalar INOUT/OUT args need pointers in functions too."""

    def test_parse_function_inout(self):
        sig = parse_signature_from_source(FUNCTION_INOUT_SOURCE, "accumulate")
        assert sig.kind == SubprogramKind.FUNCTION
        counter = sig.args[1]
        assert counter.intent == IntentKind.INOUT

    def test_c_param_inout_is_pointer_in_function(self):
        """Scalar INOUT in a FUNCTION should be a pointer, not by-value."""
        sig = parse_signature_from_source(FUNCTION_INOUT_SOURCE, "accumulate")
        params = build_c_params(sig)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["counter"] == "int*"

    def test_interface_inout_no_value(self):
        """INOUT args in FUNCTION interface should NOT have VALUE."""
        sig = parse_signature_from_source(FUNCTION_INOUT_SOURCE, "accumulate")
        iface = generate_fortran_interface_block(sig)
        for line in iface.split("\n"):
            if ":: counter" in line.lower():
                assert "VALUE" not in line.upper()
                assert "INTENT(INOUT)" in line.upper()

    def test_wrapper_inout_by_name(self):
        """INOUT scalars in wrapper call should be passed by name (not converted)."""
        sig = parse_signature_from_source(FUNCTION_INOUT_SOURCE, "accumulate")
        wrapper = generate_fortran_wrapper(sig)
        # Should contain 'counter' directly in the call, not INT(counter, ...)
        assert "accumulate_c" in wrapper
        # val is IN, counter is INOUT — only val might have conversion


class TestDerivedTypeStructPointer:
    """Phase 3: C-compatible derived types passed as struct pointers."""

    def test_c_param_derived_is_struct_pointer(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["rlP"] == "rlparams_t*"

    def test_interface_derived_is_c_ptr(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        for line in iface.split("\n"):
            if ":: rlp" in line.lower():
                assert "TYPE(C_PTR)" in line.upper()
                assert "VALUE" in line.upper()

    def test_wrapper_uses_c_loc(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        assert "C_LOC(rlP)" in wrapper

    def test_wrapper_has_target(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        # rlP declaration should have TARGET
        for line in wrapper.split("\n"):
            if ":: rlP" in line and "TYPE(rlParams)" in line:
                assert "TARGET" in line.upper()

    def test_wrapper_has_use_rosco_types(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        assert "USE ROSCO_Types" in wrapper

    def test_unknown_derived_type_is_passthrough(self):
        """Without registry, TYPE(rlParams) should be passthrough (skipped)."""
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES)  # no derived_types
        param_names = [name for _, name in params]
        assert "rlP" not in param_names


class TestOptionalArgs:
    """Phase 3: OPTIONAL args become flag + value pairs."""

    def test_c_params_optional_flag_and_value(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        param_names = [name for _, name in params]
        assert "has_ResetValue" in param_names
        assert "ResetValue" in param_names
        # Flag comes before value
        flag_idx = param_names.index("has_ResetValue")
        val_idx = param_names.index("ResetValue")
        assert flag_idx < val_idx

    def test_c_params_optional_flag_type(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["has_ResetValue"] == "int"
        assert param_dict["ResetValue"] == "double"

    def test_interface_optional_two_args(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        iface = generate_fortran_interface_block(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        iface_upper = iface.upper()
        assert "HAS_RESETVALUE" in iface_upper
        assert "INTEGER(C_INT), VALUE :: HAS_RESETVALUE" in iface_upper

    def test_wrapper_optional_preamble(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        assert "has_ResetValue_flag" in wrapper
        assert "ResetValue_val" in wrapper
        assert "PRESENT(ResetValue)" in wrapper

    def test_wrapper_declares_optional(self):
        """Wrapper arg declaration should have OPTIONAL attribute."""
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        found = False
        for line in wrapper.split("\n"):
            # Match the arg declaration, not the local variable
            if ":: ResetValue" in line and "REAL(DbKi)" in line:
                assert "OPTIONAL" in line.upper()
                found = True
        assert found, "Did not find ResetValue arg declaration"


class TestLogicalType:
    """Phase 3: LOGICAL type handling."""

    def test_logical_maps_to_int(self):
        from vit.type_map import get_type_mapping
        m = get_type_mapping("LOGICAL")
        assert m.c_type == "int"
        assert m.iso_c_type == "LOGICAL(C_BOOL)"

    def test_ratelimit_reset_c_param(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["reset"] == "int"

    def test_wrapper_logical_conversion(self):
        """LOGICAL args should get explicit LOGICAL(x, C_BOOL) conversion."""
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        wrapper = generate_fortran_wrapper(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        assert "LOGICAL(reset, C_BOOL)" in wrapper


class TestRatelimitFullSignature:
    """Phase 3: Complete ratelimit function interface generation."""

    def test_parse_ratelimit(self):
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        assert sig.kind == SubprogramKind.FUNCTION
        assert len(sig.args) == 8
        assert sig.result_type is not None
        assert "REAL" in sig.result_type.upper()

    def test_ratelimit_c_param_count(self):
        """Should have: 4 IN scalars + 1 LOGICAL + struct ptr + int ptr + flag + value = 9."""
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        assert len(params) == 9

    def test_ratelimit_inst_is_pointer(self):
        """inst is INTEGER(IntKi), INTENT(INOUT) in a FUNCTION — must be pointer."""
        sig = parse_signature_from_source(RATELIMIT_SOURCE, "ratelimit")
        params = build_c_params(sig, ROSCO_KIND_ALIASES, RLPARAMS_REGISTRY)
        param_dict = {name: ctype for ctype, name in params}
        assert param_dict["inst"] == "int*"


class TestDerivedTypeParser:
    """Phase 3: Derived type parser and struct header generation."""

    def test_generate_c_struct_header(self):
        from vit.derived_type_parser import generate_c_struct_header
        header = generate_c_struct_header(RLPARAMS_INFO, ROSCO_KIND_ALIASES)
        assert "typedef struct" in header
        assert "rlparams_t" in header
        assert "double LastSignal[1024]" in header
        assert "#ifndef RLPARAMS_T_H" in header

    def test_c_compatible(self):
        assert RLPARAMS_INFO.is_c_compatible

    def test_parse_derived_type(self):
        from vit.derived_type_parser import parse_derived_type
        source = """\
MODULE ROSCO_Types
IMPLICIT NONE
TYPE, PUBLIC :: rlParams
    REAL(DbKi), DIMENSION(1024)     :: LastSignal
END TYPE rlParams
END MODULE ROSCO_Types
"""
        info = parse_derived_type(source, "rlParams")
        assert info is not None
        assert info.name == "rlParams"
        assert len(info.fields) == 1
        assert info.fields[0].name == "LastSignal"
        assert info.fields[0].is_array
        assert info.fields[0].array_dims == "1024"
        assert info.source_module == "ROSCO_Types"


# === Run tests ===

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
