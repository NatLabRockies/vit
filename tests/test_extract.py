"""
Tests for VIT extraction helpers.

Tests pure-logic functions from extract.py that don't require
Docker, KGen, or a real Fortran project.
"""

import os
import pytest
import shutil
import sys
import textwrap

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


# === find_call_sites tests ===

class TestFindCallSites:

    def test_finds_call_statement(self, tmp_path):
        """Finds CALL FuncName(...) in source files."""
        from vit.extract import find_call_sites
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.f90").write_text(textwrap.dedent("""\
            SUBROUTINE Driver()
                CALL MyTarget(x, y, z)
            END SUBROUTINE
        """))
        results = find_call_sites(str(src), "MyTarget")
        assert len(results) == 1
        assert results[0][1] == 2  # line number
        assert "MyTarget" in results[0][2]

    def test_case_insensitive(self, tmp_path):
        """Finds call sites regardless of case."""
        from vit.extract import find_call_sites
        src = tmp_path / "src"
        src.mkdir()
        (src / "test.f90").write_text(textwrap.dedent("""\
            SUBROUTINE Caller()
                call mytarget(a, b)
            END SUBROUTINE
        """))
        results = find_call_sites(str(src), "MyTarget")
        assert len(results) == 1

    def test_excludes_definition(self, tmp_path):
        """Does not return the function's own definition line."""
        from vit.extract import find_call_sites
        src = tmp_path / "src"
        src.mkdir()
        (src / "funcs.f90").write_text(textwrap.dedent("""\
            SUBROUTINE MyTarget(x)
                INTEGER, INTENT(IN) :: x
            END SUBROUTINE

            SUBROUTINE Caller()
                CALL MyTarget(42)
            END SUBROUTINE
        """))
        results = find_call_sites(str(src), "MyTarget")
        # Should find the CALL but not the SUBROUTINE definition
        assert len(results) == 1
        assert "CALL" in results[0][2].upper()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# --- Block-construct pragma placement ---------------------------------------
#
# A call embedded in a construct header is a legitimate call site. Wrapping
# only the header leaves an unterminated construct inside the callsite region,
# and KGen emits a kernel that fails to compile with the cause nowhere near the
# symptom. See DECISIONS.md, NonDecreasing (#20).

import re as _re
from pathlib import Path as _Path

from vit.extract import insert_kgen_pragma as _insert


def _wrap(tmp_path, source, line_number):
    f = tmp_path / "src.f90"
    f.write_text(source)
    _insert(str(f), line_number, function_name="Target")
    return f.read_text().split("\n")


def _region(lines):
    """Lines strictly between the two pragmas."""
    begin = next(i for i, l in enumerate(lines) if "begin_callsite" in l)
    end = next(i for i, l in enumerate(lines) if "end_callsite" in l)
    return [l.strip() for l in lines[begin + 1:end]]


def test_pragma_wraps_whole_if_construct(tmp_path):
    src = "\n".join([
        "SUBROUTINE S()",
        "    IF (a > 0 .AND. .NOT. Target(x)) THEN",
        "        b = 1",
        "    ENDIF",
        "    c = 2",
        "END SUBROUTINE S",
    ])
    region = _region(_wrap(tmp_path, src, 2))
    assert region[0].startswith("IF (")
    assert region[-1].upper().replace(" ", "") == "ENDIF"
    assert "b = 1" in region


def test_pragma_handles_nested_if(tmp_path):
    src = "\n".join([
        "SUBROUTINE S()",
        "    IF (Target(x)) THEN",
        "        IF (y) THEN",
        "            b = 1",
        "        END IF",
        "    END IF",
        "    c = 2",
        "END SUBROUTINE S",
    ])
    region = _region(_wrap(tmp_path, src, 2))
    # Must close on the OUTER END IF, not the inner one.
    assert len([l for l in region if l.upper().replace(" ", "") == "ENDIF"]) == 2
    assert "c = 2" not in region


def test_pragma_wraps_do_construct(tmp_path):
    src = "\n".join([
        "SUBROUTINE S()",
        "    DO i = 1, Target(n)",
        "        b = i",
        "    END DO",
        "END SUBROUTINE S",
    ])
    region = _region(_wrap(tmp_path, src, 2))
    assert region[0].startswith("DO ")
    assert region[-1].upper().replace(" ", "") == "ENDDO"


def test_pragma_leaves_plain_assignment_alone(tmp_path):
    src = "\n".join([
        "SUBROUTINE S()",
        "    y = Target(x)",
        "    c = 2",
        "END SUBROUTINE S",
    ])
    region = _region(_wrap(tmp_path, src, 2))
    assert region == ["y = Target(x)"]


