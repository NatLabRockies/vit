"""Tests for Phase 4D view type infrastructure.

Tests the ALLOCATABLE size scanner, view struct header generation,
populator generation, type registry with view strategies, and
interface generation with view types.
"""

import os
import tempfile

import pytest
from vit.analyze_types import (
    AllocatableFieldMapping,
    scan_allocatable_sizes,
    _classify_size_expr,
    _split_dims_aware,
    _scan_helper_call,
)
from vit.config import _parse_allocatable_helpers
from vit.derived_type_parser import (
    DerivedTypeInfo,
    DerivedTypeField,
    generate_view_struct_header,
    apply_type_strategies,
)
from vit.view_populator import generate_view_populator
from vit.type_map import get_type_mapping, get_derived_type_mapping
from vit.interface_gen import build_c_params, generate_fortran_wrapper
from vit.fortran_parser import FortranSignature, FortranArg, IntentKind


# ---------------------------------------------------------------------------
# Helpers: build test fixtures
# ---------------------------------------------------------------------------

def _make_type_info(name, fields):
    """Build a DerivedTypeInfo from a list of (name, type, allocatable, array_dims) tuples."""
    type_fields = []
    for f in fields:
        fname, ftype = f[0], f[1]
        is_alloc = f[2] if len(f) > 2 else False
        adims = f[3] if len(f) > 3 else None
        type_fields.append(DerivedTypeField(
            name=fname, fortran_type=ftype,
            is_allocatable=is_alloc, is_array=bool(adims),
            array_dims=adims,
        ))
    return DerivedTypeInfo(name=name, fields=type_fields)


def _make_mapping(field_name, ftype, rank, size_class, dim_sizes):
    return AllocatableFieldMapping(
        field_name=field_name, fortran_type=ftype,
        rank=rank, size_class=size_class, dim_sizes=dim_sizes,
        evidence="test", needs_confirm=(size_class == "C"),
    )


def _make_sig_with_view_arg():
    """Build a FortranSignature with a view-type arg for interface gen tests."""
    args = [
        FortranArg(name="CntrPar", fortran_type="TYPE(ControlParameters)",
                    intent=IntentKind.INOUT),
        FortranArg(name="LocalVar", fortran_type="TYPE(LocalVariables)",
                    intent=IntentKind.INOUT),
    ]
    from vit.fortran_parser import SubprogramKind
    sig = FortranSignature(
        name="TestSub", kind=SubprogramKind.SUBROUTINE, args=args,
        use_statements=["USE ROSCO_Types, ONLY : ControlParameters, LocalVariables"],
    )
    return sig


def _make_registry_with_view():
    """Build a derived type registry with one view type and one bind_c type."""
    cp = _make_type_info("ControlParameters", [
        ("PC_GS_n", "INTEGER(IntKi)"),
        ("PC_GS_angles", "REAL(DbKi)", True, ":"),
    ])
    cp.view_strategy = "view"

    lv = _make_type_info("LocalVariables", [
        ("GenSpeed", "REAL(DbKi)"),
        ("RotSpeed", "REAL(DbKi)"),
    ])

    return {"ControlParameters": cp, "LocalVariables": lv}


# ---------------------------------------------------------------------------
# Fix 4: Dimension splitting
# ---------------------------------------------------------------------------

class TestDimensionSplitting:
    def test_simple_two_dims(self):
        assert _split_dims_aware("N, M") == ["N", "M"]

    def test_single_dim(self):
        assert _split_dims_aware("N") == ["N"]

    def test_nested_parens(self):
        result = _split_dims_aware("N_OL_Cables,SIZE(CntrPar%OL_Channels,DIM=1)")
        assert len(result) == 2
        assert result[0] == "N_OL_Cables"
        assert "SIZE" in result[1]
        assert "DIM=1" in result[1]

    def test_double_nested(self):
        result = _split_dims_aware("CntrPar%PerfTableSize(2),CntrPar%PerfTableSize(1)")
        assert len(result) == 2
        assert "PerfTableSize(2)" in result[0]
        assert "PerfTableSize(1)" in result[1]


# ---------------------------------------------------------------------------
# Scanner: size expression classification
# ---------------------------------------------------------------------------

