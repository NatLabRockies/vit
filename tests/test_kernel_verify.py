"""
Tests for VIT kernel verification helpers.

Tests the pure string→string patching functions and output parser
from kernel_verify.py. No Docker, KGen, or real Fortran compilation needed.
"""

import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.kernel_verify import (
    _patch_nan_comparisons,
    _patch_field_value_logging,
    _patch_kernel_makefile,
    _fix_kgen_utils_mod_only,
    _fix_use_ordering,
    _add_use_iso_c_binding,
    _add_interface_block,
    _replace_function_body,
    _parse_verify_output,
)
from vit.fortran_parser import (
    FortranSignature,
    FortranArg,
    SubprogramKind,
    IntentKind,
)


# === _patch_nan_comparisons tests ===

class TestPatchNanComparisons:

    def test_basic_comparison(self):
        """Transforms a simple var%field == kgenref_var%field comparison."""
        content = "        IF (var%ptfmtdz == kgenref_var%ptfmtdz) THEN\n"
        result = _patch_nan_comparisons(content)

        assert "var%ptfmtdz /= var%ptfmtdz" in result
        assert "kgenref_var%ptfmtdz /= kgenref_var%ptfmtdz" in result
        assert ".OR." in result
        assert ".AND." in result
        assert "both NaN" in result

    def test_no_matching_lines(self):
        """Non-matching lines are unchanged."""
        content = "    x = 1.0\n    IF (a > b) THEN\n"
        result = _patch_nan_comparisons(content)
        assert result == content

    def test_multiple_fields(self):
        """Patches multiple comparisons in the same content."""
        content = (
            "        IF (var%field1 == kgenref_var%field1) THEN\n"
            "            ! identical\n"
            "        END IF\n"
            "        IF (var%field2 == kgenref_var%field2) THEN\n"
            "            ! identical\n"
            "        END IF\n"
        )
        result = _patch_nan_comparisons(content)
        assert result.count("/= var%field1") == 2  # guard + WRITE
        assert result.count("/= var%field2") == 2

    def test_field_name_with_underscore(self):
        """Field names with underscores are handled correctly."""
        content = "        IF (var%pc_pit_com == kgenref_var%pc_pit_com) THEN\n"
        result = _patch_nan_comparisons(content)
        assert "var%pc_pit_com /= var%pc_pit_com" in result

    def test_diagnostic_write_includes_field_name(self):
        """The WRITE diagnostic identifies the field by name."""
        content = "        IF (var%myfield == kgenref_var%myfield) THEN\n"
        result = _patch_nan_comparisons(content)
        assert 'localvar%myfield is IDENTICAL (both NaN' in result


# === _fix_kgen_utils_mod_only tests ===

class TestFixKgenUtilsModOnly:

    def test_single_line_only(self):
        """Removes single-line ONLY clause."""
        content = "    USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck\n    IMPLICIT NONE\n"
        result = _fix_kgen_utils_mod_only(content)
        assert "USE kgen_utils_mod\n" in result
        assert "ONLY" not in result

    def test_multiline_continuation(self):
        """Removes multi-line ONLY clause with & continuations."""
        content = (
            "    USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck, &\n"
            "        CHECK_IDENTICAL, CHECK_IN_TOL, CHECK_OUT_TOL\n"
            "    IMPLICIT NONE\n"
        )
        result = _fix_kgen_utils_mod_only(content)
        assert "USE kgen_utils_mod\n" in result
        assert "ONLY" not in result
        assert "CHECK_IDENTICAL" not in result
        assert "IMPLICIT NONE" in result

    def test_preserves_other_use_statements(self):
        """Other USE statements are not modified."""
        content = (
            "    USE some_other_mod, ONLY: foo\n"
            "    USE kgen_utils_mod, ONLY: kgen_dp\n"
            "    USE ISO_C_BINDING\n"
        )
        result = _fix_kgen_utils_mod_only(content)
        assert "USE some_other_mod, ONLY: foo" in result
        assert "USE ISO_C_BINDING" in result

    def test_preserves_indentation(self):
        """Indentation of the original USE line is preserved."""
        content = "        USE kgen_utils_mod, ONLY: kgen_dp\n"
        result = _fix_kgen_utils_mod_only(content)
        assert result.startswith("        USE kgen_utils_mod")

    def test_no_only_clause_noop(self):
        """USE kgen_utils_mod without ONLY is left as-is."""
        content = "    USE kgen_utils_mod\n    IMPLICIT NONE\n"
        result = _fix_kgen_utils_mod_only(content)
        assert result == content


# === _fix_use_ordering tests ===