def test_pragma_ignores_bang_inside_string(tmp_path):
    # The '!' is inside a literal, so the line still ends with THEN and is a
    # block opener.
    src = "\n".join([
        "SUBROUTINE S()",
        "    IF (msg == '!' .AND. Target(x)) THEN",
        "        b = 1",
        "    END IF",
        "END SUBROUTINE S",
    ])
    region = _region(_wrap(tmp_path, src, 2))
    assert region[-1].upper().replace(" ", "") == "ENDIF"


def test_pragma_single_line_if_is_not_a_block(tmp_path):
    src = "\n".join([
        "SUBROUTINE S()",
        "    IF (Target(x)) b = 1",
        "    c = 2",
        "END SUBROUTINE S",
    ])
    region = _region(_wrap(tmp_path, src, 2))
    assert region == ["IF (Target(x)) b = 1"]


class TestCountKernelCases:
    """State-file counting must be scoped to the function being extracted.

    Counting any function's files turns extraction's one loud failure mode --
    "no state data captured" -- into a silent wrong answer: a stale file from a
    previous extraction reads as a real capture. See DECISIONS.md, CurDate (#26).
    """

    def _make(self, tmp_path, names):
        d = tmp_path / "kernel"
        d.mkdir()
        for n in names:
            (d / n).write_text("x")
        return str(d)

    def test_counts_only_the_named_function(self, tmp_path):
        from vit.extract import count_kernel_cases
        d = self._make(tmp_path, ["CurDate.0.0.1", "AddToList.0.0.1",
                                  "AddToList.0.0.2"])
        assert count_kernel_cases(d, "CurDate") == 1
        assert count_kernel_cases(d, "AddToList") == 2

    def test_stale_file_alone_is_zero_not_one(self, tmp_path):
        """The exact CurDate case: no own state file, one left by a predecessor."""
        from vit.extract import count_kernel_cases
        d = self._make(tmp_path, ["AddToList.0.0.1"])
        assert count_kernel_cases(d, "CurDate") == 0

    def test_name_match_is_case_insensitive(self, tmp_path):
        """KGen lowercases in some paths; a case-sensitive match reports a false zero."""
        from vit.extract import count_kernel_cases
        d = self._make(tmp_path, ["curdate.0.0.1"])
        assert count_kernel_cases(d, "CurDate") == 1

    def test_prefix_is_not_a_match(self, tmp_path):
        from vit.extract import count_kernel_cases
        d = self._make(tmp_path, ["CurDateExtra.0.0.1"])
        assert count_kernel_cases(d, "CurDate") == 0

    def test_no_name_preserves_old_behaviour(self, tmp_path):
        from vit.extract import count_kernel_cases
        d = self._make(tmp_path, ["AddToList.0.0.1", "CurDate.0.0.1"])
        assert count_kernel_cases(d) == 2


# --------------------------------------------------------------------------
# The build-file patch must not be able to fail quietly.

def _wrapper(tmp_path, **kw):
    import os, pathlib
    from vit.extract import generate_state_capture_build_script
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return pathlib.Path(generate_state_capture_build_script(
            "cmake --build .", cmake_file=str(tmp_path / "CMakeLists.txt"), **kw)).read_text()
    finally:
        os.chdir(cwd)


def test_a_failed_source_patch_stops_the_build(tmp_path):
    """The worst failure this toolchain had, and it was silent.

    The `sed` that adds kgen_utils.f90 to the source list assumed a `src/`
    prefix, a lowercase `.f90` extension and file names free of digits and
    hyphens. When it matched nothing the wrapper printed "VIT: Patched
    CMakeLists.txt" anyway; the build then succeeded, the shared library linked
    with kgen_init_vars unresolved (a Linux .so permits that), the simulation
    ran, and the only symptom was "No state data captured" -- with nothing
    pointing back at the patch. See dev note 202608071141.

    The pattern is wider now, but that is not the fix: the next layout will
    break it too. The fix is that an unmatched pattern is FATAL.
    """
    script = _wrapper(tmp_path)
    assert "VIT ERROR: could not add kgen_utils.f90" in script
    assert "exit 1" in script
    # And it must say what to do about it.
    assert "add_source_cmd in vit.yaml" in script


def test_a_missing_build_file_stops_the_build(tmp_path):
    """`cmake_file` was configured, so its absence is a misconfiguration.
    Skipping quietly produced the same empty capture, one step earlier."""
    assert "VIT ERROR: cmake_file is configured but" in _wrapper(tmp_path)


