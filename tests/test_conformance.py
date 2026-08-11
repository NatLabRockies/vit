"""What of Fortran can VIT actually cross? — the conformance matrix.

VIT's language coverage has been demand-driven, and the demand was one
codebase's numerical core. 52 REAL-typed controllers gave a toolchain that
handled REAL-typed controllers; widening to ROSCO_Helpers' string and IO
utilities opened 19 gaps in three days, each found by a translation failing on a
real function at whatever moment it happened to be reached.

That is discovery by accident, and it does not survive a second codebase.

This test walks `conformance/conformance.f90` -- one procedure per cell of
(type x shape x intent x attribute x procedure-kind) -- and records, for each,
what VIT does. `conformance/matrix.toml` holds the expected status. The test
fails when reality and the record diverge **in either direction**:

  a cell that regresses          -> caught, obviously
  a cell that starts working     -> also caught, so the record cannot rot

**A cell marked `unsupported` is not a failure.** A documented limit is worth
more than an undocumented one; what this file exists to prevent is a limit
nobody knew about until function 58.

Run `python3 -m tests.test_conformance --update` to regenerate the matrix after
a deliberate change, and read the diff before committing it.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = Path(__file__).parent / "conformance" / "conformance.f90"
MATRIX = Path(__file__).parent / "conformance" / "matrix.toml"

# The fixture's own kind parameters, as a campaign's vit.yaml would declare them.
ALIASES = {"R4": "REAL(4)", "R8": "REAL(8)", "I4": "INTEGER(4)"}

TYPE_NAMES = ["PlainType", "BindCType", "FixedArrayType", "AllocType",
              "NestedType", "CharFieldType", "PointerFieldType"]

# Which types a campaign would declare as views. AllocType and its relatives
# have ALLOCATABLE fields, so without this they are passthrough and vanish.
STRATEGIES = {"AllocType": "view", "NestedType": "view", "CharFieldType": "view"}

# Why a cell does not cross. **The only hand-written thing in this file**, and
# the reason the matrix is worth more than a pass count: a limit with a stated
# cause tells the next campaign whether it matters to them.
# Cells whose SIGNATURE crosses but whose SEMANTICS do not reach C++.
#
# This dimension is DECLARED, not measured, and it exists because the measured
# stages cannot see it: a Fortran unit number is an int, so parse/c_params/
# bridge/compile all say "yes" and the matrix would report full support for a
# procedure no mirror translation can implement. Mechanical crossing is not
# translatability, and a matrix that conflated them would be confidently wrong
# in the one direction that matters.
SEMANTICS = {
    "c_unit_read":
        "UNREACHABLE: `unit` is a HANDLE to state outside the signature. It crosses "
        "as an int and the file it names does not cross at all -- C++ cannot READ from "
        "a Fortran unit. A differential harness is unsound too: the two calls share one "
        "file position, so the second reads different data. Translate the variant that "
        "takes the data (ROSCO has ParseInput_Int_Opt beside ParseInput_Int), or leave "
        "the I/O in Fortran as a boundary.",
    "c_unit_write":
        "UNREACHABLE: as c_unit_read. The observable effect is a side effect on a "
        "handle the C++ side does not hold, so there is nothing to compare.",
}

REASONS = {
    "c_alloc_out":
        "UNSUPPORTED: an ALLOCATABLE dummy requires an ALLOCATABLE actual, and the "
        "bridge passes a plain local. The allocate-on-return convention (d4ba20b) "
        "covers the C++ direction; the Fortran reference side of test-validate does not.",
    "c_alloc_inout":
        "REFUSED deliberately since rosco-r2 unit #1. The harness bridge does not "
        "compile (`Actual argument for 'v' must be ALLOCATABLE`); the integration "
        "wrapper USED TO compile, silently dropping the ALLOCATABLE attribute and "
        "passing a bare pointer plus a by-value extent, so a reallocation could not "
        "reach the caller. Measured on ROSCO's AddToList: three appends, the array "
        "still the four elements it started with, the values written one past the "
        "end of the heap block, no diagnostic at any stage. "
        "interface_gen.assert_integration_bridgeable now raises instead. The limit is "
        "VIT's, not Fortran's -- a F2018 BIND(C) interface takes the ALLOCATABLE "
        "dummy directly and the C side gets a CFI_cdesc_t* it can CFI_allocate "
        "through, demonstrated byte-identical to the original on both branches under "
        "gfortran 13.",
    "c_complex_in":
        "UNSUPPORTED, found by the `integrates` column on the day it was added. The "
        "integration wrapper emits `CALL c_complex_in_c(COMPLEX(z, C_DOUBLE_COMPLEX), "
        "m)` for a dummy `z` that is ALREADY COMPLEX, and gfortran rejects it: `'x' "
        "argument of 'complex' intrinsic must be INTEGER or REAL`. The harness bridge "
        "compiles, so every column that existed before this one said the cell crossed.",
    "c_assumed_shape_2d":
        "UNSUPPORTED: a rank-2 assumed-shape dummy is passed as m(1:n_m), a rank-1 "
        "section. The C side already emits n_<x>_rows and n_<x>_cols; the bridge must "
        "declare the local with both extents.",
    "c_fn_array":
        "UNSUPPORTED: an array-valued FUNCTION result is assigned to the scalar "
        "vit_result. build_c_params already appends a result pointer for this case; "
        "the bridge does not use it.",
    "c_fn_alloc_character":
        "UNSUPPORTED and deliberate: a deferred-length CHARACTER(:) result has no "
        "length to declare and cannot cross a fixed-width C buffer. type_map raises "
        "rather than guessing a width. A campaign converts such fields to CHARACTER(n) "
        "first, as ROSCO did for ErrMsg.",
}


def _registry(source: str) -> dict:
    from vit.derived_type_parser import apply_type_strategies, parse_derived_type
    reg = {}
    for n in TYPE_NAMES:
        info = parse_derived_type(source, n)
        if info is not None:
            reg[n] = info
    apply_type_strategies(reg, STRATEGIES)
    return reg


def _procedures(source: str) -> list[str]:
    """Every `c_*` procedure in the fixture, in source order."""
    import re
    seen, out = set(), []
    for m in re.finditer(r"^\s*(?:[A-Z0-9_()\s]*?\s)??"
                         r"(?:RECURSIVE|PURE|ELEMENTAL|\s)*"
                         r"(?:SUBROUTINE|FUNCTION)\s+(c_\w+)",
                         source, re.MULTILINE | re.IGNORECASE):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def probe(name: str, source: str, registry: dict) -> dict:
    """What VIT does with one procedure. Never raises: the failure IS the datum.

    Five stages, because they fail independently and the distinction matters:
    a signature VIT cannot parse is a different problem from one it parses and
    cannot bridge.

    `bridge` / `compiles` measure `test_validate.generate_fortran_bridge` -- the
    DIFFERENTIAL HARNESS's Fortran side, which is linked into a test binary and
    never into the library. `integrates` measures
    `interface_gen.generate_fortran_interface_block` +
    `generate_fortran_wrapper`, which is what `vit integrate` writes into the
    shipped source. **They are different code paths and they can disagree.**

    That column was added at rosco-r2 unit #1, because they did. `plan.json`
    predicted `AddToList` could not cross, citing this file's
    `[c_alloc_inout] compiles = "no"`. The verdict was right and the basis was
    about the wrong generator: the harness bridge does not compile (`Actual
    argument for 'list' must be ALLOCATABLE`), while the integration wrapper
    compiles clean, silently drops the ALLOCATABLE attribute, and appends to
    an array that never grows. A matrix reporting only the first says nothing
    about what would have been shipped.
    """
    from vit.fortran_parser import find_function_in_file
    from vit.interface_gen import (build_c_params, generate_fortran_interface_block,
                                   generate_fortran_wrapper)
    from vit.test_validate import generate_fortran_bridge

    out = {"parse": "no", "c_params": "no", "bridge": "no", "compiles": "n/a",
           "integrates": "no", "detail": "", "params": 0}
    try:
        # The CLI's entry point, not `parse_signature_from_source`. It records
        # the enclosing module, which is what puts `USE <module>` in the bridge;
        # without it a FUNCTION has no known result type at the call and the
        # bridge does not compile. Measuring the wrong entry point measures the
        # probe.
        sig = find_function_in_file(str(FIXTURE), name)
    except Exception as e:                                   # noqa: BLE001
        out["detail"] = f"parse: {type(e).__name__}: {e}"[:160]
        return out
    if sig is None:
        out["detail"] = "parse: not found"
        return out
    out["parse"] = "yes"

    try:
        params = build_c_params(sig, ALIASES, registry)
        out["c_params"] = "yes"
        out["params"] = len(params)
    except Exception as e:                                   # noqa: BLE001
        out["detail"] = f"c_params: {type(e).__name__}: {e}"[:160]
        return out

    try:
        # `source_file` is what supplies the bridge's USE statements. Omitting
        # it produces a bridge that cannot see the module it calls into --
        # a harness error, not a VIT gap, and worth separating from one.
        bridge = generate_fortran_bridge(sig, ALIASES, derived_types=registry,
                                         source_file=str(FIXTURE))
        out["bridge"] = "yes"
        # A generator that did not raise has not produced a bridge -- it has
        # produced text. `CableControl`'s bridge was emitted without complaint
        # for months and had never once compiled: nested derived-type fields
        # came out as a Fortran COMMENT while still occupying a slot in the
        # argument list. So "bridge = yes" is not allowed to be the last word.
        ok, why = _compiles(bridge)
        out["compiles"] = ok
        if ok == "no" and not out["detail"]:
            out["detail"] = f"compile: {why}"[:160]
    except Exception as e:                                   # noqa: BLE001
        out["detail"] = f"bridge: {type(e).__name__}: {e}"[:160]

    # The INTEGRATION path. Measured even when the harness bridge failed:
    # the two generators are independent, and a cell where one works and the
    # other does not is the most useful row in this file.
    out["integrates"], detail = _integrates(sig, registry)
    if out["integrates"] != "yes" and not out["detail"]:
        out["detail"] = f"integrate: {detail}"[:160]
    return out


def _integrates(sig, registry) -> tuple[str, str]:
    """Does `vit integrate`'s Fortran output generate AND compile?

    The interface block goes above CONTAINS and the wrapper below it, which is
    exactly where `vit integrate` places them, so they are compiled together in
    one synthetic module rather than separately.
    """
    from vit.interface_gen import (generate_fortran_interface_block,
                                   generate_fortran_wrapper)
    try:
        iface = generate_fortran_interface_block(sig, ALIASES, registry)
        wrap = generate_fortran_wrapper(sig, ALIASES, registry)
    except Exception as e:                                   # noqa: BLE001
        # A refusal is a RESULT, not an error: it is the generator declining to
        # emit a wrapper it cannot make correct, which is the outcome this
        # column exists to be able to record.
        return "refused", f"{type(e).__name__}: {e}"
    # `USE vit_conformance_types` because `vit integrate` inserts the wrapper
    # INTO the module that already has the derived types in scope. Without it
    # seven cells reported `Derived type 'plaintype' is being used before it is
    # defined` -- a fact about this synthetic module, not about VIT. Measuring
    # the probe is the failure mode this whole file is written against.
    module = (f"MODULE vit_conformance_integrate_{sig.name}\n"
              f"    USE vit_conformance_types\n{iface}\n"
              f"CONTAINS\n{wrap}\nEND MODULE vit_conformance_integrate_{sig.name}\n")
    ok, why = _compiles(module)
    return ("yes" if ok == "yes" else ok), why


_FC = None


def _fortran_compiler():
    """A Fortran compiler, or None. Absence SKIPS the compile stage rather than
    passing it -- a check that cannot run must not report success."""
    global _FC
    if _FC is None:
        import shutil
        _FC = shutil.which("gfortran") or shutil.which("ifx") or shutil.which("flang") or ""
    return _FC or None


def _compiles(bridge_text: str) -> tuple[str, str]:
    """Compile the bridge against the fixture's own modules.

    Returns ("yes"/"no"/"n/a", diagnostic). The fixture is compiled once per
    session into a temp dir; the bridge is then compiled against its .mod files.
    """
    import subprocess, tempfile
    fc = _fortran_compiler()
    if not fc:
        return "n/a", "no Fortran compiler on PATH"
    mods = _fixture_mods(fc)
    if mods is None:
        return "n/a", "fixture did not compile"
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "bridge.f90"
        # The bridge is emitted as a bare procedure; wrap it so it is compilable
        # on its own, exactly as the generated harness does.
        src.write_text(bridge_text if bridge_text.lstrip().upper().startswith(("MODULE", "PROGRAM"))
                       else bridge_text)
        r = subprocess.run([fc, "-fsyntax-only", f"-I{mods}", "-J", d, str(src)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "yes", ""
        # `Fatal Error:` too, not only `Error:`. A missing .mod is reported as
        # Fatal, so the old predicate matched nothing and fell through to the
        # last stderr line -- `compilation terminated.`, which names no cause
        # and sends the reader looking in the wrong place.
        first = next((l for l in r.stderr.splitlines()
                      if l.startswith("Error") or l.startswith("Fatal Error")), "")
        return "no", first or (r.stderr.strip().splitlines()[-1] if r.stderr else "unknown")


_MODS = "unset"


def _fixture_mods(fc: str):
    """Compile the fixture once; return the directory holding its .mod files.

    The VIEW POPULATOR modules are generated into the same directory. An
    integration wrapper for a view-strategy type opens with
    `USE vit_<type>_view, ONLY: ...`, a module `vit analyze-types` generates and
    a campaign builds alongside its sources. Without it, three cells scored
    `integrates = no` on a missing .mod -- a fact about this test, not about
    VIT, and precisely the "measuring the probe" failure the rest of this file
    is written against.
    """
    global _MODS
    if _MODS != "unset":
        return _MODS
    import subprocess, tempfile
    d = tempfile.mkdtemp(prefix="vit-conformance-")
    r = subprocess.run([fc, "-fsyntax-only", "-J", d, str(FIXTURE)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("conformance fixture did not compile:\n" + r.stderr[-1500:], file=sys.stderr)
        _MODS = None
        return _MODS
    _MODS = d

    from vit.view_populator import generate_view_populator
    registry = _registry(FIXTURE.read_text())
    for tname in sorted(STRATEGIES):
        info = registry.get(tname)
        if info is None:
            continue
        try:
            text = generate_view_populator(info, [], ALIASES,
                                           source_module="vit_conformance_types",
                                           registry=registry)
        except Exception as e:                               # noqa: BLE001
            print(f"view populator for {tname} did not generate: {e}", file=sys.stderr)
            continue
        p = Path(d) / f"vit_{tname.lower()}_view.f90"
        p.write_text(text)
        rv = subprocess.run([fc, "-fsyntax-only", f"-I{d}", "-J", d, str(p)],
                            capture_output=True, text=True)
        if rv.returncode != 0:
            # Stated, not swallowed: a cell that then fails to integrate is
            # failing on a populator VIT could not build, which is itself a
            # finding rather than noise.
            print(f"view populator for {tname} did not compile:\n{rv.stderr[-600:]}",
                  file=sys.stderr)
    return _MODS


def test_the_fixture_itself_is_valid_fortran():
    """If the fixture does not compile, every cell below is measuring VIT
    against something that is not Fortran."""
    fc = _fortran_compiler()
    if not fc:
        pytest.skip("no Fortran compiler on PATH")
    assert _fixture_mods(fc) is not None, "the conformance fixture is not valid Fortran"


def measure() -> dict:
    source = FIXTURE.read_text()
    registry = _registry(source)
    return {n: probe(n, source, registry) for n in _procedures(source)}


# --------------------------------------------------------------------------

def _unsupported(cell: dict) -> bool:
    """A cell that does not cross, at whatever stage.

    `compiles == "no"` counts. It did not, at first, and the omission let five
    cells sit in the matrix with no stated reason -- the matrix reproducing, in
    miniature, the gap it was built to close.

    So does `integrates != "yes"`, for the same reason one step further out: a
    cell whose harness bridge compiles and whose integration wrapper does not
    is a cell that cannot ship, and the matrix said nothing about it until the
    column existed."""
    return (cell.get("c_params") == "no" or cell.get("bridge") == "no"
            or cell.get("compiles") == "no"
            or cell.get("integrates", "yes") != "yes"
            or cell.get("semantics") == "unreachable")


def _load_matrix() -> dict:
    if not MATRIX.is_file():
        return {}
    return tomllib.loads(MATRIX.read_text())


def test_the_fixture_covers_the_language_corners_that_have_bitten_us():
    """Every gap the replication hit must have a cell, or the matrix records
    coverage it does not have."""
    names = set(_procedures(FIXTURE.read_text()))
    required = {
        "c_character_in", "c_character_inout", "c_character_array",   # 4 commits
        "c_fn_character",                                             # 4f82ac1
        "c_logical_in", "c_logical_out", "c_fn_logical",              # 3 commits
        "c_complex_in",                                               # 10f7cd4
        "c_alloc_out",                                                # d4ba20b
        "c_with_interface",                                           # d60664e
        "c_with_block",                                               # 2e57357
        "c_nested_field", "c_char_field", "c_fixed_array_field",      # today
        "c_assumed_size",                                             # today
        "c_unit_read", "c_unit_write",                                # ParseInput_Int
    }
    missing = required - names
    assert not missing, f"the fixture does not exercise: {sorted(missing)}"
    assert len(names) >= 35, f"only {len(names)} cells"


def test_every_cell_is_recorded():
    """A procedure with no entry is a cell nobody has looked at."""
    recorded = set(_load_matrix())
    measured = set(measure())
    assert measured, "nothing measured -- the fixture did not parse"
    unrecorded = measured - recorded
    assert not unrecorded, (
        f"{len(unrecorded)} cell(s) have no entry in matrix.toml: "
        f"{sorted(unrecorded)[:8]}. Run `python3 tests/test_conformance.py --update`.")
    stale = recorded - measured
    assert not stale, f"matrix.toml records cells that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize("name", sorted(_load_matrix()) or ["__none__"])
def test_cell_matches_the_record(name):
    """The record and reality must agree **in both directions**.

    A regression fails, obviously. So does an improvement -- because a matrix
    that silently drifts toward optimism is exactly the artifact this repository
    exists to distrust, and because a newly-working cell is a fact worth a commit
    message.
    """
    if name == "__none__":
        pytest.skip("matrix.toml is empty; generate it first")
    expected = _load_matrix()[name]
    got = measure()[name]
    for stage in ("parse", "c_params", "bridge", "compiles", "integrates"):
        if stage in ("compiles", "integrates") and "n/a" in (got[stage], expected.get(stage, "n/a")):
            continue      # no compiler here, or none when the record was made
        assert got[stage] == expected.get(stage), (
            f"{name}.{stage}: recorded {expected[stage]!r}, measured {got[stage]!r}. "
            f"{got['detail'] or 'no detail'}")


def test_a_handle_to_external_state_is_marked_untranslatable():
    """The cell that motivated the `semantics` column.

    `ParseInput_Int(Un, ...)` reads from a Fortran unit. Every measured stage
    says yes -- a unit number is an int -- so a matrix built only on measurement
    would report it as fully supported, which is the one direction a coverage
    claim must never be wrong in.
    """
    matrix = _load_matrix()
    if not matrix:
        pytest.skip("matrix.toml is empty; generate it first")
    for name in SEMANTICS:
        cell = matrix[name]
        assert cell["compiles"] == "yes", (
            f"{name} is meant to demonstrate that MECHANICAL success is not "
            f"translatability; if it no longer crosses, the point is lost")
        assert cell["semantics"] == "unreachable"
        assert cell.get("why")


def test_the_matrix_states_a_reason_for_every_unsupported_cell():
    """An `unsupported` with no reason is indistinguishable from one nobody
    understood. The reason is what tells the next campaign whether the limit
    matters to it."""
    for name, cell in _load_matrix().items():
        if _unsupported(cell):
            assert cell.get("why"), (
                f"{name} does not cross and has no `why`. An undocumented limit is "
                f"the thing this file exists to prevent.")


def summary() -> str:
    m = measure()
    n = len(m)
    split = sum(1 for c in m.values()
                if (c['compiles'] == 'no') != (c['integrates'] != 'yes'))
    return (f"{n} cells: "
            f"{sum(1 for c in m.values() if c['c_params'] == 'yes')} reach C params, "
            f"{sum(1 for c in m.values() if c['bridge'] == 'yes')} emit a bridge, "
            f"{sum(1 for c in m.values() if c['compiles'] == 'yes')} COMPILE "
            f"({sum(1 for c in m.values() if c['compiles'] == 'n/a')} not compiled), "
            f"{sum(1 for c in m.values() if c['integrates'] == 'yes')} INTEGRATE "
            f"({sum(1 for c in m.values() if c['integrates'] == 'refused')} refused); "
            f"{split} cells where the harness column and the integration column DISAGREE; "
            f"{len(SEMANTICS)} cross mechanically but are NOT TRANSLATABLE")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_matrix() -> str:
    """Regenerate matrix.toml from what VIT does today, preserving every `why`.

    The reasons are the hand-written part -- the only judgement in the file --
    so a regeneration must never silently drop them.
    """
    old = _load_matrix()
    lines = [
        "# VIT conformance matrix -- generated by tests/test_conformance.py --update",
        "#",
        "# What VIT does with each cell of tests/conformance/conformance.f90.",
        "# `parse` / `c_params` / `bridge` are measured; `why` is written by hand and",
        "# is REQUIRED for any unsupported cell, because an undocumented limit is the",
        "# thing this file exists to prevent.",
        "#",
        "# An unsupported cell is not a failure. A limit nobody knew about is.",
        "",
    ]
    for name, cell in sorted(measure().items()):
        lines.append(f"[{name}]")
        for stage in ("parse", "c_params", "bridge", "compiles", "integrates"):
            lines.append(f'{stage} = "{cell[stage]}"')
        lines.append(f'semantics = "{"unreachable" if name in SEMANTICS else "ok"}"')
        why = ((old.get(name) or {}).get("why", "")
               or REASONS.get(name, "") or SEMANTICS.get(name, ""))
        if not why and _unsupported(cell):
            why = "TODO: " + (cell["detail"] or "no detail recorded")
        if why:
            lines.append(f'why = "{_esc(why)}"')
        lines.append("")
    text = "\n".join(lines)
    MATRIX.parent.mkdir(parents=True, exist_ok=True)
    MATRIX.write_text(text)
    return text


if __name__ == "__main__":
    if "--update" in sys.argv:
        write_matrix()
        print(f"wrote {MATRIX}")
    print(summary())
    for n, c in sorted(measure().items()):
        if c["bridge"] != "yes" or c["compiles"] == "no" or c["integrates"] != "yes":
            print(f"  {n:26s} params={c['c_params']} bridge={c['bridge']} "
                  f"compiles={c['compiles']} integrates={c['integrates']}  "
                  f"{c['detail'][:100]}")
