"""Tests for vit.test_validate — test harness generation for validation functions."""

import os
import textwrap
import pytest
from pathlib import Path

from vit.test_validate import (
    generate_public_patch,
    generate_fortran_bridge,
    generate_test_makefile,
    generate_test_harness_skeleton,
    generate_test_validate_prompt,
    _parse_link_libraries,
    _get_module_use_statements,
)
from vit.fortran_parser import (
    FortranSignature, FortranArg, SubprogramKind, IntentKind,
)

# --- Shared test fixtures ---

MODULE_WITH_PRIVATE = textwrap.dedent("""\
    MODULE AirfoilInfo
       USE AirfoilInfo_Types
       USE :: NWTC_LAPACK
       IMPLICIT NONE
       PRIVATE
       PUBLIC                                       :: AFI_Init
       PUBLIC                                       :: AFI_ComputeAirfoilCoefs
    CONTAINS
       SUBROUTINE AFI_ValidateInitInput(InitInput, ErrStat, ErrMsg)
          INTEGER, INTENT(OUT) :: ErrStat
       END SUBROUTINE
    END MODULE
""")

MODULE_WITHOUT_PRIVATE = textwrap.dedent("""\
    MODULE SimpleModule
       IMPLICIT NONE
    CONTAINS
       SUBROUTINE MySub(x)
          INTEGER, INTENT(IN) :: x
       END SUBROUTINE
    END MODULE
""")

MODULE_ALREADY_PUBLIC = textwrap.dedent("""\
    MODULE AirfoilInfo
       IMPLICIT NONE
       PRIVATE
       PUBLIC :: AFI_ValidateInitInput
    CONTAINS
       SUBROUTINE AFI_ValidateInitInput(x)
          INTEGER, INTENT(IN) :: x
       END SUBROUTINE
    END MODULE
""")


def _make_sig(name="TestFunc", module_name="TestModule", args=None):
    """Build a FortranSignature for testing."""
    if args is None:
        args = [
            FortranArg(name="ErrStat", fortran_type="INTEGER(IntKi)",
                       intent=IntentKind.OUT),
            FortranArg(name="ErrMsg", fortran_type="CHARACTER(*)",
                       intent=IntentKind.OUT),
        ]
    return FortranSignature(
        name=name,
        kind=SubprogramKind.SUBROUTINE,
        args=args,
        module_name=module_name,
    )


def _make_sig_with_derived_type():
    """Build a FortranSignature with a derived type argument."""
    return FortranSignature(
        name="AFI_ValidateInitInput",
        kind=SubprogramKind.SUBROUTINE,
        args=[
            FortranArg(name="InitInput", fortran_type="TYPE(AFI_InitInputType)",
                       intent=IntentKind.IN),
            FortranArg(name="ErrStat", fortran_type="INTEGER(IntKi)",
                       intent=IntentKind.OUT),
            FortranArg(name="ErrMsg", fortran_type="CHARACTER(*)",
                       intent=IntentKind.OUT),
        ],
        module_name="AirfoilInfo",
    )


# === generate_public_patch tests ===

class TestGeneratePublicPatch:

    def test_private_module_inserts_public(self):
        """PRIVATE module gets PUBLIC declaration for the target function."""
        result = generate_public_patch(MODULE_WITH_PRIVATE, "AFI_ValidateInitInput")
        assert result is not None
        assert "PUBLIC" in result
        assert "AFI_ValidateInitInput" in result
        # Should be inserted after the last existing PUBLIC line
        lines = result.split("\n")
        public_lines = [i for i, l in enumerate(lines)
                        if "PUBLIC" in l and "AFI_ValidateInitInput" in l]
        assert len(public_lines) == 1

    def test_private_module_alignment(self):
        """Inserted PUBLIC declaration matches alignment of existing ones."""
        result = generate_public_patch(MODULE_WITH_PRIVATE, "AFI_ValidateInitInput")
        assert result is not None
        # Existing PUBLIC lines use 3-space indent and :: at column 47
        for line in result.split("\n"):
            if "AFI_ValidateInitInput" in line and "PUBLIC" in line:
                assert "::" in line
                # Should have same indent as existing PUBLIC lines
                assert line.startswith("   ")

    def test_no_private_returns_none(self):
        """Module without PRIVATE default returns None (no patch needed)."""
        result = generate_public_patch(MODULE_WITHOUT_PRIVATE, "MySub")
        assert result is None

    def test_already_public_returns_none(self):
        """Function already PUBLIC returns None."""
        result = generate_public_patch(MODULE_ALREADY_PUBLIC, "AFI_ValidateInitInput")
        assert result is None


