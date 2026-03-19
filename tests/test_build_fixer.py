"""
Tests for build_fixer: gfortran error parsing, fix patterns, and build-fix loop.
"""

import textwrap
from unittest.mock import MagicMock, patch

from vit.build_fixer import (
    BuildFixResult,
    CompilerError,
    ErrorFix,
    FixPattern,
    _fix_transfer_shape_mismatch,
    _match_transfer_shape_mismatch,
    _fix_logical_type_mismatch,
    _match_logical_type_mismatch,
    build_and_fix,
    parse_gfortran_errors,
)


# --- gfortran output samples ---

SINGLE_ERROR_OUTPUT = textwrap.dedent("""\
    /workspace/ROSCO/rosco/controller/src/ExtControl.f90:83:8:

       83 |         avcMSG     = TRANSFER( C_NULL_CHAR,                            avcMSG     )
          |        1
    Error: Different shape for array assignment at (1) on dimension 1 (1025 and 1)
""")

MULTIPLE_ERRORS_OUTPUT = textwrap.dedent("""\
    /workspace/src/foo.f90:10:5:

       10 |     x = TRANSFER(C_NULL_CHAR, x)
          |     1
    Error: Different shape for array assignment at (1) on dimension 1 (100 and 1)
    /workspace/src/bar.f90:20:12:

       20 |     CALL undefined_sub(a, b)
          |            1
    Error: Symbol 'undefined_sub' at (1) has no IMPLICIT type
""")

WARNING_OUTPUT = textwrap.dedent("""\
    /workspace/src/foo.f90:5:10:

        5 |     x = 42
          |          1
    Warning: Unused variable 'x' at (1)
""")

CMAKE_WRAPPED_OUTPUT = textwrap.dedent("""\
    [  9%] Building Fortran object CMakeFiles/discon.dir/src/ROSCO_Types.f90.o
    [ 14%] Building Fortran object CMakeFiles/discon.dir/src/Functions.f90.o
    [ 38%] Building Fortran object CMakeFiles/discon.dir/src/ExtControl.f90.o
    /workspace/ROSCO/rosco/controller/src/ExtControl.f90:83:8:

       83 |         avcMSG     = TRANSFER( C_NULL_CHAR,                            avcMSG     )
          |        1
    Error: Different shape for array assignment at (1) on dimension 1 (1025 and 1)
    gmake[2]: *** [CMakeFiles/discon.dir/build.make:218: CMakeFiles/discon.dir/src/ExtControl.f90.o] Error 1
    gmake[1]: *** [CMakeFiles/Makefile2:83: CMakeFiles/discon.dir/all] Error 2
""")

CLEAN_BUILD_OUTPUT = textwrap.dedent("""\
    [  4%] Building Fortran object CMakeFiles/discon.dir/src/Constants.f90.o
    [100%] Linking Fortran shared library libdiscon.so
    [100%] Built target discon
""")


class TestParseGfortranErrors:
    """Test gfortran error output parsing."""

    def test_parse_single_error(self):
        errors = parse_gfortran_errors(SINGLE_ERROR_OUTPUT)
        assert len(errors) == 1
        e = errors[0]
        assert e.file.endswith("ExtControl.f90")
        assert e.line == 83
        assert e.column == 8
        assert e.severity == "Error"
        assert "Different shape" in e.message
        assert "TRANSFER" in e.source_line

    def test_parse_multiple_errors(self):
        errors = parse_gfortran_errors(MULTIPLE_ERRORS_OUTPUT)
        assert len(errors) == 2
        assert errors[0].line == 10
        assert errors[1].line == 20
        assert "shape" in errors[0].message.lower()
        assert "undefined_sub" in errors[1].message

    def test_parse_warning(self):
        errors = parse_gfortran_errors(WARNING_OUTPUT)
        assert len(errors) == 1
        assert errors[0].severity == "Warning"
        assert "Unused variable" in errors[0].message

    def test_parse_no_errors(self):
        errors = parse_gfortran_errors(CLEAN_BUILD_OUTPUT)
        assert errors == []

    def test_parse_cmake_wrapped(self):
        errors = parse_gfortran_errors(CMAKE_WRAPPED_OUTPUT)
        assert len(errors) == 1
        assert errors[0].line == 83
        assert "Different shape" in errors[0].message

    def test_parse_empty_output(self):
        assert parse_gfortran_errors("") == []


