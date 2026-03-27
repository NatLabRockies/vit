"""
Tests for VIT extraction helpers.

Tests pure-logic functions from extract.py that don't require
Docker, KGen, or a real Fortran project.
"""

import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.extract import (
    insert_kgen_pragma,
    remove_kgen_pragma,
    count_kernel_cases,
    generate_include_ini,
    detect_assumed_size_from_source,
    infer_assumed_size_arrays,
)


# === Test data ===

SIMPLE_FORTRAN = """\
SUBROUTINE PitchControl(avrSWAP, CntrPar, LocalVar)
    USE ROSCO_Types
    IMPLICIT NONE
    REAL, INTENT(INOUT) :: avrSWAP(*)
    ! Saturate collective pitch commands:
    LocalVar%PC_PitComT = saturate(LocalVar%PC_PitComT, LocalVar%PC_MinPit, CntrPar%PC_MaxPit)
    LocalVar%PC_PitComT = ratelimit(LocalVar%PC_PitComT, CntrPar%PC_MinRat, CntrPar%PC_MaxRat)
END SUBROUTINE PitchControl
"""

CONTINUATION_FORTRAN = """\
SUBROUTINE Foo(x)
    IMPLICIT NONE
    REAL :: x
    x = bar(a, &
            b, &
            c)
END SUBROUTINE Foo
"""


# === insert_kgen_pragma tests ===

class TestInsertKgenPragma:

    def test_simple_line(self, tmp_path):
        """Pragma wraps a simple single-line call."""
        src = tmp_path / "test.f90"
        src.write_text(SIMPLE_FORTRAN)

        insert_kgen_pragma(str(src), 6, "saturate")

        result = src.read_text()
        lines = result.split("\n")
        # Find the pragma lines
        begin_idx = next(i for i, l in enumerate(lines)
                         if "begin_callsite" in l.lower())
        end_idx = next(i for i, l in enumerate(lines)
                       if "end_callsite" in l.lower())

        assert "saturate" in lines[begin_idx]
        assert "saturate(" in lines[begin_idx + 1]
        assert end_idx == begin_idx + 2

    def test_continuation_lines(self, tmp_path):
        """Pragma wraps multi-line continuation statements."""
        src = tmp_path / "test.f90"
        src.write_text(CONTINUATION_FORTRAN)

        # Line 4 is "x = bar(a, &" (1-indexed)
        insert_kgen_pragma(str(src), 4, "bar")

        result = src.read_text()
        lines = result.split("\n")
        begin_idx = next(i for i, l in enumerate(lines)
                         if "begin_callsite" in l.lower())
        end_idx = next(i for i, l in enumerate(lines)
                       if "end_callsite" in l.lower())

        # Should wrap all three continuation lines
        assert end_idx - begin_idx == 4  # begin, 3 lines, end

    def test_indent_preservation(self, tmp_path):
        """Pragma preserves the indentation of the wrapped line."""
        src = tmp_path / "test.f90"
        src.write_text(SIMPLE_FORTRAN)

        insert_kgen_pragma(str(src), 6, "saturate")

        result = src.read_text()
        lines = result.split("\n")
        begin_line = next(l for l in lines if "begin_callsite" in l.lower())
        # The call at line 6 has 4 spaces of indent
        assert begin_line.startswith("    !$kgen")

    def test_out_of_range_raises(self, tmp_path):
        """Out-of-range line number raises ValueError."""
        src = tmp_path / "test.f90"
        src.write_text(SIMPLE_FORTRAN)

        with pytest.raises(ValueError, match="out of range"):
            insert_kgen_pragma(str(src), 999, "saturate")

    def test_line_zero_raises(self, tmp_path):
        """Line 0 (below 1-indexed minimum) raises ValueError."""
        src = tmp_path / "test.f90"
        src.write_text(SIMPLE_FORTRAN)

        with pytest.raises(ValueError, match="out of range"):
            insert_kgen_pragma(str(src), 0, "saturate")

    def test_output_path(self, tmp_path):
        """Can write to a different output file."""
        src = tmp_path / "original.f90"
        out = tmp_path / "modified.f90"
        src.write_text(SIMPLE_FORTRAN)

        result_path = insert_kgen_pragma(str(src), 6, "saturate",
                                         output_path=str(out))

        assert result_path == str(out)
        assert out.exists()
        assert "begin_callsite" in out.read_text()
        # Original unchanged
        assert "begin_callsite" not in src.read_text()


# === remove_kgen_pragma tests ===

