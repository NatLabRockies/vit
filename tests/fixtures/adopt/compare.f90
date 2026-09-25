! Drives the original (aor_ref) and the translated path (aor_wrap: VIT's
! generated wrapper around the C++) on the same inputs and compares every
! observable: allocation status, bounds, values bit-for-bit, scalars.
PROGRAM compare
    USE aor_types
    USE aor_ref,  ONLY: ref_init => init_par, ref_grow => grow_par
    USE aor_wrap, ONLY: new_init => init_par, new_grow => grow_par
    IMPLICIT NONE
    TYPE(ParT) :: a, b
    INTEGER :: nbad, fail, n
    REAL(R8), POINTER :: keep_before(:)
    nbad = 0
    DO fail = 0, 2
        DO n = 1, 3
            CALL ref_init(n, fail, a)
            CALL new_init(n, fail, b)
            CALL check_par(a, b, 'init', fail, n)
        END DO
    END DO
    ! INOUT: start both from the same populated state, add a KEEP array the
    ! C++ never touches -- the use-after-free case for a copy-back that
    ! deallocates components it cannot tell were replaced.
    CALL ref_init(2, 0, a)
    CALL new_init(2, 0, b)
    ALLOCATE(a%keep(0:4)); a%keep = [1.0_R8, 2.0_R8, 3.0_R8, 4.0_R8, 5.0_R8]
    ALLOCATE(b%keep(0:4)); b%keep = a%keep
    keep_before => NULL()
    CALL ref_grow(a)
    CALL new_grow(b)
    CALL check_par(a, b, 'grow', 0, 2)
    IF (nbad == 0) THEN
        PRINT '(A)', 'ADOPT ROUNDTRIP: ALL IDENTICAL'
    ELSE
        PRINT '(A,I0)', 'ADOPT ROUNDTRIP: MISMATCHES = ', nbad
        STOP 1
    END IF
CONTAINS
    SUBROUTINE bad(msg, what, fail, n)
        CHARACTER(*), INTENT(IN) :: msg, what
        INTEGER, INTENT(IN) :: fail, n
        PRINT '(A,A,A,A,A,I0,A,I0)', 'MISMATCH ', what, ' ', msg, ' fail=', fail, ' n=', n
        nbad = nbad + 1
    END SUBROUTINE bad

    SUBROUTINE cmp1(x, y, nm, what, fail, n)
        REAL(R8), ALLOCATABLE, INTENT(IN) :: x(:), y(:)
        CHARACTER(*), INTENT(IN) :: nm, what
        INTEGER, INTENT(IN) :: fail, n
        IF (ALLOCATED(x) .NEQV. ALLOCATED(y)) THEN
            CALL bad(nm//' allocation status', what, fail, n); RETURN
        END IF
        IF (.NOT. ALLOCATED(x)) RETURN
        IF (ANY(LBOUND(x) /= LBOUND(y)) .OR. ANY(UBOUND(x) /= UBOUND(y))) THEN
            CALL bad(nm//' bounds', what, fail, n); RETURN
        END IF
        IF (ANY(TRANSFER(x, 0_8, SIZE(x)) /= TRANSFER(y, 0_8, SIZE(y)))) CALL bad(nm//' values', what, fail, n)
    END SUBROUTINE cmp1

    SUBROUTINE cmp2(x, y, nm, what, fail, n)
        REAL(R8), ALLOCATABLE, INTENT(IN) :: x(:,:), y(:,:)
        CHARACTER(*), INTENT(IN) :: nm, what
        INTEGER, INTENT(IN) :: fail, n
        IF (ALLOCATED(x) .NEQV. ALLOCATED(y)) THEN
            CALL bad(nm//' allocation status', what, fail, n); RETURN
        END IF
        IF (.NOT. ALLOCATED(x)) RETURN
        IF (ANY(LBOUND(x) /= LBOUND(y)) .OR. ANY(UBOUND(x) /= UBOUND(y))) THEN
            CALL bad(nm//' bounds', what, fail, n); RETURN
        END IF
        IF (ANY(TRANSFER(x, 0_8, SIZE(x)) /= TRANSFER(y, 0_8, SIZE(y)))) CALL bad(nm//' values', what, fail, n)
    END SUBROUTINE cmp2

    SUBROUTINE cmp3(x, y, nm, what, fail, n)
        REAL(R8), ALLOCATABLE, INTENT(IN) :: x(:,:,:), y(:,:,:)
        CHARACTER(*), INTENT(IN) :: nm, what
        INTEGER, INTENT(IN) :: fail, n
        IF (ALLOCATED(x) .NEQV. ALLOCATED(y)) THEN
            CALL bad(nm//' allocation status', what, fail, n); RETURN
        END IF
        IF (.NOT. ALLOCATED(x)) RETURN
        IF (ANY(LBOUND(x) /= LBOUND(y)) .OR. ANY(UBOUND(x) /= UBOUND(y))) THEN
            CALL bad(nm//' bounds', what, fail, n); RETURN
        END IF
        IF (ANY(TRANSFER(x, 0_8, SIZE(x)) /= TRANSFER(y, 0_8, SIZE(y)))) CALL bad(nm//' values', what, fail, n)
    END SUBROUTINE cmp3

    SUBROUTINE check_par(x, y, what, fail, n)
        TYPE(ParT), INTENT(IN) :: x, y
        CHARACTER(*), INTENT(IN) :: what
        INTEGER, INTENT(IN) :: fail, n
        INTEGER :: i
        IF (x%NumTabs /= y%NumTabs) CALL bad('NumTabs', what, fail, n)
        CALL cmp1(x%secondVals, y%secondVals, 'secondVals', what, fail, n)
        CALL cmp1(x%keep, y%keep, 'keep', what, fail, n)
        IF (ALLOCATED(x%Table) .NEQV. ALLOCATED(y%Table)) THEN
            CALL bad('Table allocation status', what, fail, n); RETURN
        END IF
        IF (.NOT. ALLOCATED(x%Table)) RETURN
        IF (LBOUND(x%Table,1) /= LBOUND(y%Table,1) .OR. UBOUND(x%Table,1) /= UBOUND(y%Table,1)) THEN
            CALL bad('Table bounds', what, fail, n); RETURN
        END IF
        DO i = LBOUND(x%Table,1), UBOUND(x%Table,1)
            IF (x%Table(i)%NumAlf /= y%Table(i)%NumAlf) CALL bad('Table%NumAlf', what, fail, n)
            IF (TRANSFER(x%Table(i)%Re, 0_8) /= TRANSFER(y%Table(i)%Re, 0_8)) CALL bad('Table%Re', what, fail, n)
            CALL cmp1(x%Table(i)%Alpha, y%Table(i)%Alpha, 'Table%Alpha', what, fail, n)
            CALL cmp2(x%Table(i)%Coefs, y%Table(i)%Coefs, 'Table%Coefs', what, fail, n)
            CALL cmp3(x%Table(i)%Spl, y%Table(i)%Spl, 'Table%Spl', what, fail, n)
        END DO
    END SUBROUTINE check_par
END PROGRAM compare
