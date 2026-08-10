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

    def test_character_crosses_as_buffer_plus_length(self):
        """CHARACTER args cross as a C_CHAR buffer + length, staged locally.

        Previously they became an uninitialized CHARACTER(8192) local with no
        C-side parameter at all, so the bridge compiled, ran, and tested
        nothing. Any function whose input is a CHARACTER argument was silently
        unverifiable. See DECISIONS.md, PathIsRelative (#23).
        """
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)

        # Exposed to C++ as buffer + length...
        assert "CHARACTER(KIND=C_CHAR)" in bridge
        assert "ErrMsg_c" in bridge
        assert "INTEGER(C_INT), VALUE, INTENT(IN) :: len_ErrMsg" in bridge
        # ...and staged through a local of the caller's length.
        assert "CHARACTER(len_ErrMsg) :: ErrMsg" in bridge
        # The old uninitialized fixed buffer is gone.
        assert "CHARACTER(8192) :: ErrMsg" not in bridge

    def test_character_inout_copies_back(self):
        """An INOUT/OUT CHARACTER must be copied back into the C buffer."""
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)
        # ErrMsg is INTENT(OUT) in the fixture, so the post-call copy is required.
        assert "ErrMsg_c(vit_i_ErrMsg) = ErrMsg(vit_i_ErrMsg:vit_i_ErrMsg)" in bridge

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


# === ALLOCATABLE field decomposition tests ===

def _make_allocatable_registry():
    """Registry with a type that has ALLOCATABLE fields."""
    from vit.derived_type_parser import DerivedTypeInfo, DerivedTypeField
    return {
        "DBEMT_InitInputType": DerivedTypeInfo(
            name="DBEMT_InitInputType",
            fields=[
                DerivedTypeField(name="NumBlades", fortran_type="INTEGER(IntKi)"),
                DerivedTypeField(name="NumNodes", fortran_type="INTEGER(IntKi)"),
                DerivedTypeField(name="tau1_const", fortran_type="REAL(ReKi)"),
                DerivedTypeField(name="DBEMT_Mod", fortran_type="INTEGER(IntKi)"),
                DerivedTypeField(name="rLocal", fortran_type="REAL(ReKi)",
                                 is_allocatable=True, is_array=True,
                                 array_dims="(:,:)"),
            ],
            # The bridge decomposes this type (it has an ALLOCATABLE field), so
            # the C++ call has to receive it too or the two disagree about the
            # function's arity. `strategy: view` is what makes `build_c_params`
            # emit `dbemt_initinputtype_view_t*` for it; without it the argument
            # is dropped and `generate_test_harness_skeleton` now refuses. The
            # decomposition assertions below are unaffected -- `is_c_compatible`
            # is still False, so the bridge still decomposes.
            view_strategy="view",
        ),
    }


def _make_allocatable_sig():
    """Signature with an ALLOCATABLE-containing derived type argument."""
    return FortranSignature(
        name="DBEMT_ValidateInitInp",
        kind=SubprogramKind.SUBROUTINE,
        args=[
            FortranArg(name="interval", fortran_type="REAL(DbKi)",
                       intent=IntentKind.IN),
            FortranArg(name="InitInp", fortran_type="TYPE(DBEMT_InitInputType)",
                       intent=IntentKind.IN),
            FortranArg(name="errStat", fortran_type="INTEGER(IntKi)",
                       intent=IntentKind.OUT),
            FortranArg(name="errMsg", fortran_type="CHARACTER(*)",
                       intent=IntentKind.OUT),
        ],
        module_name="DBEMT",
    )


def _make_1d_allocatable_registry():
    """Registry with a type that has a 1D ALLOCATABLE field."""
    from vit.derived_type_parser import DerivedTypeInfo, DerivedTypeField
    return {
        "SimpleType": DerivedTypeInfo(
            name="SimpleType",
            fields=[
                DerivedTypeField(name="n", fortran_type="INTEGER(IntKi)"),
                DerivedTypeField(name="values", fortran_type="REAL(ReKi)",
                                 is_allocatable=True, is_array=True,
                                 array_dims="(:)"),
            ],
        ),
    }