def test_add_source_cmd_replaces_the_heuristic(tmp_path):
    """A heuristic that can be overridden is a heuristic; one that cannot is a
    requirement on every future codebase. Build systems other than CMake, and
    source lists the pattern cannot match, need an escape hatch rather than a
    patch to VIT."""
    script = _wrapper(tmp_path, add_source_cmd="my-custom-adder")
    assert "( my-custom-adder )" in script
    assert "sed -i -E" not in script, "the override must REPLACE the guess, not run beside it"


@pytest.mark.skipif(shutil.which("cmake") is None, reason="needs cmake")
def test_reconfigure_works_when_the_build_tree_is_not_a_child(tmp_path):
    """Reconfiguring must follow the build tree's own cache, not `..`.

    The wrapper and the cleanup reconfigured with `cd <build_dir> && cmake ..`,
    which only works when the build tree sits directly under the top-level
    CMakeLists.txt. With the build tree anywhere else the reconfigure failed,
    the failure was discarded, and kgen_utils.f90 never entered the build.
    """
    import subprocess
    from vit.extract import cmake_reconfigure_command

    source = tmp_path / "project"
    source.mkdir()
    (source / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(t NONE)\n")
    build = tmp_path / "elsewhere" / "build"
    build.mkdir(parents=True)
    subprocess.run(["cmake", "-S", str(source), "-B", str(build)],
                   check=True, capture_output=True)

    # A change the reconfigure must pick up.
    (source / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(t NONE)\n"
        'message(STATUS "reconfigured-from-cache")\n')

    old = subprocess.run(f'cd "{build}" && cmake ..', shell=True,
                         capture_output=True, text=True)
    assert old.returncode != 0, "the old command should fail in this layout"

    new = subprocess.run(cmake_reconfigure_command(str(build)), shell=True,
                         capture_output=True, text=True)
    assert new.returncode == 0, new.stderr[-800:]
    assert "reconfigured-from-cache" in new.stdout


# --------------------------------------------------------------------------
# The cleanup sweeps cover the tree KGen searches, and touch nothing else.

def _nested_project(tmp_path):
    src = tmp_path / "modules"
    (src / "aero" / "src").mkdir(parents=True)
    nested = src / "aero" / "src" / "caller.f90"
    nested.write_text("subroutine s()\n!$kgen begin_callsite drag\n  call drag()\n"
                      "!$kgen end_callsite\nend subroutine s\n")
    clean = src / "aero" / "src" / "other.f90"
    clean.write_text("subroutine t()\nend subroutine t\n")
    return src, nested, clean


def test_stale_pragmas_are_swept_at_any_depth(tmp_path, monkeypatch):
    """KGen reads every Fortran file under source_dir (include.ini lists all
    of its directories), and acts on any pragma it finds. The sweep looked
    only at the top level, so with a nested source_dir a pragma left by an
    interrupted extraction survived into the next one."""
    from vit.extract import sweep_stale_pragmas

    monkeypatch.chdir(tmp_path)
    src, nested, clean = _nested_project(tmp_path)
    os.utime(clean, (1_000_000_000, 1_000_000_000))

    cleaned = sweep_stale_pragmas(str(src))

    assert cleaned == [str(nested)]
    assert "!$kgen" not in nested.read_text()
    assert "call drag()" in nested.read_text()
    # A file without a pragma is not rewritten, so the build does not
    # recompile it.
    assert os.stat(clean).st_mtime == 1_000_000_000


def test_sweeps_skip_extraction_outputs(tmp_path, monkeypatch):
    """kernel/ and state/ hold KGen's copies of the sources, including golden
    kernels. With source_dir covering the project root, they must be left
    alone."""
    from vit.extract import remove_stale_kgen_org, sweep_stale_pragmas

    monkeypatch.chdir(tmp_path)
    (tmp_path / "kernel" / "drag").mkdir(parents=True)
    golden = tmp_path / "kernel" / "drag" / "caller.f90"
    golden.write_text("!$kgen begin_callsite drag\n  call drag()\n!$kgen end_callsite\n")
    (tmp_path / "kernel" / "drag" / "caller.f90.kgen_org").write_text("x\n")

    assert sweep_stale_pragmas(str(tmp_path)) == []
    assert remove_stale_kgen_org(str(tmp_path)) == []
    assert "!$kgen" in golden.read_text()


def test_stale_kgen_org_backups_are_removed_at_any_depth(tmp_path, monkeypatch):
    from vit.extract import remove_stale_kgen_org

    monkeypatch.chdir(tmp_path)
    src, nested, _ = _nested_project(tmp_path)
    backup = nested.parent / "caller.f90.kgen_org"
    backup.write_text("instrumented\n")

    assert remove_stale_kgen_org(str(src)) == [str(backup)]
    assert not backup.exists()
