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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
