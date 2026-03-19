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
    infer_character_length,
    fix_logical_field,
    fix_complex_field,
    fix_character_array_allocatable,
    infer_character_array_length,
    fix_bind_c,
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


# --- Inference test fixtures ---

ALLOCATE_LITERAL_SOURCE = textwrap.dedent("""\
    SUBROUTINE Init(ErrVar)
    USE ROSCO_Types
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar

    ALLOCATE(ErrVar%ErrMsg(256))
    ErrVar%ErrMsg = ''

    END SUBROUTINE Init
""")

ALLOCATE_WITH_PARAMETER_SOURCE = textwrap.dedent("""\
    SUBROUTINE Init(ErrVar, size_avcMSG)
    USE ROSCO_Types
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
    INTEGER, PARAMETER :: MAX_SIZE = 1025
    INTEGER, INTENT(IN) :: size_avcMSG

    ALLOCATE(ErrVar%ErrMsg(MAX_SIZE-1))
    ErrVar%ErrMsg = ''

    END SUBROUTINE Init
""")

ALLOCATE_COMPLEX_EXPR_SOURCE = textwrap.dedent("""\
    SUBROUTINE Init(ErrVar, n)
    USE ROSCO_Types
    TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
    INTEGER, INTENT(IN) :: n

    ALLOCATE(ErrVar%ErrMsg(compute_size(n)))

    END SUBROUTINE Init
""")


class TestInferCharacterLength:
    """Test CHARACTER length inference from ALLOCATE calls."""

    def test_infer_from_literal_allocate(self, tmp_path):
        (tmp_path / "init.f90").write_text(ALLOCATE_LITERAL_SOURCE)
        result = infer_character_length(str(tmp_path), "ErrorVariables", "ErrMsg")
        assert result is not None
        length, evidence = result
        assert length == 256
        assert "ErrMsg" in evidence

    def test_infer_from_commented_allocate_with_literal(self, tmp_path):
        """Commented-out ALLOCATE with a literal size should still be inferred."""
        source = textwrap.dedent("""\
            SUBROUTINE Init(ErrVar)
            USE ROSCO_Types
            TYPE(ErrorVariables), INTENT(INOUT) :: ErrVar
            ! ALLOCATE(ErrVar%ErrMsg(512))
            ErrVar%ErrMsg = ''
            END SUBROUTINE Init
        """)
        (tmp_path / "init.f90").write_text(source)
        result = infer_character_length(str(tmp_path), "ErrorVariables", "ErrMsg")
        assert result is not None
        length, evidence = result
        assert length == 512
        assert "ErrMsg" in evidence

    def test_infer_returns_evidence_for_runtime_variable(self, tmp_path):
        """ALLOCATE with a runtime variable returns (None, evidence)."""
        (tmp_path / "init.f90").write_text(ALLOCATE_ALREADY_COMMENTED)
        result = infer_character_length(str(tmp_path), "ErrorVariables", "ErrMsg")
        assert result is not None
        length, evidence = result
        assert length is None  # Can't evaluate runtime expression
        assert "ErrMsg" in evidence
        assert "size_avcMSG" in evidence

    def test_infer_from_parameter_expression(self, tmp_path):
        (tmp_path / "init.f90").write_text(ALLOCATE_WITH_PARAMETER_SOURCE)
        result = infer_character_length(str(tmp_path), "ErrorVariables", "ErrMsg")
        assert result is not None
        length, evidence = result
        assert length == 1024  # MAX_SIZE(1025) - 1

    def test_returns_none_when_no_allocate(self, tmp_path):
        (tmp_path / "types.f90").write_text(SIMPLE_TYPE_SOURCE)
        result = infer_character_length(str(tmp_path), "ErrorVariables", "ErrMsg")
        assert result is None

    def test_returns_none_length_for_complex_expression(self, tmp_path):
        """Complex expression returns (None, evidence) — found but can't evaluate."""
        (tmp_path / "init.f90").write_text(ALLOCATE_COMPLEX_EXPR_SOURCE)
        result = infer_character_length(str(tmp_path), "ErrorVariables", "ErrMsg")
        assert result is not None
        length, evidence = result
        assert length is None
        assert "compute_size" in evidence


