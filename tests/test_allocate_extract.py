"""Tests for ALLOCATE extraction from Fortran source."""

import pytest

from vit.allocate_extract import extract_allocations, AllocationInfo


SIMPLE_SOURCE = """
MODULE TestMod
CONTAINS
    SUBROUTINE ReadData(Input, Output, ErrVar)
        TYPE(InputType), INTENT(IN) :: Input
        TYPE(OutputType), INTENT(INOUT) :: Output
        TYPE(ErrorType), INTENT(INOUT) :: ErrVar

        INTEGER :: localN

        ! Direct ALLOCATE — safe (different args)
        ALLOCATE(Output%data(Input%n_rows, Input%n_cols))
        Output%data = 0.0

        ! Direct ALLOCATE — safe (literal size)
        ALLOCATE(Output%flags(3))

    END SUBROUTINE ReadData
END MODULE TestMod
"""

SAME_ARG_SOURCE = """
MODULE TestMod
CONTAINS
    SUBROUTINE ParseConfig(Config, ErrVar)
        TYPE(ConfigType), INTENT(INOUT) :: Config
        TYPE(ErrorType), INTENT(INOUT) :: ErrVar

        ! Same-arg: size from Config, target is Config
        ALLOCATE(Config%values(Config%n_values))
        Config%values = 0.0

    END SUBROUTINE ParseConfig
END MODULE TestMod
"""

LOCAL_VAR_SOURCE = """
MODULE TestMod
CONTAINS
    SUBROUTINE ProcessFile(Data, ErrVar)
        TYPE(DataType), INTENT(INOUT) :: Data
        TYPE(ErrorType), INTENT(INOUT) :: ErrVar

        INTEGER :: numLines
        CHARACTER(256), ALLOCATABLE :: lines(:)

        numLines = 100
        ALLOCATE(lines(numLines))

        ! Size from local variable
        ALLOCATE(Data%results(numLines))

    END SUBROUTINE ProcessFile
END MODULE TestMod
"""

HELPER_SOURCE = """
MODULE TestMod
CONTAINS
    SUBROUTINE LoadArrays(Source, Dest, ErrVar)
        TYPE(SourceType), INTENT(IN) :: Source
        TYPE(DestType), INTENT(INOUT) :: Dest
        TYPE(ErrorType), INTENT(INOUT) :: ErrVar

        ! Helper call — safe (different args)
        CALL ParseAry(lines, 'data', Dest%values, Source%n_values, filename, ErrVar, .FALSE.)

        ! Helper call — same arg
        CALL ParseAry(lines, 'sizes', Source%items, Source%n_items, filename, ErrVar, .FALSE.)

    END SUBROUTINE LoadArrays
END MODULE TestMod
"""

STAT_SOURCE = """
MODULE TestMod
CONTAINS
    SUBROUTINE AllocWithStat(Input, Output, ErrVar)
        TYPE(InputType), INTENT(IN) :: Input
        TYPE(OutputType), INTENT(INOUT) :: Output
        TYPE(ErrorType), INTENT(INOUT) :: ErrVar
        INTEGER :: ierr

        ALLOCATE(Output%data(Input%size), STAT=ierr)

    END SUBROUTINE AllocWithStat
END MODULE TestMod
"""

CONTINUATION_SOURCE = """
MODULE TestMod
CONTAINS
    SUBROUTINE AllocContinuation(Input, Output, ErrVar)
        TYPE(InputType), INTENT(IN) :: Input
        TYPE(OutputType), INTENT(INOUT) :: Output
        TYPE(ErrorType), INTENT(INOUT) :: ErrVar

        ALLOCATE(Output%matrix( &
            Input%n_rows, &
            Input%n_cols))

    END SUBROUTINE AllocContinuation
END MODULE TestMod
"""