class TestClassifySizeExpr:
    def test_literal_integer(self):
        cls, confirm = _classify_size_expr("2")
        assert cls == "B" and not confirm

    def test_field_reference(self):
        cls, confirm = _classify_size_expr("CntrPar%PC_GS_n")
        assert cls == "A" and not confirm

    def test_size_of_field(self):
        cls, confirm = _classify_size_expr("SIZE(CntrPar%OL_Channels)")
        assert cls == "D" and not confirm

    def test_array_indexed_field(self):
        cls, confirm = _classify_size_expr("CntrPar%PerfTableSize(1)")
        assert cls == "E" and not confirm

    def test_local_variable(self):
        cls, confirm = _classify_size_expr("N_OL_Cables")
        assert cls == "C" and confirm

    def test_complex_expression(self):
        cls, confirm = _classify_size_expr("N + 1")
        assert cls == "C" and confirm


# ---------------------------------------------------------------------------
# View struct header generation
# ---------------------------------------------------------------------------

class TestViewStructHeader:
    def test_scalar_field(self):
        ti = _make_type_info("TestType", [("x", "REAL(8)")])
        header = generate_view_struct_header(ti, [], {"DbKi": "REAL(8)"})
        assert "double x;" in header

    def test_1d_allocatable(self):
        ti = _make_type_info("TestType", [("arr", "REAL(8)", True, ":")])
        mapping = _make_mapping("arr", "REAL(8)", 1, "A", ["N"])
        header = generate_view_struct_header(ti, [mapping], {"DbKi": "REAL(8)"})
        assert "double* arr;" in header
        assert "int32_t n_arr;" in header

    def test_2d_allocatable(self):
        ti = _make_type_info("TestType", [("mat", "REAL(8)", True, ":,:")])
        mapping = _make_mapping("mat", "REAL(8)", 2, "E", ["N", "M"])
        header = generate_view_struct_header(ti, [mapping], {"DbKi": "REAL(8)"})
        assert "double* mat;" in header
        assert "int32_t n_mat_rows;" in header
        assert "int32_t n_mat_cols;" in header

    def test_character_field(self):
        ti = _make_type_info("TestType", [("name", "CHARACTER(1024)")])
        header = generate_view_struct_header(ti, [])
        assert "char name[1024];" in header

    def test_unmapped_still_included(self):
        """ALLOCATABLE fields are included even without scanner mappings."""
        ti = _make_type_info("TestType", [("orphan", "REAL(8)", True, ":")])
        header = generate_view_struct_header(ti, [])  # no mappings
        assert "double* orphan;" in header
        assert "int32_t n_orphan;" in header

    def test_view_t_suffix(self):
        ti = _make_type_info("TestType", [("x", "REAL(8)")])
        header = generate_view_struct_header(ti, [])
        assert "testtype_view_t" in header
        assert "_VIEW_T_H" in header


# ---------------------------------------------------------------------------
# Populator generation
# ---------------------------------------------------------------------------

class TestPopulatorGeneration:
    def _generate(self, fields, mappings):
        ti = _make_type_info("TestType", fields)
        return generate_view_populator(ti, mappings, {"DbKi": "REAL(8)"}, "TestModule")

    def test_scalar_assignment(self):
        code = self._generate([("x", "REAL(8)")], [])
        assert "view%x = REAL(src%x, C_DOUBLE)" in code

    def test_1d_cloc(self):
        mapping = _make_mapping("arr", "REAL(8)", 1, "A", ["N"])
        code = self._generate([("arr", "REAL(8)", True, ":")], [mapping])
        assert "C_LOC(src%arr(LBOUND(src%arr,1)))" in code
        assert "SIZE(src%arr)" in code

    def test_2d_cloc(self):
        mapping = _make_mapping("mat", "REAL(8)", 2, "E", ["N", "M"])
        code = self._generate([("mat", "REAL(8)", True, ":,:")], [mapping])
        assert "C_LOC(src%mat(LBOUND(src%mat,1),LBOUND(src%mat,2)))" in code
        assert "SIZE(src%mat, 1)" in code
        assert "SIZE(src%mat, 2)" in code

    def test_allocated_check(self):
        mapping = _make_mapping("arr", "REAL(8)", 1, "A", ["N"])
        code = self._generate([("arr", "REAL(8)", True, ":")], [mapping])
        assert "IF (ALLOCATED(src%arr))" in code

    def test_character_array_form(self):
        code = self._generate([("name", "CHARACTER(1024)")], [])
        assert "CHARACTER(KIND=C_CHAR) :: name(1024)" in code

    def test_bind_c_mirror_type(self):
        code = self._generate([("x", "REAL(8)")], [])
        assert "TYPE, BIND(C) :: testtype_view_t" in code

    def test_module_structure(self):
        code = self._generate([("x", "REAL(8)")], [])
        assert "MODULE vit_testtype_view" in code
        assert "CONTAINS" in code
        assert "SUBROUTINE vit_populate_testtype" in code
        assert "END MODULE vit_testtype_view" in code