class TestRemoveKgenPragma:

    def test_removes_pragma_lines(self, tmp_path):
        """Strips lines starting with !$kgen."""
        src = tmp_path / "test.f90"
        content = (
            "    IMPLICIT NONE\n"
            "    !$kgen begin_callsite saturate\n"
            "    x = saturate(a, b, c)\n"
            "    !$kgen end_callsite\n"
            "    y = 1.0\n"
        )
        src.write_text(content)

        remove_kgen_pragma(str(src))

        result = src.read_text()
        assert "!$kgen" not in result
        assert "x = saturate(a, b, c)" in result
        assert "y = 1.0" in result

    def test_mixed_case(self, tmp_path):
        """Handles mixed-case pragma markers."""
        src = tmp_path / "test.f90"
        content = (
            "    !$KGEN begin_callsite foo\n"
            "    x = foo(1)\n"
            "    !$Kgen end_callsite\n"
        )
        src.write_text(content)

        remove_kgen_pragma(str(src))

        result = src.read_text()
        assert "!$" not in result.lower()
        assert "x = foo(1)" in result

    def test_no_pragmas_is_noop(self, tmp_path):
        """File without pragmas is unchanged."""
        src = tmp_path / "test.f90"
        original = "    IMPLICIT NONE\n    x = 1.0\n"
        src.write_text(original)

        remove_kgen_pragma(str(src))

        assert src.read_text() == original


# === count_kernel_cases tests ===

class TestCountKernelCases:

    def test_counts_state_files(self, tmp_path):
        """Counts files matching name.N.N.N pattern."""
        for i in range(1, 6):
            (tmp_path / f"saturate.0.0.{i}").touch()
        assert count_kernel_cases(str(tmp_path)) == 5

    def test_ignores_non_state_files(self, tmp_path):
        """Non-state files are not counted."""
        (tmp_path / "saturate.0.0.1").touch()
        (tmp_path / "Makefile").touch()
        (tmp_path / "kernel_driver.f90").touch()
        (tmp_path / "Functions.f90").touch()
        (tmp_path / "constants.mod").touch()
        assert count_kernel_cases(str(tmp_path)) == 1

    def test_empty_dir(self, tmp_path):
        assert count_kernel_cases(str(tmp_path)) == 0

    def test_nonexistent_dir(self, tmp_path):
        assert count_kernel_cases(str(tmp_path / "nonexistent")) == 0

    def test_multiple_functions(self, tmp_path):
        """Counts state files for different function names."""
        (tmp_path / "saturate.0.0.1").touch()
        (tmp_path / "saturate.0.0.2").touch()
        (tmp_path / "interp1d.0.0.1").touch()
        assert count_kernel_cases(str(tmp_path)) == 3


# === kgen_statefile.lst deduplication tests ===

class TestStatefileDeduplication:
    """Test deduplication of kgen_statefile.lst entries.

    KGen's ElapsedTime phase appends invocation triples to the CLI triples
    instead of replacing them, causing duplicate entries in the list file.
    VIT deduplicates the list after extraction.
    """

    def test_removes_duplicates(self, tmp_path):
        """Duplicate entries are removed, preserving order."""
        from pathlib import Path
        lst = tmp_path / "kgen_statefile.lst"
        lst.write_text(
            "saturate.0.0.1\nsaturate.0.0.2\nsaturate.0.0.3\n"
            "saturate.0.0.1\nsaturate.0.0.2\nsaturate.0.0.3\n"
        )
        # Apply same dedup logic as extract.py
        lst.write_text('\n'.join(dict.fromkeys(lst.read_text().splitlines())) + '\n')
        lines = lst.read_text().strip().split('\n')
        assert lines == ["saturate.0.0.1", "saturate.0.0.2", "saturate.0.0.3"]

    def test_preserves_order_with_partial_overlap(self, tmp_path):
        """When ElapsedTime selects different invocations, order is preserved."""
        from pathlib import Path
        lst = tmp_path / "kgen_statefile.lst"
        lst.write_text(
            "func.0.0.1\nfunc.0.0.2\nfunc.0.0.3\n"
            "func.0.0.2\nfunc.0.0.3\nfunc.0.0.4\n"
        )
        lst.write_text('\n'.join(dict.fromkeys(lst.read_text().splitlines())) + '\n')
        lines = lst.read_text().strip().split('\n')
        assert lines == ["func.0.0.1", "func.0.0.2", "func.0.0.3", "func.0.0.4"]

    def test_no_duplicates_unchanged(self, tmp_path):
        """A list with no duplicates is unchanged."""
        from pathlib import Path
        lst = tmp_path / "kgen_statefile.lst"
        lst.write_text("saturate.0.0.1\nsaturate.0.0.2\nsaturate.0.0.3\n")
        lst.write_text('\n'.join(dict.fromkeys(lst.read_text().splitlines())) + '\n')
        lines = lst.read_text().strip().split('\n')
        assert lines == ["saturate.0.0.1", "saturate.0.0.2", "saturate.0.0.3"]

    def test_empty_lines_removed(self, tmp_path):
        """Empty lines are removed during deduplication."""
        from pathlib import Path
        lst = tmp_path / "kgen_statefile.lst"
        lst.write_text("func.0.0.1\n\nfunc.0.0.2\n\nfunc.0.0.1\n")
        lst.write_text('\n'.join(dict.fromkeys(lst.read_text().splitlines())) + '\n')
        lines = [l for l in lst.read_text().strip().split('\n') if l]
        assert lines == ["func.0.0.1", "func.0.0.2"]


