# The conformance matrix measured the harness generator, not the one that ships

**Date:** 2026-08-10
**Status:** completed
**Scope:** interface_gen, translate, tests/conformance

## Background

The `rosco-r2` replication dispatched `AddToList` (`ROSCO_Helpers.f90:1629`)
with a prediction from its plan: the signature cannot cross the C bridge, basis

    list: c_alloc_inout does not cross
    (UNSUPPORTED: as c_alloc_out. INTENT(INOUT) ALLOCATABLE needs an ALLOCA...)

which is this repository's `tests/conformance/matrix.toml`, cell
`[c_alloc_inout]`, `compiles = "no"`.

The verdict held. The basis was about a different generator, and finding that
out took measuring what the shipped path actually does.

## What was measured

`AddToList` is:

```fortran
subroutine AddToList(list, element)
      Integer(IntKi), intent(in) :: element
      Integer(IntKi), dimension(:), allocatable, intent(inout) :: list
      ...
          allocate(clist(isize+1)); clist(1:isize) = list; clist(isize+1) = element
          deallocate(list); call move_alloc(clist, list)
```

`fortran_parser` records `is_allocatable=True`. `vit interface` then emitted:

```fortran
    INTERFACE
        SUBROUTINE addtolist_c(list, n_list, element) BIND(C, NAME='addtolist_c')
            INTEGER(C_INT), INTENT(INOUT) :: list(*)
            INTEGER(C_INT), VALUE :: n_list
            INTEGER(C_INT), VALUE :: element
        END SUBROUTINE addtolist_c
    END INTERFACE

    SUBROUTINE AddToList(list, element)
        INTEGER(4), INTENT(INOUT) :: list(:)     ! <-- ALLOCATABLE dropped
        INTEGER(4), INTENT(IN) :: element
        CALL addtolist_c(list, SIZE(list), element)
    END SUBROUTINE AddToList
```

`generate_fortran_wrapper` attaches `, ALLOCATABLE` only when `is_alloc_return`,
so INTENT(INOUT) lost it silently. The wrapper compiles. `move_alloc` — which
replaces the caller's data pointer AND its extent — has no representation in
that call: `list` is a bare pointer and `n_list` is a copy.

Three implementations were built against one driver on the live call pattern
(both ROSCO call sites `ALLOCATE` first, then append):

```
orig  A1 size=5 ...91   A2 size=6 ...92   A3 size=7 ...17    (the oracle)
vit   A1 size=4         A2 size=4         A3 size=4
cfi   A1 size=5 ...91   A2 size=6 ...92   A3 size=7 ...17
```

The generated bridge's array never grows and the appended values land one past
the end of the caller's heap block. No diagnostic from gfortran, from g++, or at
run time. **A wrapper that compiles and does nothing is worse than one that
does not compile** — the second is caught by the build.

`cfi` is a hand-written Fortran 2018 `BIND(C)` interface taking the
`ALLOCATABLE` dummy directly, with a C++ body using `CFI_allocate` /
`CFI_deallocate` on the `CFI_cdesc_t *` it receives. It reproduces the oracle
byte for byte on both branches, including `move_alloc`'s reset of the lower
bound, under gfortran 13.3 / g++ 13.3. **The limit is ours, not Fortran's.**

## Why the matrix said `no` for the wrong reason

`tests/test_conformance.py:probe()` fed `bridge` and `compiles` from
`test_validate.generate_fortran_bridge` — the differential harness's Fortran
side, linked into a test binary and never into the library. For `AddToList` it
emits `CALL AddToList(list(1:n_list), element)` and gfortran rejects it:

    Error: Actual argument for 'list' must be ALLOCATABLE at (1)

That is the whole of `compiles = "no"`. The matrix never called
`generate_fortran_interface_block` or `generate_fortran_wrapper`. Any cell whose
harness bridge happened to compile would have read `yes`, and a campaign reading
the matrix as a feasibility verdict would have integrated a no-op.

## Changes

1. **`interface_gen.assert_integration_bridgeable`** raises
   `UnbridgeableSignature` for an `ALLOCATABLE, INTENT(INOUT)` dummy that is not
   `is_alloc_return`. Called from `generate_fortran_interface_block`,
   `generate_fortran_wrapper`, and `translate.generate_translation_scaffold` —
   `vit translate` is the earliest point a wasted C++ body is avoidable.
   `vit translate` / `interface` / `integrate` now exit 1 and write nothing.

   Only INTENT(INOUT) is refused. INTENT(IN) `ALLOCATABLE` has the same dropped
   attribute and the same undefined `SIZE()` on an unallocated actual, but no
   campaign has hit one and inventing a verdict for an unmeasured cell is the
   habit this refusal exists to break.

2. **An `integrates` column** in the conformance matrix, measuring the two
   integration generators and compiling their output in a synthetic module
   shaped the way `vit integrate` writes them (interface above `CONTAINS`,
   wrapper below). `_unsupported` counts it, `write_matrix` records it,
   `summary()` reports how many cells the two columns disagree about.

3. Three probe defects fixed while building it, each of which would have made
   the new column measure the test rather than VIT:
   - the synthetic module did not `USE vit_conformance_types` (7 cells);
   - the generated view-populator modules did not exist, so three view-strategy
     cells failed on a missing `.mod` (`_fixture_mods` now emits and compiles
     them);
   - `_compiles` matched only `Error:` and not `Fatal Error:`, so a missing
     module reported `compilation terminated.` — a diagnostic naming no cause.

## What the new column found immediately

`c_complex_in`. Every column that existed before called it supported; its
integration wrapper emits

    CALL c_complex_in_c(COMPLEX(z, C_DOUBLE_COMPLEX), m)

for a dummy `z` that is already `COMPLEX`, and gfortran rejects it. Not fixed
here — recorded, with a stated reason, which is what the matrix is for.

Five of 39 cells now disagree between the harness column and the integration
column: `c_alloc_inout`, `c_alloc_out`, `c_assumed_shape_2d`, `c_fn_array`
(harness fails, integration compiles) and `c_complex_in` (the reverse).

## Red tests

- Disabling the refusal fails the three new `test_interface_gen.py` tests and
  `test_conformance.py::test_cell_matches_the_record[c_alloc_inout]`; restoring
  it turns them green.
- Corrupting `c_complex_in.integrates` in `matrix.toml` fails the matrix test;
  restoring it passes.
- Suite: 930 passed.

## Next

The CFI bridge is demonstrated but not generated. Implementing it means three
generators, not one — `interface_gen`, `test_validate.generate_fortran_bridge`,
and the translation-loop's `vitbridge` — or a unit whose signature crosses still
cannot produce a differential harness or a mutation score. It would unblock
`AddToList`, `Read_OL_Input`, `ParseInAry_Opt` and `ParseDbAry_Opt` in the
rosco-r2 plan.