# ---------------------------------------------------------------------------
# Type registry: apply_type_strategies
# ---------------------------------------------------------------------------

class TestApplyTypeStrategies:
    def test_sets_view_strategy(self):
        ti = _make_type_info("ControlParameters", [("x", "REAL(8)", True, ":")])
        registry = {"ControlParameters": ti}
        apply_type_strategies(registry, {"ControlParameters": "view"})
        assert ti.view_strategy == "view"

    def test_case_insensitive(self):
        ti = _make_type_info("ControlParameters", [("x", "REAL(8)")])
        registry = {"ControlParameters": ti}
        apply_type_strategies(registry, {"controlparameters": "view"})
        assert ti.view_strategy == "view"

    def test_no_match_leaves_none(self):
        ti = _make_type_info("OtherType", [("x", "REAL(8)")])
        registry = {"OtherType": ti}
        apply_type_strategies(registry, {"ControlParameters": "view"})
        assert ti.view_strategy is None


# ---------------------------------------------------------------------------
# TypeMapping: view type flow
# ---------------------------------------------------------------------------

class TestTypeMappingViewType:
    def test_view_strategy_makes_compatible(self):
        ti = _make_type_info("CP", [("arr", "REAL(8)", True, ":")])
        assert not ti.is_c_compatible  # has ALLOCATABLE
        ti.view_strategy = "view"
        mapping = get_derived_type_mapping("TYPE(CP)", ti)
        assert mapping.is_c_compatible is True
        assert mapping.is_view_type is True

    def test_no_strategy_stays_incompatible(self):
        ti = _make_type_info("CP", [("arr", "REAL(8)", True, ":")])
        mapping = get_derived_type_mapping("TYPE(CP)", ti)
        assert mapping.is_c_compatible is False
        assert mapping.is_view_type is False

    def test_bind_c_type_not_view(self):
        ti = _make_type_info("LV", [("x", "REAL(8)")])
        mapping = get_derived_type_mapping("TYPE(LV)", ti)
        assert mapping.is_c_compatible is True
        assert mapping.is_view_type is False


# ---------------------------------------------------------------------------
# Interface generation: view type params and wrapper
# ---------------------------------------------------------------------------

class TestInterfaceGenViewType:
    def setup_method(self):
        self.sig = _make_sig_with_view_arg()
        self.registry = _make_registry_with_view()
        self.aliases = {"IntKi": "INTEGER(4)", "DbKi": "REAL(8)"}

    def test_build_c_params_view_suffix(self):
        params = build_c_params(self.sig, self.aliases, self.registry)
        param_types = [p[0] for p in params]
        assert "controlparameters_view_t*" in param_types
        assert "localvariables_t*" in param_types

    def test_wrapper_view_use_statement(self):
        wrapper = generate_fortran_wrapper(self.sig, self.aliases, self.registry)
        assert "USE vit_controlparameters_view" in wrapper

    def test_wrapper_view_local_decl(self):
        wrapper = generate_fortran_wrapper(self.sig, self.aliases, self.registry)
        assert "TYPE(controlparameters_view_t), TARGET :: CntrPar_view" in wrapper

    def test_wrapper_view_populator_call(self):
        wrapper = generate_fortran_wrapper(self.sig, self.aliases, self.registry)
        assert "CALL vit_populate_controlparameters(CntrPar, CntrPar_view)" in wrapper

    def test_wrapper_view_cloc(self):
        wrapper = generate_fortran_wrapper(self.sig, self.aliases, self.registry)
        assert "C_LOC(CntrPar_view)" in wrapper
        # Should NOT pass C_LOC(CntrPar) for the view type
        assert "C_LOC(CntrPar)" not in wrapper

    def test_wrapper_regular_type_unchanged(self):
        wrapper = generate_fortran_wrapper(self.sig, self.aliases, self.registry)
        assert "C_LOC(LocalVar)" in wrapper
        # LocalVar should NOT have a view
        assert "LocalVar_view" not in wrapper


# ---------------------------------------------------------------------------
# Configurable helper scanner (_scan_helper_call)
# ---------------------------------------------------------------------------