# === generate_fortran_bridge tests ===

class TestGenerateFortranBridge:

    def test_derived_type_uses_c_ptr(self):
        """Derived type args become TYPE(C_PTR), VALUE with C_F_POINTER."""
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)
        assert "TYPE(C_PTR), VALUE :: InitInput_ptr" in bridge
        assert "C_F_POINTER(InitInput_ptr, InitInput)" in bridge

    def test_character_handled_locally(self):
        """CHARACTER args become local buffers, no C-side parameter."""
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)
        assert "CHARACTER(8192) :: ErrMsg" in bridge
        # No ErrMsg in the SUBROUTINE parameter list
        assert "ErrMsg_stat" not in bridge

    def test_integer_direct_mapping(self):
        """INTEGER args map directly to INTEGER(C_INT)."""
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)
        assert "INTEGER(C_INT), INTENT(OUT) :: ErrStat" in bridge

    def test_module_use_statements_from_source(self, tmp_path):
        """USE statements extracted from source file, not _Types convention."""
        source = tmp_path / "TestModule.f90"
        source.write_text(MODULE_WITH_PRIVATE)
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig, source_file=str(source))
        # Should have USE from the module header
        assert "USE AirfoilInfo_Types" in bridge or "AirfoilInfo_Types" in bridge
        assert "USE" in bridge and "NWTC_LAPACK" in bridge

    def test_bind_c_name(self):
        """Bridge has BIND(C) with lowercase name."""
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)
        assert "BIND(C, NAME='afi_validateinitinput_f90')" in bridge


# === _get_module_use_statements tests ===

class TestGetModuleUseStatements:

    def test_extracts_module_level_use(self):
        """Extracts USE statements from module scope."""
        uses = _get_module_use_statements(MODULE_WITH_PRIVATE)
        use_upper = [u.upper() for u in uses]
        assert any("AIRFOILINFO_TYPES" in u for u in use_upper)
        assert any("NWTC_LAPACK" in u for u in use_upper)

    def test_stops_at_contains(self):
        """Doesn't include USE statements from inside functions."""
        source = textwrap.dedent("""\
            MODULE TestMod
               USE ModuleLevel
            CONTAINS
               SUBROUTINE MySub()
                  USE FunctionLevel
               END SUBROUTINE
            END MODULE
        """)
        uses = _get_module_use_statements(source)
        use_upper = [u.upper() for u in uses]
        assert any("MODULELEVEL" in u for u in use_upper)
        assert not any("FUNCTIONLEVEL" in u for u in use_upper)

    def test_no_module_returns_empty(self):
        """Source without MODULE returns empty list."""
        uses = _get_module_use_statements("SUBROUTINE foo()\nEND SUBROUTINE")
        assert uses == []


# === _parse_link_libraries tests ===

