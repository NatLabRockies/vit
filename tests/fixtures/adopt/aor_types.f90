! Types for the adopt-path round-trip test: the shape of AeroDyn's
! AFI_ParameterType -- a parameter type with ALLOCATABLE arrays and an
! ALLOCATABLE array of table types, each table with rank-1, rank-2 and
! rank-3 components, the last with a non-default lower bound.
MODULE aor_types
    IMPLICIT NONE
    INTEGER, PARAMETER :: R8 = SELECTED_REAL_KIND(12)

    TYPE, PUBLIC :: TabT
        REAL(R8), ALLOCATABLE :: Alpha(:)
        REAL(R8), ALLOCATABLE :: Coefs(:,:)
        REAL(R8), ALLOCATABLE :: Spl(:,:,:)
        REAL(R8) :: Re = 0.0_R8
        INTEGER  :: NumAlf = 0
    END TYPE TabT

    TYPE, PUBLIC :: ParT
        INTEGER :: NumTabs = 0
        REAL(R8), ALLOCATABLE :: secondVals(:)
        REAL(R8), ALLOCATABLE :: keep(:)
        TYPE(TabT), ALLOCATABLE :: Table(:)
    END TYPE ParT
END MODULE aor_types
