"""
Tests for the VIT fortran_parser module.

Covers edge cases and features not exercised in test_core.py:
RESULT clauses, zero-arg subroutines, CHARACTER args, 2-D arrays,
multiple OPTIONAL args, module-enclosed functions, continuation lines,
derived type args, INTENT(OUT), assumed-shape vs fixed-size arrays,
and the find_function_in_file file-based lookup.
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.fortran_parser import (
    parse_signature_from_source,
    find_function_in_file,
    _is_libc_symbol,
    FortranSignature,
    FortranArg,
    SubprogramKind,
    IntentKind,
)


class TestParseSignature:

    def test_function_with_result_clause(self):
        """Parse a FUNCTION with an explicit RESULT clause."""
        source = """\
FUNCTION foo(x) RESULT(foo_result)
    REAL(8), INTENT(IN) :: x
    REAL(8) :: foo_result
END FUNCTION foo
"""
        sig = parse_signature_from_source(source, "foo")

        assert sig.name == "foo"
        assert sig.result_name == "foo_result"
        assert sig.result_type is not None
        assert "REAL" in sig.result_type.upper()
        assert sig.kind == SubprogramKind.FUNCTION

    def test_subroutine_no_args(self):
        """Parse a SUBROUTINE with an empty argument list."""
        source = """\
SUBROUTINE init()
    IMPLICIT NONE
END SUBROUTINE init
"""
        sig = parse_signature_from_source(source, "init")

        assert sig.kind == SubprogramKind.SUBROUTINE
        assert len(sig.args) == 0

    def test_function_with_character_arg(self):
        """Parse a SUBROUTINE with a CHARACTER argument."""
        source = """\
SUBROUTINE greet(msg, n)
    CHARACTER(*), INTENT(IN) :: msg
    INTEGER, INTENT(IN) :: n
END SUBROUTINE greet
"""
        sig = parse_signature_from_source(source, "greet")

        assert len(sig.args) == 2
        assert "CHARACTER" in sig.args[0].fortran_type.upper()
        assert sig.args[1].fortran_type.upper().startswith("INTEGER")

    def test_2d_array_arg(self):
        """Parse a SUBROUTINE with 2-D assumed-shape arrays."""
        source = """\
SUBROUTINE matmul_sub(A, B, C, n, m)
    REAL(8), DIMENSION(:,:), INTENT(IN) :: A, B
    REAL(8), DIMENSION(:,:), INTENT(OUT) :: C
    INTEGER, INTENT(IN) :: n, m
END SUBROUTINE matmul_sub
"""
        sig = parse_signature_from_source(source, "matmul_sub")

        a_arg = sig.args[0]
        assert a_arg.name.upper() == "A"
        assert a_arg.is_array is True
        assert a_arg.array_dims is not None
        assert ":" in a_arg.array_dims
        # 2-D: should have two colon-separated dimensions
        assert a_arg.array_dims.count(":") == 2
        assert a_arg.is_assumed_shape is True

    def test_multiple_optional_args(self):
        """Parse a FUNCTION with multiple OPTIONAL arguments."""
        source = """\
FUNCTION interp(x, y, z, method, tol) RESULT(r)
    REAL(8), INTENT(IN) :: x, y, z
    INTEGER, OPTIONAL, INTENT(IN) :: method
    REAL(8), OPTIONAL, INTENT(IN) :: tol
    REAL(8) :: r
END FUNCTION interp
"""
        sig = parse_signature_from_source(source, "interp")

        # Find args by name
        args_by_name = {a.name.lower(): a for a in sig.args}
        assert args_by_name["method"].is_optional is True
        assert args_by_name["tol"].is_optional is True
        assert args_by_name["x"].is_optional is False
        assert args_by_name["y"].is_optional is False
        assert args_by_name["z"].is_optional is False

    def test_function_in_module(self):
        """Parse a FUNCTION defined inside a MODULE block."""
        source = """\
MODULE MyModule
USE Constants
IMPLICIT NONE
CONTAINS
    FUNCTION add(a, b) RESULT(c)
        REAL(8), INTENT(IN) :: a, b
        REAL(8) :: c
        c = a + b
    END FUNCTION add
END MODULE MyModule
"""
        sig = parse_signature_from_source(source, "add")

        assert sig.name == "add"
        assert len(sig.args) == 2
        # parse_signature_from_source does not set module_name;
        # that is done by find_function_in_file
        assert sig.kind == SubprogramKind.FUNCTION

    def test_multiline_continuation(self):
        """Parse a FUNCTION with continuation lines (&) in the signature."""
        source = """\
