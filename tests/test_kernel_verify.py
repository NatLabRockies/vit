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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
