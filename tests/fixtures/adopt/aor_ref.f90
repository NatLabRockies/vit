! The ORIGINAL Fortran: the reference the translated path must reproduce.
MODULE aor_ref
    USE aor_types
    IMPLICIT NONE
CONTAINS
    ! An initializer: p arrives with every component deallocated.
    ! fail = 1 returns after allocating secondVals only (an error branch);
    ! fail = 2 returns before allocating anything.
    SUBROUTINE init_par(n, fail, p)
        INTEGER, INTENT(IN) :: n
        INTEGER, INTENT(IN) :: fail
        TYPE(ParT), INTENT(OUT) :: p
        INTEGER :: i, j, k
        IF (fail == 2) RETURN
        p%NumTabs = n
        ALLOCATE(p%secondVals(n))
        DO i = 1, n
            p%secondVals(i) = 0.1_R8 * i + 1.0_R8 / 3.0_R8
        END DO
        IF (fail == 1) RETURN
        ALLOCATE(p%Table(n))
        DO i = 1, n
            p%Table(i)%NumAlf = 3 + i
            p%Table(i)%Re = 1.0E6_R8 * i
            ALLOCATE(p%Table(i)%Alpha(p%Table(i)%NumAlf))
            ALLOCATE(p%Table(i)%Coefs(p%Table(i)%NumAlf, 2))
            ALLOCATE(p%Table(i)%Spl(p%Table(i)%NumAlf - 1, 2, 0:3))
            DO j = 1, p%Table(i)%NumAlf
                p%Table(i)%Alpha(j) = SIN(0.3_R8 * j + i)
                p%Table(i)%Coefs(j, 1) = COS(0.7_R8 * j) / i
                p%Table(i)%Coefs(j, 2) = EXP(-0.1_R8 * j * i)
            END DO
            DO k = 0, 3
                DO j = 1, p%Table(i)%NumAlf - 1
                    p%Table(i)%Spl(j, 1, k) = p%Table(i)%Alpha(j) * (k + 1) / 7.0_R8
                    p%Table(i)%Spl(j, 2, k) = p%Table(i)%Coefs(j, 2) - k
                END DO
            END DO
        END DO
    END SUBROUTINE init_par

    ! An updater: p arrives populated. secondVals is REPLACED (grown by one),
    ! Table(1)%Alpha is written IN PLACE, keep is never touched.
    SUBROUTINE grow_par(p)
        TYPE(ParT), INTENT(INOUT) :: p
        REAL(R8), ALLOCATABLE :: tmp(:)
        INTEGER :: n
        n = SIZE(p%secondVals)
        ALLOCATE(tmp(n + 1))
        tmp(1:n) = p%secondVals
        tmp(n + 1) = -2.5_R8
        CALL MOVE_ALLOC(tmp, p%secondVals)
        p%Table(1)%Alpha(1) = 42.0_R8
        p%NumTabs = p%NumTabs + 100
    END SUBROUTINE grow_par
END MODULE aor_ref