# ============================================================
# Phase 4C: LOGICAL, COMPLEX, CHARACTER array, BIND(C) fixes
# ============================================================

LOGICAL_TYPE_SOURCE = textwrap.dedent("""\
    MODULE ROSCO_Types
    IMPLICIT NONE

    TYPE, PUBLIC :: LocalVariables
        INTEGER(IntKi)                :: iStatus                     ! Init status
        LOGICAL                       :: restart                     ! Restart flag
        REAL(DbKi)                    :: Time                        ! Time [s]
    END TYPE LocalVariables

    END MODULE ROSCO_Types
""")

LOGICAL_ASSIGN_SOURCE = textwrap.dedent("""\
    SUBROUTINE Init(LocalVar)
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar

    IF (LocalVar%iStatus == 0) THEN
        LocalVar%restart = .True.
    ELSE
        LocalVar%restart = .False.
    ENDIF

    END SUBROUTINE Init
""")

LOGICAL_IF_SOURCE = textwrap.dedent("""\
    SUBROUTINE Check(LocalVar)
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar

    IF (LocalVar%restart) THEN
        LocalVar%Time = 0.0
    ENDIF

    END SUBROUTINE Check
""")

LOGICAL_FUNC_ARG_SOURCE = textwrap.dedent("""\
    SUBROUTINE Control(LocalVar, objInst)
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar

    LocalVar%Time = LPFilter(1.0, 0.01, 1.0, LocalVar%FP, 0, LocalVar%restart, objInst%instLPF)

    END SUBROUTINE Control
""")

LOGICAL_IO_SOURCE = textwrap.dedent("""\
    SUBROUTINE WriteState(LocalVar, Un)
    TYPE(LocalVariables), INTENT(IN) :: LocalVar
    INTEGER, INTENT(IN) :: Un

    WRITE( Un, IOSTAT=ErrStat) LocalVar%restart
    READ( Un, IOSTAT=ErrStat) LocalVar%restart

    END SUBROUTINE WriteState
""")

LOGICAL_NOT_SOURCE = textwrap.dedent("""\
    SUBROUTINE Test(LocalVar)
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar

    IF (.NOT. LocalVar%restart) THEN
        LocalVar%Time = 0.0
    ENDIF

    END SUBROUTINE Test
""")