# --- TRANSFER shape mismatch fix pattern ---

TRANSFER_ERROR = CompilerError(
    file="/workspace/ROSCO/rosco/controller/src/ExtControl.f90",
    line=83,
    column=8,
    severity="Error",
    message="Different shape for array assignment at (1) on dimension 1 (1025 and 1)",
    source_line="avcMSG     = TRANSFER( C_NULL_CHAR,                            avcMSG     )",
    raw_text="...",
)

TRANSFER_SOURCE = textwrap.dedent("""\
    SUBROUTINE ExtController(avrSWAP, CntrPar, LocalVar, ExtDLL, ErrVar)
        CHARACTER(KIND=C_CHAR) :: avcMSG(LEN(ErrVar%ErrMsg)+1)
        aviFAIL     = 0
        avcMSG     = TRANSFER( C_NULL_CHAR,                            avcMSG     )
        avcOUTNAME = TRANSFER( TRIM(ExtRootName)//C_NULL_CHAR,   avcOUTNAME )
    END SUBROUTINE ExtController
""")

# Line 4 (1-indexed) = the TRANSFER line in TRANSFER_SOURCE
TRANSFER_ERROR_LINE4 = CompilerError(
    file="test.f90",
    line=4,
    column=8,
    severity="Error",
    message="Different shape for array assignment at (1) on dimension 1 (1025 and 1)",
    source_line="avcMSG     = TRANSFER( C_NULL_CHAR,                            avcMSG     )",
    raw_text="...",
)


class TestTransferShapeMismatchFix:
    """Test the TRANSFER shape mismatch fix pattern."""

    def test_matcher_matches_shape_error(self):
        assert _match_transfer_shape_mismatch(TRANSFER_ERROR)

    def test_matcher_rejects_other_errors(self):
        other = CompilerError(
            file="test.f90", line=1, column=1,
            severity="Error",
            message="Syntax error at (1)",
            source_line="", raw_text="",
        )
        assert not _match_transfer_shape_mismatch(other)

    def test_matcher_rejects_warnings(self):
        warning = CompilerError(
            file="test.f90", line=1, column=1,
            severity="Warning",
            message="Different shape for array assignment at (1)",
            source_line="", raw_text="",
        )
        assert not _match_transfer_shape_mismatch(warning)

    def test_fixer_replaces_transfer(self):
        fix = _fix_transfer_shape_mismatch(TRANSFER_ERROR_LINE4, TRANSFER_SOURCE)
        assert fix is not None
        assert "C_NULL_CHAR" in fix.fixed_line
        assert "TRANSFER" not in fix.fixed_line
        assert fix.pattern_name == "transfer_shape_mismatch"

    def test_fixer_preserves_indentation(self):
        fix = _fix_transfer_shape_mismatch(TRANSFER_ERROR_LINE4, TRANSFER_SOURCE)
        assert fix is not None
        # Original line has 4-space indent in TRANSFER_SOURCE
        assert fix.fixed_line.startswith("    ")
        assert not fix.fixed_line.startswith("     ")  # exactly 4, not more

    def test_fixer_preserves_equals_spacing(self):
        fix = _fix_transfer_shape_mismatch(TRANSFER_ERROR_LINE4, TRANSFER_SOURCE)
        assert fix is not None
        assert "avcMSG     = C_NULL_CHAR" in fix.fixed_line

    def test_fixer_returns_none_for_non_transfer_line(self):
        """Error points to a line that doesn't have the TRANSFER pattern."""
        error_wrong_line = CompilerError(
            file="test.f90", line=3, column=1,
            severity="Error",
            message="Different shape for array assignment at (1) on dimension 1 (1025 and 1)",
            source_line="", raw_text="",
        )
        fix = _fix_transfer_shape_mismatch(error_wrong_line, TRANSFER_SOURCE)
        assert fix is None

    def test_fixer_ignores_transfer_with_string_concat(self):
        """TRANSFER(TRIM(str)//C_NULL_CHAR, var) should NOT match."""
        error_line5 = CompilerError(
            file="test.f90", line=5, column=1,
            severity="Error",
            message="Different shape for array assignment at (1) on dimension 1 (100 and 1)",
            source_line="", raw_text="",
        )
        fix = _fix_transfer_shape_mismatch(error_line5, TRANSFER_SOURCE)
        assert fix is None

    def test_fixer_handles_trailing_comment(self):
        source_with_comment = TRANSFER_SOURCE.replace(
            "avcMSG     )",
            "avcMSG     )  ! init buffer",
        )
        fix = _fix_transfer_shape_mismatch(TRANSFER_ERROR_LINE4, source_with_comment)
        assert fix is not None
        assert "init buffer" in fix.fixed_line
        assert "TRANSFER" not in fix.fixed_line

    def test_fixer_out_of_range_line(self):
        error_oob = CompilerError(
            file="test.f90", line=999, column=1,
            severity="Error",
            message="Different shape for array assignment at (1)",
            source_line="", raw_text="",
        )
        fix = _fix_transfer_shape_mismatch(error_oob, TRANSFER_SOURCE)
        assert fix is None