class TestFixUseOrdering:

    def test_moves_use_before_implicit_none(self):
        """USE after IMPLICIT NONE is moved before it."""
        content = (
            "    IMPLICIT NONE\n"
            "    USE ISO_C_BINDING\n"
            "    REAL :: x\n"
        )
        result = _fix_use_ordering(content)
        lines = result.split("\n")
        use_idx = next(i for i, l in enumerate(lines) if "USE ISO_C_BINDING" in l)
        impl_idx = next(i for i, l in enumerate(lines) if "IMPLICIT NONE" in l)
        assert use_idx < impl_idx

    def test_already_correct_is_noop(self):
        """Content with correct ordering is unchanged."""
        content = (
            "    USE ISO_C_BINDING\n"
            "    IMPLICIT NONE\n"
            "    REAL :: x\n"
        )
        result = _fix_use_ordering(content)
        assert result == content

    def test_multiple_use_statements(self):
        """Multiple USE statements after IMPLICIT NONE are all moved."""
        content = (
            "    IMPLICIT NONE\n"
            "    USE mod_a\n"
            "    USE mod_b\n"
            "    REAL :: x\n"
        )
        result = _fix_use_ordering(content)
        lines = result.split("\n")
        impl_idx = next(i for i, l in enumerate(lines) if "IMPLICIT NONE" in l)
        for line in lines[:impl_idx]:
            if line.strip():
                assert line.strip().startswith("USE")

    def test_skips_comments_and_blanks(self):
        """Comments and blank lines between IMPLICIT NONE and USE are handled."""
        content = (
            "    IMPLICIT NONE\n"
            "\n"
            "    ! a comment\n"
            "    USE mod_a\n"
            "    REAL :: x\n"
        )
        result = _fix_use_ordering(content)
        lines = result.split("\n")
        use_idx = next(i for i, l in enumerate(lines) if "USE mod_a" in l)
        impl_idx = next(i for i, l in enumerate(lines) if "IMPLICIT NONE" in l)
        assert use_idx < impl_idx


# === _add_use_iso_c_binding tests ===

class TestAddUseIsoCBinding:

    def test_inserts_before_implicit_none(self):
        content = "    IMPLICIT NONE\n    REAL :: x\n"
        result = _add_use_iso_c_binding(content)
        lines = result.split("\n")
        iso_idx = next(i for i, l in enumerate(lines) if "ISO_C_BINDING" in l)
        impl_idx = next(i for i, l in enumerate(lines) if "IMPLICIT NONE" in l)
        assert iso_idx < impl_idx

    def test_preserves_indentation(self):
        content = "        IMPLICIT NONE\n"
        result = _add_use_iso_c_binding(content)
        assert "        USE ISO_C_BINDING" in result

    def test_no_implicit_none(self):
        """If there's no IMPLICIT NONE, content is unchanged."""
        content = "    REAL :: x\n    x = 1.0\n"
        result = _add_use_iso_c_binding(content)
        assert result == content


# === _add_interface_block tests ===

class TestAddInterfaceBlock:

    INTERFACE_BLOCK = (
        "    INTERFACE\n"
        "        FUNCTION foo_c(x) BIND(C, NAME='foo_c')\n"
        "        END FUNCTION foo_c\n"
        "    END INTERFACE"
    )

    def test_inserts_above_contains(self):
        content = "MODULE Foo\nIMPLICIT NONE\nCONTAINS\n    FUNCTION bar()\n    END FUNCTION bar\nEND MODULE Foo"
        result = _add_interface_block(content, self.INTERFACE_BLOCK)
        lines = result.split("\n")
        iface_idx = next(i for i, l in enumerate(lines) if "INTERFACE" in l and "END" not in l)
        contains_idx = next(i for i, l in enumerate(lines) if l.strip() == "CONTAINS")
        assert iface_idx < contains_idx

    def test_falls_back_to_end_module(self):
        """Without CONTAINS, inserts before END MODULE."""
        content = "MODULE Foo\nIMPLICIT NONE\nEND MODULE Foo"
        result = _add_interface_block(content, self.INTERFACE_BLOCK)
        assert "INTERFACE" in result
        lines = result.split("\n")
        iface_idx = next(i for i, l in enumerate(lines) if "INTERFACE" in l and "END INTERFACE" not in l)
        end_idx = next(i for i, l in enumerate(lines) if "END MODULE" in l)
        assert iface_idx < end_idx

    def test_appends_if_neither_found(self):
        """Without CONTAINS or END MODULE, appends to end."""
        content = "IMPLICIT NONE\nREAL :: x"
        result = _add_interface_block(content, self.INTERFACE_BLOCK)
        assert self.INTERFACE_BLOCK in result


# === _replace_function_body tests ===

