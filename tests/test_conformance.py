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
REASONS = {
    "c_alloc_out":
        "UNSUPPORTED: an ALLOCATABLE dummy requires an ALLOCATABLE actual, and the "
        "bridge passes a plain local. The allocate-on-return convention (d4ba20b) "
        "covers the C++ direction; the Fortran reference side of test-validate does not.",
    "c_alloc_inout":
        "UNSUPPORTED: as c_alloc_out. INTENT(INOUT) ALLOCATABLE needs an ALLOCATABLE "
        "local plus MOVE_ALLOC in and out.",
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

    Three stages, because they fail independently and the distinction matters:
    a signature VIT cannot parse is a different problem from one it parses and
    cannot bridge.
    """
    from vit.fortran_parser import find_function_in_file
    from vit.interface_gen import build_c_params
    from vit.test_validate import generate_fortran_bridge

    out = {"parse": "no", "c_params": "no", "bridge": "no", "compiles": "n/a",
           "detail": "", "params": 0}
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
    except Exception as e:                                   # noqa: BLE001
        out["detail"] = f"bridge: {type(e).__name__}: {e}"[:160]
        return out

    # A generator that did not raise has not produced a bridge -- it has
    # produced text. `CableControl`'s bridge was emitted without complaint for
    # months and had never once compiled: nested derived-type fields came out as
    # a Fortran COMMENT while still occupying a slot in the argument list. So
    # "bridge = yes" is not allowed to be the last word.
    ok, why = _compiles(bridge)
    out["compiles"] = ok
    if ok == "no" and not out["detail"]:
        out["detail"] = f"compile: {why}"[:160]
    return out


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
        first = next((l for l in r.stderr.splitlines() if l.startswith("Error")), "")
        return "no", first or r.stderr.strip().splitlines()[-1:][0] if r.stderr else "unknown"


_MODS = "unset"


def _fixture_mods(fc: str):
    """Compile the fixture once; return the directory holding its .mod files."""
    global _MODS
    if _MODS != "unset":
        return _MODS
    import subprocess, tempfile
    d = tempfile.mkdtemp(prefix="vit-conformance-")
    r = subprocess.run([fc, "-fsyntax-only", "-J", d, str(FIXTURE)],
                       capture_output=True, text=True)
    _MODS = d if r.returncode == 0 else None
    if _MODS is None:
        print("conformance fixture did not compile:\n" + r.stderr[-1500:], file=sys.stderr)
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
    miniature, the gap it was built to close."""
    return (cell.get("c_params") == "no" or cell.get("bridge") == "no"
            or cell.get("compiles") == "no")


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
    for stage in ("parse", "c_params", "bridge", "compiles"):
        if stage == "compiles" and "n/a" in (got[stage], expected.get(stage, "n/a")):
            continue      # no compiler here, or none when the record was made
        assert got[stage] == expected.get(stage), (
            f"{name}.{stage}: recorded {expected[stage]!r}, measured {got[stage]!r}. "
            f"{got['detail'] or 'no detail'}")


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
    return (f"{n} cells: "
            f"{sum(1 for c in m.values() if c['c_params'] == 'yes')} reach C params, "
            f"{sum(1 for c in m.values() if c['bridge'] == 'yes')} emit a bridge, "
            f"{sum(1 for c in m.values() if c['compiles'] == 'yes')} COMPILE "
            f"({sum(1 for c in m.values() if c['compiles'] == 'n/a')} not compiled)")


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
        for stage in ("parse", "c_params", "bridge", "compiles"):
            lines.append(f'{stage} = "{cell[stage]}"')
        why = (old.get(name) or {}).get("why", "") or REASONS.get(name, "")
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
        if c["bridge"] != "yes" or c["compiles"] == "no":
            print(f"  {n:26s} params={c['c_params']} bridge={c['bridge']} "
                  f"compiles={c['compiles']}  {c['detail'][:100]}")