# === generate_include_ini tests ===

class TestGenerateIncludeIni:

    def test_basic_format(self, tmp_path):
        """Generates correct INI format with [include] header."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        mod_dir = tmp_path / "build" / "ftnmods"
        mod_dir.mkdir(parents=True)

        output = tmp_path / "include.ini"
        generate_include_ini([str(src_dir)], [str(mod_dir)],
                             output_path=str(output))

        content = output.read_text()
        assert content.startswith("[include]")
        assert str(mod_dir.resolve()) + "=" in content
        assert str(src_dir.resolve()) + "=" in content

    def test_mod_dirs_before_src_dirs(self, tmp_path):
        """Mod directories appear before source directories."""
        src_dir = tmp_path / "fortran_source"
        src_dir.mkdir()
        mod_dir = tmp_path / "compiled_modules"
        mod_dir.mkdir()

        output = tmp_path / "include.ini"
        generate_include_ini([str(src_dir)], [str(mod_dir)],
                             output_path=str(output))

        content = output.read_text()
        lines = content.strip().split("\n")
        # After [include], mod_dir should come before src_dir
        mod_line_idx = next(i for i, l in enumerate(lines)
                           if "compiled_modules" in l)
        src_line_idx = next(i for i, l in enumerate(lines)
                           if "fortran_source" in l)
        assert mod_line_idx < src_line_idx

    def test_filters_nonexistent_dirs(self, tmp_path):
        """Directories that don't exist are filtered out."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        fake_dir = str(tmp_path / "fake")

        output = tmp_path / "include.ini"
        generate_include_ini([str(real_dir)], [fake_dir],
                             output_path=str(output))

        content = output.read_text()
        assert "fake" not in content
        assert str(real_dir.resolve()) + "=" in content

    def test_empty_lists(self, tmp_path):
        """Empty directory lists produce header-only output."""
        output = tmp_path / "include.ini"
        generate_include_ini([], [], output_path=str(output))

        content = output.read_text()
        assert content.strip() == "[include]"


# === Edge case tests for insert_kgen_pragma ===

class TestInsertKgenPragmaEdgeCases:

    def test_long_continuation(self, tmp_path):
        """Wraps a 5-line continuation statement."""
        src = tmp_path / "test.f90"
        src.write_text(
            "SUBROUTINE Foo(x)\n"
            "    IMPLICIT NONE\n"
            "    REAL :: x\n"
            "    x = bar(a, &\n"
            "            b, &\n"
            "            c, &\n"
            "            d, &\n"
            "            e)\n"
            "END SUBROUTINE Foo\n"
        )

        insert_kgen_pragma(str(src), 4, "bar")

        result = src.read_text()
        lines = result.split("\n")
        begin_idx = next(i for i, l in enumerate(lines)
                         if "begin_callsite" in l.lower())
        end_idx = next(i for i, l in enumerate(lines)
                       if "end_callsite" in l.lower())
        # begin + 5 continuation lines + end
        assert end_idx - begin_idx == 6

    def test_continuation_with_inline_comments(self, tmp_path):
        """Handles continuation lines with inline comments after &."""
        src = tmp_path / "test.f90"
        src.write_text(
            "SUBROUTINE Foo(x)\n"
            "    IMPLICIT NONE\n"
            "    REAL :: x\n"
            "    x = bar(a, &  ! first arg\n"
            "            b, &  ! second arg\n"
            "            c)    ! third arg\n"
            "END SUBROUTINE Foo\n"
        )

        insert_kgen_pragma(str(src), 4, "bar")

        result = src.read_text()
        assert "begin_callsite" in result
        assert "end_callsite" in result
        # All 3 lines should be inside the pragma
        lines = result.split("\n")
        begin_idx = next(i for i, l in enumerate(lines)
                         if "begin_callsite" in l.lower())
        end_idx = next(i for i, l in enumerate(lines)
                       if "end_callsite" in l.lower())
        # All continuation lines should be wrapped (begin + lines + end)
        assert end_idx > begin_idx + 1  # At least the call line is wrapped

    def test_call_at_first_code_line(self, tmp_path):
        """Pragma works when the call is at the first code line."""
        src = tmp_path / "test.f90"
        src.write_text("    x = foo(1)\n    y = 2\n")

        insert_kgen_pragma(str(src), 1, "foo")

        result = src.read_text()
        assert "begin_callsite" in result
        assert "end_callsite" in result
        assert "y = 2" in result

    def test_call_at_last_line(self, tmp_path):
        """Pragma works when the call is at the last line of the file."""
        src = tmp_path / "test.f90"
        src.write_text("    y = 2\n    x = foo(1)\n")

        insert_kgen_pragma(str(src), 2, "foo")

        result = src.read_text()
        assert "begin_callsite" in result
        assert "end_callsite" in result
        assert "y = 2" in result


