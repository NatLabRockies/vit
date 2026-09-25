"""Generate the adopt-path round-trip build with VIT's own generators.

Everything the translated path needs that is not the C++ algorithm body is
produced here by VIT: the C view header, both view populator modules, the
BIND(C) interface blocks, the Fortran wrappers and the extern "C" shims.
Usage: python3 generate.py OUTDIR [--mutant NAME]
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from vit.analyze_types import scan_allocatable_sizes
from vit.derived_type_parser import (apply_type_strategies, generate_consolidated_types_header,
                                     parse_derived_type)
from vit.fortran_parser import find_function_in_file
from vit.interface_gen import (generate_cpp_wrapper, generate_fortran_interface_block,
                               generate_fortran_wrapper)
from vit.view_populator import generate_view_populator

ALIASES = {"R8": "REAL(8)"}


def main(out: Path, impl_source: str = None) -> None:
    """impl_source: C++ body text to use instead of aor_impl.cpp (guard tests)."""
    out.mkdir(parents=True, exist_ok=True)
    impl = "aor_impl.cpp"
    for f in ("aor_types.f90", "aor_ref.f90", "compare.f90", impl):
        shutil.copy(HERE / f, out / f)
    if impl_source is not None:
        (out / impl).write_text(impl_source)
    src = (HERE / "aor_types.f90").read_text()
    reg = {n: parse_derived_type(src, n) for n in ("TabT", "ParT")}
    apply_type_strategies(reg, {"TabT": "view", "ParT": "view"})
    for n, info in reg.items():
        info.field_mappings = scan_allocatable_sizes(str(HERE), n, info)

    (out / "vit_types.h").write_text(generate_consolidated_types_header(reg, ALIASES))
    for n in ("TabT", "ParT"):
        info = reg[n]
        (out / f"vit_{n.lower()}_view.f90").write_text(generate_view_populator(
            info, info.field_mappings, ALIASES, source_module="aor_types", registry=reg))

    ref = str(HERE / "aor_ref.f90")
    s_init = find_function_in_file(ref, "init_par")
    s_grow = find_function_in_file(ref, "grow_par")
    ifaces = (generate_fortran_interface_block(s_init, ALIASES, reg)
              + "\n" + generate_fortran_interface_block(s_grow, ALIASES, reg))
    wraps = (generate_fortran_wrapper(s_init, ALIASES, reg)
             + "\n" + generate_fortran_wrapper(s_grow, ALIASES, reg,
                                               reverse_copy=True, copy_arrays=["p"]))
    (out / "aor_wrap.f90").write_text(
        "MODULE aor_wrap\n    USE aor_types\n    IMPLICIT NONE\n"
        f"{ifaces}\nCONTAINS\n{wraps}\nEND MODULE aor_wrap\n")

    cpp_init = generate_cpp_wrapper(s_init, impl, ALIASES, reg, ["vit_types.h"])
    cpp_grow = generate_cpp_wrapper(s_grow, impl, ALIASES, reg, ["vit_types.h"])
    grow_extern = cpp_grow[cpp_grow.index('extern "C"'):]
    (out / "aor_shim.cpp").write_text(cpp_init + "\n" + grow_extern)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
