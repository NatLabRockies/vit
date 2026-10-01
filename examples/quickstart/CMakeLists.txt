cmake_minimum_required(VERSION 3.16)
project(quickstart LANGUAGES Fortran)

# VIT verifies bit for bit, so fused multiply-add contraction must be off, for
# the Fortran and for the C++ that `vit integrate` adds.
set(CMAKE_Fortran_FLAGS "${CMAKE_Fortran_FLAGS} -ffp-contract=off")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -ffp-contract=off")

add_library(physics STATIC
    src/physics.f90
)

add_executable(simulate app/main.f90)
target_link_libraries(simulate physics)