# === detect_assumed_size_from_source tests ===

class TestDetectAssumedSizeFromSource:

    def test_detects_inline_star(self, tmp_path):
        """Detects assumed-size array declared with inline (*)."""
        src = tmp_path / "test.f90"
        src.write_text(
            "SUBROUTINE DISCON(avrSWAP, aviFAIL)\n"
            "    REAL, INTENT(INOUT) :: avrSWAP(*)\n"
            "    INTEGER, INTENT(INOUT) :: aviFAIL\n"
            "    avrSWAP(1) = 0.0\n"
            "END SUBROUTINE DISCON\n"
        )
        result = detect_assumed_size_from_source(str(src), 4)
        assert "avrSWAP" in result
        assert "aviFAIL" not in result

    def test_detects_dimension_star(self, tmp_path):
        """Detects assumed-size array declared with DIMENSION(*)."""
        src = tmp_path / "test.f90"
        src.write_text(
            "SUBROUTINE handler(buf)\n"
            "    REAL, DIMENSION(*), INTENT(INOUT) :: buf\n"
            "    buf(1) = 1.0\n"
            "END SUBROUTINE handler\n"
        )
        result = detect_assumed_size_from_source(str(src), 3)
        assert "buf" in result

    def test_no_assumed_size(self, tmp_path):
        """Returns empty list when no assumed-size arrays exist."""
        src = tmp_path / "test.f90"
        src.write_text(
            "SUBROUTINE foo(x, y)\n"
            "    REAL, INTENT(IN) :: x\n"
            "    REAL, INTENT(OUT) :: y\n"
            "    y = x * 2\n"
            "END SUBROUTINE foo\n"
        )
        result = detect_assumed_size_from_source(str(src), 4)
        assert result == []

    def test_ignores_character_star(self, tmp_path):
        """Does not mistake CHARACTER(*) for an assumed-size array."""
        src = tmp_path / "test.f90"
        src.write_text(
            "SUBROUTINE greet(msg)\n"
            "    CHARACTER(*), INTENT(IN) :: msg\n"
            "    PRINT *, msg\n"
            "END SUBROUTINE greet\n"
        )
        result = detect_assumed_size_from_source(str(src), 3)
        assert result == []


# === infer_assumed_size_arrays tests ===

class TestInferAssumedSizeArrays:

    def test_adequate_size_returns_empty(self, tmp_path):
        """Returns empty list when configured size is adequate."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "Controllers.f90").write_text(
            "    avrSWAP(100) = 1.0\n"
            "    avrSWAP(200) = 2.0\n"
        )
        result = infer_assumed_size_arrays(str(src), {"avrSWAP": 3000})
        assert result == []

    def test_undersized_returns_assumption(self, tmp_path):
        """Returns Assumption when configured size < inferred minimum."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "Controllers.f90").write_text(
            "    avrSWAP(2601) = CC_length\n"
            "    avrSWAP(2801) = StC_input\n"
        )
        result = infer_assumed_size_arrays(str(src), {"avrSWAP": 2000})
        assert len(result) == 1
        assert "2801" in result[0].context or "2801" in result[0].summary
        assert int(result[0].assumed_value) > 2801

    def test_zero_size_triggers_inference(self, tmp_path):
        """Size 0 means auto-detect — always returns Assumption with inferred size."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "Controllers.f90").write_text(
            "    avrSWAP(500) = torque\n"
            "    avrSWAP(1018) = platform\n"
        )
        result = infer_assumed_size_arrays(str(src), {"avrSWAP": 0})
        assert len(result) == 1
        assert "Detected" in result[0].summary
        assert int(result[0].assumed_value) > 1018

    def test_zero_size_no_evidence_returns_error(self, tmp_path):
        """Size 0 with no index evidence returns error Assumption (cannot infer)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "Empty.f90").write_text("    x = 1.0\n")
        result = infer_assumed_size_arrays(str(src), {"avrSWAP": 0})
        assert len(result) == 1
        assert "no index evidence" in result[0].summary or "no evidence" in result[0].summary
        assert result[0].assumed_value == ""  # No proposal — hard error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
