"""
Tests for VIT integration plan generation.

Tests IntegrationPlan.to_markdown() and generate_integration_plan()
using synthetic Fortran signatures — no real files or Docker needed
except where noted.
"""

import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.integrate import (
    IntegrationPatch,
    IntegrationPlan,
    generate_integration_plan,
    apply_integration_plan,
    _generate_standalone_cpp,
    _generate_cmake_patches,
    _generate_fortran_patches,
    _generate_c_declaration,
)
from vit.fortran_parser import (
    parse_signature_from_source,
    FortranSignature,
    FortranArg,
    SubprogramKind,
    IntentKind,
)


# === Shared test data ===

ROSCO_KIND_ALIASES = {
    "ReKi": "REAL(8)",
    "DbKi": "REAL(8)",
    "SiKi": "REAL(4)",
    "IntKi": "INTEGER(4)",
}

SIMPLE_MODULE_SOURCE = """\
MODULE Functions

USE Constants

IMPLICIT NONE

CONTAINS

    REAL(8) FUNCTION saturate(inputValue, minValue, maxValue)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: inputValue
        REAL(8), INTENT(IN) :: minValue
        REAL(8), INTENT(IN) :: maxValue
        saturate = MIN(MAX(inputValue, minValue), maxValue)
    END FUNCTION saturate

END MODULE Functions
"""

CPP_IMPL = """\
#include <algorithm>
double saturate(double val, double minval, double maxval) {
    return std::min(std::max(val, minval), maxval);
}
"""


# === IntegrationPlan.to_markdown tests ===

class TestIntegrationPlanMarkdown:

    def test_has_title(self):
        plan = IntegrationPlan(
            function_name="saturate",
            patches=[],
            summary="Test summary",
        )
        md = plan.to_markdown()
        assert "# Integration Plan: saturate" in md

    def test_has_summary(self):
        plan = IntegrationPlan(
            function_name="foo",
            patches=[],
            summary="This is the summary.",
        )
        md = plan.to_markdown()
        assert "This is the summary." in md

    def test_has_patch_count(self):
        patches = [
            IntegrationPatch("file1.f90", "Patch 1", "create", "content1"),
            IntegrationPatch("file2.f90", "Patch 2", "modify", "content2",
                             "old", "new"),
        ]
        plan = IntegrationPlan("foo", patches, "summary")
        md = plan.to_markdown()
        assert "2 files" in md

    def test_create_patch_shows_content(self):
        patch = IntegrationPatch("new_file.cpp", "Create file", "create",
                                 "int main() { return 0; }")
        plan = IntegrationPlan("foo", [patch], "summary")
        md = plan.to_markdown()
        assert "int main()" in md
        assert "```" in md

    def test_modify_patch_shows_replace(self):
        patch = IntegrationPatch("file.f90", "Modify file", "modify", "",
                                 "IMPLICIT NONE",
                                 "USE ISO_C_BINDING\nIMPLICIT NONE")
        plan = IntegrationPlan("foo", [patch], "summary")
        md = plan.to_markdown()
        assert "Replace:" in md
        assert "With:" in md

    def test_empty_patches(self):
        plan = IntegrationPlan("foo", [], "No changes needed")
        md = plan.to_markdown()
        assert "0 files" in md


# === _generate_standalone_cpp tests ===

