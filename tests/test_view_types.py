"""Tests for Phase 4D view type infrastructure.

Tests the ALLOCATABLE size scanner, view struct header generation,
populator generation, type registry with view strategies, and
interface generation with view types.
"""

import pytest
from vit.analyze_types import (
    AllocatableFieldMapping,
    scan_allocatable_sizes,
    _classify_size_expr,
    _split_dims_aware,
)
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

    def test_unmapped_commented_out(self):
        ti = _make_type_info("TestType", [("orphan", "REAL(8)", True, ":")])
        header = generate_view_struct_header(ti, [])  # no mappings
        assert "orphan: unmapped" in header

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
        assert "C_LOC(src%arr(1))" in code
        assert "SIZE(src%arr)" in code

    def test_2d_cloc(self):
        mapping = _make_mapping("mat", "REAL(8)", 2, "E", ["N", "M"])
        code = self._generate([("mat", "REAL(8)", True, ":,:")], [mapping])
        assert "C_LOC(src%mat(1,1))" in code
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