FUNCTION long_name(alpha, beta, &
                    gamma, delta) RESULT(r)
    REAL(8), INTENT(IN) :: alpha
    REAL(8), INTENT(IN) :: beta
    REAL(8), INTENT(IN) :: gamma
    REAL(8), INTENT(IN) :: delta
    REAL(8) :: r
END FUNCTION long_name
"""
        sig = parse_signature_from_source(source, "long_name")

        assert len(sig.args) == 4
        arg_names = [a.name.lower() for a in sig.args]
        assert arg_names == ["alpha", "beta", "gamma", "delta"]

    def test_derived_type_arg(self):
        """Parse a SUBROUTINE with derived TYPE arguments."""
        source = """\
SUBROUTINE process(ErrVar, LocalVar)
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar
END SUBROUTINE process
"""
        sig = parse_signature_from_source(source, "process")

        assert len(sig.args) == 2
        assert sig.args[0].fortran_type == "TYPE(ErrorVariables)"
        assert sig.args[1].fortran_type == "TYPE(LocalVariables)"
        assert sig.args[0].is_derived_type is True
        assert sig.args[0].derived_type_name == "ErrorVariables"
        assert sig.args[1].derived_type_name == "LocalVariables"

    def test_intent_out(self):
        """Parse a SUBROUTINE with an INTENT(OUT) argument."""
        source = """\
SUBROUTINE get_value(x, result)
    REAL(8), INTENT(IN) :: x
    REAL(8), INTENT(OUT) :: result
END SUBROUTINE get_value
"""
        sig = parse_signature_from_source(source, "get_value")

        args_by_name = {a.name.lower(): a for a in sig.args}
        assert args_by_name["x"].intent == IntentKind.IN
        assert args_by_name["result"].intent == IntentKind.OUT

    def test_assumed_shape_vs_fixed(self):
        """Distinguish assumed-shape (:) from fixed-size array dimensions."""
        source = """\
SUBROUTINE mixed(a, b, c)
    REAL(8), DIMENSION(:), INTENT(IN) :: a
    REAL(8), DIMENSION(3), INTENT(IN) :: b
    REAL(8), DIMENSION(:,:), INTENT(OUT) :: c
END SUBROUTINE mixed
"""
        sig = parse_signature_from_source(source, "mixed")

        args_by_name = {a.name.lower(): a for a in sig.args}

        assert args_by_name["a"].is_assumed_shape is True
        assert args_by_name["a"].is_array is True

        assert args_by_name["b"].is_assumed_shape is False
        assert args_by_name["b"].is_array is True

        assert args_by_name["c"].is_assumed_shape is True
        assert args_by_name["c"].is_array is True

    def test_assumed_size_array(self):
        """Detect assumed-size (*) arrays and distinguish from assumed-shape (:)."""
        source = """\
SUBROUTINE handler(swap, data, n)
    REAL, INTENT(INOUT) :: swap(*)
    REAL(8), DIMENSION(:), INTENT(IN) :: data
    INTEGER, INTENT(IN) :: n
END SUBROUTINE handler
"""
        sig = parse_signature_from_source(source, "handler")
        args_by_name = {a.name.lower(): a for a in sig.args}

        assert args_by_name["swap"].is_assumed_size is True
        assert args_by_name["swap"].is_assumed_shape is False
        assert args_by_name["swap"].is_array is True

        assert args_by_name["data"].is_assumed_size is False
        assert args_by_name["data"].is_assumed_shape is True

        assert args_by_name["n"].is_assumed_size is False
        assert args_by_name["n"].is_array is False


class TestFindFunctionInFile:

    def test_finds_existing_function(self, tmp_path):
        """find_function_in_file returns a correct signature for a known function."""
        source = """\
MODULE TestMod
IMPLICIT NONE
CONTAINS
    FUNCTION square(x) RESULT(r)
        REAL(8), INTENT(IN) :: x
        REAL(8) :: r
        r = x * x
    END FUNCTION square