class TestGenerateStandaloneCpp:

    def _make_sig(self):
        return FortranSignature(
            name="saturate",
            kind=SubprogramKind.FUNCTION,
            args=[
                FortranArg("inputValue", "REAL(8)", IntentKind.IN),
                FortranArg("minValue", "REAL(8)", IntentKind.IN),
                FortranArg("maxValue", "REAL(8)", IntentKind.IN),
            ],
            result_name="saturate_result",
            result_type="REAL(8)",
        )

    def test_contains_extern_c(self):
        sig = self._make_sig()
        result = _generate_standalone_cpp(sig, CPP_IMPL)
        assert 'extern "C"' in result

    def test_contains_c_wrapper_function(self):
        sig = self._make_sig()
        result = _generate_standalone_cpp(sig, CPP_IMPL)
        assert "saturate_c" in result

    def test_contains_user_implementation(self):
        sig = self._make_sig()
        result = _generate_standalone_cpp(sig, CPP_IMPL)
        assert "std::min(std::max" in result

    def test_wrapper_has_correct_return_type(self):
        sig = self._make_sig()
        result = _generate_standalone_cpp(sig, CPP_IMPL)
        assert "double saturate_c" in result

    def test_wrapper_has_correct_params(self):
        sig = self._make_sig()
        result = _generate_standalone_cpp(sig, CPP_IMPL)
        assert "double inputValue" in result
        assert "double minValue" in result
        assert "double maxValue" in result

    def test_subroutine_wrapper_returns_void(self):
        sig = FortranSignature(
            name="update",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg("x", "REAL(8)", IntentKind.INOUT),
                FortranArg("y", "REAL(8)", IntentKind.OUT),
            ],
            result_name=None,
            result_type=None,
        )
        result = _generate_standalone_cpp(sig, "void update(double& x, double& y) {}")
        assert "void update_c" in result

    def test_header_comment(self):
        sig = self._make_sig()
        result = _generate_standalone_cpp(sig, CPP_IMPL)
        assert "Generated by VIT" in result


# === _generate_cmake_patches tests ===

class TestGenerateCmakePatches:

    def _write_cmake(self, tmp_path, content):
        cmake = tmp_path / "CMakeLists.txt"
        cmake.write_text(content)
        return str(cmake)

    def test_adds_enable_cxx(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO VERSION 2.9.0 LANGUAGES Fortran C)\n"
            "set(SOURCES\n"
            "    src/Functions.f90\n"
            ")\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        descriptions = [p.description for p in patches]
        assert any("CXX" in d for d in descriptions)

    def test_skips_cxx_if_present(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO VERSION 2.9.0 LANGUAGES Fortran C)\n"
            "enable_language(CXX)\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        descriptions = [p.description for p in patches]
        assert not any("Enable CXX" in d for d in descriptions)

    def test_adds_cpp_source(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO LANGUAGES Fortran C)\n"
            "set(SOURCES\n"
            "    src/Functions.f90\n"
            "    src/Controllers.f90\n"
            ")\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        cpp_patch = [p for p in patches if "saturate_cpp.cpp" in p.description]
        assert len(cpp_patch) == 1
        assert cpp_patch[0].action == "modify"
        assert cpp_patch[0].original_snippet is not None
        assert "saturate_cpp.cpp" in cpp_patch[0].replacement_snippet

    def test_skips_cpp_source_if_present(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO LANGUAGES Fortran C)\n"
            "set(SOURCES src/saturate_cpp.cpp)\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        descriptions = [p.description for p in patches]
        assert not any("saturate_cpp.cpp" in d for d in descriptions)

    def test_adds_stdc_link(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO LANGUAGES Fortran C)\n"
            "set(SOURCES\n"
            "    src/Functions.f90\n"
            ")\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        stdc_patch = [p for p in patches if "stdc++" in p.description]
        assert len(stdc_patch) == 1
        assert stdc_patch[0].action == "modify"
        assert stdc_patch[0].original_snippet is not None
        assert "target_link_libraries" in stdc_patch[0].replacement_snippet

    def test_skips_stdc_if_present(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO LANGUAGES Fortran C)\n"
            "add_library(discon SHARED ${SOURCES})\n"
            "target_link_libraries(discon PRIVATE stdc++)\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        descriptions = [p.description for p in patches]
        assert not any("stdc++" in d for d in descriptions)

    def test_all_present_no_patches(self, tmp_path):
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO LANGUAGES Fortran C)\n"
            "enable_language(CXX)\n"
            "set(SOURCES src/saturate_cpp.cpp)\n"
            "add_library(discon SHARED ${SOURCES})\n"
            "target_link_libraries(discon PRIVATE stdc++)\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        assert len(patches) == 0


# === generate_integration_plan tests (requires tmp_path for file I/O) ===

