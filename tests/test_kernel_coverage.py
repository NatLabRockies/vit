"""Which lines of a function a kernel's captured calls run (vit/kernel_coverage.py).

Every kernel used to hold a function's first twenty calls, and nothing said
what they exercised (dev note 202608080640). The fixture here is the same
shape: a function with a branch that only later calls take. Captured from the
start, the kernel runs one branch and the verification still passes.
"""

import gzip
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from vit import coverage as cov
from vit import kernel_coverage
from vit.fortran_parser import find_function_in_file

SOURCE = textwrap.dedent("""\
    module physics
      implicit none
      integer, parameter :: dp = kind(1.0d0)
    contains
      real(dp) function drag(v)
        real(dp), intent(in) :: v
        if (v > 30.0_dp) then
          drag = 1.1_dp * v * v
        else
          drag = v * v
        end if
      end function drag
    end module physics
""")
# Lines 7-10 hold the body; 8 runs above 30, 10 below.

DRIVER = textwrap.dedent("""\
    program kernel_driver
      use physics
      implicit none
      character(len=256) :: name
      integer :: list, unit, ios
      real(dp) :: v, r
      open(newunit=list, file="kgen_statefile.lst", status="old")
      do
        read(list, "(A)", iostat=ios) name
        if (ios /= 0) exit
        open(newunit=unit, file=trim(name), status="old")
        read(unit, *) v
        close(unit)
        r = drag(v)
      end do
      close(list)
      print *, "verified"
    end program kernel_driver
""")

MAKEFILE = textwrap.dedent("""\
    FC_0 ?= gfortran
    FC_FLAGS_SET_0 ?= -O2 -ffree-form
    ALL_OBJS := physics.o kernel_driver.o

    build: ${ALL_OBJS}
    \t${FC_0} ${FC_FLAGS_SET_0} -o kernel.exe $^

    physics.o: physics.f90
    \t${FC_0} ${FC_FLAGS_SET_0} -c -o $@ $<

    kernel_driver.o: kernel_driver.f90 physics.o
    \t${FC_0} ${FC_FLAGS_SET_0} -c -o $@ $<

    clean:
    \trm -f kernel.exe *.mod ${ALL_OBJS}
""")


def _kernel(tmp_path, speeds):
    """A kernel directory as `vit verify` leaves it: the target file, its
    `.kgen` backup (here the same Fortran), a Makefile owning FC_FLAGS_SET_0,
    and one state file per captured call."""
    kernel = tmp_path / "kernel" / "drag"
    kernel.mkdir(parents=True)
    (kernel / "physics.f90").write_text(SOURCE)
    (kernel / "physics.f90.kgen").write_text(SOURCE)
    (kernel / "kernel_driver.f90").write_text(DRIVER)
    (kernel / "Makefile").write_text(MAKEFILE)
    names = []
    for n, v in enumerate(speeds, 1):
        names.append(f"drag.0.0.{n}")
        (kernel / names[-1]).write_text(f"{v}\n")
    (kernel / "kgen_statefile.lst").write_text("\n".join(names) + "\n")
    return kernel


def _toolchain():
    if not shutil.which("gfortran") or not shutil.which("make"):
        pytest.skip("needs gfortran and make")
    if cov.find_gcov("gfortran")[0] is None:
        pytest.skip("no gcov matching gfortran")


def _sig(tmp_path):
    source = tmp_path / "src" / "physics.f90"
    source.parent.mkdir(exist_ok=True)
    source.write_text(SOURCE)
    return find_function_in_file(str(source), "drag")


@pytest.mark.parametrize("speeds, missed", [
    ([40.0] * 20, ["drag = v * v"]),          # the first calls: the branch above 30 only
    ([40.0, 35.0, 25.0, 15.0], []),           # spread over the run: both branches
])
def test_measure_reports_the_lines_the_captured_calls_run(tmp_path, speeds, missed):
    _toolchain()
    kernel = _kernel(tmp_path, speeds)
    before = {p.name: p.read_bytes() for p in kernel.iterdir()}

    measured = kernel_coverage.measure(str(kernel), _sig(tmp_path), "gfortran")

    assert measured["ok"], measured.get("reason")
    assert measured["calls"] == len(speeds)
    result = kernel_coverage.compare(measured, _sig(tmp_path))
    assert [text for _, text in result["missed"]] == missed
    assert result["run"] == result["executable"] - len(missed)
    # A build of its own: the kernel directory is exactly as it was.
    assert {p.name: p.read_bytes() for p in kernel.iterdir()} == before


