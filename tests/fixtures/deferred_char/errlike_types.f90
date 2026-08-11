! The shape of the field this fixture is about, and nothing else.
!
! Modelled on ROSCO's TYPE(ErrorVariables): three interoperable scalars and one
! `CHARACTER(:), ALLOCATABLE`. The scalars are here so the round-trip exercises
! the ordinary path beside the new one -- a populator that got the deferred-
! length field right and silently stopped copying the scalars would still be
! wrong.
MODULE errlike_types
    USE ISO_C_BINDING
    IMPLICIT NONE

    TYPE, PUBLIC :: ErrLike
        INTEGER(C_INT)            :: size_avcMSG = 0
        INTEGER(C_INT)            :: aviFAIL = 0
        INTEGER(C_INT)            :: ErrStat = 0
        CHARACTER(:), ALLOCATABLE :: ErrMsg
    END TYPE ErrLike

END MODULE errlike_types