class TestReplaceFunctionBody:

    def _make_sig(self, name="saturate", is_function=True):
        return FortranSignature(
            name=name,
            kind=SubprogramKind.FUNCTION if is_function else SubprogramKind.SUBROUTINE,
            args=[],
            result_name="result" if is_function else None,
            result_type="REAL(8)" if is_function else None,
        )

    def test_replaces_function_body(self):
        content = (
            "CONTAINS\n"
            "    FUNCTION saturate(x, lo, hi)\n"
            "        REAL :: x, lo, hi\n"
            "        saturate = MIN(MAX(x, lo), hi)\n"
            "    END FUNCTION saturate\n"
            "END MODULE"
        )
        sig = self._make_sig("saturate")
        wrapper = "    FUNCTION saturate(x, lo, hi)\n        ! wrapper\n    END FUNCTION saturate"

        result = _replace_function_body(content, sig, wrapper)
        assert "! wrapper" in result
        assert "MIN(MAX" not in result
        assert "END MODULE" in result

    def test_function_not_found_returns_unchanged(self):
        content = "    FUNCTION other_func(x)\n    END FUNCTION other_func\n"
        sig = self._make_sig("saturate")
        result = _replace_function_body(content, sig, "wrapper")
        assert result == content

    def test_subroutine_replacement(self):
        content = (
            "    SUBROUTINE update(x, y)\n"
            "        x = x + y\n"
            "    END SUBROUTINE update\n"
        )
        sig = self._make_sig("update", is_function=False)
        wrapper = "    SUBROUTINE update(x, y)\n        ! c wrapper\n    END SUBROUTINE update"

        result = _replace_function_body(content, sig, wrapper)
        assert "! c wrapper" in result
        assert "x = x + y" not in result


# === _parse_verify_output tests ===

class TestParseVerifyOutput:

    def test_all_passed(self):
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "localvar%istatus is IDENTICAL.\n"
            "Verification PASSED with saturate.0.0.1\n"
            "***************** Verification against 'saturate.0.0.2' *****************\n"
            "localvar%istatus is IDENTICAL.\n"
            "Verification PASSED with saturate.0.0.2\n"
            "\n"
            " Total number of verification cases  :     2\n"
            " Number of verification-passed cases :     2\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is True
        assert result.total_cases == 2
        assert result.passed_cases == 2
        assert result.failed_cases == 0
        assert result.skipped_cases == 0

    def test_all_failed(self):
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "Verification FAILED with saturate.0.0.1\n"
            "\n"
            " Total number of verification cases  :     1\n"
            " Number of verification-passed cases :     0\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is False
        assert result.total_cases == 1
        assert result.passed_cases == 0
        assert result.failed_cases == 1

    def test_nan_cases_skipped(self):
        """Cases with NaN reference data are classified as skipped, not failed."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "localvar%istatus is IDENTICAL.\n"
            "Verification PASSED with saturate.0.0.1\n"
            "***************** Verification against 'saturate.0.0.2' *****************\n"
            "Difference is                        NaN\n"
            "Verification FAILED with saturate.0.0.2\n"
            "\n"
            " Total number of verification cases  :     2\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is True  # NaN skip doesn't count as failure
        assert result.total_cases == 2
        assert result.passed_cases == 1
        assert result.skipped_cases == 1
        assert result.failed_cases == 0

    def test_mixed_pass_fail(self):
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "Verification PASSED with saturate.0.0.1\n"
            "***************** Verification against 'saturate.0.0.2' *****************\n"
            "Verification FAILED with saturate.0.0.2\n"
            "\n"
            " Total number of verification cases  :     2\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is False
        assert result.passed_cases == 1
        assert result.failed_cases == 1

    def test_no_cases(self):
        """Empty output with no cases should not be success."""
        result = _parse_verify_output("", 1e-14)
        assert result.success is False
        assert result.total_cases == 0

    def test_no_summary_uses_per_case_counts(self):
        """Without summary lines, falls back to per-case counting."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "Verification PASSED with saturate.0.0.1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is True
        assert result.total_cases == 1
        assert result.passed_cases == 1

    def test_all_nan_skipped_is_not_success(self):
        """If ALL cases are NaN-skipped, that's not success (no valid cases)."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "Difference is                        NaN\n"
            "Verification FAILED with saturate.0.0.1\n"
            "\n"
            " Total number of verification cases  :     1\n"
            " Number of verification-passed cases :     0\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is False
        assert result.skipped_cases == 1
        assert result.failed_cases == 0

    def test_details_populated(self):
        """Per-case details are tracked in the details list."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "Verification PASSED with saturate.0.0.1\n"
            "***************** Verification against 'saturate.0.0.2' *****************\n"
            "Difference is                        NaN\n"
            "Verification FAILED with saturate.0.0.2\n"
            "\n"
            " Total number of verification cases  :     2\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert len(result.details) == 2
        assert result.details[0]["status"] == "PASSED"
        assert result.details[1]["status"] == "SKIPPED"


# === _patch_field_value_logging tests ===

