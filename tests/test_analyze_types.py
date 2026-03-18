"""
Tests for Phase 4A: analyze-types command, CHARACTER(N) type mapping,
scan_all_derived_types, classify_field_blockers, and fix_character_allocatable.
"""

import textwrap
from pathlib import Path

from vit.type_map import TypeMapping, get_type_mapping, normalize_fortran_type, DEFAULT_KIND_ALIASES
from vit.derived_type_parser import (
    DerivedTypeInfo,
    DerivedTypeField,
    FieldBlocker,
    classify_field_blockers,
    generate_c_struct_header,
    parse_derived_type,
    scan_all_derived_types,
)
from vit.analyze_types import (
    TypeAnalysis,
    analyze_all_types,
    format_analysis_report,
    fix_character_allocatable,
)


# --- Synthetic Fortran sources for testing ---

ERRORVARIABLES_SOURCE = textwrap.dedent("""\
    MODULE ROSCO_Types
    IMPLICIT NONE

    TYPE, PUBLIC :: ErrorVariables
        INTEGER(IntKi)                :: size_avcMSG
        INTEGER(C_INT)                :: aviFAIL
        INTEGER(C_INT)                :: ErrStat
        CHARACTER(:), ALLOCATABLE     :: ErrMsg  ! error message
    END TYPE ErrorVariables

    END MODULE ROSCO_Types
""")

SIMPLE_TYPE_SOURCE = textwrap.dedent("""\
    MODULE SimpleModule
    IMPLICIT NONE

    TYPE, PUBLIC :: SimpleParams
        REAL(ReKi)    :: Gain
        INTEGER(IntKi) :: Mode
    END TYPE SimpleParams

    END MODULE SimpleModule
""")

MULTI_TYPE_SOURCE = textwrap.dedent("""\
    MODULE MultiModule
    IMPLICIT NONE

    TYPE, PUBLIC :: TypeA
        REAL(8)    :: x
        REAL(8)    :: y
    END TYPE TypeA

    TYPE, PUBLIC :: TypeB
        INTEGER(4) :: count
        REAL(8), ALLOCATABLE :: data(:)
    END TYPE TypeB

    TYPE, PUBLIC :: TypeC
        REAL(8)    :: value
        TYPE(TypeA) :: nested
    END TYPE TypeC

    TYPE, PUBLIC :: TypeD
        REAL(8), POINTER :: ptr
    END TYPE TypeD

    END MODULE MultiModule
""")

ALLOCATE_SOURCE = textwrap.dedent("""\
    SUBROUTINE InitErrorVars(ErrVar, size_avcMSG)
    USE ROSCO_Types
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
    INTEGER, INTENT(IN) :: size_avcMSG

    ErrVar%size_avcMSG = size_avcMSG
    ALLOCATE(ErrVar%ErrMsg(size_avcMSG-1))
    ErrVar%ErrMsg = ''

    END SUBROUTINE InitErrorVars
""")

ALLOCATE_ALREADY_COMMENTED = textwrap.dedent("""\
    SUBROUTINE InitErrorVars(ErrVar, size_avcMSG)
    USE ROSCO_Types
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
    INTEGER, INTENT(IN) :: size_avcMSG

    ErrVar%size_avcMSG = size_avcMSG
    ! ALLOCATE(ErrVar%ErrMsg(size_avcMSG-1))
    ErrVar%ErrMsg = ''

    END SUBROUTINE InitErrorVars
""")

FIXED_CHAR_TYPE_SOURCE = textwrap.dedent("""\
    MODULE FixedModule
    IMPLICIT NONE

    TYPE, PUBLIC :: FixedType
        INTEGER(4)       :: code
        CHARACTER(256)   :: name
        CHARACTER(1024)  :: description
    END TYPE FixedType

    END MODULE FixedModule
""")