class TestAllocatableBridge:

    def test_decomposed_scalar_fields(self):
        """ALLOCATABLE type decomposes scalar fields into VALUE parameters."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "vit_InitInp_NumBlades" in bridge
        assert "vit_InitInp_NumNodes" in bridge
        assert "vit_InitInp_tau1_const" in bridge
        assert "vit_InitInp_DBEMT_Mod" in bridge
        assert "VALUE :: vit_InitInp_NumBlades" in bridge

    def test_decomposed_allocatable_ptr_and_dims(self):
        """ALLOCATABLE field gets pointer + dimension parameters."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "TYPE(C_PTR), VALUE :: vit_InitInp_rLocal_ptr" in bridge
        assert "vit_InitInp_rLocal_rows" in bridge
        assert "vit_InitInp_rLocal_cols" in bridge

    def test_no_c_f_pointer_on_struct(self):
        """Decomposed bridge does NOT use C_F_POINTER on the whole struct."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "C_F_POINTER(InitInp_ptr" not in bridge

    def test_allocate_and_deallocate(self):
        """Bridge ALLOCATEs and DEALLOCATEs ALLOCATABLE fields."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "ALLOCATE(vit_local_InitInp%rLocal" in bridge
        assert "DEALLOCATE(vit_local_InitInp%rLocal)" in bridge

    def test_c_associated_null_check(self):
        """Bridge checks C_ASSOCIATED before ALLOCATE."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "C_ASSOCIATED(vit_InitInp_rLocal_ptr)" in bridge

    def test_scalar_assignment(self):
        """Bridge assigns scalar fields to the local type."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "vit_local_InitInp%NumBlades = vit_InitInp_NumBlades" in bridge
        assert "vit_local_InitInp%tau1_const = vit_InitInp_tau1_const" in bridge

    def test_call_uses_local_var(self):
        """Bridge calls the original function with the local Fortran type."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "CALL DBEMT_ValidateInitInp(" in bridge
        assert "vit_local_InitInp" in bridge

    def test_1d_allocatable(self):
        """1D ALLOCATABLE gets _n size parameter (not _rows/_cols)."""
        sig = FortranSignature(
            name="TestFunc",
            kind=SubprogramKind.SUBROUTINE,
            args=[
                FortranArg(name="data", fortran_type="TYPE(SimpleType)",
                           intent=IntentKind.IN),
            ],
            module_name="TestMod",
        )
        registry = _make_1d_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert "vit_data_values_n" in bridge
        assert "vit_data_values_rows" not in bridge

    def test_non_derived_args_unchanged(self):
        """Non-derived-type args keep their original names (no vit_ prefix)."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        bridge = generate_fortran_bridge(sig, derived_types=registry)
        assert ":: interval" in bridge or ":: vit_interval" not in bridge
        assert ":: errStat" in bridge

    def test_c_compatible_type_still_uses_c_f_pointer(self):
        """C-compatible types (no ALLOCATABLE) still use C_F_POINTER."""
        sig = _make_sig_with_derived_type()
        bridge = generate_fortran_bridge(sig)
        assert "C_F_POINTER" in bridge
        assert "InitInput_ptr" in bridge


class TestAllocatableSkeleton:

    def test_extern_uses_decomposed_params(self):
        """C++ extern uses individual params for ALLOCATABLE types."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        skeleton = generate_test_harness_skeleton(sig, derived_types=registry)
        assert "vit_InitInp_NumBlades" in skeleton
        assert "vit_InitInp_rLocal_ptr" in skeleton

    def test_extern_no_void_ptr_for_allocatable(self):
        """extern declaration does NOT use void* for ALLOCATABLE types."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        skeleton = generate_test_harness_skeleton(sig, derived_types=registry)
        assert "void* InitInp_ptr" not in skeleton

    def test_extern_allocatable_dims_match_bridge(self):
        """2D ALLOCATABLE gets _rows/_cols in extern matching the bridge."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        skeleton = generate_test_harness_skeleton(sig, derived_types=registry)
        assert "int vit_InitInp_rLocal_rows" in skeleton
        assert "int vit_InitInp_rLocal_cols" in skeleton

    def test_c_compatible_still_void_ptr(self):
        """C-compatible types still use void* in extern."""
        sig = _make_sig_with_derived_type()
        skeleton = generate_test_harness_skeleton(sig)
        assert "void*" in skeleton


def _sig(src: str, name: str):
    from vit.fortran_parser import parse_signature_from_source
    return parse_signature_from_source(src, name)


# --------------------------------------------------------------------------
# The toolchain is configuration, not a literal.

def test_the_makefile_honours_the_configured_compilers():
    """`FC = gfortran` and `CXX = g++` were literals in a generator whose
    Fortran compiler is a vit.yaml field. A campaign on ifx or flang got a
    Makefile naming a compiler it does not have."""
    from vit.test_validate import generate_test_makefile
    sig = _sig("SUBROUTINE Foo(x)\n  REAL(8), INTENT(IN) :: x\nEND SUBROUTINE Foo", "Foo")
    mk = generate_test_makefile(sig=sig, build_dir="/b", source_dir="/s",
                                fortran_compiler="ifx", cxx_compiler="icpx")
    assert "FC = ifx" in mk and "CXX = icpx" in mk
    assert "FC = gfortran" not in mk


def test_the_fortran_runtime_follows_the_compiler():
    """The harness links with $(CXX), so nothing pulls in the Fortran runtime.
    `-lgfortran -lm` was hardcoded; on another toolchain that is an undefined
    symbol naming `_gfortran_*`, which reads as a source problem rather than a
    configuration one."""
    from vit.test_validate import fortran_runtime_for, generate_test_makefile
    assert fortran_runtime_for("gfortran") == "-lgfortran -lm"
    assert "-lifcore" in fortran_runtime_for("ifx")
    assert "-lifcore" in fortran_runtime_for("/opt/intel/bin/ifort"), "a path must resolve"
    # An unknown compiler must not pretend to know.
    assert "set fortran.runtime_libs" in fortran_runtime_for("some-vendor-fc")

    sig = _sig("SUBROUTINE Foo(x)\n  REAL(8), INTENT(IN) :: x\nEND SUBROUTINE Foo", "Foo")
    mk = generate_test_makefile(sig=sig, build_dir="/b", source_dir="/s",
                                fortran_compiler="ifx")
    assert "-lifcore" in mk and "-lgfortran" not in mk
    # And an explicit setting wins over the table.
    mk = generate_test_makefile(sig=sig, build_dir="/b", source_dir="/s",
                                fortran_compiler="ifx", fortran_runtime_libs="-lmyrt")
    assert "LIBS += -lmyrt" in mk


# --------------------------------------------------------------------------
# Three generators over one type model, and where they disagreed.
# Dev note 202608092223.

class TestGeneratorsAgree:
    """VIT has several independent generators over one type model. Each of
    these was a place two of them contradicted each other, and every one
    surfaced as a symptom naming neither cause -- an arity mismatch, a missing
    struct, an undeclared identifier."""

    def test_a_bridge_argument_the_cpp_call_lacks_is_refused(self):
        """`build_c_params` drops a non-C-compatible derived type; the bridge
        decomposes the same argument. The skeleton then declared a C++ call of
        N arguments directly above an `#include` of a translation taking N+1,
        and the disagreement surfaced as a link error naming neither."""
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        registry["DBEMT_InitInputType"].view_strategy = None   # unconfigured
        with pytest.raises(ValueError, match="disagree about this function's arity"):
            generate_test_harness_skeleton(sig, derived_types=registry)

    def test_the_refusal_names_the_argument_the_type_and_the_remedy(self):
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        registry["DBEMT_InitInputType"].view_strategy = None
        with pytest.raises(ValueError) as e:
            generate_test_harness_skeleton(sig, derived_types=registry)
        msg = str(e.value)
        assert "InitInp" in msg                       # which argument
        assert "DBEMT_InitInputType" in msg           # of what type
        assert "ALLOCATABLE field(s)" in msg          # why it was dropped
        assert "strategy: view" in msg                # what to do about it

    def test_no_registry_is_not_a_disagreement(self):
        """Without a registry `build_c_params` omits EVERY derived type and the
        bridge decomposes none, so both sides are consistently degraded. That
        is a fact about the call, not about the function, and reporting it as a
        contradiction is absence rendered as a finding."""
        sig = _make_sig_with_derived_type()
        skeleton = generate_test_harness_skeleton(sig)     # no registry
        assert "void*" in skeleton

    def test_dropped_derived_args_reports_what_build_c_params_omits(self):
        from vit.interface_gen import build_c_params, dropped_derived_args
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()
        registry["DBEMT_InitInputType"].view_strategy = None
        emitted = {n for _, n in build_c_params(sig, None, registry)}
        dropped = dropped_derived_args(sig, None, registry)
        assert "InitInp" not in emitted, "precondition: the argument is dropped"
        assert [a for a, _, _ in dropped] == ["InitInp"]

    def test_a_configured_view_type_is_not_reported_as_dropped(self):
        from vit.interface_gen import dropped_derived_args
        sig = _make_allocatable_sig()
        registry = _make_allocatable_registry()          # view_strategy set
        assert dropped_derived_args(sig, None, registry) == []


class TestPerUnitArtifacts:

    def test_the_output_directory_is_per_unit_not_per_module(self):
        """`vit_types.h`, the Makefile, the `test` binary and `clean`'s
        `rm -f *.o test` are all unit-specific. Sharing a module directory made
        two units overwrite each other: running for one left a `vit_types.h`
        without the other's types, and `make` built whichever Makefile landed
        last -- so one unit's case file could be fed to another's binary."""
        import inspect
        from vit import cli
        src = inspect.getsource(cli.cmd_test_validate)
        assert 'Path("translations") / module_name / f"{sig.name.lower()}_test"' in src


class TestComplexTypedef:

    def test_the_complex_typedefs_are_defined_whenever_a_header_is_written(self):
        """`type_map` hands `vit_complex_double` to every caller that asks what
        COMPLEX(8) maps to -- including the extern "C" builder, which never
        consults a struct generator. Defining it only when some type in the
        header happened to have a complex field left that name undeclared."""
        from vit.derived_type_parser import (DerivedTypeField, DerivedTypeInfo,
                                             generate_consolidated_types_header)
        plain = {"Plain": DerivedTypeInfo(
            name="Plain",
            fields=[DerivedTypeField(name="x", fortran_type="REAL(8)")])}
        header = generate_consolidated_types_header(plain)
        assert "vit_complex_double" in header, \
            "no complex field here, and the name must still be defined"
        assert "VIT_COMPLEX_T_DEFINED" in header, "guarded against repeats"

    def test_the_mapper_and_the_header_use_the_same_name(self):
        from vit.type_map import get_type_mapping
        from vit.derived_type_parser import (DerivedTypeField, DerivedTypeInfo,
                                             generate_consolidated_types_header)
        name = get_type_mapping("COMPLEX(8)").c_type
        header = generate_consolidated_types_header(
            {"P": DerivedTypeInfo(name="P",
                                  fields=[DerivedTypeField(name="x",
                                                           fortran_type="REAL(8)")])})
        assert f"}} {name};" in header
