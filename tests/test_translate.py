"""
Tests for the translation scaffold and prompt generation.

Tests generate_translation_scaffold() and generate_translation_prompt()
using synthetic Fortran fixtures (same pattern as test_core.py).
"""

import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.fortran_parser import parse_signature_from_source
from vit.translate import (
    generate_translation_scaffold,
    generate_translation_prompt,
    _generate_metadata_header,
    _get_c_return_type,
    _extract_function_text,
)


# === Test data ===

SATURATE_SOURCE = """\
MODULE Functions

IMPLICIT NONE

CONTAINS

    FUNCTION saturate(val, minval, maxval) RESULT(saturate_result)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: val
        REAL(8), INTENT(IN) :: minval
        REAL(8), INTENT(IN) :: maxval
        REAL(8) :: saturate_result
        saturate_result = MIN(MAX(val, minval), maxval)
    END FUNCTION saturate

END MODULE Functions
"""

INTERP1D_SOURCE = """\
MODULE Functions

USE ROSCO_Types, ONLY: ErrorVariables

IMPLICIT NONE

CONTAINS

    FUNCTION interp1d(xData, yData, xq, ErrVar) RESULT(yq)
        USE ROSCO_Types, ONLY: ErrorVariables
        IMPLICIT NONE

        REAL(8), DIMENSION(:), INTENT(IN)    :: xData
        REAL(8), DIMENSION(:), INTENT(IN)    :: yData
        REAL(8), INTENT(IN)                  :: xq
        TYPE(ErrorVariables), INTENT(INOUT)  :: ErrVar
        REAL(8)                              :: yq

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


# === Scaffold generation tests ===

class TestScaffoldBasic:

    def test_scaffold_has_correct_signature(self):
        """Scaffold for saturate has 3 double params and returns double."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "double saturate(double val, double minval, double maxval)" in scaffold

    def test_scaffold_has_placeholder_body(self):
        """Scaffold has TODO placeholder."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "// TODO: implement saturate" in scaffold

    def test_scaffold_has_return_statement(self):
        """Function scaffold has a return statement."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "return 0;" in scaffold

    def test_scaffold_has_braces(self):
        """Scaffold has opening and closing braces."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "{" in scaffold
        assert "}" in scaffold


class TestScaffoldWithArrays:

    def test_scaffold_has_array_params(self):
        """interp1d scaffold has pointer params for arrays."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        scaffold = generate_translation_scaffold(sig)
        assert "double* xData" in scaffold
        assert "double* yData" in scaffold

    def test_scaffold_has_size_params(self):
        """interp1d scaffold has size params for assumed-shape arrays."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        scaffold = generate_translation_scaffold(sig)
        assert "int n_xData" in scaffold
        assert "int n_yData" in scaffold

    def test_scaffold_excludes_passthrough(self):
        """interp1d C++ signature excludes ErrVar (passthrough derived type)."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        scaffold = generate_translation_scaffold(sig)
        # ErrVar appears in the metadata Fortran signature line, but NOT
        # in the C++ function signature or body
        cpp_lines = [l for l in scaffold.split("\n") if not l.startswith("//")]
        cpp_part = "\n".join(cpp_lines)
        assert "ErrVar" not in cpp_part

    def test_scaffold_has_scalar_param(self):
        """interp1d scaffold includes xq scalar."""
        sig = parse_signature_from_source(INTERP1D_SOURCE, "interp1d")
        scaffold = generate_translation_scaffold(sig)
        assert "double xq" in scaffold


class TestScaffoldMetadata:

    def test_metadata_has_function_name(self):
        """Metadata header includes function name."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "// Function: saturate" in scaffold

    def test_metadata_has_vit_version(self):
        """Metadata header includes VIT version."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig, vit_version="0.1.0")
        assert "// VIT: 0.1.0" in scaffold

    def test_metadata_has_status(self):
        """Metadata header includes unverified status."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "// Status: unverified" in scaffold

    def test_metadata_has_fortran_signature(self):
        """Metadata header includes Fortran signature summary."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "// Fortran: FUNCTION saturate(" in scaffold

    def test_metadata_has_generated_timestamp(self):
        """Metadata header includes a timestamp."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert "// Generated:" in scaffold

    def test_metadata_has_module(self):
        """Metadata header includes module name when available."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        sig.module_name = "Functions"
        scaffold = generate_translation_scaffold(sig)
        assert "// Module: Functions" in scaffold


class TestScaffoldWithStructHeaders:

    def test_scaffold_includes_type_headers(self):
        """Scaffold with struct_headers emits #include directives."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(
            sig, struct_headers=["vit_types.h"])
        assert '#include "vit_types.h"' in scaffold

    def test_scaffold_no_struct_headers(self):
        """Without struct_headers, no type #include directives."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        assert '#include "' not in scaffold


