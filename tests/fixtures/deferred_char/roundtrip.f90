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
        SUBROUTINE c_assign(v, s, n) BIND(C, name="c_assign")
            IMPORT :: errlike_view_t, C_INT32_T, C_CHAR
            TYPE(errlike_view_t), INTENT(INOUT) :: v
            CHARACTER(KIND=C_CHAR), INTENT(IN) :: s(*)
            INTEGER(C_INT32_T), VALUE :: n
        END SUBROUTINE
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
    CALL check_true('read byte 1', first(1) == 'h')
    CALL check_true('read byte 6 is blank', first(6) == ' ')

    ! --- 2. GROW: the C side writes a LONGER string; the field reallocates.
    e%ErrMsg = 'boom'
    CALL vit_populate_errlike(e, v)
    CALL c_grow(v, 'ExtController', 13_C_INT32_T)
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check('grow len', LEN(e%ErrMsg) == 18, LEN(e%ErrMsg), 18)
    CALL check_true('grow text', e%ErrMsg == 'ExtController:boom')

    ! --- 3. SHRINK: a shorter assignment reallocates DOWN, as Fortran does.
    e%ErrMsg = 'a long message here'
    CALL vit_populate_errlike(e, v)
    CALL c_shrink(v, 'xy', 2_C_INT32_T)
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check('shrink len', LEN(e%ErrMsg) == 2, LEN(e%ErrMsg), 2)
    CALL check_true('shrink text', e%ErrMsg == 'xy')

    ! --- 4. OVERRUN: refused, and the field is LEFT ALONE, not truncated.
    e%ErrMsg = 'keep me'
    CALL vit_populate_errlike(e, v)
    CALL c_overrun(v)
    WRITE(*,'(A)') 'expect one VIT refusal on stderr:'
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check('overrun len unchanged', LEN(e%ErrMsg) == 7, LEN(e%ErrMsg), 7)
    CALL check_true('overrun text unchanged', e%ErrMsg == 'keep me')

    ! --- 5. UNALLOCATED renders as LENGTH -1, and the buffer is still there.
    !        Absence moved off the pointer because the pointer had to become
    !        non-null for case 7 to be possible at all.
    IF (ALLOCATED(e%ErrMsg)) DEALLOCATE(e%ErrMsg)
    CALL vit_populate_errlike(e, v)
    CALL check('unallocated -> len -1', v%n_ErrMsg == -1, v%n_ErrMsg, -1)
    CALL check_true('unallocated -> non-null buffer', c_is_null(v) == 0)
    CALL check_true('unallocated -> writable cap', v%n_ErrMsg_cap > 0)
    ! -1 must also survive the trip back: the reverse copy has to leave the
    ! field UNALLOCATED rather than materialise a zero-length string, or the
    ! distinction is preserved on the way in and destroyed on the way out.
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check_true('unallocated survives round trip', .NOT. ALLOCATED(e%ErrMsg))

    ! --- 6. An ALLOCATED EMPTY string is a DIFFERENT input: length 0, not -1.
    !        This is the pair the encoding exists to keep apart.
    e%ErrMsg = ''
    CALL vit_populate_errlike(e, v)
    CALL check('empty -> len 0', v%n_ErrMsg == 0, v%n_ErrMsg, 0)
    CALL check_true('empty -> non-null buffer', c_is_null(v) == 0)
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check_true('empty stays allocated round trip', ALLOCATED(e%ErrMsg))

    ! --- 7. ASSIGN INTO AN UNALLOCATED FIELD. Fortran permits it, so the C++
    !        must be able to perform it. This is the case the old NULL-pointer
    !        encoding made impossible and the reason it was given up.
    IF (ALLOCATED(e%ErrMsg)) DEALLOCATE(e%ErrMsg)
    CALL vit_populate_errlike(e, v)
    CALL c_assign(v, 'from unalloc', 12_C_INT32_T)
    CALL vit_copy_scalars_to_errlike(v, e)
    CALL check_true('assign to unallocated -> allocated', ALLOCATED(e%ErrMsg))
    IF (ALLOCATED(e%ErrMsg)) THEN
        CALL check('assign to unallocated -> len', LEN(e%ErrMsg) == 12, &
                   LEN(e%ErrMsg), 12)
        CALL check_true('assign to unallocated -> text', e%ErrMsg == 'from unalloc')
    END IF

    IF (fails == 0) THEN
        WRITE(*,'(A)') 'ROUNDTRIP OK'
    ELSE
        WRITE(*,'(A,I0,A)') 'ROUNDTRIP FAILED: ', fails, ' assertion(s)'
        STOP 1
    END IF

CONTAINS

    SUBROUTINE check_true(what, ok)
        CHARACTER(*), INTENT(IN) :: what
        LOGICAL, INTENT(IN) :: ok
        ! `check` below takes got/want to print on failure. Every LOGICAL
        ! assertion here used to pass literal 0, 0 for them, so a real failure
        ! printed `got=0 want=0` -- two values that were never read, rendered as
        ! though they had been measured. Absence of a number is not the number
        ! zero; this reporter prints nothing rather than something false.
        IF (ok) THEN
            WRITE(*,'(A,A)') '  ok   ', what
        ELSE
            WRITE(*,'(A,A)') ' FAIL ', what
            fails = fails + 1
        END IF
    END SUBROUTINE

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
