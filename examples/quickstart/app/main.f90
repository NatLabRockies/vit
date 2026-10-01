! Runs 500 steps and writes speed and distance at full precision, so a run
! can be compared with an earlier one byte for byte.
program simulate
    use physics
    implicit none
    real(dp) :: v, x
    integer :: i, u
    v = 40.0_dp
    x = 0.0_dp
    open(newunit=u, file='trajectory.txt', status='replace')
    do i = 1, 500
        call step(v, x, 0.01_dp, 0.145_dp)
        write(u, '(I5, 2ES26.17E3)') i, v, x
    end do
    close(u)
end program simulate