def _write_temp_fortran(content):
    """Write content to a temp .f90 file and return {Path: content} dict."""
    from pathlib import Path
    tmp = tempfile.NamedTemporaryFile(suffix=".f90", mode="w", delete=False)
    tmp.write(content)
    tmp.close()
    return {Path(tmp.name): content}


class TestScanHelperCall:
    def test_adjacent_args(self):
        """Helper with size_arg = array_arg + 1 (fast regex path)."""
        source = "        CALL AllocHelper(FileLines, 'label', Var%myfield, Var%mysize, extra)\n"
        files = _write_temp_fortran(source)
        field = DerivedTypeField(name="myfield", fortran_type="REAL(8)",
                                  is_allocatable=True, is_array=True, array_dims=":")
        helper = {"name": "AllocHelper", "array_arg": 3, "size_arg": 4}
        result = _scan_helper_call(field, files, helper)
        assert result is not None
        assert result.field_name == "myfield"
        assert "Var%mysize" in result.dim_sizes[0]
        assert result.size_class == "A"

    def test_non_adjacent_args(self):
        """Helper with size_arg != array_arg + 1 (positional parsing path)."""
        source = "        CALL CustomAlloc(Var%myfield, extra1, extra2, 42, moreargs)\n"
        files = _write_temp_fortran(source)
        field = DerivedTypeField(name="myfield", fortran_type="REAL(8)",
                                  is_allocatable=True, is_array=True, array_dims=":")
        helper = {"name": "CustomAlloc", "array_arg": 1, "size_arg": 4}
        result = _scan_helper_call(field, files, helper)
        assert result is not None
        assert result.field_name == "myfield"
        assert "42" in result.dim_sizes[0]
        assert result.size_class == "B"

    def test_skips_comments(self):
        """Commented-out helper call should not match."""
        source = "    ! CALL AllocHelper(FileLines, 'label', Var%myfield, Var%mysize, extra)\n"
        files = _write_temp_fortran(source)
        field = DerivedTypeField(name="myfield", fortran_type="REAL(8)",
                                  is_allocatable=True, is_array=True, array_dims=":")
        helper = {"name": "AllocHelper", "array_arg": 3, "size_arg": 4}
        result = _scan_helper_call(field, files, helper)
        assert result is None

    def test_no_match(self):
        """Field not in any helper call."""
        source = "        CALL AllocHelper(FileLines, 'label', Var%otherfield, Var%othersize, extra)\n"
        files = _write_temp_fortran(source)
        field = DerivedTypeField(name="myfield", fortran_type="REAL(8)",
                                  is_allocatable=True, is_array=True, array_dims=":")
        helper = {"name": "AllocHelper", "array_arg": 3, "size_arg": 4}
        result = _scan_helper_call(field, files, helper)
        assert result is None


# ---------------------------------------------------------------------------
# scan_allocatable_sizes with helpers
# ---------------------------------------------------------------------------

