! VIT conformance fixture -- what of Fortran can this toolchain actually cross?
!
! WHY THIS EXISTS. VIT's language coverage has been demand-driven, and the
! demand was one codebase's numerical core. The first ROSCO campaign translated
! 52 procedures, all REAL-typed controllers, so VIT supported REAL-typed
! controllers. The replication widened to 67 -- adding ROSCO_Helpers' string and
! IO utilities -- and 19 gaps opened in three days: CHARACTER arguments,
! CHARACTER results, CHARACTER(*) arrays, LOGICAL crossing in both directions,
! COMPLEX narrowing under a kind alias, allocate-on-return for INTENT(OUT)
! ALLOCATABLE, procedure-end nesting past an INTERFACE block.
!
! Every one of those was found by a translation failing on a real function.
! That is discovery by accident, and it does not scale to a second codebase: the
! next one will use a different corner of the language and the gaps will open
! again, one function at a time, at whatever moment they happen to be reached.
!
! This file enumerates the corners instead. One procedure per cell of
! (type x shape x intent x attribute x procedure-kind). `matrix.toml` records
! what VIT does with each, and `tests/test_conformance.py` asserts that the
! record still matches reality -- so a cell that regresses fails, and a cell
! that starts working must be acknowledged rather than drifting.
!
! NOTHING HERE NEEDS TO PASS. A cell marked `unsupported` is a documented limit,
! which is worth more than an undocumented one. The point is that the answer is
! written down before a campaign starts, not discovered at function 58.

MODULE vit_conformance_types
    IMPLICIT NONE

    INTEGER, PARAMETER :: R4 = SELECTED_REAL_KIND(6)
    INTEGER, PARAMETER :: R8 = SELECTED_REAL_KIND(12)
    INTEGER, PARAMETER :: I4 = SELECTED_INT_KIND(9)

    ! Plain: every field primitive, no allocatables. Crosses as a C struct.
    TYPE, PUBLIC :: PlainType
        REAL(R8)    :: a
        INTEGER(I4) :: n
    END TYPE PlainType

    ! Explicitly interoperable.
    TYPE, BIND(C), PUBLIC :: BindCType
        REAL(R8)    :: x
        INTEGER(I4) :: k
    END TYPE BindCType

    ! Fixed-size array field: crosses by reference, must copy back.
    TYPE, PUBLIC :: FixedArrayType
        REAL(R8) :: v(4)
    END TYPE FixedArrayType

    ! ALLOCATABLE fields: forces the decomposed bridge.
    TYPE, PUBLIC :: AllocType
        REAL(R8), ALLOCATABLE :: v(:)
        REAL(R8), ALLOCATABLE :: m(:,:)
        INTEGER(I4)           :: n
    END TYPE AllocType

    ! A nested derived-type field inside a decomposed type.
    TYPE, PUBLIC :: NestedType
        TYPE(PlainType)       :: inner
        REAL(R8), ALLOCATABLE :: v(:)
    END TYPE NestedType

    ! A CHARACTER field, fixed length.
    TYPE, PUBLIC :: CharFieldType
        CHARACTER(32)         :: name
        REAL(R8), ALLOCATABLE :: v(:)
    END TYPE CharFieldType

    ! A POINTER field: not reconstructible from a C view.
    TYPE, PUBLIC :: PointerFieldType
        REAL(R8), POINTER :: p => NULL()
    END TYPE PointerFieldType

END MODULE vit_conformance_types


MODULE vit_conformance
    USE vit_conformance_types
    IMPLICIT NONE