class TestCharacterNTypeMapping:
    """Test CHARACTER(N) type mapping and kind alias merging."""

    def test_character_1024_mapping(self):
        mapping = get_type_mapping("CHARACTER(1024)")
        assert mapping.c_type == "char"
        assert mapping.character_length == 1024
        assert mapping.byte_size == 1024
        assert mapping.c_kind == "C_CHAR"

    def test_character_256_mapping(self):
        mapping = get_type_mapping("CHARACTER(256)")
        assert mapping.c_type == "char"
        assert mapping.character_length == 256
        assert mapping.byte_size == 256

    def test_character_1_mapping(self):
        """Single CHARACTER still works via dict lookup."""
        mapping = get_type_mapping("CHARACTER")
        assert mapping.c_type == "char"
        assert mapping.character_length is None
        assert mapping.byte_size == 1

    def test_character_deferred_raises(self):
        """CHARACTER(:) cannot be mapped — must raise with helpful message."""
        try:
            get_type_mapping("CHARACTER(:)")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "CHARACTER(:)" in str(e)
            assert "analyze-types" in str(e)

    def test_c_int_alias(self):
        """C_INT should resolve to INTEGER(4) via DEFAULT_KIND_ALIASES."""
        assert "C_INT" in DEFAULT_KIND_ALIASES
        normalized = normalize_fortran_type("INTEGER(C_INT)")
        assert normalized == "INTEGER(4)"
        mapping = get_type_mapping("INTEGER(C_INT)")
        assert mapping.c_type == "int"

    def test_c_double_alias(self):
        normalized = normalize_fortran_type("REAL(C_DOUBLE)")
        assert normalized == "REAL(8)"

    def test_kind_alias_merging_defaults_remain(self):
        """Project aliases should not wipe out defaults."""
        project_aliases = {"MyKind": "REAL(8)"}
        # C_INT from defaults should still work
        normalized = normalize_fortran_type("INTEGER(C_INT)", project_aliases)
        assert normalized == "INTEGER(4)"
        # MyKind from project should work
        normalized2 = normalize_fortran_type("REAL(MyKind)", project_aliases)
        assert normalized2 == "REAL(8)"

    def test_kind_alias_project_overrides_default(self):
        """Project alias should override a default if same key."""
        project_aliases = {"ReKi": "REAL(4)"}  # Override default ReKi=REAL(8)
        normalized = normalize_fortran_type("REAL(ReKi)", project_aliases)
        assert normalized == "REAL(4)"


class TestScanAllDerivedTypes:
    """Test scan_all_derived_types() discovery."""

    def test_discovers_single_type(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        types = scan_all_derived_types(str(tmp_path))
        assert len(types) == 1
        assert types[0].name == "ErrorVariables"

    def test_discovers_multiple_types(self, tmp_path):
        (tmp_path / "multi.f90").write_text(MULTI_TYPE_SOURCE)
        types = scan_all_derived_types(str(tmp_path))
        names = {t.name for t in types}
        assert names == {"TypeA", "TypeB", "TypeC", "TypeD"}

    def test_sets_source_file(self, tmp_path):
        f = tmp_path / "types.f90"
        f.write_text(ERRORVARIABLES_SOURCE)
        types = scan_all_derived_types(str(tmp_path))
        assert types[0].source_file == str(f.resolve())

    def test_across_multiple_files(self, tmp_path):
        (tmp_path / "err.f90").write_text(ERRORVARIABLES_SOURCE)
        (tmp_path / "simple.f90").write_text(SIMPLE_TYPE_SOURCE)
        types = scan_all_derived_types(str(tmp_path))
        names = {t.name for t in types}
        assert "ErrorVariables" in names
        assert "SimpleParams" in names

    def test_empty_directory(self, tmp_path):
        types = scan_all_derived_types(str(tmp_path))
        assert types == []


class TestClassifyFieldBlockers:
    """Test classify_field_blockers() detection."""

    def test_errorvariables_has_one_blocker(self):
        info = parse_derived_type(ERRORVARIABLES_SOURCE, "ErrorVariables")
        assert info is not None
        rosco_aliases = {"IntKi": "INTEGER(4)", "C_INT": "INTEGER(4)"}
        blockers = classify_field_blockers(info, rosco_aliases)
        assert len(blockers) == 1
        assert blockers[0].field_name == "ErrMsg"
        assert blockers[0].reason == "character_allocatable"

    def test_simple_type_no_blockers(self):
        info = parse_derived_type(SIMPLE_TYPE_SOURCE, "SimpleParams")
        assert info is not None
        blockers = classify_field_blockers(info, {"ReKi": "REAL(8)", "IntKi": "INTEGER(4)"})
        assert blockers == []

    def test_allocatable_array_blocker(self):
        info = parse_derived_type(MULTI_TYPE_SOURCE, "TypeB")
        assert info is not None
        blockers = classify_field_blockers(info)
        reasons = [b.reason for b in blockers]
        assert "allocatable" in reasons

    def test_nested_type_blocker(self):
        info = parse_derived_type(MULTI_TYPE_SOURCE, "TypeC")
        assert info is not None
        blockers = classify_field_blockers(info)
        reasons = [b.reason for b in blockers]
        assert "nested_type" in reasons

    def test_pointer_blocker(self):
        info = parse_derived_type(MULTI_TYPE_SOURCE, "TypeD")
        assert info is not None
        blockers = classify_field_blockers(info)
        reasons = [b.reason for b in blockers]
        assert "pointer" in reasons


class TestGenerateCStructWithCharacter:
    """Test C struct generation with CHARACTER(N) fields."""

    def test_fixed_char_emits_array(self):
        info = parse_derived_type(FIXED_CHAR_TYPE_SOURCE, "FixedType")
        assert info is not None
        header = generate_c_struct_header(info)
        assert "char name[256];" in header
        assert "char description[1024];" in header
        assert "int code;" in header

    def test_errorvariables_after_fix(self):
        """After fixing ErrMsg to CHARACTER(1024), struct should generate correctly."""
        fixed_source = ERRORVARIABLES_SOURCE.replace(
            "CHARACTER(:), ALLOCATABLE     :: ErrMsg",
            "CHARACTER(1024)               :: ErrMsg",
        )
        info = parse_derived_type(fixed_source, "ErrorVariables")
        assert info is not None
        assert info.is_c_compatible  # No allocatable anymore
        aliases = {"IntKi": "INTEGER(4)", "C_INT": "INTEGER(4)"}
        header = generate_c_struct_header(info, aliases)
        assert "char ErrMsg[1024];" in header
        assert "int aviFAIL;" in header
        assert "int ErrStat;" in header
        assert "int size_avcMSG;" in header
        assert "errorvariables_t" in header


class TestFixCharacterAllocatable:
    """Test fix_character_allocatable() automated fix."""

    def test_dry_run_no_file_changes(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 1024, dry_run=True
        )
        assert result.success
        assert len(result.changes) >= 1
        assert result.files_modified == []
        # File should be unchanged
        content = (tmp_path / "types.f90").read_text()
        assert "ALLOCATABLE" in content

    def test_apply_rewrites_type_definition(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 1024, dry_run=False
        )
        assert result.success
        content = (tmp_path / "types.f90").read_text()
        assert "CHARACTER(1024)" in content
        assert "ALLOCATABLE" not in content.split("ErrMsg")[0].split("ErrorVariables")[1]

    def test_comments_out_allocate(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        (tmp_path / "init.f90").write_text(ALLOCATE_SOURCE)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 1024, dry_run=False
        )
        assert result.success
        init_content = (tmp_path / "init.f90").read_text()
        # ALLOCATE line should be commented out
        for line in init_content.split("\n"):
            if "ALLOCATE" in line.upper() and "ErrMsg" in line:
                assert line.strip().startswith("!")

    def test_skips_already_commented_allocate(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        (tmp_path / "init.f90").write_text(ALLOCATE_ALREADY_COMMENTED)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 1024, dry_run=False
        )
        assert result.success
        init_content = (tmp_path / "init.f90").read_text()
        # Should not double-comment
        assert "! !" not in init_content

    def test_preserves_other_variables(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 1024, dry_run=False
        )
        content = (tmp_path / "types.f90").read_text()
        # Other fields should be unchanged
        assert "size_avcMSG" in content
        assert "aviFAIL" in content
        assert "ErrStat" in content

    def test_custom_length(self, tmp_path):
        (tmp_path / "types.f90").write_text(ERRORVARIABLES_SOURCE)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 512, dry_run=False
        )
        content = (tmp_path / "types.f90").read_text()
        assert "CHARACTER(512)" in content

    def test_type_not_found_error(self, tmp_path):
        (tmp_path / "types.f90").write_text(SIMPLE_TYPE_SOURCE)
        result = fix_character_allocatable(
            str(tmp_path), "ErrorVariables", "ErrMsg", 1024, dry_run=False
        )
        assert not result.success
        assert any("Could not find" in e for e in result.errors)