class TestParseLinkLibraries:

    def test_parses_static_libs_and_flags(self, tmp_path):
        """Extracts .a paths and -l flags from link.txt."""
        # Create mock CMake directory structure
        cmake_dir = tmp_path / "modules" / "aerodyn" / "CMakeFiles" / "mydriver.dir"
        cmake_dir.mkdir(parents=True)
        link_txt = cmake_dir / "link.txt"
        link_txt.write_text(
            "/usr/bin/c++ -fPIC obj1.o obj2.o -o mydriver "
            "libfoo.a ../nwtc/libnwtc.a -lstdc++ -lm -lgfortran"
        )
        libs = _parse_link_libraries(str(tmp_path), "mydriver")
        assert libs is not None
        assert any("libfoo.a" in l for l in libs)
        assert any("libnwtc.a" in l for l in libs)
        assert "-lstdc++" in libs
        assert "-lm" in libs
        assert "-lgfortran" in libs

    def test_no_link_txt_returns_none(self, tmp_path):
        """Missing link.txt returns None."""
        result = _parse_link_libraries(str(tmp_path), "nonexistent")
        assert result is None

    def test_skips_object_files_and_flags(self, tmp_path):
        """Object files, compiler flags, and -Wl options are excluded."""
        cmake_dir = tmp_path / "CMakeFiles" / "target.dir"
        cmake_dir.mkdir(parents=True)
        (cmake_dir / "link.txt").write_text(
            "/usr/bin/c++ -fPIC -O3 -DNDEBUG main.o helper.o "
            "-o target -Wl,-rpath,/lib libcore.a -lm"
        )
        libs = _parse_link_libraries(str(tmp_path), "target")
        assert libs is not None
        assert not any(l.endswith(".o") for l in libs)
        assert not any(l.startswith("-f") for l in libs)
        assert not any(l.startswith("-Wl") for l in libs)
        assert any("libcore.a" in l for l in libs)


# === generate_test_makefile tests ===

class TestGenerateTestMakefile:

    def test_shared_files_generate_include_paths(self):
        """shared_files paths are resolved to -I include directories."""
        sig = _make_sig()
        makefile = generate_test_makefile(
            sig, build_dir="/build", source_dir="/proj/src",
            config_dir="/proj",
            shared_files=["src/vit_nwtc.h", "src/vit_nwtc.cpp"],
        )
        assert "-I" in makefile
        assert "CXX_FLAGS" in makefile

    def test_no_target_name_shows_warning(self):
        """Without target_name, Makefile has WARNING comment."""
        sig = _make_sig()
        makefile = generate_test_makefile(
            sig, build_dir="/build", source_dir="/proj/src",
            config_dir="/proj",
        )
        assert "WARNING" in makefile
        assert "LIBS =" in makefile

    def test_with_patched_source(self):
        """Patched source name generates compilation rule."""
        sig = _make_sig()
        makefile = generate_test_makefile(
            sig, build_dir="/build", source_dir="/proj/src",
            config_dir="/proj",
            patched_source="TestModule.f90",
        )
        assert "TestModule.o" in makefile
        assert "patched" in makefile.lower()


# === generate_test_harness_skeleton tests ===

class TestGenerateTestHarnessSkeleton:

    def test_derived_type_extern_uses_void_ptr(self):
        """extern "C" bridge declaration uses void* for derived types."""
        sig = _make_sig_with_derived_type()
        skeleton = generate_test_harness_skeleton(sig)
        assert "void*" in skeleton
        assert "InitInput_ptr" in skeleton

    def test_character_skipped_in_bridge_params(self):
        """CHARACTER args are not in the extern bridge declaration."""
        sig = _make_sig_with_derived_type()
        skeleton = generate_test_harness_skeleton(sig)
        # The extern "C" declaration should NOT have ErrMsg_stat
        assert "ErrMsg_stat" not in skeleton

    def test_has_test_infrastructure(self):
        """Skeleton includes TestResult struct and main runner."""
        sig = _make_sig()
        skeleton = generate_test_harness_skeleton(sig)
        assert "TestResult" in skeleton
        assert "int main()" in skeleton
        assert "PASS" in skeleton
        assert "FAIL" in skeleton


# === generate_test_validate_prompt tests ===