class TestScanAllocatableSizes:
    def test_with_helpers(self):
        """Helper match takes priority over ALLOCATE."""
        source = (
            "    CALL ParseAry(FileLines, 'lbl', Var%arr, Var%n_arr, extra)\n"
            "    ALLOCATE(Var%arr(100))\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test.f90")
            with open(fpath, "w") as f:
                f.write(source)
            ti = _make_type_info("T", [("arr", "REAL(8)", True, ":")])
            helpers = [{"name": "ParseAry", "array_arg": 3, "size_arg": 4}]
            result = scan_allocatable_sizes(tmpdir, "T", ti, allocatable_helpers=helpers)
            assert len(result) == 1
            assert "Var%n_arr" in result[0].dim_sizes[0]  # helper matched, not ALLOCATE

    def test_without_helpers(self):
        """Without helpers, falls through to ALLOCATE."""
        source = "    ALLOCATE(Var%arr(100))\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test.f90")
            with open(fpath, "w") as f:
                f.write(source)
            ti = _make_type_info("T", [("arr", "REAL(8)", True, ":")])
            result = scan_allocatable_sizes(tmpdir, "T", ti, allocatable_helpers=[])
            assert len(result) == 1
            assert "100" in result[0].dim_sizes[0]

    def test_empty_type(self):
        """Type with no ALLOCATABLE fields returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test.f90")
            with open(fpath, "w") as f:
                f.write("! nothing\n")
            ti = _make_type_info("T", [("x", "REAL(8)")])  # no ALLOCATABLE
            result = scan_allocatable_sizes(tmpdir, "T", ti)
            assert result == []


# ---------------------------------------------------------------------------
# Config parser: _parse_allocatable_helpers
# ---------------------------------------------------------------------------

class TestParseAllocatableHelpers:
    def test_valid(self):
        section = {"helpers": [{"name": "ParseAry", "array_arg": 3, "size_arg": 4}]}
        result = _parse_allocatable_helpers(section)
        assert len(result) == 1
        assert result[0]["name"] == "ParseAry"
        assert result[0]["array_arg"] == 3
        assert result[0]["size_arg"] == 4

    def test_defaults(self):
        """Missing array_arg/size_arg default to 3/4."""
        section = {"helpers": [{"name": "MyHelper"}]}
        result = _parse_allocatable_helpers(section)
        assert result[0]["array_arg"] == 3
        assert result[0]["size_arg"] == 4

    def test_empty(self):
        result = _parse_allocatable_helpers({})
        assert result == []

    def test_malformed(self):
        """Non-dict entries and entries without 'name' are skipped."""
        section = {"helpers": ["not_a_dict", {"no_name": True}, {"name": "Valid"}]}
        result = _parse_allocatable_helpers(section)
        assert len(result) == 1
        assert result[0]["name"] == "Valid"


# ---------------------------------------------------------------------------
# Unmapped 2D rank inference
# ---------------------------------------------------------------------------

class TestUnmapped2DRankInference:
    def test_view_header_unmapped_2d(self):
        """2D ALLOCATABLE with no mapping infers rank from array_dims."""
        ti = _make_type_info("T", [("mat", "REAL(8)", True, ":,:")])
        header = generate_view_struct_header(ti, [], {"DbKi": "REAL(8)"})
        assert "double* mat;" in header
        assert "n_mat_rows" in header
        assert "n_mat_cols" in header

    def test_populator_unmapped_2d(self):
        """Populator handles unmapped 2D with SIZE(field, 1) and SIZE(field, 2)."""
        ti = _make_type_info("T", [("mat", "REAL(8)", True, ":,:")])
        code = generate_view_populator(ti, [], {"DbKi": "REAL(8)"}, "TestMod")
        assert "C_LOC(src%mat(LBOUND(src%mat,1),LBOUND(src%mat,2)))" in code
        assert "SIZE(src%mat, 1)" in code
        assert "SIZE(src%mat, 2)" in code


# ---------------------------------------------------------------------------
# Source module error cases
# ---------------------------------------------------------------------------

class TestSourceModuleErrors:
    def test_integrate_missing_source_module(self):
        """View type without source_module raises ValueError in integration."""
        from vit.integrate import generate_integration_plan
        ti = _make_type_info("ViewType", [("arr", "REAL(8)", True, ":")])
        ti.view_strategy = "view"
        ti.source_module = None
        ti.source_file = None
        ti.field_mappings = []

        sig = _make_sig_with_view_arg()
        registry = {"ControlParameters": ti, "LocalVariables": _make_type_info("LocalVariables", [("x", "REAL(8)")])}

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create minimal files for integration
            src = os.path.join(tmpdir, "test.f90")
            cpp = os.path.join(tmpdir, "test.cpp")
            with open(src, "w") as f:
                f.write("! source\n")
            with open(cpp, "w") as f:
                f.write("// impl\n")

            with pytest.raises(ValueError, match="source module unknown"):
                generate_integration_plan(
                    sig=sig, cpp_impl_path=cpp, source_file=src,
                    derived_types=registry,
                )


# ---------------------------------------------------------------------------
# POINTER field handling
# ---------------------------------------------------------------------------

class TestPointerFieldHandling:
    def test_view_header_pointer_commented(self):
        """POINTER fields appear as comments, not struct members."""
        ti = _make_type_info("T", [("x", "REAL(8)")])
        # Add a POINTER field manually
        ti.fields.append(DerivedTypeField(
            name="ptr_field", fortran_type="REAL(8)",
            is_pointer=True, is_array=True, array_dims=":",
        ))
        header = generate_view_struct_header(ti, [])
        assert "ptr_field: POINTER" in header
        assert "double* ptr_field;" not in header


# ---------------------------------------------------------------------------
# Deferred-length CHARACTER fields — `CHARACTER(:), ALLOCATABLE`
#
# The field VIT refused until unit #5 of the rosco-r2 campaign, where it blocked
# `TYPE(ErrorVariables)` and with it 37 of that campaign's 69 units. It has no C
# type: its LENGTH is part of its allocation, so reassigning it REALLOCATES it,
# and C++ cannot reallocate Fortran's storage.
#
# What crosses instead is three members -- a staging buffer Fortran owns, the
# length, and the CAPACITY -- with the reallocating assignment performed by the
# reverse-copy. `n_<f>_cap` is the member that makes the field writable rather
# than merely readable; without it the C++ has no way to know how much room it
# has, and the branch that grows an error message cannot be translated.
#
# The generated code's RUNTIME behaviour is checked by compiling and running it
# (`tests/fixtures/deferred_char/`); these tests pin the SHAPE, which is the part
# a later refactor can break silently.
# ---------------------------------------------------------------------------

class TestDeferredLengthCharacter:
    def _type(self):
        return _make_type_info("ErrLike", [
            ("ErrStat", "INTEGER(4)"),
            ("ErrMsg", "CHARACTER(:)", True, None),
        ])

    def test_predicate_matches_only_deferred(self):
        from vit.type_map import is_deferred_character
        assert is_deferred_character("CHARACTER(:)")
        assert is_deferred_character("character(:)")
        # An assumed-LENGTH dummy is a different thing and still has a C type.
        assert not is_deferred_character("CHARACTER(*)")
        assert not is_deferred_character("CHARACTER(1024)")
        assert not is_deferred_character("REAL(8)")

    def test_header_emits_pointer_length_and_capacity(self):
        header = generate_view_struct_header(self._type(), [])
        assert "char* ErrMsg;" in header
        assert "int32_t n_ErrMsg;" in header
        assert "int32_t n_ErrMsg_cap;" in header
        # NOT the array shape: a deferred-length CHARACTER is a scalar string,
        # and `rank` does not describe it.
        assert "n_ErrMsg_rows" not in header

    def test_populator_publishes_capacity_and_does_not_reallocate_the_buffer(self):
        code = generate_view_populator(self._type(), [], None, "TestModule")
        buf = "vit_defchar_errmsg_errlike"
        assert f"CHARACTER(LEN=:), ALLOCATABLE, TARGET, SAVE :: {buf}" in code
        assert f"view%ErrMsg = C_LOC({buf}(1:1))" in code
        assert f"view%n_ErrMsg_cap = INT(LEN({buf}), C_INT32_T)" in code
        # THE REGRESSION THIS PINS. `buf = src%ErrMsg` is a whole-variable
        # assignment to a deferred-length ALLOCATABLE, so Fortran 2003
        # automatic reallocation applies and the buffer comes back out at
        # LEN(src%ErrMsg) -- discarding the headroom just allocated and making
        # the published capacity a lie in the dangerous direction. The
        # round-trip fixture measured cap=5 where 4101 had been allocated.
        # Assignment to a SUBSTRING does not reallocate.
        assert f"{buf}(1:LEN({buf})) = src%ErrMsg" in code
        assert f"\n            {buf} = src%ErrMsg" not in code

    def test_populator_distinguishes_unallocated_from_empty(self):
        code = generate_view_populator(self._type(), [], None, "TestModule")
        # P6: absence must not render as a value. A null pointer is the only
        # thing the C++ can tell apart from an allocated zero-length string,
        # both of which report n_ErrMsg == 0.
        assert "view%ErrMsg = C_NULL_PTR" in code

    def test_reverse_copy_reallocates_and_refuses_rather_than_truncating(self):
        code = generate_view_populator(self._type(), [], None, "TestModule")
        buf = "vit_defchar_errmsg_errlike"
        assert f"dest%ErrMsg = {buf}(1:view%n_ErrMsg)" in code
        assert f"IF (view%n_ErrMsg <= LEN({buf})) THEN" in code
        # An over-long request is REFUSED and reported, never truncated: a
        # shortened error message is a plausible wrong answer, which is the one
        # kind of wrong answer a bit-for-bit comparison cannot catch.
        assert "ERROR_UNIT" in code
        assert "field left unchanged" in code

    def test_buffer_name_is_qualified_by_type(self):
        """Two types with a field of the same name compile into one program."""
        a = generate_view_populator(self._type(), [], None, "TestModule")
        other = _make_type_info("OtherLike", [("ErrMsg", "CHARACTER(:)", True, None)])
        b = generate_view_populator(other, [], None, "TestModule")
        assert "vit_defchar_errmsg_errlike" in a
        assert "vit_defchar_errmsg_otherlike" in b