class TestGenerateIntegrationPlan:

    def test_creates_patches_for_simple_function(self, tmp_path):
        """Full plan generation for a simple function produces expected patches."""
        # Write the Fortran source and C++ impl
        f90 = tmp_path / "Functions.f90"
        f90.write_text(SIMPLE_MODULE_SOURCE)
        cpp = tmp_path / "saturate.cpp"
        cpp.write_text(CPP_IMPL)
        cmake = tmp_path / "CMakeLists.txt"
        cmake.write_text(
            "project(ROSCO VERSION 2.9.0 LANGUAGES Fortran C)\n"
            "set(SOURCES src/Functions.f90)\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )

        sig = parse_signature_from_source(SIMPLE_MODULE_SOURCE, "saturate")
        plan = generate_integration_plan(
            sig, str(cpp), str(f90),
            cmake_file=str(cmake),
            target_name="discon",
        )

        assert plan.function_name == "saturate"
        assert len(plan.patches) >= 4  # cpp file + USE + interface + body + cmake patches
        actions = [p.action for p in plan.patches]
        assert "create" in actions
        assert "modify" in actions

    def test_summary_mentions_function_name(self, tmp_path):
        f90 = tmp_path / "Functions.f90"
        f90.write_text(SIMPLE_MODULE_SOURCE)
        cpp = tmp_path / "saturate.cpp"
        cpp.write_text(CPP_IMPL)

        sig = parse_signature_from_source(SIMPLE_MODULE_SOURCE, "saturate")
        plan = generate_integration_plan(sig, str(cpp), str(f90))

        assert "saturate" in plan.summary


# === apply_integration_plan tests ===

class TestApplyIntegrationPlan:

    def test_dry_run_does_not_create_files(self, tmp_path):
        patch = IntegrationPatch(
            str(tmp_path / "new_file.cpp"), "Create file", "create",
            "int main() {}"
        )
        plan = IntegrationPlan("foo", [patch], "summary")

        result = apply_integration_plan(plan, dry_run=True)
        assert result["success"] is True
        assert not (tmp_path / "new_file.cpp").exists()
        assert "DRY RUN" in result["applied"][0]

    def test_create_writes_file(self, tmp_path):
        patch = IntegrationPatch(
            str(tmp_path / "new_file.cpp"), "Create file", "create",
            "int main() { return 0; }"
        )
        plan = IntegrationPlan("foo", [patch], "summary")

        result = apply_integration_plan(plan, dry_run=False)
        assert result["success"] is True
        assert (tmp_path / "new_file.cpp").exists()
        assert "int main()" in (tmp_path / "new_file.cpp").read_text()

    def test_modify_replaces_text(self, tmp_path):
        target = tmp_path / "file.f90"
        target.write_text("IMPLICIT NONE\nREAL :: x\n")

        patch = IntegrationPatch(
            str(target), "Add ISO_C_BINDING", "modify", "",
            "IMPLICIT NONE",
            "USE ISO_C_BINDING\nIMPLICIT NONE",
        )
        plan = IntegrationPlan("foo", [patch], "summary")

        result = apply_integration_plan(plan, dry_run=False)
        assert result["success"] is True
        assert "ISO_C_BINDING" in target.read_text()

    def test_modify_missing_text_reports_error(self, tmp_path):
        target = tmp_path / "file.f90"
        target.write_text("REAL :: x\n")

        patch = IntegrationPatch(
            str(target), "Replace something", "modify", "",
            "NONEXISTENT TEXT",
            "replacement",
        )
        plan = IntegrationPlan("foo", [patch], "summary")

        result = apply_integration_plan(plan, dry_run=False)
        assert result["success"] is False
        assert len(result["errors"]) > 0


# === Multi-function test data ===

TWO_FUNCTION_MODULE = """\
MODULE Functions

USE Constants

IMPLICIT NONE

CONTAINS

    REAL(8) FUNCTION saturate(inputValue, minValue, maxValue)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: inputValue
        REAL(8), INTENT(IN) :: minValue
        REAL(8), INTENT(IN) :: maxValue
        saturate = MIN(MAX(inputValue, minValue), maxValue)
    END FUNCTION saturate

    REAL(8) FUNCTION wrap_180(x)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: x
        wrap_180 = MODULO(x + 180.0d0, 360.0d0) - 180.0d0
    END FUNCTION wrap_180

END MODULE Functions
"""

MULTI_SOURCE_CMAKE = """\
project(ROSCO VERSION 2.9.0 LANGUAGES Fortran C)
set(SOURCES
    src/Constants.f90
    src/Controllers.f90
    src/Functions.f90
    src/ExtControl.f90
)
add_library(discon SHARED ${SOURCES})
"""


# === Multi-function CMake tests ===

class TestMultiFunctionCmakePatches:

    def _write_cmake(self, tmp_path, content):
        cmake = tmp_path / "CMakeLists.txt"
        cmake.write_text(content)
        return str(cmake)

    def test_first_function_cpp_patch_is_modify(self, tmp_path):
        cmake = self._write_cmake(tmp_path, MULTI_SOURCE_CMAKE)
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        cpp_patch = [p for p in patches if "saturate_cpp.cpp" in p.description]
        assert len(cpp_patch) == 1
        assert cpp_patch[0].action == "modify"

    def test_stdc_patch_is_modify(self, tmp_path):
        cmake = self._write_cmake(tmp_path, MULTI_SOURCE_CMAKE)
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        stdc_patch = [p for p in patches if "stdc++" in p.description]
        assert len(stdc_patch) == 1
        assert stdc_patch[0].action == "modify"

    def test_sequential_apply_adds_both_cpp_files(self, tmp_path):
        cmake_path = self._write_cmake(tmp_path, MULTI_SOURCE_CMAKE)

        # Apply first function's patches
        patches1 = _generate_cmake_patches(cmake_path, "saturate_cpp.cpp", "discon")
        plan1 = IntegrationPlan("saturate", patches1, "")
        result1 = apply_integration_plan(plan1)
        assert result1["success"]

        # Apply second function's patches (re-reads file)
        patches2 = _generate_cmake_patches(cmake_path, "wrap_180_cpp.cpp", "discon")
        plan2 = IntegrationPlan("wrap_180", patches2, "")
        result2 = apply_integration_plan(plan2)
        assert result2["success"]

        content = (tmp_path / "CMakeLists.txt").read_text()
        assert "saturate_cpp.cpp" in content
        assert "wrap_180_cpp.cpp" in content

    def test_second_function_skips_stdc(self, tmp_path):
        cmake_path = self._write_cmake(tmp_path, MULTI_SOURCE_CMAKE)

        # Apply first function
        patches1 = _generate_cmake_patches(cmake_path, "saturate_cpp.cpp", "discon")
        plan1 = IntegrationPlan("saturate", patches1, "")
        apply_integration_plan(plan1)

        # Second function should not re-add stdc++
        patches2 = _generate_cmake_patches(cmake_path, "wrap_180_cpp.cpp", "discon")
        stdc_patches = [p for p in patches2 if "stdc++" in p.description]
        assert len(stdc_patches) == 0

    def test_second_function_skips_enable_cxx(self, tmp_path):
        cmake_path = self._write_cmake(tmp_path, MULTI_SOURCE_CMAKE)

        # Apply first function
        patches1 = _generate_cmake_patches(cmake_path, "saturate_cpp.cpp", "discon")
        plan1 = IntegrationPlan("saturate", patches1, "")
        apply_integration_plan(plan1)

        # Second function should not re-add enable_language(CXX)
        patches2 = _generate_cmake_patches(cmake_path, "wrap_180_cpp.cpp", "discon")
        cxx_patches = [p for p in patches2 if "CXX" in p.description]
        assert len(cxx_patches) == 0

    def test_cpp_file_positioned_before_closing_paren(self, tmp_path):
        cmake_path = self._write_cmake(tmp_path, MULTI_SOURCE_CMAKE)
        patches = _generate_cmake_patches(cmake_path, "saturate_cpp.cpp", "discon")
        cpp_patch = [p for p in patches if "saturate_cpp.cpp" in p.description][0]

        # The original snippet should be the last source entry
        assert "ExtControl.f90" in cpp_patch.original_snippet
        # The replacement appends the new file after it
        assert cpp_patch.replacement_snippet.endswith("src/saturate_cpp.cpp")

    def test_no_patch_without_sources_block(self, tmp_path):
        """If CMakeLists.txt has no set(SOURCES ...) block, no cpp source patch is generated."""
        cmake = self._write_cmake(tmp_path,
            "project(ROSCO LANGUAGES Fortran C)\n"
            "add_library(discon SHARED ${SOURCES})\n"
        )
        patches = _generate_cmake_patches(cmake, "saturate_cpp.cpp", "discon")
        cpp_patches = [p for p in patches if "saturate_cpp.cpp" in p.description]
        assert len(cpp_patches) == 0


# === Multi-function Fortran tests ===

class TestMultiFunctionFortranPatches:

    def test_two_interface_blocks_added(self, tmp_path):
        f90 = tmp_path / "Functions.f90"
        f90.write_text(TWO_FUNCTION_MODULE)

        sig1 = parse_signature_from_source(TWO_FUNCTION_MODULE, "saturate")
        patches1 = _generate_fortran_patches(sig1, str(f90))
        plan1 = IntegrationPlan("saturate", patches1, "")
        result1 = apply_integration_plan(plan1)
        assert result1["success"]

        # Re-read updated content for second function
        sig2 = parse_signature_from_source(TWO_FUNCTION_MODULE, "wrap_180")
        patches2 = _generate_fortran_patches(sig2, str(f90))
        plan2 = IntegrationPlan("wrap_180", patches2, "")
        result2 = apply_integration_plan(plan2)
        assert result2["success"]

        content = f90.read_text()
        assert content.count("INTERFACE") >= 2

    def test_iso_c_binding_not_duplicated(self, tmp_path):
        f90 = tmp_path / "Functions.f90"
        f90.write_text(TWO_FUNCTION_MODULE)

        sig1 = parse_signature_from_source(TWO_FUNCTION_MODULE, "saturate")
        patches1 = _generate_fortran_patches(sig1, str(f90))
        plan1 = IntegrationPlan("saturate", patches1, "")
        apply_integration_plan(plan1)

        sig2 = parse_signature_from_source(TWO_FUNCTION_MODULE, "wrap_180")
        patches2 = _generate_fortran_patches(sig2, str(f90))

        # Second function should not add USE ISO_C_BINDING again
        iso_patches = [p for p in patches2 if "ISO_C_BINDING" in p.description]
        assert len(iso_patches) == 0

    def test_both_function_bodies_replaced(self, tmp_path):
        f90 = tmp_path / "Functions.f90"
        f90.write_text(TWO_FUNCTION_MODULE)

        sig1 = parse_signature_from_source(TWO_FUNCTION_MODULE, "saturate")
        patches1 = _generate_fortran_patches(sig1, str(f90))
        plan1 = IntegrationPlan("saturate", patches1, "")
        apply_integration_plan(plan1)

        sig2 = parse_signature_from_source(TWO_FUNCTION_MODULE, "wrap_180")
        patches2 = _generate_fortran_patches(sig2, str(f90))
        plan2 = IntegrationPlan("wrap_180", patches2, "")
        apply_integration_plan(plan2)

        content = f90.read_text()
        # Original function bodies should be replaced with C-calling wrappers
        assert "saturate_c" in content.lower()
        assert "wrap_180_c" in content.lower()
        # Original implementation lines should be gone
        assert "MIN(MAX(" not in content
        assert "MODULO(" not in content


class TestSharedHeader:
    """Test shared C header generation for translated function entry points."""

    def test_c_declaration_for_function(self):
        """_generate_c_declaration produces correct extern C declaration."""
        sig = parse_signature_from_source(SIMPLE_MODULE_SOURCE, "saturate")
        decl = _generate_c_declaration(sig)
        assert "double saturate_c(" in decl
        assert decl.endswith(";")
        assert "double inputValue" in decl
        assert "double minValue" in decl

    def test_plan_includes_header_patch(self, tmp_path):
        """generate_integration_plan produces an append_header patch."""
        f90 = tmp_path / "Functions.f90"
        f90.write_text(SIMPLE_MODULE_SOURCE)
        cpp = tmp_path / "saturate.cpp"
        cpp.write_text(CPP_IMPL)

        plan = generate_integration_plan(
            sig=parse_signature_from_source(SIMPLE_MODULE_SOURCE, "saturate"),
            cpp_impl_path=str(cpp),
            source_file=str(f90),
        )
        header_patches = [p for p in plan.patches if p.action == "append_header"]
        assert len(header_patches) == 1
        assert "saturate_c" in header_patches[0].content
        assert header_patches[0].filepath.endswith("vit_translated.h")

    def test_apply_creates_header_file(self, tmp_path):
        """apply_integration_plan creates vit_translated.h with declaration."""
        f90 = tmp_path / "Functions.f90"
        f90.write_text(SIMPLE_MODULE_SOURCE)
        cpp = tmp_path / "saturate.cpp"
        cpp.write_text(CPP_IMPL)

        plan = generate_integration_plan(
            sig=parse_signature_from_source(SIMPLE_MODULE_SOURCE, "saturate"),
            cpp_impl_path=str(cpp),
            source_file=str(f90),
        )
        apply_integration_plan(plan)

        header = tmp_path / "vit_translated.h"
        assert header.exists()
        content = header.read_text()
        assert "VIT_TRANSLATED_H" in content
        assert "saturate_c" in content
        assert 'extern "C"' in content

    def test_apply_appends_to_existing_header(self, tmp_path):
        """Second integration appends to existing header, doesn't overwrite."""
        f90 = tmp_path / "Functions.f90"
        f90.write_text(SIMPLE_MODULE_SOURCE.replace(
            "END MODULE Functions",
            """
    REAL(8) FUNCTION wrap_180(x)
        IMPLICIT NONE
        REAL(8), INTENT(IN) :: x
        wrap_180 = MODULO(x + 180.0, 360.0) - 180.0
    END FUNCTION wrap_180

END MODULE Functions"""
        ))
        cpp1 = tmp_path / "saturate.cpp"
        cpp1.write_text(CPP_IMPL)
        cpp2 = tmp_path / "wrap_180.cpp"
        cpp2.write_text("double wrap_180(double x) { return x; }\n")

        # First integration
        plan1 = generate_integration_plan(
            sig=parse_signature_from_source(f90.read_text(), "saturate"),
            cpp_impl_path=str(cpp1),
            source_file=str(f90),
        )
        apply_integration_plan(plan1)

        # Second integration
        plan2 = generate_integration_plan(
            sig=parse_signature_from_source(f90.read_text(), "wrap_180"),
            cpp_impl_path=str(cpp2),
            source_file=str(f90),
        )
        apply_integration_plan(plan2)

        header = tmp_path / "vit_translated.h"
        content = header.read_text()
        assert "saturate_c" in content
        assert "wrap_180_c" in content
        # Only one include guard
        assert content.count("VIT_TRANSLATED_H") == 3  # #ifndef, #define, #endif

    def test_apply_is_idempotent(self, tmp_path):
        """Applying the same integration twice doesn't duplicate declarations."""
        f90 = tmp_path / "Functions.f90"
        f90.write_text(SIMPLE_MODULE_SOURCE)
        cpp = tmp_path / "saturate.cpp"
        cpp.write_text(CPP_IMPL)

        plan = generate_integration_plan(
            sig=parse_signature_from_source(SIMPLE_MODULE_SOURCE, "saturate"),
            cpp_impl_path=str(cpp),
            source_file=str(f90),
        )
        apply_integration_plan(plan)
        apply_integration_plan(plan)

        header = tmp_path / "vit_translated.h"
        content = header.read_text()
        assert content.count("saturate_c") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