class TestGenerateTestValidatePrompt:

    def test_prompt_contains_sections(self):
        """Prompt has Fortran source, C++ translation, and instructions."""
        sig = _make_sig_with_derived_type()
        prompt = generate_test_validate_prompt(
            sig,
            fortran_source_text="SUBROUTINE Test()\nEND SUBROUTINE",
            cpp_translation_text="void Test() {}",
            skeleton_text="// skeleton",
        )
        assert "Fortran Source" in prompt
        assert "C++ Translation" in prompt
        assert "Instructions" in prompt
        assert "SUBROUTINE Test" in prompt
        assert "void Test" in prompt


# === FUNCTION return type and assumed-shape array tests ===

def _make_function_sig():
    """Create a FUNCTION signature returning LOGICAL with assumed-shape array arg."""
    return FortranSignature(
        name="CheckValuesAreUniqueMonotonicIncreasing",
        kind=SubprogramKind.FUNCTION,
        result_type="LOGICAL",
        args=[
            FortranArg(name="secondVals", fortran_type="REAL(ReKi)",
                       intent=IntentKind.IN,
                       is_array=True, array_dims="(:)"),
        ],
        module_name="AirfoilInfo",
    )


class TestFunctionBridge:

    def test_function_gets_result_parameter(self):
        """FUNCTION bridge adds vit_result output parameter."""
        sig = _make_function_sig()
        bridge = generate_fortran_bridge(sig)
        assert "vit_result" in bridge
        assert "LOGICAL(C_BOOL), INTENT(OUT) :: vit_result" in bridge

    def test_function_call_captures_return(self):
        """FUNCTION bridge uses assignment, not CALL."""
        sig = _make_function_sig()
        bridge = generate_fortran_bridge(sig)
        assert "CALL CheckValuesAreUniqueMonotonicIncreasing" not in bridge
        assert "vit_result = LOGICAL(CheckValuesAreUniqueMonotonicIncreasing(" in bridge

    def test_logical_return_uses_c_bool_conversion(self):
        """LOGICAL return is converted via LOGICAL(..., C_BOOL)."""
        sig = _make_function_sig()
        bridge = generate_fortran_bridge(sig)
        assert "C_BOOL)" in bridge

    def test_subroutine_still_uses_call(self):
        """SUBROUTINE bridge still uses CALL (not affected by FUNCTION changes)."""
        sig = _make_sig()
        bridge = generate_fortran_bridge(sig)
        assert "CALL TestFunc" in bridge
        assert "vit_result" not in bridge


class TestAssumedShapeArrayBridge:

    def test_assumed_shape_gets_size_parameter(self):
        """Assumed-shape array arg adds n_<name> size parameter."""
        sig = _make_function_sig()
        bridge = generate_fortran_bridge(sig)
        assert "n_secondVals" in bridge
        assert "INTEGER(C_INT), VALUE" in bridge

    def test_assumed_shape_declared_assumed_size(self):
        """Assumed-shape array is declared as assumed-size (*) in BIND(C)."""
        sig = _make_function_sig()
        bridge = generate_fortran_bridge(sig)
        assert "secondVals(*)" in bridge

    def test_assumed_shape_explicit_section_in_call(self):
        """Call passes explicit section secondVals(1:n_secondVals)."""
        sig = _make_function_sig()
        bridge = generate_fortran_bridge(sig)
        assert "secondVals(1:n_secondVals)" in bridge

    def test_integer_array_uses_c_int(self):
        """INTEGER assumed-shape array uses INTEGER(C_INT)."""
        sig = FortranSignature(
            name="TestFunc",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="indices", fortran_type="INTEGER(IntKi)",
                           intent=IntentKind.IN,
                           is_array=True, array_dims="(:)"),
            ],
            module_name="TestMod",
        )
        bridge = generate_fortran_bridge(sig)
        assert "INTEGER(C_INT), INTENT(IN) :: indices(*)" in bridge
        assert "n_indices" in bridge