class TestFixLogical:
    """Test fix_logical_field() for LOGICAL → INTEGER(C_INT) conversion."""

    def test_logical_field_is_blocker(self):
        info = parse_derived_type(LOGICAL_TYPE_SOURCE, "LocalVariables")
        assert info is not None
        aliases = {"IntKi": "INTEGER(4)", "DbKi": "REAL(8)"}
        blockers = classify_field_blockers(info, aliases)
        reasons = [b.reason for b in blockers]
        assert "logical" in reasons

    def test_fix_logical_rewrites_type_definition(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        assert result.success
        content = (tmp_path / "types.f90").read_text()
        assert "INTEGER(C_INT)" in content
        assert "LOGICAL" not in content

    def test_fix_logical_rewrites_true_assignment(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "init.f90").write_text(LOGICAL_ASSIGN_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        assert result.success
        content = (tmp_path / "init.f90").read_text()
        assert "%restart = 1" in content
        assert ".True." not in content

    def test_fix_logical_rewrites_false_assignment(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "init.f90").write_text(LOGICAL_ASSIGN_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        content = (tmp_path / "init.f90").read_text()
        assert "%restart = 0" in content
        assert ".False." not in content

    def test_fix_logical_rewrites_if_condition(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "check.f90").write_text(LOGICAL_IF_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        content = (tmp_path / "check.f90").read_text()
        assert "LocalVar%restart /= 0" in content

    def test_fix_logical_wraps_function_argument(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "ctrl.f90").write_text(LOGICAL_FUNC_ARG_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        content = (tmp_path / "ctrl.f90").read_text()
        assert "(LocalVar%restart /= 0)" in content

    def test_fix_logical_preserves_binary_io(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "io.f90").write_text(LOGICAL_IO_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        content = (tmp_path / "io.f90").read_text()
        # I/O lines should be unchanged — no (/= 0) wrapping
        assert "WRITE( Un, IOSTAT=ErrStat) LocalVar%restart" in content
        assert "READ( Un, IOSTAT=ErrStat) LocalVar%restart" in content
        assert "/= 0" not in content

    def test_fix_logical_rewrites_not(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "test.f90").write_text(LOGICAL_NOT_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=False
        )
        content = (tmp_path / "test.f90").read_text()
        assert "(LocalVar%restart == 0)" in content
        assert ".NOT." not in content

    def test_fix_logical_dry_run(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        (tmp_path / "init.f90").write_text(LOGICAL_ASSIGN_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "restart", dry_run=True
        )
        assert result.success
        assert len(result.changes) >= 1
        assert result.files_modified == []
        # Files should be unchanged
        content = (tmp_path / "types.f90").read_text()
        assert "LOGICAL" in content

    def test_fix_logical_field_not_found(self, tmp_path):
        (tmp_path / "types.f90").write_text(LOGICAL_TYPE_SOURCE)
        result = fix_logical_field(
            str(tmp_path), "LocalVariables", "nonexistent", dry_run=False
        )
        assert not result.success


# --- COMPLEX fix test fixtures ---

COMPLEX_TYPE_SOURCE = textwrap.dedent("""\
    MODULE ROSCO_Types
    IMPLICIT NONE

    TYPE, PUBLIC :: LocalVariables
        INTEGER(IntKi)                :: iStatus
        COMPLEX(DbKi)                :: AWC_complexangle(3)         ! Complex angle
        REAL(DbKi)                    :: Time
    END TYPE LocalVariables

    END MODULE ROSCO_Types
""")

COMPLEX_EULER_SOURCE = textwrap.dedent("""\
    SUBROUTINE AWC(LocalVar, AWC_angle, K)
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar
    REAL(DbKi) :: AWC_angle(3), amp
    COMPLEX(DbKi)              :: complexI = (0.0, 1.0)
    INTEGER :: K

    LocalVar%AWC_complexangle = 0.0D0
    LocalVar%AWC_complexangle(K) = LocalVar%AWC_complexangle(K) + amp * EXP(complexI * (AWC_angle(K)))
    LocalVar%PitCom(K) = LocalVar%PitCom(K) + REAL(LocalVar%AWC_complexangle(K))

    END SUBROUTINE AWC
""")

COMPLEX_IO_SOURCE = textwrap.dedent("""\
    SUBROUTINE WriteState(LocalVar, Un)
    INTEGER :: Un, ErrStat
    WRITE( Un, IOSTAT=ErrStat) LocalVar%AWC_complexangle(1)
    WRITE( Un, IOSTAT=ErrStat) LocalVar%AWC_complexangle(2)
    READ( Un, IOSTAT=ErrStat) LocalVar%AWC_complexangle(1)
    END SUBROUTINE WriteState
""")


class TestFixComplex:
    """Test fix_complex_field() for COMPLEX → REAL re/im split."""

    def test_fix_complex_splits_type_definition(self, tmp_path):
        (tmp_path / "types.f90").write_text(COMPLEX_TYPE_SOURCE)
        result = fix_complex_field(
            str(tmp_path), "LocalVariables", "AWC_complexangle", dry_run=False
        )
        assert result.success
        content = (tmp_path / "types.f90").read_text()
        assert "AWC_complexangle_re(3)" in content
        assert "AWC_complexangle_im(3)" in content
        assert "COMPLEX" not in content.split("END TYPE")[0]

    def test_fix_complex_rewrites_zero_init(self, tmp_path):
        (tmp_path / "types.f90").write_text(COMPLEX_TYPE_SOURCE)
        (tmp_path / "awc.f90").write_text(COMPLEX_EULER_SOURCE)
        result = fix_complex_field(
            str(tmp_path), "LocalVariables", "AWC_complexangle", dry_run=False
        )
        content = (tmp_path / "awc.f90").read_text()
        assert "AWC_complexangle_re = 0.0D0" in content
        assert "AWC_complexangle_im = 0.0D0" in content

    def test_fix_complex_rewrites_euler_formula(self, tmp_path):
        (tmp_path / "types.f90").write_text(COMPLEX_TYPE_SOURCE)
        (tmp_path / "awc.f90").write_text(COMPLEX_EULER_SOURCE)
        result = fix_complex_field(
            str(tmp_path), "LocalVariables", "AWC_complexangle", dry_run=False
        )
        content = (tmp_path / "awc.f90").read_text()
        assert "COS(AWC_angle(K))" in content
        assert "SIN(AWC_angle(K))" in content
        assert "EXP" not in content

    def test_fix_complex_rewrites_real_extraction(self, tmp_path):
        (tmp_path / "types.f90").write_text(COMPLEX_TYPE_SOURCE)
        (tmp_path / "awc.f90").write_text(COMPLEX_EULER_SOURCE)
        result = fix_complex_field(
            str(tmp_path), "LocalVariables", "AWC_complexangle", dry_run=False
        )
        content = (tmp_path / "awc.f90").read_text()
        assert "AWC_complexangle_re(K)" in content
        # Should not have REAL(...) wrapper anymore
        assert "REAL(LocalVar%AWC_complexangle" not in content

    def test_fix_complex_splits_binary_io(self, tmp_path):
        (tmp_path / "types.f90").write_text(COMPLEX_TYPE_SOURCE)
        (tmp_path / "io.f90").write_text(COMPLEX_IO_SOURCE)
        result = fix_complex_field(
            str(tmp_path), "LocalVariables", "AWC_complexangle", dry_run=False
        )
        content = (tmp_path / "io.f90").read_text()
        assert "AWC_complexangle_re(1)" in content
        assert "AWC_complexangle_im(1)" in content
        assert "AWC_complexangle_re(2)" in content
        assert "AWC_complexangle_im(2)" in content

    def test_fix_complex_removes_complexi_decl(self, tmp_path):
        (tmp_path / "types.f90").write_text(COMPLEX_TYPE_SOURCE)
        (tmp_path / "awc.f90").write_text(COMPLEX_EULER_SOURCE)
        result = fix_complex_field(
            str(tmp_path), "LocalVariables", "AWC_complexangle", dry_run=False
        )
        content = (tmp_path / "awc.f90").read_text()
        # complexI declaration should be commented out
        for line in content.split("\n"):
            if "complexI" in line and "=" in line and "COMPLEX" in line.upper():
                assert line.strip().startswith("!")


# --- CHARACTER array fix test fixtures ---

CHAR_ARRAY_TYPE_SOURCE = textwrap.dedent("""\
    MODULE ROSCO_Types
    IMPLICIT NONE

    TYPE, PUBLIC :: LocalVariables
        INTEGER(IntKi)                :: ACC_INFILE_SIZE
        CHARACTER, DIMENSION(:), ALLOCATABLE     :: ACC_INFILE        ! Parameter input filename
        REAL(DbKi)                    :: Time
    END TYPE LocalVariables

    END MODULE ROSCO_Types
""")

CHAR_ARRAY_ALLOC_SOURCE = textwrap.dedent("""\
    SUBROUTINE Init(LocalVar)
    TYPE(LocalVariables), INTENT(INOUT) :: LocalVar

    LocalVar%ACC_INFILE_SIZE = 256
    Allocate(LocalVar%ACC_INFILE(LocalVar%ACC_INFILE_SIZE))
    LocalVar%ACC_INFILE = accINFILE

    END SUBROUTINE Init
""")


class TestFixCharacterArray:
    """Test fix_character_array_allocatable() for CHARACTER array fields."""

    def test_fix_character_array_rewrites_type(self, tmp_path):
        (tmp_path / "types.f90").write_text(CHAR_ARRAY_TYPE_SOURCE)
        result = fix_character_array_allocatable(
            str(tmp_path), "LocalVariables", "ACC_INFILE", 1024, dry_run=False
        )
        assert result.success
        content = (tmp_path / "types.f90").read_text()
        assert "ACC_INFILE(1024)" in content
        assert "ALLOCATABLE" not in content.split("ACC_INFILE")[0].split("LocalVariables")[1]

    def test_fix_character_array_comments_allocate(self, tmp_path):
        (tmp_path / "types.f90").write_text(CHAR_ARRAY_TYPE_SOURCE)
        (tmp_path / "init.f90").write_text(CHAR_ARRAY_ALLOC_SOURCE)
        result = fix_character_array_allocatable(
            str(tmp_path), "LocalVariables", "ACC_INFILE", 1024, dry_run=False
        )
        content = (tmp_path / "init.f90").read_text()
        for line in content.split("\n"):
            if "ALLOCATE" in line.upper() and "ACC_INFILE" in line:
                assert line.strip().startswith("!")

    def test_fix_character_array_dry_run(self, tmp_path):
        (tmp_path / "types.f90").write_text(CHAR_ARRAY_TYPE_SOURCE)
        result = fix_character_array_allocatable(
            str(tmp_path), "LocalVariables", "ACC_INFILE", 1024, dry_run=True
        )
        assert result.success
        assert result.files_modified == []
        content = (tmp_path / "types.f90").read_text()
        assert "ALLOCATABLE" in content

    def test_infer_character_array_length(self, tmp_path):
        (tmp_path / "init.f90").write_text(CHAR_ARRAY_ALLOC_SOURCE)
        result = infer_character_array_length(
            str(tmp_path), "LocalVariables", "ACC_INFILE"
        )
        assert result is not None
        length, evidence = result
        # Expression is LocalVar%ACC_INFILE_SIZE which is not evaluable
        assert "ACC_INFILE" in evidence


# --- BIND(C) fix test fixtures ---

BIND_C_TYPE_SOURCE = textwrap.dedent("""\
    MODULE ROSCO_Types
    IMPLICIT NONE

    TYPE, PUBLIC :: LocalVariables
        INTEGER(IntKi)                :: iStatus                     ! Init status
        INTEGER(IntKi)                :: AlreadyInitialized = 0      ! Has ROSCO been initialized
        REAL(DbKi)                    :: Time                        ! Time [s]
    END TYPE LocalVariables

    END MODULE ROSCO_Types
""")


class TestFixBindC:
    """Test fix_bind_c() for adding BIND(C) and removing default initializers."""

    def test_fix_bind_c_adds_attribute(self, tmp_path):
        (tmp_path / "types.f90").write_text(BIND_C_TYPE_SOURCE)
        result = fix_bind_c(str(tmp_path), "LocalVariables", dry_run=False)
        assert result.success
        content = (tmp_path / "types.f90").read_text()
        assert "BIND(C)" in content

    def test_fix_bind_c_removes_default_init(self, tmp_path):
        (tmp_path / "types.f90").write_text(BIND_C_TYPE_SOURCE)
        result = fix_bind_c(str(tmp_path), "LocalVariables", dry_run=False)
        content = (tmp_path / "types.f90").read_text()
        # AlreadyInitialized should no longer have = 0
        for line in content.split("\n"):
            if "AlreadyInitialized" in line and "::" in line:
                assert "= 0" not in line

    def test_fix_bind_c_dry_run(self, tmp_path):
        (tmp_path / "types.f90").write_text(BIND_C_TYPE_SOURCE)
        result = fix_bind_c(str(tmp_path), "LocalVariables", dry_run=True)
        assert result.success
        assert result.files_modified == []
        content = (tmp_path / "types.f90").read_text()
        assert "BIND(C)" not in content

    def test_default_initializer_is_blocker(self):
        info = parse_derived_type(BIND_C_TYPE_SOURCE, "LocalVariables")
        assert info is not None
        aliases = {"IntKi": "INTEGER(4)", "DbKi": "REAL(8)"}
        blockers = classify_field_blockers(info, aliases)
        reasons = [b.reason for b in blockers]
        assert "default_initializer" in reasons
        # Check it points to AlreadyInitialized
        init_blockers = [b for b in blockers if b.reason == "default_initializer"]
        assert init_blockers[0].field_name == "AlreadyInitialized"
