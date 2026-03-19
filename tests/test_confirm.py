"""
Tests for the VIT assumption confirmation protocol.
"""

import io
import textwrap
from contextlib import redirect_stdout

from vit.confirm import (
    EXIT_CONFIRM,
    EXIT_MANUAL,
    Assumption,
    ManualEdit,
    ManualReview,
    parse_assumptions,
    parse_manual_reviews,
    print_assumption,
    print_manual_review,
)


SAMPLE_ASSUMPTION = Assumption(
    id="character_length",
    summary="CHARACTER buffer size for ErrorVariables.ErrMsg",
    assumed_value="1024",
    inferred_from="ALLOCATE(%ErrMsg(size_avcMSG-1)) in ReadSetParameters.f90:173",
    context="VIT found a commented-out ALLOCATE that allocates (size_avcMSG-1) chars.\nUsing 1024 as the fixed length.",
    cli_override="--length 1024",
)


class TestAssumption:
    """Test Assumption dataclass and formatting."""

    def test_exit_confirm_is_10(self):
        assert EXIT_CONFIRM == 10

    def test_print_contains_delimiters(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(SAMPLE_ASSUMPTION)
        output = buf.getvalue()
        assert "[VIT:CONFIRM]" in output
        assert "[/VIT:CONFIRM]" in output

    def test_print_contains_all_fields(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(SAMPLE_ASSUMPTION)
        output = buf.getvalue()
        assert "id: character_length" in output
        assert "assumed_value: 1024" in output
        assert "cli_override: --length 1024" in output
        assert "ReadSetParameters.f90:173" in output

    def test_print_multiline_context(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(SAMPLE_ASSUMPTION)
        output = buf.getvalue()
        assert "context: |" in output
        assert "  VIT found a commented-out" in output
        assert "  Using 1024" in output

    def test_print_singleline_context(self):
        a = Assumption(
            id="test", summary="test", assumed_value="42",
            inferred_from="test", context="single line context",
            cli_override="--val 42",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(a)
        output = buf.getvalue()
        assert "context: single line context" in output
        assert "context: |" not in output


class TestParseAssumptions:
    """Test parsing [VIT:CONFIRM] blocks from output."""

    def test_parse_roundtrip(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(SAMPLE_ASSUMPTION)
        output = buf.getvalue()
        parsed = parse_assumptions(output)
        assert len(parsed) == 1
        a = parsed[0]
        assert a.id == "character_length"
        assert a.assumed_value == "1024"
        assert a.cli_override == "--length 1024"
        assert "ReadSetParameters" in a.inferred_from

    def test_parse_multiline_context_roundtrip(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(SAMPLE_ASSUMPTION)
        output = buf.getvalue()
        parsed = parse_assumptions(output)
        assert "commented-out" in parsed[0].context
        assert "1024" in parsed[0].context

    def test_parse_multiple_blocks(self):
        a1 = Assumption(
            id="length_a", summary="A", assumed_value="100",
            inferred_from="test", context="ctx a", cli_override="--a 100",
        )
        a2 = Assumption(
            id="length_b", summary="B", assumed_value="200",
            inferred_from="test", context="ctx b", cli_override="--b 200",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_assumption(a1)
            print_assumption(a2)
        output = buf.getvalue()
        parsed = parse_assumptions(output)
        assert len(parsed) == 2
        assert parsed[0].id == "length_a"
        assert parsed[1].id == "length_b"

    def test_parse_with_surrounding_text(self):
        output = textwrap.dedent("""\
            Analyzing types...
            Found 13 types.

            [VIT:CONFIRM]
            id: test_id
            summary: test summary
            assumed_value: 42
            inferred_from: test source
            context: test context
            cli_override: --val 42
            [/VIT:CONFIRM]

            Done.
        """)
        parsed = parse_assumptions(output)
        assert len(parsed) == 1
        assert parsed[0].id == "test_id"
        assert parsed[0].assumed_value == "42"

    def test_parse_empty_output(self):
        assert parse_assumptions("") == []

    def test_parse_no_blocks(self):
        assert parse_assumptions("just normal output\nno blocks here\n") == []


# --- Manual review protocol tests ---

SAMPLE_EDIT = ManualEdit(
    file="/workspace/src/Controllers.f90",
    line=759,
    reason="complex intrinsic: EXP",
    source_text="LocalVar%AWC_complexangle(K) = LocalVar%AWC_complexangle(K) + amp * EXP(complexI * (AWC_angle(K)))",
    cli_override="--rewrite 'Controllers.f90:759=<replacement>'",
)

SAMPLE_REVIEW = ManualReview(
    fix_type="complex",
    summary="1 reference(s) need manual rewriting after --fix complex",
    edits=[SAMPLE_EDIT],
    changes_applied=7,
)


class TestManualReview:
    """Test ManualReview and ManualEdit formatting."""

    def test_exit_manual_is_11(self):
        assert EXIT_MANUAL == 11

    def test_print_contains_delimiters(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_manual_review(SAMPLE_REVIEW)
        output = buf.getvalue()
        assert "[VIT:MANUAL]" in output
        assert "[/VIT:MANUAL]" in output

    def test_print_contains_all_fields(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_manual_review(SAMPLE_REVIEW)
        output = buf.getvalue()
        assert "fix_type: complex" in output
        assert "changes_applied: 7" in output
        assert "edits_needed: 1" in output
        assert "edit_1_file: /workspace/src/Controllers.f90" in output
        assert "edit_1_line: 759" in output
        assert "edit_1_reason: complex intrinsic: EXP" in output
        assert "edit_1_cli_override: --rewrite" in output
        assert "EXP(complexI" in output

    def test_print_multiple_edits(self):
        edit2 = ManualEdit(
            file="/workspace/src/Other.f90", line=42,
            reason="complex intrinsic: CMPLX",
            source_text="x = CMPLX(a, b)",
        )
        review = ManualReview(
            fix_type="complex", summary="2 edits",
            edits=[SAMPLE_EDIT, edit2], changes_applied=5,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_manual_review(review)
        output = buf.getvalue()
        assert "edits_needed: 2" in output
        assert "edit_1_file:" in output
        assert "edit_2_file:" in output


class TestParseManualReviews:
    """Test parsing [VIT:MANUAL] blocks from output."""

    def test_parse_roundtrip(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_manual_review(SAMPLE_REVIEW)
        output = buf.getvalue()
        parsed = parse_manual_reviews(output)
        assert len(parsed) == 1
        r = parsed[0]
        assert r.fix_type == "complex"
        assert r.changes_applied == 7
        assert len(r.edits) == 1
        assert r.edits[0].file == "/workspace/src/Controllers.f90"
        assert r.edits[0].line == 759
        assert "EXP" in r.edits[0].reason
        assert "--rewrite" in r.edits[0].cli_override
        assert "Controllers.f90:759" in r.edits[0].cli_override

    def test_parse_empty(self):
        assert parse_manual_reviews("") == []

    def test_parse_with_surrounding_text(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print(f"Some output before")
            print_manual_review(SAMPLE_REVIEW)
            print(f"Some output after")
        output = buf.getvalue()
        parsed = parse_manual_reviews(output)
        assert len(parsed) == 1