class TestPatchFieldValueLogging:

    # Scalar comparison block (after NaN patching) — numeric type
    SCALAR_BLOCK = (
        "    dtype_check_status%numTotal = dtype_check_status%numTotal + 1\n"
        "    IF ((var%dt_out == kgenref_var%dt_out) .OR. "
        "((var%dt_out /= var%dt_out) .AND. (kgenref_var%dt_out /= kgenref_var%dt_out))) THEN\n"
        "        IF (var%dt_out /= var%dt_out) WRITE(*, *) \"both NaN\"\n"
        "        dtype_check_status%numIdentical = dtype_check_status%numIdentical + 1\n"
        "        check_result = CHECK_IDENTICAL\n"
        "    ELSE\n"
        "        diff_dt_out = ABS(var%dt_out - kgenref_var%dt_out)\n"
        "        IF (diff_dt_out <= kgen_tolerance) THEN\n"
        "            check_result = CHECK_IN_TOL\n"
        "        ELSE\n"
        "            check_result = CHECK_OUT_TOL\n"
        "        END IF\n"
        "    END IF\n"
    )

    # Character scalar — no diff variable, only IDENTICAL/OUT_TOL
    CHAR_SCALAR_BLOCK = (
        "    dtype_check_status%numTotal = dtype_check_status%numTotal + 1\n"
        "    IF ((var%dll_filename == kgenref_var%dll_filename) .OR. "
        "((var%dll_filename /= var%dll_filename) .AND. (kgenref_var%dll_filename /= kgenref_var%dll_filename))) THEN\n"
        "        dtype_check_status%numIdentical = dtype_check_status%numIdentical + 1\n"
        "        check_result = CHECK_IDENTICAL\n"
        "    ELSE\n"
        "        dtype_check_status%numOutTol = dtype_check_status%numOutTol + 1\n"
        "        check_result = CHECK_OUT_TOL\n"
        "    END IF\n"
    )

    # Numeric array comparison block
    ARRAY_BLOCK = (
        "    IF (ALL(var%blpitch == kgenref_var%blpitch)) THEN\n"
        "        dtype_check_status%numIdentical = dtype_check_status%numIdentical + 1\n"
        "        check_result = CHECK_IDENTICAL\n"
        "    ELSE\n"
        "        n_blpitch = COUNT(var%blpitch /= kgenref_var%blpitch)\n"
        "        rmsdiff_blpitch = SQRT(SUM(buf2)/REAL(n_blpitch))\n"
        "        IF (rmsdiff_blpitch > kgen_tolerance) THEN\n"
        "            check_result = CHECK_OUT_TOL\n"
        "        ELSE\n"
        "            check_result = CHECK_IN_TOL\n"
        "        END IF\n"
        "    END IF\n"
    )

    # Character array — no rmsdiff variable, only IDENTICAL/OUT_TOL
    CHAR_ARRAY_BLOCK = (
        "    IF (ALL(var%acc_infile == kgenref_var%acc_infile)) THEN\n"
        "        dtype_check_status%numIdentical = dtype_check_status%numIdentical + 1\n"
        "        check_result = CHECK_IDENTICAL\n"
        "    ELSE\n"
        "        n_acc_infile = COUNT(var%acc_infile /= kgenref_var%acc_infile)\n"
        "        dtype_check_status%numOutTol = dtype_check_status%numOutTol + 1\n"
        "        check_result = CHECK_OUT_TOL\n"
        "    END IF\n"
    )

    def test_scalar_identical_write(self):
        """Injects [VIT_FIELD] WRITE after scalar CHECK_IDENTICAL."""
        result = _patch_field_value_logging(self.SCALAR_BLOCK)
        assert '[VIT_FIELD] dt_out | IDENTICAL' in result
        assert 'var%dt_out' in result.split('[VIT_FIELD]')[1]
        assert 'kgenref_var%dt_out' in result.split('[VIT_FIELD]')[1]

    def test_scalar_in_tol_write(self):
        """Injects [VIT_FIELD] WRITE after scalar CHECK_IN_TOL."""
        result = _patch_field_value_logging(self.SCALAR_BLOCK)
        lines = result.split("\n")
        in_tol_writes = [l for l in lines if '[VIT_FIELD]' in l and 'IN_TOL' in l]
        assert len(in_tol_writes) == 1
        assert 'diff_dt_out' in in_tol_writes[0]

    def test_scalar_out_tol_write(self):
        """Injects [VIT_FIELD] WRITE after scalar CHECK_OUT_TOL."""
        result = _patch_field_value_logging(self.SCALAR_BLOCK)
        lines = result.split("\n")
        out_tol_writes = [l for l in lines if '[VIT_FIELD]' in l and 'OUT_TOL' in l]
        assert len(out_tol_writes) == 1
        assert 'diff_dt_out' in out_tol_writes[0]

    def test_array_identical_write(self):
        """Injects [VIT_ARRAY] WRITE after array CHECK_IDENTICAL."""
        result = _patch_field_value_logging(self.ARRAY_BLOCK)
        assert '[VIT_ARRAY] blpitch | IDENTICAL' in result
        assert 'SIZE(var%blpitch)' in result

    def test_array_out_tol_write(self):
        """Injects [VIT_ARRAY] WRITE after array CHECK_OUT_TOL."""
        result = _patch_field_value_logging(self.ARRAY_BLOCK)
        lines = result.split("\n")
        out_tol_writes = [l for l in lines if '[VIT_ARRAY]' in l and 'OUT_TOL' in l]
        assert len(out_tol_writes) == 1
        assert 'n_blpitch' in out_tol_writes[0]
        assert 'rmsdiff_blpitch' in out_tol_writes[0]

    def test_array_in_tol_write(self):
        """Injects [VIT_ARRAY] WRITE after array CHECK_IN_TOL."""
        result = _patch_field_value_logging(self.ARRAY_BLOCK)
        lines = result.split("\n")
        in_tol_writes = [l for l in lines if '[VIT_ARRAY]' in l and 'IN_TOL' in l]
        assert len(in_tol_writes) == 1
        assert 'n_blpitch' in in_tol_writes[0]

    def test_multiple_fields(self):
        """Handles multiple scalar fields in sequence."""
        second_block = self.SCALAR_BLOCK.replace("dt_out", "pc_maxpit")
        content = self.SCALAR_BLOCK + second_block
        result = _patch_field_value_logging(content)
        assert result.count('[VIT_FIELD] dt_out') == 3
        assert result.count('[VIT_FIELD] pc_maxpit') == 3

    def test_mixed_scalar_and_array(self):
        """Handles scalar followed by array fields."""
        content = self.SCALAR_BLOCK + self.ARRAY_BLOCK
        result = _patch_field_value_logging(content)
        assert '[VIT_FIELD] dt_out' in result
        assert '[VIT_ARRAY] blpitch' in result

    def test_preserves_indentation(self):
        """Injected WRITEs match the indentation of check_result."""
        result = _patch_field_value_logging(self.SCALAR_BLOCK)
        lines = result.split("\n")
        for line in lines:
            if '[VIT_FIELD]' in line and 'IDENTICAL' in line and 'IN_TOL' not in line and 'OUT_TOL' not in line:
                # The check_result = CHECK_IDENTICAL line has 8 spaces indent
                assert line.startswith("        WRITE")

    def test_no_match_is_noop(self):
        """Content without comparison patterns is unchanged."""
        content = "    x = 1.0\n    IF (a > b) THEN\n    END IF\n"
        result = _patch_field_value_logging(content)
        assert result == content

    def test_does_not_match_if_condition(self):
        """Does not inject after IF (check_result == CHECK_*) conditions."""
        content = (
            "    diff_foo = ABS(var%foo - kgenref_var%foo)\n"
            "    IF ((var%foo == kgenref_var%foo) .OR. "
            "((var%foo /= var%foo) .AND. (kgenref_var%foo /= kgenref_var%foo))) THEN\n"
            "        check_result = CHECK_IDENTICAL\n"
            "    END IF\n"
            "    IF (check_result == CHECK_IDENTICAL) THEN\n"
            "        CONTINUE\n"
            "    END IF\n"
        )
        result = _patch_field_value_logging(content)
        # Only 1 VIT_FIELD write (after the assignment), not after the IF condition
        assert result.count('[VIT_FIELD]') == 1

    def test_char_scalar_no_diff(self):
        """CHARACTER scalars don't reference diff_FIELD in OUT_TOL WRITE."""
        result = _patch_field_value_logging(self.CHAR_SCALAR_BLOCK)
        lines = result.split("\n")
        out_tol_writes = [l for l in lines if '[VIT_FIELD]' in l and 'OUT_TOL' in l]
        assert len(out_tol_writes) == 1
        # No diff_ reference for character fields
        assert 'diff_dll_filename' not in out_tol_writes[0]
        # But values are still logged
        assert 'var%dll_filename' in out_tol_writes[0]

    def test_char_array_no_rmsdiff(self):
        """CHARACTER arrays don't reference rmsdiff_FIELD in OUT_TOL WRITE."""
        result = _patch_field_value_logging(self.CHAR_ARRAY_BLOCK)
        lines = result.split("\n")
        out_tol_writes = [l for l in lines if '[VIT_ARRAY]' in l and 'OUT_TOL' in l]
        assert len(out_tol_writes) == 1
        # No rmsdiff reference for character arrays
        assert 'rmsdiff_acc_infile' not in out_tol_writes[0]
        # But n_diff count is still logged
        assert 'n_acc_infile' in out_tol_writes[0]

    def test_idempotent(self):
        """Patching already-patched content is a no-op."""
        result1 = _patch_field_value_logging(self.SCALAR_BLOCK)
        result2 = _patch_field_value_logging(result1)
        assert result1 == result2


