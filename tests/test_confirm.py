"""
Tests for the VIT assumption confirmation protocol.
"""

import io
import textwrap
from contextlib import redirect_stdout

from vit.confirm import (
    EXIT_CONFIRM,
    Assumption,
    parse_assumptions,
    print_assumption,
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