CONTAINS

    ! ---------------------------------------------------------------- scalars
    SUBROUTINE c_real8_in(x, y)
        REAL(R8), INTENT(IN)  :: x
        REAL(R8), INTENT(OUT) :: y
        y = x * 2.0_R8
    END SUBROUTINE c_real8_in

    SUBROUTINE c_real4_in(x, y)
        REAL(R4), INTENT(IN)  :: x
        REAL(R4), INTENT(OUT) :: y
        y = x * 2.0_R4
    END SUBROUTINE c_real4_in

    SUBROUTINE c_integer_inout(n)
        INTEGER(I4), INTENT(INOUT) :: n
        n = n + 1
    END SUBROUTINE c_integer_inout

    SUBROUTINE c_logical_in(flag, y)
        LOGICAL,  INTENT(IN)  :: flag
        REAL(R8), INTENT(OUT) :: y
        y = MERGE(1.0_R8, 0.0_R8, flag)
    END SUBROUTINE c_logical_in

    SUBROUTINE c_logical_out(x, flag)
        REAL(R8), INTENT(IN)  :: x
        LOGICAL,  INTENT(OUT) :: flag
        flag = (x > 0.0_R8)
    END SUBROUTINE c_logical_out

    SUBROUTINE c_complex_in(z, m)
        COMPLEX(R8), INTENT(IN)  :: z
        REAL(R8),    INTENT(OUT) :: m
        m = ABS(z)
    END SUBROUTINE c_complex_in

    SUBROUTINE c_complex_inout(z)
        COMPLEX(R8), INTENT(INOUT) :: z
        z = CONJG(z)
    END SUBROUTINE c_complex_inout

    ! ------------------------------------------------------------- characters
    SUBROUTINE c_character_in(s, n)
        CHARACTER(*), INTENT(IN)  :: s
        INTEGER(I4),  INTENT(OUT) :: n
        n = LEN_TRIM(s)
    END SUBROUTINE c_character_in

    SUBROUTINE c_character_inout(s)
        CHARACTER(*), INTENT(INOUT) :: s
        s = ADJUSTL(s)
    END SUBROUTINE c_character_inout

    SUBROUTINE c_character_array(words, n)
        CHARACTER(*), INTENT(IN)  :: words(:)
        INTEGER(I4),  INTENT(OUT) :: n
        n = SIZE(words)
    END SUBROUTINE c_character_array

    ! ------------------------------------------------------------------ shape
    SUBROUTINE c_assumed_shape_1d(v, s)
        REAL(R8), INTENT(IN)  :: v(:)
        REAL(R8), INTENT(OUT) :: s
        s = SUM(v)
    END SUBROUTINE c_assumed_shape_1d

    SUBROUTINE c_assumed_shape_2d(m, s)
        REAL(R8), INTENT(IN)  :: m(:,:)
        REAL(R8), INTENT(OUT) :: s
        s = SUM(m)
    END SUBROUTINE c_assumed_shape_2d

    SUBROUTINE c_assumed_size(v, n, s)
        INTEGER(I4), INTENT(IN)  :: n
        REAL(R8),    INTENT(IN)  :: v(*)
        REAL(R8),    INTENT(OUT) :: s
        INTEGER(I4) :: i
        s = 0.0_R8
        DO i = 1, n
            s = s + v(i)
        END DO
    END SUBROUTINE c_assumed_size

    SUBROUTINE c_explicit_shape(v, s)
        REAL(R8), INTENT(IN)  :: v(3)
        REAL(R8), INTENT(OUT) :: s
        s = SUM(v)
    END SUBROUTINE c_explicit_shape

    SUBROUTINE c_alloc_out(n, v)
        INTEGER(I4),           INTENT(IN)  :: n
        REAL(R8), ALLOCATABLE, INTENT(OUT) :: v(:)
        ALLOCATE(v(n))
        v = 1.0_R8
    END SUBROUTINE c_alloc_out

    SUBROUTINE c_alloc_inout(v)
        REAL(R8), ALLOCATABLE, INTENT(INOUT) :: v(:)
        IF (ALLOCATED(v)) v = v + 1.0_R8
    END SUBROUTINE c_alloc_inout

    ! ------------------------------------------------------------ derived types
    SUBROUTINE c_plain_type(t, s)
        TYPE(PlainType), INTENT(IN)  :: t
        REAL(R8),        INTENT(OUT) :: s
        s = t%a * REAL(t%n, R8)
    END SUBROUTINE c_plain_type

    SUBROUTINE c_bindc_type(t)
        TYPE(BindCType), INTENT(INOUT) :: t
        t%x = t%x + 1.0_R8
    END SUBROUTINE c_bindc_type

    SUBROUTINE c_fixed_array_field(t)
        TYPE(FixedArrayType), INTENT(INOUT) :: t
        t%v = t%v * 2.0_R8
    END SUBROUTINE c_fixed_array_field

    SUBROUTINE c_alloc_field(t, s)
        TYPE(AllocType), INTENT(INOUT) :: t
        REAL(R8),        INTENT(OUT)   :: s
        s = 0.0_R8
        IF (ALLOCATED(t%v)) s = SUM(t%v)
    END SUBROUTINE c_alloc_field

    SUBROUTINE c_nested_field(t, s)
        TYPE(NestedType), INTENT(INOUT) :: t
        REAL(R8),         INTENT(OUT)   :: s
        s = t%inner%a
    END SUBROUTINE c_nested_field

    SUBROUTINE c_char_field(t, n)
        TYPE(CharFieldType), INTENT(INOUT) :: t
        INTEGER(I4),         INTENT(OUT)   :: n
        n = LEN_TRIM(t%name)
    END SUBROUTINE c_char_field

    SUBROUTINE c_pointer_field(t, s)
        TYPE(PointerFieldType), INTENT(IN)  :: t
        REAL(R8),               INTENT(OUT) :: s
        s = 0.0_R8
        IF (ASSOCIATED(t%p)) s = t%p
    END SUBROUTINE c_pointer_field

    ! ------------------------------------------------------------- attributes
    SUBROUTINE c_optional_arg(x, y, extra)
        REAL(R8), INTENT(IN)           :: x
        REAL(R8), INTENT(OUT)          :: y
        REAL(R8), INTENT(IN), OPTIONAL :: extra
        y = x
        IF (PRESENT(extra)) y = y + extra
    END SUBROUTINE c_optional_arg

    SUBROUTINE c_optional_array(v, s, w)
        REAL(R8), INTENT(IN)           :: v(:)
        REAL(R8), INTENT(OUT)          :: s
        REAL(R8), INTENT(IN), OPTIONAL :: w(:)
        s = SUM(v)
        IF (PRESENT(w)) s = s + SUM(w)
    END SUBROUTINE c_optional_array

    SUBROUTINE c_value_arg(x, y)
        REAL(R8), VALUE       :: x
        REAL(R8), INTENT(OUT) :: y
        x = x + 1.0_R8
        y = x
    END SUBROUTINE c_value_arg

    ! -------------------------------------------------------------- functions
    REAL(R8) FUNCTION c_fn_scalar(x)
        REAL(R8), INTENT(IN) :: x
        c_fn_scalar = x * 3.0_R8
    END FUNCTION c_fn_scalar

    LOGICAL FUNCTION c_fn_logical(x)
        REAL(R8), INTENT(IN) :: x
        c_fn_logical = (x > 0.0_R8)
    END FUNCTION c_fn_logical

    FUNCTION c_fn_array(v) RESULT(r)
        REAL(R8), INTENT(IN) :: v(:)
        REAL(R8)             :: r(SIZE(v))
        r = v * 2.0_R8
    END FUNCTION c_fn_array

    FUNCTION c_fn_character(n) RESULT(s)
        INTEGER(I4), INTENT(IN) :: n
        CHARACTER(16)           :: s
        WRITE(s, '(I0)') n
    END FUNCTION c_fn_character

    FUNCTION c_fn_alloc_character(n) RESULT(s)
        INTEGER(I4), INTENT(IN)   :: n
        CHARACTER(:), ALLOCATABLE :: s
        ALLOCATE(CHARACTER(4) :: s)
        WRITE(s, '(I4)') n
    END FUNCTION c_fn_alloc_character

    ! --------------------------------------------------- procedure attributes
    RECURSIVE FUNCTION c_fn_recursive(n) RESULT(r)
        INTEGER(I4), INTENT(IN) :: n
        INTEGER(I4)             :: r
        IF (n <= 1) THEN
            r = 1
        ELSE
            r = n * c_fn_recursive(n - 1)
        END IF
    END FUNCTION c_fn_recursive

    PURE SUBROUTINE c_pure(x, y)
        REAL(R8), INTENT(IN)  :: x
        REAL(R8), INTENT(OUT) :: y
        y = x + 1.0_R8
    END SUBROUTINE c_pure

    ELEMENTAL FUNCTION c_elemental(x) RESULT(r)
        REAL(R8), INTENT(IN) :: x
        REAL(R8)             :: r
        r = x * x
    END FUNCTION c_elemental

    ! ------------------------------------------------------ parsing hazards
    ! A procedure containing an INTERFACE block: the naive "first END
    ! SUBROUTINE" scan stops inside it and orphans the real body.
    SUBROUTINE c_with_interface(x, y)
        REAL(R8), INTENT(IN)  :: x
        REAL(R8), INTENT(OUT) :: y
        INTERFACE
            SUBROUTINE some_external(a)
                IMPORT :: R8
                REAL(R8), INTENT(IN) :: a
            END SUBROUTINE some_external
        END INTERFACE
        y = x
    END SUBROUTINE c_with_interface

    ! A call site inside a BLOCK construct.
    SUBROUTINE c_with_block(x, y)
        REAL(R8), INTENT(IN)  :: x
        REAL(R8), INTENT(OUT) :: y
        BLOCK
            REAL(R8) :: tmp
            CALL c_real8_in(x, tmp)
            y = tmp
        END BLOCK
    END SUBROUTINE c_with_block

    ! ------------------------------------------- implicit external state
    ! An INTEGER that is a HANDLE, not a value: a Fortran unit number. The
    ! signature crosses perfectly -- it is just an int -- and the thing the
    ! procedure actually reads crosses not at all. C++ cannot READ from a
    ! Fortran unit, so no mirror translation exists, and a differential harness
    ! is unsound besides: calling one implementation and then the other on the
    ! same unit advances the file position, so the second call reads different
    ! data. Same shape as sharing a mutable struct between the two sides, except
    ! the state lives in the Fortran runtime and cannot be copied.
    !
    ! ROSCO's ParseInput_Int is exactly this, and ParseInput_Int_Opt is the same
    ! job with the file already read into a CHARACTER array -- pure, and
    ! directly harnessable. A codebase offering both shapes is telling you which
    ! one to translate.
    SUBROUTINE c_unit_read(unit, n)
        INTEGER(I4), INTENT(IN)  :: unit
        INTEGER(I4), INTENT(OUT) :: n
        READ(unit, *) n
    END SUBROUTINE c_unit_read

    SUBROUTINE c_unit_write(unit, n)
        INTEGER(I4), INTENT(IN) :: unit
        INTEGER(I4), INTENT(IN) :: n
        WRITE(unit, *) n
    END SUBROUTINE c_unit_write

    ! A procedure containing an internal one, via CONTAINS.
    SUBROUTINE c_with_contains(x, y)
        REAL(R8), INTENT(IN)  :: x
        REAL(R8), INTENT(OUT) :: y
        y = helper(x)
    CONTAINS
        REAL(R8) FUNCTION helper(a)
            REAL(R8), INTENT(IN) :: a
            helper = a + 1.0_R8
        END FUNCTION helper
    END SUBROUTINE c_with_contains

END MODULE vit_conformance
