! A projectile slowed by air drag. `drag` is the function to translate;
! `step` calls it, so `step` is where its inputs are captured.
module physics
    implicit none
    integer, parameter :: dp = kind(1.0d0)
contains

    real(dp) function drag(v, cd, area)
        real(dp), intent(in) :: v, cd, area
        drag = 0.5_dp * 1.225_dp * cd * area * v**2
    end function drag

    subroutine step(v, x, dt, mass)
        real(dp), intent(inout) :: v, x
        real(dp), intent(in)    :: dt, mass
        real(dp) :: f
        f = drag(v, 0.47_dp, 0.0042_dp)
        v = v - dt * f / mass
        x = x + dt * v
    end subroutine step

end module physics