END MODULE TestMod
"""
        f = tmp_path / "test_mod.f90"
        f.write_text(source)

        sig = find_function_in_file(str(f), "square")

        assert sig.name == "square"
        assert sig.kind == SubprogramKind.FUNCTION
        assert sig.source_file == str(f)
        assert sig.module_name == "TestMod"
        assert len(sig.args) == 1
        assert sig.result_name == "r"

    def test_returns_none_for_missing(self, tmp_path):
        """find_function_in_file raises ValueError for a function not in the file."""
        source = """\
SUBROUTINE hello()
    IMPLICIT NONE
END SUBROUTINE hello
"""
        f = tmp_path / "hello.f90"
        f.write_text(source)

        with pytest.raises(ValueError):
            find_function_in_file(str(f), "nonexistent")

    def test_finds_subroutine(self, tmp_path):
        """find_function_in_file works for subroutines, not just functions."""
        source = """\
SUBROUTINE reset(val, default_val)
    REAL(8), INTENT(OUT) :: val
    REAL(8), INTENT(IN) :: default_val
    val = default_val
END SUBROUTINE reset
"""
        f = tmp_path / "reset.f90"
        f.write_text(source)

        sig = find_function_in_file(str(f), "reset")

        assert sig.kind == SubprogramKind.SUBROUTINE
        assert sig.source_file == str(f)
        assert len(sig.args) == 2
        assert sig.args[0].intent == IntentKind.OUT
        assert sig.args[1].intent == IntentKind.IN


class TestSourceCasingAndLibcCollision:
    """Test that VIT preserves Fortran source casing and detects libc collisions."""

    def test_preserves_pascal_case_from_source(self):
        """Function name casing comes from the Fortran source, not CLI arg."""
        source = """\
SUBROUTINE Shutdown(LocalVar, CntrPar)
    INTEGER, INTENT(INOUT) :: LocalVar
    INTEGER, INTENT(IN) :: CntrPar
END SUBROUTINE Shutdown
"""
        # CLI arg is lowercase, but source has PascalCase
        sig = parse_signature_from_source(source, "shutdown")
        assert sig.name == "Shutdown"

    def test_preserves_lowercase_from_source(self):
        """All-lowercase source names are preserved as-is."""
        source = """\
REAL(8) FUNCTION saturate(inputSignal, minValue, maxValue)
    REAL(8), INTENT(IN) :: inputSignal, minValue, maxValue
    saturate = MAX(minValue, MIN(inputSignal, maxValue))
END FUNCTION saturate
"""
        sig = parse_signature_from_source(source, "saturate")
        assert sig.name == "saturate"

    def test_c_func_name_always_lowercase(self):
        """The _c wrapper name is always lowercase for backward compatibility."""
        source = """\
SUBROUTINE WindSpeedEstimator(x)
    INTEGER, INTENT(INOUT) :: x
END SUBROUTINE WindSpeedEstimator
"""
        sig = parse_signature_from_source(source, "WindSpeedEstimator")
        assert sig.c_func_name == "windspeedestimator_c"

    def test_is_libc_symbol_detects_shutdown(self):
        """shutdown is a POSIX system call and should be detected."""
        assert _is_libc_symbol("shutdown") is True

    def test_is_libc_symbol_case_sensitive(self):
        """PascalCase names don't collide with lowercase libc symbols."""
        assert _is_libc_symbol("Shutdown") is False

    def test_is_libc_symbol_non_posix(self):
        """Application-specific names aren't in libc."""
        assert _is_libc_symbol("saturate") is False
        assert _is_libc_symbol("WindSpeedEstimator") is False

    def test_cpp_impl_name_pascal_case_no_suffix(self):
        """PascalCase names don't get _impl suffix even if lowercase form is in libc."""
        sig = FortranSignature(
            name="Shutdown", kind=SubprogramKind.SUBROUTINE,
            args=[], line_number=1,
        )
        assert sig.cpp_impl_name == "Shutdown"

    def test_cpp_impl_name_lowercase_libc_gets_suffix(self):
        """All-lowercase names that match libc symbols get _impl suffix."""
        sig = FortranSignature(
            name="shutdown", kind=SubprogramKind.SUBROUTINE,
            args=[], line_number=1,
        )
        assert sig.cpp_impl_name == "shutdown_impl"

    def test_cpp_impl_name_non_libc_no_suffix(self):
        """Names not in libc are returned as-is."""
        sig = FortranSignature(
            name="saturate", kind=SubprogramKind.FUNCTION,
            args=[], line_number=1,
        )
        assert sig.cpp_impl_name == "saturate"