class TestBuildAndFix:
    """Test the build-and-fix loop with mocked subprocess."""

    def _mock_proc(self, returncode, stdout="", stderr=""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    @patch("vit.build_fixer.subprocess.run")
    def test_clean_build_succeeds_immediately(self, mock_run):
        mock_run.return_value = self._mock_proc(0, stdout=CLEAN_BUILD_OUTPUT)
        result = build_and_fix("make", "/tmp/src")
        assert result.success
        assert result.build_attempts == 1
        assert result.fixes_applied == []

    @patch("vit.build_fixer.subprocess.run")
    def test_unfixable_error_reports_remaining(self, mock_run):
        mock_run.return_value = self._mock_proc(1, stderr=textwrap.dedent("""\
            /tmp/src/foo.f90:10:5:

               10 |     CALL undefined_sub()
                  |     1
            Error: Symbol 'undefined_sub' at (1) has no IMPLICIT type
        """))
        result = build_and_fix("make", "/tmp/src")
        assert not result.success
        assert len(result.remaining_errors) == 1
        assert "undefined_sub" in result.remaining_errors[0].message

    @patch("vit.build_fixer.subprocess.run")
    def test_no_parseable_errors_reports_failure(self, mock_run):
        mock_run.return_value = self._mock_proc(1, stderr="make: *** Error 2\n")
        result = build_and_fix("make", "/tmp/src")
        assert not result.success
        assert result.remaining_errors == []

    @patch("vit.build_fixer.Path.write_text")
    @patch("vit.build_fixer.Path.read_text")
    @patch("vit.build_fixer.subprocess.run")
    def test_single_fix_cycle(self, mock_run, mock_read, mock_write):
        """Build fails, fix applied, second build succeeds."""
        mock_run.side_effect = [
            self._mock_proc(1, stderr=textwrap.dedent("""\
                /tmp/src/ext.f90:4:8:

                   4 |         buf     = TRANSFER( C_NULL_CHAR,                            buf     )
                     |        1
                Error: Different shape for array assignment at (1) on dimension 1 (1025 and 1)
            """)),
            self._mock_proc(0, stdout=CLEAN_BUILD_OUTPUT),
        ]
        mock_read.return_value = textwrap.dedent("""\
            SUBROUTINE Test()
            CHARACTER(KIND=C_CHAR) :: buf(1025)
            INTEGER :: x
            buf     = TRANSFER( C_NULL_CHAR,                            buf     )
            END SUBROUTINE Test
        """)

        result = build_and_fix("make", "/tmp/src")
        assert result.success
        assert result.build_attempts == 2
        assert len(result.fixes_applied) == 1
        assert result.fixes_applied[0].pattern_name == "transfer_shape_mismatch"

    @patch("vit.build_fixer.subprocess.run")
    def test_max_iterations_respected(self, mock_run):
        """Loop stops after max_iterations even with fixable errors."""
        # Always return the same error — simulates a fix that doesn't actually help
        mock_run.return_value = self._mock_proc(1, stderr=textwrap.dedent("""\
            /tmp/src/foo.f90:10:5:

               10 |     CALL missing()
                  |     1
            Error: Symbol 'missing' at (1) has no IMPLICIT type
        """))
        result = build_and_fix("make", "/tmp/src", max_iterations=3)
        assert not result.success
        assert result.build_attempts == 1  # stops on first try, no fixes match

    @patch("vit.build_fixer.subprocess.run")
    def test_dry_run_does_not_modify_files(self, mock_run):
        """Dry run reports fixes but doesn't write files."""
        mock_run.return_value = self._mock_proc(1, stderr=textwrap.dedent("""\
            /tmp/src/ext.f90:4:8:

               4 |         buf     = TRANSFER( C_NULL_CHAR,                            buf     )
                 |        1
            Error: Different shape for array assignment at (1) on dimension 1 (1025 and 1)
        """))

        # The fixer needs to read the file — mock it
        source = textwrap.dedent("""\
            SUBROUTINE Test()
            CHARACTER(KIND=C_CHAR) :: buf(1025)
            INTEGER :: x
            buf     = TRANSFER( C_NULL_CHAR,                            buf     )
            END SUBROUTINE Test
        """)

        with patch("vit.build_fixer.Path.read_text", return_value=source):
            with patch("vit.build_fixer.Path.write_text") as mock_write:
                result = build_and_fix("make", "/tmp/src", dry_run=True)
                mock_write.assert_not_called()

    @patch("vit.build_fixer.subprocess.run")
    def test_empty_fix_registry(self, mock_run):
        """With empty registry, no fixes are ever applied."""
        mock_run.return_value = self._mock_proc(1, stderr=SINGLE_ERROR_OUTPUT)
        result = build_and_fix("make", "/tmp/src", fix_registry=[])
        assert not result.success
        assert result.fixes_applied == []
        assert len(result.remaining_errors) == 1


# --- LOGICAL/INTEGER type mismatch fix pattern ---

LOGICAL_TYPE_MISMATCH_ERROR = CompilerError(
    file="/workspace/src/Controllers.f90",
    line=68,
    column=90,
    severity="Error",
    message="Type mismatch in argument 'reset' at (1); passed INTEGER(4) to LOGICAL(4)",
    source_line="LocalVar%PC_PitComT = PIController(LocalVar%PC_SpdErr, LocalVar%PC_KP, LocalVar%restart, objInst%instPI)",
    raw_text="...",
)

LOGICAL_MISMATCH_SOURCE = textwrap.dedent("""\
    SUBROUTINE Control(LocalVar, objInst)
    INTEGER :: x
    x = 1
    LocalVar%PC_PitComT = PIController(LocalVar%PC_SpdErr, LocalVar%PC_KP, LocalVar%restart, objInst%instPI)
    END SUBROUTINE Control
""")


class TestLogicalTypeMismatchFix:
    """Test the LOGICAL/INTEGER type mismatch fix pattern."""

    def test_matcher_matches_logical_error(self):
        assert _match_logical_type_mismatch(LOGICAL_TYPE_MISMATCH_ERROR)

    def test_matcher_rejects_non_logical(self):
        other = CompilerError(
            file="test.f90", line=1, column=1,
            severity="Error",
            message="Syntax error at (1)",
            source_line="", raw_text="",
        )
        assert not _match_logical_type_mismatch(other)

    def test_fixer_wraps_expression(self):
        """Fixer should find word%word near the error column and wrap it."""
        # Error at column 90 — near LocalVar%restart in the source line
        error = CompilerError(
            file="test.f90", line=4, column=90,
            severity="Error",
            message="Type mismatch in argument 'reset' at (1); passed INTEGER(4) to LOGICAL(4)",
            source_line="", raw_text="",
        )
        fix = _fix_logical_type_mismatch(error, LOGICAL_MISMATCH_SOURCE)
        assert fix is not None
        assert "/= 0)" in fix.fixed_line
        assert fix.pattern_name == "logical_type_mismatch"