class TestModuleDetection:

    def test_module_from_signature(self):
        """Module name is detected from the Fortran source."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        # parse_signature_from_source doesn't set module_name,
        # but find_function_in_file does. Test the header directly.
        sig.module_name = "Functions"
        header = _generate_metadata_header(sig)
        assert "// Module: Functions" in header

    def test_no_module_omits_line(self):
        """If no module name, the Module line is not present."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        sig.module_name = None
        header = _generate_metadata_header(sig)
        assert "// Module:" not in header


# === Translation prompt tests ===

class TestTranslationPrompt:

    def test_prompt_includes_fortran_source(self):
        """Prompt includes the Fortran source code."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        prompt = generate_translation_prompt(
            sig=sig,
            fortran_source_text="FUNCTION saturate(val, minval, maxval)",
        )
        assert "FUNCTION saturate" in prompt

    def test_prompt_includes_scaffold(self):
        """Prompt includes the C++ scaffold."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        scaffold = generate_translation_scaffold(sig)
        prompt = generate_translation_prompt(
            sig=sig,
            scaffold_text=scaffold,
        )
        assert "double saturate(" in prompt

    def test_prompt_reads_the_shared_header_inventory(self, tmp_path):
        """What replaced the tips file: the callable inventory is READ from the
        shared headers, so it cannot drift from them."""
        h = tmp_path / "vit_nwtc.h"
        h.write_text("constexpr int ErrMsgLen = 1024;\n"
                     "bool EqualRealNos(double a, double b);\n")
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        prompt = generate_translation_prompt(sig=sig, shared_header_paths=[str(h)])
        assert "EqualRealNos" in prompt
        assert "ErrMsgLen" in prompt

    def test_prompt_carries_no_hand_written_tips_section(self):
        """`TRANSLATION_TIPS.md` is gone and nothing may reintroduce a prose
        section beside the generated one -- two sources of guidance is exactly
        the drift this replaced."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        prompt = generate_translation_prompt(sig=sig)
        assert "Translation Tips" not in prompt
        assert "TRANSLATION_TIPS" not in prompt

    def test_prompt_has_instructions(self):
        """Prompt includes general translation instructions."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        prompt = generate_translation_prompt(sig=sig)
        assert "Instructions" in prompt
        assert "Array indexing" in prompt


# === Helper function tests ===

class TestHelpers:

    def test_get_c_return_type_function(self):
        """Function return type is double for REAL(8)."""
        sig = parse_signature_from_source(SATURATE_SOURCE, "saturate")
        assert _get_c_return_type(sig) == "double"

    def test_extract_function_text(self):
        """Extract function text from source."""
        text = _extract_function_text(SATURATE_SOURCE, "saturate")
        assert text is not None
        assert "FUNCTION saturate" in text
        assert "END FUNCTION saturate" in text
        assert "MIN(MAX" in text

    def test_extract_function_text_not_found(self):
        """Returns None when function not found."""
        text = _extract_function_text(SATURATE_SOURCE, "nonexistent")
        assert text is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# --------------------------------------------------------------------------
# The scaffold carries what the campaign declares, so no tip has to say it.
# Step 0 of POST_REPLICATION_ACTIONS.md; dev note 202608100030.

class TestScaffoldCarriesTheCampaignsHeaders:
    """Four translation tips existed only because the scaffold did not include
    the shared headers `vit.yaml` already declares -- "use Fortran's constants,
    not `M_PI`", "shared constants header", "use `vit_fortran_read.h`", and
    "`PathSep` is platform-selected". Prose on rung 4 for want of one line on
    rung 1."""

    def _sig(self, src="SUBROUTINE Foo(x)\n  REAL(8), INTENT(IN) :: x\nEND SUBROUTINE Foo"):
        from vit.fortran_parser import parse_signature_from_source
        return parse_signature_from_source(src, "Foo")

    def test_shared_headers_are_included(self):
        out = generate_translation_scaffold(
            self._sig(), shared_headers=["rosco_constants.h", "vit_fortran_read.h"])
        assert '#include "rosco_constants.h"' in out
        assert '#include "vit_fortran_read.h"' in out

    def test_the_type_header_comes_first_and_nothing_is_duplicated(self):
        out = generate_translation_scaffold(
            self._sig(), struct_headers=["vit_types.h"],
            shared_headers=["vit_types.h", "rosco_constants.h"])
        assert out.count('#include "vit_types.h"') == 1, "a duplicate include"
        assert out.index('vit_types.h') < out.index('rosco_constants.h')

    def test_no_headers_means_no_blank_include_block(self):
        out = generate_translation_scaffold(self._sig())
        assert "#include" not in out

    def test_the_reference_build_flags_are_stated(self):
        """Whether a bare Fortran literal is single or double precision depends
        on `-fdefault-real-8`, and nothing in the source says so."""
        out = generate_translation_scaffold(
            self._sig(), fortran_flags="-fdefault-real-8 -ffp-contract=off")
        assert "-fdefault-real-8" in out
        assert "Reference built with:" in out

    def test_flags_are_omitted_when_the_campaign_declares_none(self):
        assert "Reference built with:" not in generate_translation_scaffold(self._sig())