def test_a_kernel_that_will_not_build_is_reported_not_raised(tmp_path):
    _toolchain()
    kernel = _kernel(tmp_path, [40.0])
    (kernel / "physics.f90").write_text(SOURCE.replace("end module physics", ""))
    (kernel / "physics.f90.kgen").write_text(SOURCE.replace("end module physics", ""))
    measured = kernel_coverage.measure(str(kernel), _sig(tmp_path), "gfortran")
    assert not measured["ok"]
    assert "did not build with coverage" in measured["reason"]


def _coverage_file(tmp_path, lines):
    """A coverage file for src/physics.f90 with `lines` ({line: count}) run
    in case `long`, measured outside git so the text is stored."""
    gcov = tmp_path / "gcov" / "long"
    gcov.mkdir(parents=True)
    with gzip.open(gcov / "p.gcov.json.gz", "wt") as fh:
        json.dump({"format_version": "1", "files": [{
            "file": str(tmp_path / "src" / "physics.f90"),
            "lines": [{"line_number": n, "count": c} for n, c in lines.items()],
            "functions": []}]}, fh)
    data, _ = cov.import_gcov(tmp_path / "gcov", tmp_path)
    path = tmp_path / "coverage.json"
    cov.write(data, path)
    return str(path)


def _measured(ran):
    """What measure returns, for the kernel copy of drag, which KGen places
    at other line numbers than the source's."""
    body = SOURCE.split("\n")[6:11]
    return {"ok": True, "calls": 20, "file": "physics.f90",
            "lines": [{"line": 40 + i, "text": t, "count": 20 if t.strip() in ran else 0}
                      for i, t in enumerate(body) if t.strip() not in ("else", "end if")]}


def test_compare_numbers_missed_lines_as_measured(tmp_path):
    """The kernel's copy sits at other line numbers; its lines are matched to
    the source by text, and the report numbers them as the coverage does."""
    sig = _sig(tmp_path)
    coverage = _coverage_file(tmp_path, {7: 500, 8: 99, 10: 401})
    measured = _measured({"if (v > 30.0_dp) then", "drag = 1.1_dp * v * v"})

    result = kernel_coverage.compare(measured, sig, coverage, str(tmp_path / "src"))

    assert (result["run"], result["full_run"]) == (2, 3)
    assert result["missed"] == [(10, "drag = v * v")]
    lines = kernel_coverage.report(result)
    assert lines[0].startswith("Kernel coverage: the 20 captured call(s) run 2 of the 3 lines "
                               "of drag that the full run executes in any case")
    assert "    10: drag = v * v" in lines


def test_a_line_the_full_run_never_executes_is_not_counted_against_the_kernel(tmp_path):
    """Against the full run, an error branch no case reaches is not a gap in
    the captured calls: they could not have reached it either."""
    sig = _sig(tmp_path)
    coverage = _coverage_file(tmp_path, {7: 500, 8: 500})
    result = kernel_coverage.compare(_measured({"if (v > 30.0_dp) then", "drag = 1.1_dp * v * v"}),
                                     sig, coverage, str(tmp_path / "src"))
    assert (result["run"], result["full_run"], result["missed"]) == (2, 2, [])


def test_without_a_coverage_file_the_kernel_is_the_basis(tmp_path):
    result = kernel_coverage.compare(_measured({"if (v > 30.0_dp) then"}), _sig(tmp_path))
    assert (result["run"], result["executable"]) == (1, 3)
    assert kernel_coverage.summary(result) == "1/3 executable lines"
    assert "No coverage file to compare with the full run" in kernel_coverage.report(result)[1]