class TestAnalyzeAllTypes:
    """Test analyze_all_types() integration."""

    def test_mixed_compatibility(self, tmp_path):
        (tmp_path / "err.f90").write_text(ERRORVARIABLES_SOURCE)
        (tmp_path / "simple.f90").write_text(SIMPLE_TYPE_SOURCE)
        aliases = {"IntKi": "INTEGER(4)", "ReKi": "REAL(8)", "C_INT": "INTEGER(4)"}
        analyses = analyze_all_types(str(tmp_path), aliases)
        assert len(analyses) == 2
        compatible = [a for a in analyses if a.is_compatible]
        incompatible = [a for a in analyses if not a.is_compatible]
        assert len(compatible) == 1
        assert len(incompatible) == 1
        assert incompatible[0].type_info.name == "ErrorVariables"

    def test_report_format(self, tmp_path):
        (tmp_path / "err.f90").write_text(ERRORVARIABLES_SOURCE)
        (tmp_path / "simple.f90").write_text(SIMPLE_TYPE_SOURCE)
        aliases = {"IntKi": "INTEGER(4)", "ReKi": "REAL(8)", "C_INT": "INTEGER(4)"}
        analyses = analyze_all_types(str(tmp_path), aliases)
        report = format_analysis_report(analyses)
        assert "Total types:" in report
        assert "C-compatible:" in report
        assert "ErrorVariables" in report
        assert "character_allocatable" in report

    def test_report_verbose_shows_compatible(self, tmp_path):
        (tmp_path / "simple.f90").write_text(SIMPLE_TYPE_SOURCE)
        aliases = {"IntKi": "INTEGER(4)", "ReKi": "REAL(8)"}
        analyses = analyze_all_types(str(tmp_path), aliases)
        report = format_analysis_report(analyses, verbose=True)
        assert "SimpleParams" in report
        assert "Compatible Types" in report