class TestDirectAllocates:
    """Test extraction of direct ALLOCATE statements."""

    def test_safe_different_args(self):
        results = extract_allocations(
            "ReadData", SIMPLE_SOURCE,
            ["Input", "Output", "ErrVar"], []
        )
        safe = [r for r in results if r.field_name == "data"]
        assert len(safe) == 1
        assert safe[0].category == "safe"
        assert safe[0].target_arg == "Output"
        assert len(safe[0].size_exprs) == 2
        assert "Input%n_rows" in safe[0].size_exprs[0]
        assert "Input%n_cols" in safe[0].size_exprs[1]

    def test_safe_literal_size(self):
        results = extract_allocations(
            "ReadData", SIMPLE_SOURCE,
            ["Input", "Output", "ErrVar"], []
        )
        flags = [r for r in results if r.field_name == "flags"]
        assert len(flags) == 1
        assert flags[0].category == "safe"
        assert flags[0].size_exprs == ["3"]

    def test_same_arg(self):
        results = extract_allocations(
            "ParseConfig", SAME_ARG_SOURCE,
            ["Config", "ErrVar"], []
        )
        assert len(results) == 1
        assert results[0].category == "same_arg"
        assert results[0].target_arg == "Config"
        assert "Config%n_values" in results[0].size_exprs[0]

    def test_local_var_size(self):
        results = extract_allocations(
            "ProcessFile", LOCAL_VAR_SOURCE,
            ["Data", "ErrVar"], []
        )
        # lines(numLines) should be skipped (local variable, not arg%field)
        # Data%results(numLines) should be found with category "local"
        data_results = [r for r in results if r.field_name == "results"]
        assert len(data_results) == 1
        assert data_results[0].category == "local"

    def test_skips_local_variables(self):
        results = extract_allocations(
            "ProcessFile", LOCAL_VAR_SOURCE,
            ["Data", "ErrVar"], []
        )
        # lines(numLines) should NOT be extracted (lines is not an argument field)
        local_allocs = [r for r in results if r.field_name == "lines"]
        assert len(local_allocs) == 0

    def test_stat_clause_handled(self):
        results = extract_allocations(
            "AllocWithStat", STAT_SOURCE,
            ["Input", "Output", "ErrVar"], []
        )
        assert len(results) == 1
        assert results[0].field_name == "data"
        assert results[0].category == "safe"
        assert "Input%size" in results[0].size_exprs[0]

    def test_continuation_lines(self):
        results = extract_allocations(
            "AllocContinuation", CONTINUATION_SOURCE,
            ["Input", "Output", "ErrVar"], []
        )
        assert len(results) == 1
        assert results[0].field_name == "matrix"
        assert len(results[0].size_exprs) == 2


class TestHelperAllocates:
    """Test extraction of helper-mediated allocations."""

    def test_helper_safe_different_args(self):
        helpers = [{"name": "ParseAry", "array_arg": 3, "size_arg": 4}]
        results = extract_allocations(
            "LoadArrays", HELPER_SOURCE,
            ["Source", "Dest", "ErrVar"], helpers
        )
        safe = [r for r in results if r.field_name == "values"]
        assert len(safe) == 1
        assert safe[0].category == "safe"
        assert safe[0].target_arg == "Dest"
        assert "Source%n_values" in safe[0].size_exprs[0]
        assert safe[0].source == "helper:ParseAry"

    def test_helper_same_arg(self):
        helpers = [{"name": "ParseAry", "array_arg": 3, "size_arg": 4}]
        results = extract_allocations(
            "LoadArrays", HELPER_SOURCE,
            ["Source", "Dest", "ErrVar"], helpers
        )
        same = [r for r in results if r.field_name == "items"]
        assert len(same) == 1
        assert same[0].category == "same_arg"
        assert same[0].target_arg == "Source"


class TestEdgeCases:
    """Test edge cases."""

    def test_function_not_found(self):
        results = extract_allocations(
            "NonExistent", SIMPLE_SOURCE,
            ["Input", "Output"], []
        )
        assert results == []

    def test_no_allocates(self):
        source = """
MODULE TestMod
CONTAINS
    SUBROUTINE DoNothing(A, B)
        TYPE(TypeA), INTENT(IN) :: A
        TYPE(TypeB), INTENT(INOUT) :: B
        B%x = A%y
    END SUBROUTINE DoNothing
END MODULE TestMod
"""
        results = extract_allocations("DoNothing", source, ["A", "B"], [])
        assert results == []

    def test_case_insensitive(self):
        source = """
module testmod
contains
    subroutine myFunc(inp, out, err)
        type(InType), intent(in) :: inp
        type(OutType), intent(inout) :: out
        type(ErrType), intent(inout) :: err
        allocate(out%arr(inp%n))
    end subroutine myFunc
end module testmod
"""
        results = extract_allocations("myFunc", source, ["inp", "out", "err"], [])
        assert len(results) == 1
        assert results[0].category == "safe"

    def test_commented_allocate_skipped(self):
        source = """
MODULE TestMod
CONTAINS
    SUBROUTINE Func(A, B)
        TYPE(TypeA), INTENT(IN) :: A
        TYPE(TypeB), INTENT(INOUT) :: B
        ! ALLOCATE(B%data(A%n))
        B%x = 1
    END SUBROUTINE Func
END MODULE TestMod
"""
        results = extract_allocations("Func", source, ["A", "B"], [])
        assert results == []