# === Field data parsing tests ===

class TestParseFieldData:

    def test_scalar_field_parsing(self):
        """Parses [VIT_FIELD] lines into structured field_data."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            ' [VIT_FIELD] zmq_id | IDENTICAL |            0  |            0\n'
            ' [VIT_FIELD] dt_out | IN_TOL |   0.125E+00  |   0.125E+00  |   1.11E-16\n'
            "Verification PASSED with saturate.0.0.1\n"
            "\n"
            " Total number of verification cases  :     1\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert len(result.field_data) == 2
        assert result.field_data[0]["field"] == "zmq_id"
        assert result.field_data[0]["type"] == "scalar"
        assert result.field_data[0]["status"] == "IDENTICAL"
        assert result.field_data[0]["case"] == "saturate.0.0.1"
        assert result.field_data[1]["field"] == "dt_out"
        assert result.field_data[1]["status"] == "IN_TOL"

    def test_array_field_parsing(self):
        """Parses [VIT_ARRAY] lines into structured field_data."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            ' [VIT_ARRAY] blpitch | IDENTICAL | size=           3\n'
            ' [VIT_ARRAY] notch | OUT_TOL | n_diff=           2  | rms=  5.55E-17\n'
            "Verification PASSED with saturate.0.0.1\n"
            "\n"
            " Total number of verification cases  :     1\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert len(result.field_data) == 2
        assert result.field_data[0]["field"] == "blpitch"
        assert result.field_data[0]["type"] == "array"
        assert result.field_data[0]["status"] == "IDENTICAL"
        assert result.field_data[1]["field"] == "notch"
        assert result.field_data[1]["status"] == "OUT_TOL"

    def test_field_data_tracks_cases(self):
        """Field data entries are associated with the correct case."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            ' [VIT_FIELD] zmq_id | IDENTICAL |  0  |  0\n'
            "Verification PASSED with saturate.0.0.1\n"
            "***************** Verification against 'saturate.0.0.2' *****************\n"
            ' [VIT_FIELD] zmq_id | IDENTICAL |  0  |  0\n'
            "Verification PASSED with saturate.0.0.2\n"
            "\n"
            " Total number of verification cases  :     2\n"
            " Number of verification-passed cases :     2\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert len(result.field_data) == 2
        assert result.field_data[0]["case"] == "saturate.0.0.1"
        assert result.field_data[1]["case"] == "saturate.0.0.2"

    def test_no_field_data_when_no_tags(self):
        """Standard output without VIT tags produces empty field_data."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            "localvar%istatus is IDENTICAL.\n"
            "Verification PASSED with saturate.0.0.1\n"
            "\n"
            " Total number of verification cases  :     1\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.field_data == []
        assert result.success is True

    def test_field_data_with_verification_still_works(self):
        """Field data parsing does not interfere with pass/fail detection."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            ' [VIT_FIELD] zmq_id | IDENTICAL |  0  |  0\n'
            ' [VIT_FIELD] dt_out | IDENTICAL |  0.125  |  0.125\n'
            "Verification PASSED with saturate.0.0.1\n"
            "***************** Verification against 'saturate.0.0.2' *****************\n"
            ' [VIT_FIELD] zmq_id | IDENTICAL |  0  |  0\n'
            "Verification PASSED with saturate.0.0.2\n"
            "\n"
            " Total number of verification cases  :     2\n"
            " Number of verification-passed cases :     2\n"
        )
        result = _parse_verify_output(output, 1e-14)
        assert result.success is True
        assert result.total_cases == 2
        assert result.passed_cases == 2
        assert len(result.field_data) == 3

    def test_scalar_computed_reference_values(self):
        """Computed and reference values are extracted from scalar fields."""
        output = (
            "***************** Verification against 'saturate.0.0.1' *****************\n"
            ' [VIT_FIELD] pc_maxpit | IN_TOL |   1.5708E+00  |   1.5708E+00  |   1.11E-16\n'
            "Verification PASSED with saturate.0.0.1\n"
            "\n"
            " Total number of verification cases  :     1\n"
            " Number of verification-passed cases :     1\n"
        )
        result = _parse_verify_output(output, 1e-14)
        entry = result.field_data[0]
        assert entry["computed"] == "1.5708E+00"
        assert entry["reference"] == "1.5708E+00"
        assert entry["diff"] == "1.11E-16"


# === Bare variable NaN patching tests ===

class TestPatchNanComparisonsBare:
    """Test NaN patching for bare variable comparisons (kv_ipc_* subroutines)."""

    def test_bare_scalar_comparison(self):
        """Transforms bare var == kgenref_var comparison."""
        content = "        IF (var == kgenref_var) THEN\n"
        result = _patch_nan_comparisons(content)
        assert "var /= var" in result
        assert "kgenref_var /= kgenref_var" in result
        assert ".OR." in result
        assert "both NaN" in result

    def test_bare_does_not_affect_struct_fields(self):
        """Struct field patterns still work alongside bare patterns."""
        content = (
            "        IF (var%field1 == kgenref_var%field1) THEN\n"
            "        END IF\n"
            "        IF (var == kgenref_var) THEN\n"
            "        END IF\n"
        )
        result = _patch_nan_comparisons(content)
        assert "var%field1 /= var%field1" in result
        assert result.count(".OR.") == 2

    def test_bare_uses_varname_in_diagnostic(self):
        """Bare NaN diagnostic uses trim(adjustl(varname)), not a hardcoded name."""
        content = "        IF (var == kgenref_var) THEN\n"
        result = _patch_nan_comparisons(content)
        assert "varname" in result


# === Bare variable field logging tests ===

class TestPatchFieldValueLoggingBare:
    """Test field logging for bare variable comparisons (kv_ipc_* subroutines)."""

    # Bare scalar block (after NaN patching) — matches kv_ipc_real__dbki
    BARE_SCALAR_BLOCK = (
        "    RECURSIVE SUBROUTINE kv_ipc_real__dbki(varname, check_status, var, kgenref_var)\n"
        "        CHARACTER(LEN=*), INTENT(IN) :: varname\n"
        "        real(KIND=dbki) :: diff\n"
        "        IF ((var == kgenref_var) .OR. "
        "((var /= var) .AND. (kgenref_var /= kgenref_var))) THEN\n"
        "            check_result = CHECK_IDENTICAL\n"
        "        ELSE\n"
        "            diff = ABS(var - kgenref_var)\n"
        "            IF (diff <= kgen_tolerance) THEN\n"
        "                check_result = CHECK_IN_TOL\n"
        "            ELSE\n"
        "                check_result = CHECK_OUT_TOL\n"
        "            END IF\n"
        "        END IF\n"
        "    END SUBROUTINE\n"
    )

    # Bare array block — matches kv_ipc_real__dbki_dim1
    BARE_ARRAY_BLOCK = (
        "    RECURSIVE SUBROUTINE kv_ipc_real__dbki_dim1(varname, check_status, var, kgenref_var)\n"
        "        CHARACTER(LEN=*), INTENT(IN) :: varname\n"
        "        INTEGER :: n\n"
        "        real(KIND=dbki) :: rmsdiff\n"
        "        IF (ALL(var == kgenref_var)) THEN\n"
        "            check_result = CHECK_IDENTICAL\n"
        "        ELSE\n"
        "            rmsdiff = SQRT(SUM(buf2)/DBLE(n))\n"
        "            IF (rmsdiff > kgen_tolerance) THEN\n"
        "                check_result = CHECK_OUT_TOL\n"
        "            ELSE\n"
        "                check_result = CHECK_IN_TOL\n"
        "            END IF\n"
        "        END IF\n"
        "    END SUBROUTINE\n"
    )

    def test_bare_scalar_identical_write(self):
        """Injects [VIT_FIELD] WRITE after bare scalar CHECK_IDENTICAL."""
        result = _patch_field_value_logging(self.BARE_SCALAR_BLOCK)
        assert '[VIT_FIELD]' in result
        lines = [l for l in result.split("\n") if '[VIT_FIELD]' in l and 'IDENTICAL' in l]
        assert len(lines) == 1
        assert 'varname' in lines[0]

    def test_bare_scalar_in_tol_write(self):
        """Injects [VIT_FIELD] WRITE after bare scalar CHECK_IN_TOL."""
        result = _patch_field_value_logging(self.BARE_SCALAR_BLOCK)
        lines = [l for l in result.split("\n") if '[VIT_FIELD]' in l and 'IN_TOL' in l]
        assert len(lines) == 1
        assert 'diff' in lines[0]

    def test_bare_scalar_out_tol_write(self):
        """Injects [VIT_FIELD] WRITE after bare scalar CHECK_OUT_TOL."""
        result = _patch_field_value_logging(self.BARE_SCALAR_BLOCK)
        lines = [l for l in result.split("\n") if '[VIT_FIELD]' in l and 'OUT_TOL' in l]
        assert len(lines) == 1
        assert 'diff' in lines[0]

    def test_bare_array_identical_write(self):
        """Injects [VIT_ARRAY] WRITE after bare array CHECK_IDENTICAL."""
        result = _patch_field_value_logging(self.BARE_ARRAY_BLOCK)
        lines = [l for l in result.split("\n") if '[VIT_ARRAY]' in l and 'IDENTICAL' in l]
        assert len(lines) == 1
        assert 'varname' in lines[0]
        assert 'SIZE(var)' in lines[0]

    def test_bare_array_out_tol_write(self):
        """Injects [VIT_ARRAY] WRITE after bare array CHECK_OUT_TOL."""
        result = _patch_field_value_logging(self.BARE_ARRAY_BLOCK)
        lines = [l for l in result.split("\n") if '[VIT_ARRAY]' in l and 'OUT_TOL' in l]
        assert len(lines) == 1
        assert 'rmsdiff' in lines[0]

    def test_bare_does_not_use_percent(self):
        """Bare variable logging uses var/kgenref_var, not var%field."""
        result = _patch_field_value_logging(self.BARE_SCALAR_BLOCK)
        vit_lines = [l for l in result.split("\n") if '[VIT_FIELD]' in l]
        for line in vit_lines:
            assert 'var%' not in line

    def test_mixed_struct_and_bare(self):
        """Struct field patterns and bare patterns coexist."""
        struct_block = TestPatchFieldValueLogging.SCALAR_BLOCK
        content = struct_block + self.BARE_SCALAR_BLOCK
        result = _patch_field_value_logging(content)
        assert '[VIT_FIELD] dt_out' in result  # struct field
        vit_lines = [l for l in result.split("\n") if '[VIT_FIELD]' in l]
        # 3 from struct (IDENTICAL, IN_TOL, OUT_TOL) + 3 from bare
        assert len(vit_lines) == 6

    def test_idempotent(self):
        """Patching already-patched bare content is a no-op."""
        result1 = _patch_field_value_logging(self.BARE_SCALAR_BLOCK)
        result2 = _patch_field_value_logging(result1)
        assert result1 == result2


class TestPatchKernelMakefileFortranFlags:
    """Tests for fortran_flags propagation to kernel Makefile."""

    KGEN_MAKEFILE = """\
