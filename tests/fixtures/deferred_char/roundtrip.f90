PROGRAM roundtrip
    USE ISO_C_BINDING
    USE errlike_types, ONLY: ErrLike
    USE vit_errlike_view
    IMPLICIT NONE

    INTERFACE
        SUBROUTINE c_read(v, l, c, first, n) BIND(C, name="c_read")
            IMPORT :: errlike_view_t, C_INT32_T, C_CHAR
            TYPE(errlike_view_t), INTENT(INOUT) :: v
            INTEGER(C_INT32_T), INTENT(OUT) :: l, c
            CHARACTER(KIND=C_CHAR), INTENT(OUT) :: first(*)
            INTEGER(C_INT32_T), VALUE :: n
        END SUBROUTINE
        SUBROUTINE c_grow(v, p, plen) BIND(C, name="c_grow")
            IMPORT :: errlike_view_t, C_INT32_T, C_CHAR
            TYPE(errlike_view_t), INTENT(INOUT) :: v
            CHARACTER(KIND=C_CHAR), INTENT(IN) :: p(*)
            INTEGER(C_INT32_T), VALUE :: plen
        END SUBROUTINE
        SUBROUTINE c_shrink(v, s, n) BIND(C, name="c_shrink")
            IMPORT :: errlike_view_t, C_INT32_T, C_CHAR
            TYPE(errlike_view_t), INTENT(INOUT) :: v
            CHARACTER(KIND=C_CHAR), INTENT(IN) :: s(*)
            INTEGER(C_INT32_T), VALUE :: n
        END SUBROUTINE
        SUBROUTINE c_overrun(v) BIND(C, name="c_overrun")
            IMPORT :: errlike_view_t
            TYPE(errlike_view_t), INTENT(INOUT) :: v
        END SUBROUTINE
        INTEGER(C_INT) FUNCTION c_is_null(v) BIND(C, name="c_is_null")
            IMPORT :: errlike_view_t, C_INT
            TYPE(errlike_view_t), INTENT(INOUT) :: v
        END FUNCTION
    END INTERFACE

    TYPE(ErrLike) :: e
    TYPE(errlike_view_t) :: v
    INTEGER(C_INT32_T) :: l, c
    CHARACTER(KIND=C_CHAR) :: first(8)
    INTEGER :: fails

    fails = 0

    ! --- 1. READ: length and capacity reach the C side, contents blank-padded.
    e%ErrMsg = 'hello'
    CALL vit_populate_errlike(e, v)
    CALL c_read(v, l, c, first, 8_C_INT32_T)
    CALL check('read len', l == 5, l, 5)
    CALL check('read cap', c == 5 + 4096, c, 5 + 4096)
    CALL check('read byte 1', first(1) == 'h', 0, 0)
    CALL check('read byte 6 is blank', first(6) == ' ', 0, 0)

    ! --- 2. GROW: the C side writes a LONGER string; the field reallocates.
    e%ErrMsg = 'boom'
    CALL vit_populate_errlike(e, v)
    CALL c_grow(v, 'ExtController', 13_C_INT32_T)
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check('grow len', LEN(e%ErrMsg) == 18, LEN(e%ErrMsg), 18)
    CALL check('grow text', e%ErrMsg == 'ExtController:boom', 0, 0)

    ! --- 3. SHRINK: a shorter assignment reallocates DOWN, as Fortran does.
    e%ErrMsg = 'a long message here'
    CALL vit_populate_errlike(e, v)
    CALL c_shrink(v, 'xy', 2_C_INT32_T)
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check('shrink len', LEN(e%ErrMsg) == 2, LEN(e%ErrMsg), 2)
    CALL check('shrink text', e%ErrMsg == 'xy', 0, 0)

    ! --- 4. OVERRUN: refused, and the field is LEFT ALONE, not truncated.
    e%ErrMsg = 'keep me'
    CALL vit_populate_errlike(e, v)
    CALL c_overrun(v)
    WRITE(*,'(A)') 'expect one VIT refusal on stderr:'
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check('overrun len unchanged', LEN(e%ErrMsg) == 7, LEN(e%ErrMsg), 7)
    CALL check('overrun text unchanged', e%ErrMsg == 'keep me', 0, 0)

    ! --- 5. UNALLOCATED renders as a NULL pointer, not as an empty string.
    IF (ALLOCATED(e%ErrMsg)) DEALLOCATE(e%ErrMsg)
    CALL vit_populate_errlike(e, v)
    CALL check('unallocated -> null', c_is_null(v) == 1, 0, 0)
    CALL check('unallocated -> len 0', v%n_ErrMsg == 0, v%n_ErrMsg, 0)
    CALL check('unallocated -> cap 0', v%n_ErrMsg_cap == 0, v%n_ErrMsg_cap, 0)

    ! --- 6. And an ALLOCATED EMPTY string is a DIFFERENT input: non-null.
    e%ErrMsg = ''
    CALL vit_populate_errlike(e, v)
    CALL check('empty -> non-null', c_is_null(v) == 0, 0, 0)
    CALL check('empty -> len 0', v%n_ErrMsg == 0, v%n_ErrMsg, 0)

    IF (fails == 0) THEN
        WRITE(*,'(A)') 'ROUNDTRIP OK'
    ELSE
        WRITE(*,'(A,I0,A)') 'ROUNDTRIP FAILED: ', fails, ' assertion(s)'
        STOP 1
    END IF

CONTAINS

    SUBROUTINE check(what, ok, got, want)
        CHARACTER(*), INTENT(IN) :: what
        LOGICAL, INTENT(IN) :: ok
        INTEGER, INTENT(IN) :: got, want
        IF (ok) THEN
            WRITE(*,'(A,A)') '  ok   ', what
        ELSE
            WRITE(*,'(A,A,A,I0,A,I0)') ' FAIL ', what, '  got=', got, ' want=', want
            fails = fails + 1
        END IF
    END SUBROUTINE

END PROGRAM roundtrip