# Makefile for KGEN-generated kernel

ALL_OBJS := Controllers.o ROSCO_Types.o Constants.o kernel_driver.o kgen_utils.o

build: ${ALL_OBJS}
\t${FC_0} ${FC_FLAGS_SET_0} -o kernel.exe $^

Controllers.o: Controllers.f90 ROSCO_Types.o
\t${FC_0} ${FC_FLAGS_SET_0} -c -o $@ $<
"""

    def _make_kernel(self, tmp_path):
        """Create a minimal kernel directory with a KGen-style Makefile."""
        kernel = tmp_path / "kernel"
        kernel.mkdir()
        (kernel / "Makefile").write_text(self.KGEN_MAKEFILE)
        return kernel

    def test_fortran_flags_added_to_fc_flags_set_0(self, tmp_path):
        """fortran_flags appear in FC_FLAGS_SET_0 when provided."""
        kernel = self._make_kernel(tmp_path)
        sig = FortranSignature(
            name="test_func", kind=SubprogramKind.FUNCTION,
            args=[], result_name="test_func_result",
            result_type="REAL(8)", source_file="test.f90", line_number=1,
        )
        _patch_kernel_makefile(kernel, sig, "test_cpp.cpp",
                               fortran_flags="-fdefault-real-8 -fdefault-double-8")
        content = (kernel / "Makefile").read_text()
        assert "-fdefault-real-8" in content
        assert "-fdefault-double-8" in content
        # Should be in the FC_FLAGS_SET_0 line
        for line in content.split("\n"):
            if line.startswith("FC_FLAGS_SET_0"):
                assert "-fdefault-real-8" in line
                assert "-fdefault-double-8" in line
                break
        else:
            pytest.fail("FC_FLAGS_SET_0 line not found")

    def test_no_fortran_flags_uses_defaults(self, tmp_path):
        """Without fortran_flags, FC_FLAGS_SET_0 uses only base flags."""
        kernel = self._make_kernel(tmp_path)
        sig = FortranSignature(
            name="test_func", kind=SubprogramKind.FUNCTION,
            args=[], result_name="test_func_result",
            result_type="REAL(8)", source_file="test.f90", line_number=1,
        )
        _patch_kernel_makefile(kernel, sig, "test_cpp.cpp")
        content = (kernel / "Makefile").read_text()
        assert "-fdefault-real-8" not in content
        for line in content.split("\n"):
            if line.startswith("FC_FLAGS_SET_0"):
                assert "-O2" in line
                assert "-ffree-form" in line
                break

    def test_fortran_flags_updated_on_rerun(self, tmp_path):
        """Re-running with different fortran_flags updates FC_FLAGS_SET_0."""
        kernel = self._make_kernel(tmp_path)
        sig = FortranSignature(
            name="test_func", kind=SubprogramKind.FUNCTION,
            args=[], result_name="test_func_result",
            result_type="REAL(8)", source_file="test.f90", line_number=1,
        )
        # First run without flags
        _patch_kernel_makefile(kernel, sig, "test_cpp.cpp")
        # Second run with flags
        _patch_kernel_makefile(kernel, sig, "test_cpp.cpp",
                               fortran_flags="-fdefault-real-8")
        content = (kernel / "Makefile").read_text()
        for line in content.split("\n"):
            if line.startswith("FC_FLAGS_SET_0"):
                assert "-fdefault-real-8" in line
                break


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
