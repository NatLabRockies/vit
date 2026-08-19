"""
Test-validate prompt and harness generation.

Generates a structured prompt for LLM-assisted writing of branch-coverage
tests for validation functions. Also generates a test harness skeleton
(includes, main loop, result reporting) that the LLM fills in with test
implementations.

Validation functions check input parameters and report errors. KGen kernel
verification only exercises the happy path. This tool enables testing of
the error branches — verifying the C++ translation correctly rejects
invalid input.

Design follows the same pattern as translate.py: VIT extracts raw materials
from the codebase, assembles them into a prompt, and the LLM writes the
actual test code with semantic understanding of the validation conditions.
"""

import glob as globmod
import re
from pathlib import Path
from typing import List, Optional

from .fortran_parser import FortranSignature, parse_signature_from_source
from .interface_gen import (build_c_params, dropped_derived_args,
                            is_alloc_return, is_cfi_inout_array,
                            _alloc_return_extent_names, _get_char_len_expr,
                            _resolve_fortran_type)
from .type_map import get_type_mapping, is_deferred_character


def _parse_link_libraries(build_dir: str, target_name: str,
                          exclude_stems: tuple[str, ...] = ()) -> Optional[list[str]]:
    """Parse CMake link.txt to extract library dependencies for a target.

    Searches for the target's link.txt in the build directory, parses the
    linker command, and returns all library references (.a paths and -l flags).

    Args:
        build_dir: Absolute path to the build directory
        target_name: CMake target name (e.g., "aerodyn_driver")
        exclude_stems: object basenames, without the `.o`, that must NOT be
            linked because the harness supplies those symbols itself. See the
            filter below for why an unfiltered glob is wrong on an integrated
            tree.

    Returns:
        List of library strings for Makefile LIBS, or None if link.txt not found.
    """
    # Find link.txt for this target
    pattern = f"{build_dir}/**/CMakeFiles/{target_name}.dir/link.txt"
    matches = globmod.glob(pattern, recursive=True)
    if not matches:
        return None

    link_txt = Path(matches[0])
    link_dir = link_txt.parent  # CMakeFiles/<target>.dir/
    # Libraries are relative to the directory containing the CMakeFiles/ dir
    lib_base = link_dir.parent.parent  # e.g., build/modules/aerodyn/

    content = link_txt.read_text().strip()
    tokens = content.split()

    libs = []
    skip_next = False
    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        # Skip compiler, flags, object files, output target, -Wl options
        if tok.endswith(".o"):
            continue
        if tok in ("-o",):
            skip_next = True  # skip the output filename
            continue
        if tok.startswith("-Wl,"):
            continue
        if tok.startswith("-D") or tok.startswith("-f") or tok.startswith("-O"):
            continue
        if tok.startswith("/usr/bin/"):
            continue
        if tok == "-DNDEBUG":
            continue

        if tok.endswith(".a") or tok.endswith(".so"):
            # Resolve relative paths against the lib_base directory
            p = Path(tok)
            if not p.is_absolute():
                p = (lib_base / tok).resolve()
            libs.append(str(p))
        elif tok.startswith("-l"):
            libs.append(tok)

    # A target built directly from sources -- no intermediate static libraries --
    # yields only system -l flags here, and the harness then fails to link with
    # undefined references to the very function it is testing. ROSCO's `discon`
    # is such a target; AeroDyn's driver is not, which is why this went unnoticed.
    #
    # Fall back to the target's own object files, which is exactly what the
    # target itself links. Searched recursively: sources in subdirectories
    # (ROSCO's src/SysFiles/) land in matching subdirectories of the .dir, and a
    # flat glob silently omits them, producing a link error one level further on.
    #
    # NOTE: these objects come from the CURRENT build. Run test-validate BEFORE
    # integrating, or the "Fortran reference" is a wrapper around the C++
    # translation and the comparison is against itself.
    if not any(lib.endswith((".a", ".so")) for lib in libs):
        obj_dir = link_txt.parent
        objs = sorted(str(o) for o in obj_dir.rglob("*.o"))
        # AN UNFILTERED GLOB LINKS THE VERY SYMBOLS THE HARNESS DEFINES, and on
        # an integrated tree that is not a warning, it is a link failure:
        #
        #   multiple definition of `findline_c';
        #     parseinary_opt_callees.o: first defined here
        #   multiple definition of `ParseInAry_Opt(...)';
        #     parseinary_opt_test.o: first defined here
        #
        # Two sources, one rule. The UNIT UNDER TEST is defined by the header
        # the harness includes -- that is the whole point, and under a mutation
        # sweep it is the MUTATED text, so linking the production object would
        # score the unmutated translation. And every BRIDGED CALLEE already has
        # a `<callee>_c` from `<unit>_callees.o`, backed by the original
        # Fortran; `generate_callee_bridges` explains at length why it must be
        # the Fortran one. Linking the translated object offers a second
        # definition of the same symbol and, if the linker took it, would make
        # the harness compare a pair of translations that can cancel.
        #
        # Measured on rosco-r2 ParseInAry_Opt: FindLine, GetWords and Int2LStr
        # were integrated after the harness was generated, and the next
        # regeneration globbed all three in.
        if exclude_stems:
            drop = {f"{st.lower()}.cpp.o" for st in exclude_stems}
            objs = [o for o in objs if Path(o).name.lower() not in drop]
        if objs:
            libs = objs + libs

    return libs if libs else None


def generate_public_patch(source_text: str, function_name: str) -> Optional[str]:
    """Patch Fortran source to make a PRIVATE function PUBLIC.

    If the module has default PRIVATE access and the function is not
    already PUBLIC, insert a PUBLIC declaration. Returns the patched
    source, or None if no patch is needed (function is already public
    or module doesn't use PRIVATE default).

    Args:
        source_text: Full Fortran source file content
        function_name: Function to make PUBLIC

    Returns:
        Patched source string, or None if no patch needed
    """
    import re

    lines = source_text.split("\n")

    # Check if module has PRIVATE default
    has_private_default = False
    for line in lines:
        stripped = line.strip().upper()
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]
        # Match standalone PRIVATE (not PRIVATE :: something)
        if re.match(r'^\s*PRIVATE\s*$', stripped):
            has_private_default = True
            break

    if not has_private_default:
        return None  # No patch needed

    # Check if already PUBLIC
    name_upper = function_name.upper()
    for line in lines:
        stripped = line.strip().upper()
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]
        if "PUBLIC" in stripped and name_upper in stripped:
            return None  # Already public

    # Find the last PUBLIC declaration and insert after it
    last_public_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]
        if re.match(r'^\s*PUBLIC\s', stripped):
            last_public_idx = i

    if last_public_idx is not None:
        # Auto-detect indentation and alignment from existing PUBLIC declaration
        existing_line = lines[last_public_idx]
        indent = existing_line[:len(existing_line) - len(existing_line.lstrip())]
        # Match alignment of :: if present
        if "::" in existing_line:
            col = existing_line.index("::")
            padding = " " * max(0, col - len(indent) - len("PUBLIC"))
            public_line = f"{indent}PUBLIC{padding}:: {function_name}"
        else:
            public_line = f"{indent}PUBLIC :: {function_name}"
        lines.insert(last_public_idx + 1, public_line)
        return "\n".join(lines)

    return None  # Couldn't find where to insert


def _get_module_use_statements(source_text: str) -> list[str]:
    """Extract module-level USE statements from Fortran source.

    Scans from MODULE declaration to the first CONTAINS, SUBROUTINE, or
    FUNCTION to collect USE statements that import types at module scope.

    Args:
        source_text: Full Fortran source file content

    Returns:
        List of USE statement lines (stripped)
    """
    import re
    lines = source_text.split("\n")
    in_module = False
    uses = []
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        # Remove comments
        if "!" in upper:
            upper = upper[:upper.index("!")]
            stripped = stripped[:stripped.index("!")]
        if not upper.strip():
            continue
        if re.match(r'^\s*MODULE\s+\w', upper) and "PROCEDURE" not in upper:
            in_module = True
            continue
        if in_module:
            if re.match(r'^\s*(CONTAINS|SUBROUTINE|FUNCTION)\b', upper):
                break
            if upper.strip().startswith("USE "):
                uses.append(stripped.strip())
    return uses


def _get_procedure_use_statements(source_text: str, proc_name: str) -> list[str]:
    """USE statements written INSIDE one procedure's own specification part.

    Beside `_get_module_use_statements`, not instead of it. That function stops
    at the first CONTAINS/SUBROUTINE/FUNCTION, so a type imported inside the
    procedure body is invisible to it -- and the bridge then declares a dummy of
    a type nothing in its scope defines.

    Measured rather than anticipated: ROSCO's `Filters.f90` declares its filter
    procedures with

        REAL(DbKi) FUNCTION HPFilter( ... )
            USE ROSCO_Types, ONLY : FilterParameters
            TYPE(FilterParameters), INTENT(INOUT) :: FP

    and `Filters` itself does not USE `ROSCO_Types` at module scope. The bridge
    emitted `TYPE(FilterParameters), POINTER :: FP` under `USE Filters` alone,
    which gfortran rejects with "Derived type 'filterparameters' at (1) is being
    used before it is defined" followed by "Symbol 'fp' has no IMPLICIT type".
    Every one of that file's seven filters has the same shape.

    Scans from the procedure's own SUBROUTINE/FUNCTION statement to the first
    executable-looking line, which is where a specification part must end.
    """
    import re
    lines = source_text.split("\n")
    uses: list[str] = []
    in_proc = False
    # `HPFilter` appears both in `REAL(DbKi) FUNCTION HPFilter(` and in
    # assignments to the result variable, so the opener is matched on the
    # keyword immediately preceding the name.
    opener = re.compile(
        r'^\s*(?:.*\s)?(?:SUBROUTINE|FUNCTION)\s+' + re.escape(proc_name.upper()) + r'\s*\(')
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if "!" in upper:
            upper = upper[:upper.index("!")]
            stripped = stripped[:stripped.index("!")]
        if not upper.strip():
            continue
        if not in_proc:
            if opener.match(upper):
                in_proc = True
            continue
        if upper.strip().startswith("USE "):
            uses.append(stripped.strip())
            continue
        # The specification part is over at the first statement that cannot
        # appear in one. Being conservative here costs nothing: a USE must
        # precede every declaration, so anything after the first declaration
        # is already past the point where one could appear.
        if re.match(r'^\s*(END\s+(SUBROUTINE|FUNCTION)|CONTAINS)\b', upper):
            break
        if not re.match(r'^\s*(IMPLICIT|TYPE\s*\(|REAL|INTEGER|LOGICAL|COMPLEX|'
                        r'CHARACTER|DOUBLE|PROCEDURE|INTENT|DIMENSION|'
                        r'ALLOCATABLE|POINTER|TARGET|SAVE|PARAMETER|EXTERNAL|'
                        r'INTRINSIC|OPTIONAL|VALUE|&)', upper):
            break
    return uses


def _nested_type_use_statements(copied_uses: set, local_decls: list[str]) -> list[str]:
    """Import the NESTED types the bridge declares but the reference never named.

    The bridge copies the reference's own USE statements verbatim, which is
    right for the reference and insufficient for the bridge: decomposing a
    derived-type argument makes the bridge declare

        TYPE(rlParams), POINTER :: vit_nested_LocalVar_rlP

    for every nested-type FIELD, and those type names appear nowhere in the
    reference, so an `ONLY` list written for the reference cannot contain them.

    Measured on ROSCO's `ZeroMQInterface.f90`, whose `UpdateZeroMQ` carries

        USE ROSCO_Types, ONLY : LocalVariables, ControlParameters, ErrorVariables

    -- gfortran rejects the generated bridge with "Derived type 'rlparams' at
    (1) is being used before it is defined" and five "has no IMPLICIT type".

    IT COMPILED FOR EVERY EARLIER UNIT BY ACCIDENT. `ReadAvrSWAP` decomposes the
    same `LocalVariables` and its bridge also imports only three names -- and it
    builds, because `ReadSetParameters` USEs `ROSCO_Types` at module scope and
    re-exports it, so the bridge's `USE ReadSetParameters` reaches the nested
    types by a route this unit's module does not have. A file whose module keeps
    its imports inside its procedures escapes it.

    Additive: a name is imported only when no copied `ONLY` list already names
    it, and the module is taken from the copied statement that supplies the
    argument types rather than guessed. An unrestricted `USE M` (no `ONLY`)
    already makes every type accessible and suppresses the addition entirely, so
    no bridge that compiled before gains a line.
    """
    import re
    nested = []
    for decl in local_decls:
        m = re.match(r'\s*TYPE\(([^)]+)\)\s*,\s*POINTER\s*::\s*vit_nested_', decl)
        if m and m.group(1).strip() not in nested:
            nested.append(m.group(1).strip())
    if not nested:
        return []

    # Which copied statements restrict what they import, and which module does
    # each name come from.
    #
    # AN UNRESTRICTED `USE M` PUTS *M's* NAMES IN SCOPE, NOT EVERY NAME. This
    # used to `return []` the moment any unrestricted USE appeared, on the
    # reasoning that it made every name accessible -- and that is false for the
    # ordinary shape, measured on ROSCO's `PreFilterMeasuredSignals`: the bridge
    # copies `USE Constants` and `USE Functions` (both unrestricted, neither
    # re-exporting a type) beside `USE ROSCO_Types, ONLY : ControlParameters,
    # LocalVariables, DebugVariables, ObjectInstances, ErrorVariables`, then
    # declares POINTERs to the five NESTED types that ONLY list does not name:
    #
    #   Error: Derived type 'filterparameters' at (1) is being used before it
    #          is defined
    #
    # An unrestricted USE of the TYPES module itself still suppresses the
    # addition, which is the case the original reasoning was right about, so no
    # bridge that compiled before gains a line.
    restricted: list[tuple[str, set]] = []
    unrestricted: set[str] = set()
    for stmt in copied_uses:
        m = re.match(r'\s*USE\s*(?:,\s*[^:]+::)?\s*(\w+)\s*,\s*ONLY\s*:(.*)$',
                     stmt, re.IGNORECASE)
        if not m:
            bare = re.match(r'\s*USE\s*(?:,\s*[^:]+::)?\s*(\w+)\s*$',
                            stmt.rstrip().rstrip(","), re.IGNORECASE)
            if bare:
                unrestricted.add(bare.group(1).upper())
            else:
                return []      # a shape this rule cannot read; add nothing
            continue
        names = {n.strip().split("=>")[-1].strip().upper()
                 for n in m.group(2).split(",") if n.strip()}
        restricted.append((m.group(1), names))
    if not restricted:
        return []

    already = set().union(*(names for _, names in restricted))
    missing = [t for t in nested if t.upper() not in already]
    if not missing:
        return []
    # The types module is the one whose ONLY list is longest -- it is the
    # statement that imported the argument types, and a nested field's type is
    # declared beside them by construction.
    module = max(restricted, key=lambda r: len(r[1]))[0]
    if module.upper() in unrestricted:
        return []              # already in scope through `USE <module>`
    return [f"        USE {module}, ONLY : {', '.join(missing)}"]


def _has_allocatable_fields(arg, derived_types):
    """Check if a derived-type arg's type has ALLOCATABLE fields.

    Returns the DerivedTypeInfo if decomposition is needed, None otherwise.
    """
    if not arg.is_derived_type or not derived_types:
        return None
    type_name = arg.derived_type_name
    for k, v in derived_types.items():
        if k.upper() == type_name.upper():
            if not v.is_c_compatible:
                return v
            return None
    return None



def _safe_break_pieces(line: str) -> list:
    """Split a Fortran line at points a continuation may legally go.

    The pieces CONCATENATE BACK TO THE ORIGINAL, exactly -- that is what makes
    the packing above content-preserving rather than a reformatter.

    Commas are preferred and used alone when there are any: a comma-separated
    list is the shape that produces these lines, and breaking it anywhere else
    is harder to read. Spaces are the fallback for a long assignment, which has
    no commas at its top level.

    Quoted text is opaque. A break inside a string literal changes the string,
    silently, and no compiler would object.
    """
    commas: list = []
    spaces: list = []
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == ",":
            commas.append(i + 1)
        elif ch == " " and i > 0 and line[i - 1] != " ":
            spaces.append(i)

    cuts = commas or spaces
    # A cut at 0 or at the end produces an empty piece and no progress.
    cuts = [c for c in cuts if 0 < c < len(line)]
    if not cuts:
        return [line]

    pieces = []
    prev = 0
    for c in cuts:
        pieces.append(line[prev:c])
        prev = c
    pieces.append(line[prev:])
    return pieces


def _wrap_long_fortran_lines(lines: list, limit: int = 120) -> list:
    """Wrap generated free-form Fortran onto continuation lines.

    A RULE ABOUT LINE CONTENTS, not a list of the places that emit long lines.
    The dummy-argument list was the longest offender and fixing only it left the
    array copy-in statements at 133-153 columns, which fail the same way:

        vit_local_CntrPar%F_GenSpdNotch_Ind(1:vit_CntrPar_F_GenSpdNotch_Ind_n) =
            vit_tmp_CntrPar_F_GenSpdNotch_Ind(1:vit_CntrPar_F_GenSpdNotch_Ind_n)

    -- one statement, two decomposed field names, neither shortenable. Any
    generator that names decomposed fields will keep producing them.

    Breaks only at a comma or a space OUTSIDE a quoted string, and refuses to
    break a line it cannot break safely, returning it unchanged rather than
    corrupting it. An over-long line that reaches the compiler is a loud
    failure; a line broken inside a string literal is a quiet one.

    SPLIT FIRST, THEN PACK. The first version cut the line repeatedly and
    tracked the offset of the remainder against the original break positions --
    and `lstrip()` on the remainder made that mapping wrong by an unknown
    amount, so a break point could become usable again and the loop never
    terminated. It hung the test suite rather than producing a wrong answer,
    which was the good outcome; the shape below cannot loop at all, because
    every piece is consumed exactly once.
    """
    out: list = []
    for line in lines:
        if len(line) <= limit:
            out.append(line)
            continue

        pieces = _safe_break_pieces(line)
        if len(pieces) < 2:
            out.append(line)                  # nothing safe to break on
            continue

        indent = " " * (len(line) - len(line.lstrip()) + 4)
        cur = ""
        for p in pieces:
            # `+ 2` leaves room for the " &" this line will need if another
            # piece follows it.
            if cur.strip() and len(cur) + len(p.rstrip()) + 2 > limit:
                out.append(cur.rstrip() + " &")
                cur = indent + p.lstrip()
            else:
                cur += p
        out.append(cur)
    return out


def _wrapped_dummy_list(head: str, names: list, tail: str,
                        limit: int = 100) -> list:
    """A Fortran dummy-argument list, wrapped onto continuation lines.

    FREE-FORM FORTRAN HAS A LINE-LENGTH LIMIT, and gfortran's default is 132
    columns. Past it the line is TRUNCATED -- with `-Werror=line-truncation` a
    hard error, and without it a silent one, which is worse: the argument list
    simply ends mid-name and the compiler reports something else entirely a few
    lines further down ("USE statement cannot follow attribute declaration").

    This list is not a style question here. Decomposing a derived type emits one
    dummy per field, so `ControlParameters` (214 fields) and `LocalVariables`
    (168) between them produced a SUBROUTINE statement 11,747 characters long.
    Measured on the rosco-r2 campaign's `StateMachine`: 1,153 diagnostics, none
    of which named the cause. Every unit in that campaign taking either type --
    which is most of ROSCO's controllers -- was therefore outside test-validate
    entirely, and nothing said so.

    The limit is 100 rather than 132 so that the wrapped lines stay comfortably
    inside the standard's minimum even after an indent change; the cost of
    wrapping earlier than necessary is nothing.
    """
    out: list = []
    cur = head
    for i, n in enumerate(names):
        piece = n + ("" if i == len(names) - 1 else ", ")
        if len(cur) + len(piece) > limit and cur.strip() not in ("", head.strip()):
            out.append(cur + "&")
            cur = "        " + piece
        else:
            cur += piece
    out.append(cur + tail)
    return out


def _decomposed_param_names(arg, type_info, kind_aliases=None):
    """Ordered, collision-free bridge parameter names for a decomposed type.

    Returns a list of (field, kind, rank, names) where `kind` is one of
    "alloc" / "nested" / "char" / "defchar" / "fixed" / "scalar" and `names`
    maps a role ("ptr", "n", "rows", "cols", "buf", "cap", "scalar") to the
    parameter name to emit.

    WHY THIS EXISTS. The naive scheme is `vit_<arg>_<field>` with `_ptr`/`_n`
    appended for an ALLOCATABLE field, and it breaks when a SCALAR field is
    named exactly like some other field's generated size parameter. ROSCO's
    ControlParameters has both, twice over:

        WE_CP        ALLOCATABLE -> emits vit_CntrPar_WE_CP_ptr, .._WE_CP_n
        WE_CP_n      scalar      -> emits vit_CntrPar_WE_CP_n       <-- same name

        WE_FOPoles   ALLOCATABLE -> emits ..._WE_FOPoles_n
        WE_FOPoles_N scalar      -> emits ..._WE_FOPoles_N          <-- same name
                                                                        in Fortran

    The first collides in both languages; the second collides ONLY in Fortran,
    which is case-insensitive, and would compile in C++ while producing a
    duplicate dummy argument in the bridge. Uniqueness is therefore enforced
    case-insensitively, by suffixing the LATER occurrence with _v2, _v3, ...

    Both the Fortran bridge and the C++ extern declaration must agree on these
    names, so both call this rather than deriving names independently -- which
    is how the two loops drifted into emitting the same duplicate twice.
    """
    used = set()

    def uniq(name):
        key = name.lower()
        if key not in used:
            used.add(key)
            return name
        i = 2
        while f"{name}_v{i}".lower() in used:
            i += 1
        out = f"{name}_v{i}"
        used.add(out.lower())
        return out

    prefix = f"vit_{arg.name}"
    result = []
    for fld in type_info.fields:
        if fld.is_pointer:
            continue
        base = f"{prefix}_{fld.name}"
        if is_deferred_character(fld.fortran_type, kind_aliases):
            # `CHARACTER(:), ALLOCATABLE :: ErrMsg`. Asked BEFORE is_allocatable,
            # because it is allocatable and the allocatable path would ask
            # `get_type_mapping` for the C type of `CHARACTER(:)` -- which has
            # none, and which raises. That raise is the whole of what kept
            # TYPE(ErrorVariables) out of test-validate, and with it every unit
            # in the rosco-r2 campaign taking an error argument.
            #
            # Three parameters, the same shape the view struct already crosses
            # (see view_populator.VIT_DEFERRED_CHAR_HEADROOM), because the
            # LENGTH of a deferred-length CHARACTER is part of its allocation:
            #
            #   buf  CHARACTER(KIND=C_CHAR) (*)     the bytes, C-owned
            #   n    INTEGER(C_INT), by REFERENCE   LEN() in; LEN() out
            #   cap  INTEGER(C_INT), by VALUE       how many bytes may be written
            #
            # `n` is by reference and not by VALUE, unlike every other extent
            # here, and that is the point: the function under test may REPLACE
            # the string with a longer one, so its length is an OUTPUT. An
            # extent passed by value would let the bridge run and would leave
            # the caller unable to see that the length had changed.
            result.append((fld, "defchar", 0, {
                "buf": uniq(base),
                "n": uniq(f"{base}_n"),
                "cap": uniq(f"{base}_cap"),
            }))
            continue
        if fld.is_allocatable:
            rank = fld.array_dims.count(",") + 1 if fld.array_dims else 1
            names = {"ptr": uniq(f"{base}_ptr")}
            if rank == 1:
                names["n"] = uniq(f"{base}_n")
                # CAPACITY, and it is what makes a rank-1 ALLOCATABLE field an
                # OUTPUT rather than only an input. The callee may allocate the
                # field itself, or reallocate it to a different extent, and the
                # caller's buffer has to be big enough to receive whatever it
                # chose -- so the buffer's size and the number of elements
                # SUPPLIED are two different numbers and both have to be stated.
                names["cap"] = uniq(f"{base}_cap")
            else:
                names["rows"] = uniq(f"{base}_rows")
                names["cols"] = uniq(f"{base}_cols")
            result.append((fld, "alloc", rank, names))
        elif fld.fortran_type.upper().startswith("TYPE("):
            result.append((fld, "nested", 1, {"ptr": uniq(f"{base}_ptr")}))
        elif "CHARACTER" in fld.fortran_type.upper():
            # A CHARACTER field of a decomposed type. Emitted as
            # `CHARACTER(1024), VALUE`, which gfortran rejects outright ("must
            # be of length 1 as it has the VALUE attribute"), so the bridge did
            # not compile. It crosses the way a CHARACTER ARGUMENT does -- a
            # C_CHAR buffer of the declared length, by reference -- which is
            # also what makes `ErrMsg` comparable rather than merely present.
            m = re.search(r"\((?:LEN\s*=\s*)?(\d+)\)", fld.fortran_type, re.IGNORECASE)
            result.append((fld, "char", int(m.group(1)) if m else 0,
                           {"scalar": uniq(base)}))
        elif getattr(fld, "is_array", False) and fld.array_dims:
            # A FIXED-SIZE array field -- `REAL(DbKi) :: CC_DesiredL(12)`.
            #
            # This used to fall through to "scalar", and the consequences were
            # both silent and severe. The bridge declared ONE `REAL(C_DOUBLE),
            # VALUE` and then assigned it to the whole array, so every element
            # received the SAME value; and being VALUE it could not be written,
            # so there was no copy-back and the field was invisible as an
            # output. `CableControl` writes `LocalVar%CC_ActuatedL(I_GROUP)` --
            # its principal result -- and a harness on this bridge could
            # neither vary those elements independently nor read any of them
            # back. That is the per-element blindness of the #55 defect,
            # rebuilt inside the tool meant to catch it.
            #
            # `rank` here is the bound expression, not a number, because the
            # declaration needs the bounds verbatim.
            result.append((fld, "fixed", fld.array_dims, {"scalar": uniq(base)}))
        else:
            result.append((fld, "scalar", 1, {"scalar": uniq(base)}))
    return result


def _emit_decomposed_bridge_params(arg, type_info, kind_aliases,
                                   c_params, local_decls, pre_call, post_call,
                                   call_args):
    """Emit bridge parameters for a derived type with ALLOCATABLE fields.

    Instead of passing the whole struct as C_PTR (which fails for ALLOCATABLE),
    emits individual parameters for each field. Scalar fields are passed by VALUE;
    ALLOCATABLE fields are passed as C_PTR + dimension sizes. The bridge constructs
    a real Fortran type internally.

    Parameter names are prefixed with vit_{arg.name}_ to avoid collisions with
    names imported via USE statements.
    """
    local_var = f"vit_local_{arg.name}"
    spec = _decomposed_param_names(arg, type_info, kind_aliases)
    is_out = decomposed_arg_is_out(arg)
    # A CHARACTER field whose declared length cannot be read out of the
    # declaration has no length to declare, so it cannot cross. Collected and
    # reported rather than dropped in silence. (A DEFERRED-length field is not
    # this case -- it crosses as buf/n/cap, see the "defchar" branches.)
    skipped_char: list[str] = []

    for fld, kind, rank, names in spec:
        if kind == "alloc":
            c_params.append((f"        TYPE(C_PTR), VALUE :: {names['ptr']}", names["ptr"]))
            if rank == 1:
                # `n` BY REFERENCE, not by VALUE, and this is a change of shape
                # rather than of spelling. Every ALLOCATABLE field used to cross
                # as (pointer, extent-by-value) with NO copy-back at all, so a
                # field the callee WRITES -- `ExtDLL%avrSWAP`, which is the whole
                # output of ExtController -- was supplied to the reference and
                # then discarded, while the translation wrote into the caller's
                # buffer. Input-only, silently, for every such field in the
                # campaign.
                #
                # Three states have to be distinguishable and one of them is
                # ABSENCE (P6), so the encoding is stated here and re-stated in
                # the copy-back:
                #     n <  0   the field is NOT ALLOCATED
                #     n == 0   allocated, empty
                #     n >  0   allocated with n elements
                # and `cap` is the buffer's size in either direction.
                c_params.append((
                    f"        INTEGER(C_INT) :: {names['n']}", names["n"]))
                c_params.append((
                    f"        INTEGER(C_INT), VALUE :: {names['cap']}", names["cap"]))
            else:
                c_params.append((
                    f"        INTEGER(C_INT), VALUE :: {names['rows']}", names["rows"]))
                c_params.append((
                    f"        INTEGER(C_INT), VALUE :: {names['cols']}", names["cols"]))
        elif kind == "nested":
            # A nested derived-type field -- `TYPE(piParams) :: piP` inside
            # LocalVariables.
            #
            # This used to emit a Fortran COMMENT while still putting the name in
            # the argument list, so the bridge did not compile at all: "Symbol
            # 'vit_localvar_piP' has no IMPLICIT type". Every unit taking
            # `LocalVariables` -- which is most of ROSCO's controllers, including
            # CableControl -- was therefore outside test-validate entirely, and
            # the failure came at build time with no hint that the generator was
            # the cause.
            #
            # It crosses as an opaque pointer, exactly as a whole derived-type
            # argument does, and is copied in and back out.
            c_params.append((f"        TYPE(C_PTR), VALUE :: {names['ptr']}",
                             names["ptr"]))
        elif kind == "char":
            if not rank:
                skipped_char.append(fld.name)
                continue
            c_params.append((f"        CHARACTER(KIND=C_CHAR) :: "
                             f"{names['scalar']}({rank})", names["scalar"]))
            local_decls.append(f"        INTEGER :: vit_ci_{arg.name}_{fld.name}")
        elif kind == "defchar":
            c_params.append((f"        CHARACTER(KIND=C_CHAR) :: "
                             f"{names['buf']}(*)", names["buf"]))
            c_params.append((f"        INTEGER(C_INT) :: {names['n']}", names["n"]))
            c_params.append((f"        INTEGER(C_INT), VALUE :: {names['cap']}",
                             names["cap"]))
            local_decls.append(f"        INTEGER :: vit_dc_{arg.name}_{fld.name}")
        elif kind == "fixed":
            iso_type = get_type_mapping(fld.fortran_type, kind_aliases).iso_c_type
            # By reference, with the declared bounds: C passes the buffer, every
            # element crosses, and INTENT(INOUT/OUT) can copy back.
            c_params.append((f"        {iso_type} :: {names['scalar']}({rank})",
                             names["scalar"]))
        else:
            iso_type = _decomposed_scalar_iso_c(fld, kind_aliases)
            # BY REFERENCE when the owning argument is INOUT/OUT, so the field
            # can be copied back. `ErrVar%ErrStat` is `ExtController`'s error
            # output and it used to cross by VALUE with no copy-back at all --
            # the reference set it, the bridge dropped it, and the harness
            # compared the caller's own input against the translation's answer
            # and called the translation wrong on all 163 cases.
            value_attr = "" if is_out else ", VALUE"
            c_params.append((f"        {iso_type}{value_attr} :: {names['scalar']}",
                             names["scalar"]))

    # Local: real Fortran type (not POINTER)
    local_decls.append(f"        TYPE({type_info.name}) :: {local_var}")

    # Temp pointers and loop variables for ALLOCATABLE fields
    for fld in type_info.fields:
        if not fld.is_allocatable:
            continue
        if is_deferred_character(fld.fortran_type, kind_aliases):
            # Staged through a CHARACTER local, not a POINTER to an element
            # type: `CHARACTER(:)` has no element type to point at. This is the
            # second place the allocatable path would have called
            # `get_type_mapping` on it and raised.
            continue
        iso_type = get_type_mapping(fld.fortran_type, kind_aliases).iso_c_type
        local_decls.append(f"        {iso_type}, POINTER :: vit_tmp_{arg.name}_{fld.name}(:)")
        rank = fld.array_dims.count(",") + 1 if fld.array_dims else 1
        if rank == 2:
            local_decls.append(
                f"        INTEGER :: vit_i_{fld.name}, vit_j_{fld.name}")

    # Temp POINTERs for nested derived-type fields
    for fld, kind, rank, names in spec:
        if kind == "nested":
            tname = fld.fortran_type.strip()[5:-1].strip()   # TYPE(x) -> x
            local_decls.append(
                f"        TYPE({tname}), POINTER :: vit_nested_{arg.name}_{fld.name}")

    # Pre-call: populate scalar fields
    for fld, kind, rank, names in spec:
        if kind == "alloc":
            tmp = f"vit_tmp_{arg.name}_{fld.name}"
            if rank == 1:
                p_ptr, p_n, p_cap = names["ptr"], names["n"], names["cap"]
                # The Fortran pointer spans the whole BUFFER, not the supplied
                # elements: the copy-back writes into the part beyond `n`.
                pre_call.append(f"        IF (C_ASSOCIATED({p_ptr})) &")
                pre_call.append(
                    f"            CALL C_F_POINTER({p_ptr}, {tmp}, [MAX({p_cap}, 1)])")
                pre_call.append(f"        IF ({p_n} >= 0) THEN")
                pre_call.append(f"            ALLOCATE({local_var}%{fld.name}({p_n}))")
                pre_call.append(f"            IF ({p_n} > 0 .AND. C_ASSOCIATED({p_ptr})) &")
                pre_call.append(
                    f"                {local_var}%{fld.name}(1:{p_n}) = {tmp}(1:{p_n})")
                pre_call.append(f"        END IF")
            elif rank == 2:
                p_ptr, p_rows, p_cols = names["ptr"], names["rows"], names["cols"]
                vi = f"vit_i_{fld.name}"
                vj = f"vit_j_{fld.name}"
                pre_call.append(f"        IF (C_ASSOCIATED({p_ptr})) THEN")
                pre_call.append(
                    f"            ALLOCATE({local_var}%{fld.name}({p_rows}, {p_cols}))")
                pre_call.append(
                    f"            CALL C_F_POINTER({p_ptr}, {tmp}, [{p_rows} * {p_cols}])")
                pre_call.append(f"            DO {vj} = 1, {p_cols}")
                pre_call.append(f"                DO {vi} = 1, {p_rows}")
                pre_call.append(
                    f"                    {local_var}%{fld.name}({vi}, {vj}) = &")
                pre_call.append(
                    f"                        {tmp}(({vj}-1)*{p_rows} + {vi})")
                pre_call.append(f"                END DO")
                pre_call.append(f"            END DO")
                pre_call.append(f"        END IF")
        elif kind == "defchar":
            di = f"vit_dc_{arg.name}_{fld.name}"
            # `cap > 0` IS "the field is allocated", and it is the only thing
            # the C side states that can say so. The populator publishes a NULL
            # pointer with n = 0 AND cap = 0 for an unallocated field, and
            # cap = LEN + HEADROOM (> 0) for an allocated one -- including an
            # allocated EMPTY string, whose n is also 0. Branching on `n` would
            # collapse those two into one input, which is P6: absence rendering
            # as a value.
            pre_call.append(f"        IF ({names['cap']} > 0) THEN")
            pre_call.append(f"            ALLOCATE(CHARACTER(LEN={names['n']}) :: "
                            f"{local_var}%{fld.name})")
            pre_call.append(f"            DO {di} = 1, {names['n']}")
            pre_call.append(f"                {local_var}%{fld.name}({di}:{di}) = "
                            f"{names['buf']}({di})")
            pre_call.append(f"            END DO")
            pre_call.append(f"        END IF")
        elif kind == "char":
            if not rank:
                continue
            ci = f"vit_ci_{arg.name}_{fld.name}"
            pre_call.append(f"        DO {ci} = 1, {rank}")
            pre_call.append(f"            {local_var}%{fld.name}({ci}:{ci}) = "
                            f"{names['scalar']}({ci})")
            pre_call.append(f"        END DO")
        elif kind == "nested":
            tmp = f"vit_nested_{arg.name}_{fld.name}"
            pre_call.append(f"        IF (C_ASSOCIATED({names['ptr']})) THEN")
            pre_call.append(f"            CALL C_F_POINTER({names['ptr']}, {tmp})")
            pre_call.append(f"            {local_var}%{fld.name} = {tmp}")
            pre_call.append(f"        END IF")
        elif kind == "fixed":
            pre_call.append(f"        {local_var}%{fld.name} = {names['scalar']}")
        elif kind == "scalar":
            pre_call.append(f"        {local_var}%{fld.name} = {names['scalar']}")

    # Copy fixed-size array fields BACK. A struct crossing as a decomposed list
    # loses the aliasing a pointer would have given it, so without this every
    # such field is write-only from the harness's point of view -- supplied and
    # never observed, which is R4 defeated at the bridge.
    if is_out:
        for fld, kind, rank, names in spec:
            if kind == "alloc" and rank == 1:
                # THE COPY-BACK THAT DID NOT EXIST. Without it the reference
                # writes into a Fortran allocation the bridge then deallocates,
                # while the translation writes into the caller's buffer -- so the
                # two sides are not being compared on this field at all.
                #
                # The callee's chosen EXTENT comes back too, in `n`, because a
                # translation that allocates a different number of elements is
                # exactly the defect a fixed-size comparison cannot see (unit
                # #1's CFI descriptor, one container in).
                #
                # REFUSED and REPORTED, never truncated, when the result does
                # not fit the buffer the caller stated: a short prefix of the
                # right answer is the one wrong answer a bit comparison passes.
                tmp = f"vit_tmp_{arg.name}_{fld.name}"
                p_n, p_cap = names["n"], names["cap"]
                post_call.append(f"        IF (.NOT. ALLOCATED({local_var}%{fld.name})) THEN")
                post_call.append(f"            {p_n} = -1_C_INT")
                # An EMPTY result needs no buffer, so it must not be reported as
                # one that did not fit. Without this a field the caller supplied
                # unallocated and the callee left empty printed a refusal on
                # every case -- a true statement about a buffer of zero, and
                # noise that would teach a reader to ignore the message.
                post_call.append(f"        ELSE IF (SIZE({local_var}%{fld.name}) == 0) THEN")
                post_call.append(f"            {p_n} = 0_C_INT")
                post_call.append(f"        ELSE IF (SIZE({local_var}%{fld.name}) <= {p_cap} "
                                 f".AND. C_ASSOCIATED({names['ptr']})) THEN")
                post_call.append(f"            {p_n} = INT(SIZE({local_var}%{fld.name}), C_INT)")
                post_call.append(f"            IF ({p_n} > 0) &")
                post_call.append(f"                {tmp}(1:{p_n}) = {local_var}%{fld.name}(1:{p_n})")
                post_call.append(f"        ELSE")
                post_call.append(f"            WRITE(ERROR_UNIT,'(A,I0,A,I0,A)') &")
                post_call.append(f"                'VIT bridge: {arg.name}%{fld.name} came back with ', &")
                post_call.append(f"                SIZE({local_var}%{fld.name}), ' element(s), past the ', &")
                post_call.append(f"                {p_cap}, '-element buffer the caller supplied; not copied back'")
                post_call.append(f"        END IF")
            elif kind == "alloc" and rank == 2:
                # Rank 2 has TWO extents and no capacity, so it can only be
                # copied back when the callee kept the shape the caller stated.
                # Anything else is reported rather than reshaped: guessing which
                # of two dimensions moved is how a stride collapse is written.
                tmp = f"vit_tmp_{arg.name}_{fld.name}"
                vi, vj = f"vit_i_{fld.name}", f"vit_j_{fld.name}"
                p_rows, p_cols = names["rows"], names["cols"]
                post_call.append(f"        IF (ALLOCATED({local_var}%{fld.name}) "
                                 f".AND. C_ASSOCIATED({names['ptr']})) THEN")
                post_call.append(f"            IF (SIZE({local_var}%{fld.name}, 1) == {p_rows} "
                                 f".AND. SIZE({local_var}%{fld.name}, 2) == {p_cols}) THEN")
                post_call.append(f"                DO {vj} = 1, {p_cols}")
                post_call.append(f"                    DO {vi} = 1, {p_rows}")
                post_call.append(f"                        {tmp}(({vj}-1)*{p_rows} + {vi}) = &")
                post_call.append(f"                            {local_var}%{fld.name}({vi}, {vj})")
                post_call.append(f"                    END DO")
                post_call.append(f"                END DO")
                post_call.append(f"            ELSE")
                post_call.append(f"                WRITE(ERROR_UNIT,'(A,I0,A,I0,A)') &")
                post_call.append(f"                    'VIT bridge: {arg.name}%{fld.name} came back ', &")
                post_call.append(f"                    SIZE({local_var}%{fld.name}, 1), ' x ', &")
                post_call.append(f"                    SIZE({local_var}%{fld.name}, 2), &")
                post_call.append(f"                    ', not the shape the caller stated; not copied back'")
                post_call.append(f"            END IF")
                post_call.append(f"        END IF")
            elif kind == "fixed":
                post_call.append(f"        {names['scalar']} = {local_var}%{fld.name}")
            elif kind == "nested":
                tmp = f"vit_nested_{arg.name}_{fld.name}"
                post_call.append(f"        IF (C_ASSOCIATED({names['ptr']})) "
                                 f"{tmp} = {local_var}%{fld.name}")
            elif kind == "scalar":
                post_call.append(f"        {names['scalar']} = {local_var}%{fld.name}")
            elif kind == "char" and rank:
                ci = f"vit_ci_{arg.name}_{fld.name}"
                post_call.append(f"        DO {ci} = 1, {rank}")
                post_call.append(f"            {names['scalar']}({ci}) = "
                                 f"{local_var}%{fld.name}({ci}:{ci})")
                post_call.append(f"        END DO")
            elif kind == "defchar":
                di = f"vit_dc_{arg.name}_{fld.name}"
                # REFUSED, not truncated, and the refusal is REPORTED. The
                # capacity is finite, so a function that grows the string past
                # it cannot be honoured -- and writing the first `cap` bytes
                # would produce a shorter-but-plausible string, which is the one
                # kind of wrong answer a bit-for-bit comparison cannot catch.
                # Same rule as the view populator's reverse copy, for the same
                # reason; if the two disagreed, the harness and the shipped
                # bridge would be measuring different functions.
                post_call.append(f"        IF (ALLOCATED({local_var}%{fld.name})) THEN")
                post_call.append(f"            IF (LEN({local_var}%{fld.name}) <= "
                                 f"{names['cap']}) THEN")
                post_call.append(f"                DO {di} = 1, LEN({local_var}%{fld.name})")
                post_call.append(f"                    {names['buf']}({di}) = "
                                 f"{local_var}%{fld.name}({di}:{di})")
                post_call.append(f"                END DO")
                post_call.append(f"                {names['n']} = "
                                 f"INT(LEN({local_var}%{fld.name}), C_INT)")
                post_call.append(f"            ELSE")
                post_call.append(f"                WRITE(ERROR_UNIT,'(A,I0,A,I0,A)') &")
                post_call.append(f"                    'VIT bridge: {arg.name}%{fld.name} "
                                 f"assignment of ', LEN({local_var}%{fld.name}), &")
                post_call.append(f"                    ' bytes exceeds the ', {names['cap']}, &")
                post_call.append(f"                    '-byte buffer the caller supplied; "
                                 f"left unchanged'")
                post_call.append(f"            END IF")
                post_call.append(f"        END IF")

    # Post-call: DEALLOCATE ALLOCATABLEs
    for fld in type_info.fields:
        if fld.is_allocatable:
            post_call.append(
                f"        IF (ALLOCATED({local_var}%{fld.name})) "
                f"DEALLOCATE({local_var}%{fld.name})")

    call_args.append(local_var)


def decomposed_scalar_c_type(fld, kind_aliases=None) -> str:
    """The C type of a SCALAR field of a decomposed derived type.

    `get_type_mapping` alone is not the answer, and the exception is LOGICAL.
    The VIEW STRUCT generator emits `LOGICAL(C_BOOL)` / `bool` for it
    (`view_populator._field_to_iso_c_decl`) while the general type map says
    `INTEGER(C_INT)` / `int32_t` -- two generators over one type, which is the
    disagreement this bridge exists to be measured against rather than to
    contain. While such a field crossed by VALUE the mismatch was an implicit
    conversion nobody saw; by reference it is `cannot convert 'int8_t*' to
    'int32_t*'`, which is the same defect finally saying so.

    PUBLIC for the same reason as `decomposed_arg_is_out`: the loop's harness
    emitter needs the identical answer.
    """
    if fld.fortran_type.upper().startswith("LOGICAL"):
        # `int8_t`, which is what VIT's C-struct generator emits for a LOGICAL
        # field, and it is one byte -- the same object `LOGICAL(C_BOOL)` is on
        # the Fortran side. `bool` would be the natural spelling and is the
        # WRONG one here: the struct member is what the pointer has to point at.
        return "int8_t"
    return get_type_mapping(fld.fortran_type, kind_aliases).c_type


def _decomposed_scalar_iso_c(fld, kind_aliases=None) -> str:
    """The Fortran ISO_C_BINDING type matching `decomposed_scalar_c_type`."""
    if fld.fortran_type.upper().startswith("LOGICAL"):
        return "LOGICAL(C_BOOL)"
    return get_type_mapping(fld.fortran_type, kind_aliases).iso_c_type


def decomposed_arg_is_out(arg) -> bool:
    """Is a decomposed derived-type argument written by the callee?

    PUBLIC, and it is the only reason it is not a `_name`: the loop's harness
    emitter has to agree with this exactly -- a field the bridge takes by
    reference and the caller passes by value is a wrong answer the linker
    cannot see. One spelling, imported, like `_decomposed_param_names`.

    An argument with NO declared INTENT counts as written: that is what Fortran
    permits, and the safe direction here is to compare a field that could not
    change rather than to drop one that did.
    """
    intent = getattr(arg, "intent", None)
    text = str(getattr(intent, "value", None) or getattr(intent, "name", None)
               or intent or "").upper()
    return "OUT" in text or text in ("", "NONE")


def _bridge_reports_on_stderr(sig, kind_aliases, derived_types) -> bool:
    """Does this bridge contain a branch that REFUSES a copy-back and says so?

    True whenever any argument decomposes, because both refusal branches -- the
    deferred-length CHARACTER field that will not fit its buffer, and the
    ALLOCATABLE field that came back a different size -- live in the decomposed
    emitter. Asked of `_has_allocatable_fields`, the same authority the emitter
    uses, rather than by re-reading the declarations already emitted: a
    predicate that parses generated text is a second spelling of the rule, and
    the two drift.
    """
    for arg in getattr(sig, "args", ()):
        if not getattr(arg, "is_derived_type", False):
            continue
        if _has_allocatable_fields(arg, derived_types) is not None:
            return True
    return False


def _iso_c_for(fortran_type: str, kind_aliases: Optional[dict]) -> str:
    """The ISO_C_BINDING type for a Fortran declaration, via the kind aliases.

    Several branches in the bridge generator wrote `REAL(C_DOUBLE)` as a literal,
    which is right for `REAL(DbKi)` and wrong for `REAL(ReKi)` and `REAL(SiKi)` --
    both REAL(4) in ROSCO. Falls back to the old literals only if the mapping
    cannot resolve the type, so an unknown type degrades rather than raising.
    """
    try:
        iso = get_type_mapping(fortran_type, kind_aliases).iso_c_type
        if iso:
            return iso
    except Exception:
        pass
    up = fortran_type.upper()
    return ("REAL(C_DOUBLE)" if "REAL" in up else
            "INTEGER(C_INT)" if "INTEGER" in up else
            "LOGICAL(C_BOOL)" if "LOGICAL" in up else f"! TODO: {fortran_type}")


_SIZE_OF_ARG = re.compile(r"^\s*SIZE\s*\(\s*([A-Za-z_]\w*)\s*\)\s*$", re.IGNORECASE)


def _bridge_result_dims(result_dims: str, param_names: List[str]) -> str:
    """A FUNCTION RESULT's DIMENSION text, spelled in the BRIDGE's own dummies.

    Every dimension is passed through UNCHANGED except one shape:
    `SIZE(<arg>)`, where `n_<arg>` is a dummy of this bridge. The original
    declares such an argument assumed-shape and `SIZE()` of it is its extent;
    the bridge declares it assumed-size, where gfortran rejects `SIZE()`
    outright. Both spellings name the same number and only one of them
    compiles here.

    Deliberately not an expression evaluator. `SIZE(x) + 1` and `SIZE(y)` for a
    `y` that did not cross fall through untouched, so they fail loudly at the
    compiler rather than being sized by a guess -- and every dimension shape
    that already worked (`n`, `n, n`, `3`) takes a path this function does not
    change.
    """
    parts = []
    for part in str(result_dims).split(","):
        m = _SIZE_OF_ARG.match(part)
        if m and f"n_{m.group(1)}" in param_names:
            parts.append(f"n_{m.group(1)}")
        else:
            parts.append(part.strip())
    return ", ".join(parts)


def generate_fortran_bridge(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    source_file: Optional[str] = None,
    derived_types: Optional[dict] = None,
) -> str:
    """Generate a BIND(C) Fortran bridge for calling the original function from C++.

    The bridge allows the test harness to call the original Fortran validation
    function for comparison against the C++ translation. Named <name>_f90 to
    distinguish from the C++ function.

    When a derived-type argument has ALLOCATABLE fields, the bridge decomposes
    it into individual scalar parameters plus pointer+size pairs for each
    ALLOCATABLE field. This is necessary because C_F_POINTER cannot reconstruct
    Fortran ALLOCATABLE descriptors from C view structs.

    Args:
        sig: Parsed Fortran function signature
        kind_aliases: Project kind mappings
        source_file: Path to the Fortran source file (for extracting module USE statements)
        derived_types: Registry of known derived types (for ALLOCATABLE detection)

    Returns:
        Fortran source string for the bridge subroutine
    """
    func_name = sig.name
    func_lower = func_name.lower()
    bridge_name = f"{func_lower}_f90"

    lines = []
    lines.append(f"! VIT: Test-validate bridge for {func_name}")
    lines.append(f"! Allows C++ test harness to call the original Fortran function.")
    lines.append(f"! Handles C↔Fortran type conversions for derived types and CHARACTER.")

    # Build C-compatible parameter list for BIND(C)
    c_params = []  # (fortran_decl, name)
    local_decls = []  # Local Fortran variables for type conversion
    pre_call = []  # Conversion before the call
    post_call = []  # Conversion after the call
    call_args = []  # Arguments to pass to the original function

    for arg in sig.args:
        fortran_type = arg.fortran_type.upper()
        # arg.intent is IntentKind.NONE (empty value) for a dummy declared
        # without an INTENT attribute, which is legal Fortran and common in
        # older code -- ROSCO_Helpers declares several that way. Emitting
        # INTENT() is a syntax error, so fall back to INOUT, which is what an
        # unspecified intent permits.
        intent = arg.intent.value if (arg.intent and arg.intent.value) else "INOUT"

        if getattr(arg, "is_optional", False):
            # AN OPTIONAL DUMMY IS TWO THINGS ON THE C SIDE, AND THIS BRIDGE
            # USED TO EMIT ONE OF THEM.
            #
            # `interface_gen.build_c_params` emits `has_<x>` immediately before
            # `<x>` for every OPTIONAL argument, and the generated C++ test
            # declares and passes it. This generator had no OPTIONAL branch at
            # all: the argument fell through to the scalar branches below, the
            # bridge took one fewer dummy than the caller passed, and the
            # reference was called with the argument ALWAYS PRESENT.
            #
            # Two defects in one, and the second is the dangerous one:
            #   1. an argument-count mismatch across a BIND(C) boundary that no
            #      linker checks -- the reference would have read `InitialValue`
            #      out of the `has_InitialValue` slot;
            #   2. `PRESENT(x) == .FALSE.` unreachable through the reference,
            #      which is precisely the branch the harness's flag rule
            #      generates `has_<x> = 0` cases to exercise. A green with that
            #      path unreachable is a green about half the procedure.
            #
            # Absence is expressed by passing an UNALLOCATED ALLOCATABLE local.
            # Measured under `gfortran -std=f2008 -Wall`, both that and a
            # disassociated POINTER make PRESENT() false; ALLOCATABLE is used
            # because it needs no TARGET and introduces no aliasing.
            #
            # DELIBERATELY NARROW, in the shape unit #6's KGen predicate
            # established: scalar, intrinsic type, INTENT(IN). All 24 OPTIONAL
            # dummies in ROSCO are exactly that. Anything else RAISES rather
            # than falling through, because falling through is what produced
            # this defect and a refusal is the only outcome that cannot be
            # mistaken for a measurement.
            if (arg.is_array or arg.is_derived_type
                    or "CHARACTER" in fortran_type or intent != "IN"):
                raise ValueError(
                    f"{sig.name}: OPTIONAL argument '{arg.name}' is "
                    f"{'an array' if arg.is_array else 'a derived type' if arg.is_derived_type else 'CHARACTER' if 'CHARACTER' in fortran_type else f'INTENT({intent})'}; "
                    f"the bridge only stages scalar intrinsic INTENT(IN) "
                    f"OPTIONALs, and this shape is UNMEASURED. Emitting it as "
                    f"always-present is what this branch exists to stop.")
            is_logical = "LOGICAL" in fortran_type
            iso = "INTEGER(C_INT)" if is_logical else _iso_c_for(fortran_type, kind_aliases)
            c_params.append(
                (f"        INTEGER(C_INT), VALUE, INTENT(IN) :: has_{arg.name}",
                 f"has_{arg.name}"))
            c_params.append(
                (f"        {iso}, VALUE, INTENT(IN) :: {arg.name}", arg.name))
            # The staging local is declared in the ISO_C type, not the
            # original's kind alias -- the same choice every non-optional
            # branch below already makes when it passes its C dummy straight
            # through to the reference, so the two paths cannot drift apart.
            # `LOGICAL` is the default kind 4, matching the `<name>_f` local
            # the LOGICAL branch declares.
            local_type = "LOGICAL" if is_logical else iso
            local_decls.append(
                f"        {local_type}, ALLOCATABLE :: {arg.name}_opt")
            pre_call.append(f"        IF (has_{arg.name} /= 0) THEN")
            pre_call.append(f"            ALLOCATE({arg.name}_opt)")
            pre_call.append(
                f"            {arg.name}_opt = "
                + (f"({arg.name} /= 0)" if is_logical else arg.name))
            pre_call.append(f"        END IF")
            call_args.append(f"{arg.name}_opt")
            continue

        if arg.is_derived_type:
            type_name = arg.derived_type_name
            type_info = _has_allocatable_fields(arg, derived_types)
            if type_info is not None:
                # ALLOCATABLE fields: decompose into individual parameters
                _emit_decomposed_bridge_params(
                    arg, type_info, kind_aliases,
                    c_params, local_decls, pre_call, post_call, call_args,
                )
            else:
                # C-compatible type: use simple C_F_POINTER
                c_params.append((f"        TYPE(C_PTR), VALUE :: {arg.name}_ptr", f"{arg.name}_ptr"))
                local_decls.append(f"        TYPE({type_name}), POINTER :: {arg.name}")
                pre_call.append(f"        CALL C_F_POINTER({arg.name}_ptr, {arg.name})")
                call_args.append(arg.name)
        elif is_alloc_return(arg):
            # ALLOCATE-ON-RETURN, the convention `interface_gen` SHIPS for an
            # `INTENT(OUT), ALLOCATABLE` array: `build_c_params` emits
            # `double** <x>` plus one `int* n_<x>[_rows|_cols]` per rank, the
            # callee mallocs and hands the pointer back, and the caller frees
            # with `vit_free`.
            #
            # This bridge used to have no branch for it. An INTENT(OUT)
            # ALLOCATABLE array is also assumed-shape, so it fell into the
            # assumed-shape branch below and came out as
            # `REAL(C_DOUBLE), INTENT(OUT) :: X(*)` + `n_X`, called as
            # `X(1:n_X)` -- a rank-1 non-allocatable section passed to a rank-2
            # ALLOCATABLE dummy, which is not Fortran and does not compile.
            # That is the `c_assumed_shape_2d` cell the conformance matrix
            # records as UNSUPPORTED, and it is a statement about THIS
            # generator; `interface_gen` crosses the same argument (rosco-r2
            # unit #17).
            #
            # The two generators must agree, because the harness calls both
            # sides through one C prototype: the reference through this bridge,
            # the translation directly. So the parameter list here is
            # `_alloc_return_extent_names` -- the same helper `interface_gen`
            # names its extents with -- rather than a second spelling.
            resolved = _resolve_fortran_type(arg.fortran_type, kind_aliases)
            dims = ",".join(":" for _ in range(max(1, arg.array_rank)))
            extents = _alloc_return_extent_names(arg)
            c_params.append(
                (f"        TYPE(C_PTR), INTENT(OUT) :: {arg.name}_cptr",
                 f"{arg.name}_cptr"))
            for n in extents:
                c_params.append(
                    (f"        INTEGER(C_INT), INTENT(OUT) :: {n}", n))
            local_decls.append(
                f"        {resolved}, ALLOCATABLE :: {arg.name}({dims})")
            local_decls.append(f"        {resolved}, POINTER :: {arg.name}_fptr({dims})")
            # The C side owns the buffer either way, so the bridge allocates it
            # with the SAME allocator the translation uses and the harness frees
            # both with the same `free`. `vit_malloc` is emitted by the harness
            # beside `vit_free`.
            local_decls.append("        INTERFACE")
            local_decls.append("            FUNCTION vit_malloc(vit_n) BIND(C, NAME='vit_malloc') RESULT(vit_p)")
            local_decls.append("                USE ISO_C_BINDING")
            local_decls.append("                INTEGER(C_SIZE_T), VALUE :: vit_n")
            local_decls.append("                TYPE(C_PTR) :: vit_p")
            local_decls.append("            END FUNCTION vit_malloc")
            local_decls.append("        END INTERFACE")
            call_args.append(arg.name)
            # C_NULL_PTR means NOT ALLOCATED. An extent of 0 does not: an
            # allocated zero-size array and an unallocated one are different
            # answers, and this is the only place the difference survives.
            post_call.append(f"        {arg.name}_cptr = C_NULL_PTR")
            for n in extents:
                post_call.append(f"        {n} = 0")
            post_call.append(f"        IF (ALLOCATED({arg.name})) THEN")
            for i, n in enumerate(extents, start=1):
                post_call.append(f"            {n} = INT(SIZE({arg.name}, {i}), C_INT)")
            post_call.append(f"            {arg.name}_cptr = vit_malloc("
                             f"MAX(INT(SIZE({arg.name}), C_SIZE_T), 1_C_SIZE_T) * "
                             f"INT(STORAGE_SIZE({arg.name}) / 8, C_SIZE_T))")
            post_call.append(f"            CALL C_F_POINTER({arg.name}_cptr, "
                             f"{arg.name}_fptr, [{', '.join(extents)}])")
            post_call.append(f"            {arg.name}_fptr = {arg.name}")
            post_call.append(f"        END IF")
        elif "CHARACTER" in fortran_type:
            # CHARACTER is not directly C-interoperable, so it crosses as a
            # C_CHAR buffer plus its length and is staged through a local
            # CHARACTER(len) that the Fortran function can actually take.
            #
            # This used to declare an uninitialized CHARACTER(8192) local and
            # pass THAT, exposing no C-side parameter at all: the bridge
            # compiled, ran, and tested nothing. Any function whose input is a
            # CHARACTER argument was silently unverifiable.
            #
            # THE LENGTH IS A C PARAMETER ONLY WHEN IT IS ONE. `build_c_params`
            # emits `int len_<x>` for `CHARACTER(*)` -- a width the CALLER
            # chooses -- and emits NOTHING for `CHARACTER(1024)`, whose width is
            # a literal COMPILED INTO BOTH SIDES. This branch used to emit
            # `len_<x>` unconditionally, so on a fixed-width dummy the bridge
            # took one more argument than the prototype the harness calls it
            # through, on a C linkage no linker checks. rosco-r2 unit #17
            # (`Read_OL_Input`, `CHARACTER(1024), INTENT(IN)`): the mapper
            # refused the argument -- "build_c_params emitted no len_" -- and
            # the unit had no differential harness at all.
            #
            # `_get_char_len_expr` is `interface_gen`'s own answer to "how many
            # bytes", so the two generators cannot drift; only the DECISION to
            # pass it is restated, and it is restated as the same test
            # `build_c_params` makes.
            _cmap = get_type_mapping(arg.fortran_type, kind_aliases, derived_types)
            _len_is_param = (_cmap.character_len_param == "*")
            len_expr = (f"len_{arg.name}" if _len_is_param
                        else (_get_char_len_expr(arg, _cmap, sig) or "1"))
            c_params.append(
                (f"        CHARACTER(KIND=C_CHAR), INTENT({intent}) :: {arg.name}_c(*)",
                 f"{arg.name}_c"))
            # A CHARACTER ARRAY dummy has a second, independent size. Dropping
            # it produced a bridge declaring a CHARACTER SCALAR where the
            # function takes an array -- which does not even match the
            # function's interface, so the whole class was unbridgeable.
            #
            # THE COUNT COMES BEFORE THE LENGTH, and the order is not a choice:
            # `build_c_params` emits `T* <x>`, then `n_<x>` for an assumed-shape
            # array, then `len_<x>` for a `CHARACTER(*)`. This branch emitted
            # the length first, so for an ASSUMED-SHAPE CHARACTER ARRAY the
            # bridge's two extents were TRANSPOSED against the ABI the shipped
            # wrapper uses -- on a C linkage no linker checks.
            #
            # It fails silently and it does not look like a marshalling bug.
            # rosco-r2 unit #32 `FindLine`, whose `FileLines(:)` is the first
            # dummy of this shape in the campaign: 47 of 2358 differential cases
            # disagreed, all on `LineNum` and `Line`, and the reference's
            # `LineNum` was exactly `len_FileLines` on every one of them --
            # `DO I = 1, SIZE(FileLines)` running over the WIDTH. A case with
            # width 4 and count 3 was read by the reference as four elements of
            # three characters, which is a well-formed array of the wrong shape,
            # so nothing crashed and 2311 cases still agreed.
            #
            # `_c_param_names` below now asserts this agreement for every unit
            # rather than leaving the next shape to be found the same way.
            count_expr = None
            if getattr(arg, "is_array", False):
                if getattr(arg, "is_assumed_shape", False) or not arg.array_dims:
                    c_params.append(
                        (f"        INTEGER(C_INT), VALUE, INTENT(IN) :: n_{arg.name}",
                         f"n_{arg.name}"))
                    count_expr = f"n_{arg.name}"
                else:
                    # Explicit shape, usually in terms of another dummy
                    # (Words(NumWords)); that dummy is already a bridge
                    # parameter and is declared before the locals.
                    count_expr = arg.array_dims
            if _len_is_param:
                c_params.append(
                    (f"        INTEGER(C_INT), VALUE, INTENT(IN) :: len_{arg.name}",
                     f"len_{arg.name}"))
            if count_expr:
                local_decls.append(
                    f"        CHARACTER({len_expr}) :: {arg.name}({count_expr})")
                local_decls.append(f"        INTEGER :: vit_i_{arg.name}, vit_j_{arg.name}")
                pre_call.append(f"        DO vit_j_{arg.name} = 1, {count_expr}")
                pre_call.append(f"            DO vit_i_{arg.name} = 1, {len_expr}")
                pre_call.append(f"                {arg.name}(vit_j_{arg.name})(vit_i_{arg.name}:vit_i_{arg.name}) = &")
                pre_call.append(f"                    {arg.name}_c((vit_j_{arg.name} - 1) * {len_expr} + vit_i_{arg.name})")
                pre_call.append(f"            END DO")
                pre_call.append(f"        END DO")
                if intent in ("INOUT", "OUT"):
                    post_call.append(f"        DO vit_j_{arg.name} = 1, {count_expr}")
                    post_call.append(f"            DO vit_i_{arg.name} = 1, {len_expr}")
                    post_call.append(f"                {arg.name}_c((vit_j_{arg.name} - 1) * {len_expr} + vit_i_{arg.name}) = &")
                    post_call.append(f"                    {arg.name}(vit_j_{arg.name})(vit_i_{arg.name}:vit_i_{arg.name})")
                    post_call.append(f"            END DO")
                    post_call.append(f"        END DO")
            else:
                local_decls.append(f"        CHARACTER({len_expr}) :: {arg.name}")
                local_decls.append(f"        INTEGER :: vit_i_{arg.name}")
                pre_call.append(f"        DO vit_i_{arg.name} = 1, {len_expr}")
                pre_call.append(f"            {arg.name}(vit_i_{arg.name}:vit_i_{arg.name}) = {arg.name}_c(vit_i_{arg.name})")
                pre_call.append(f"        END DO")
                if intent in ("INOUT", "OUT"):
                    post_call.append(f"        DO vit_i_{arg.name} = 1, {len_expr}")
                    post_call.append(f"            {arg.name}_c(vit_i_{arg.name}) = {arg.name}(vit_i_{arg.name}:vit_i_{arg.name})")
                    post_call.append(f"        END DO")
            call_args.append(arg.name)
        elif is_cfi_inout_array(arg):
            # ALLOCATABLE, INTENT(INOUT): the bridge takes the dummy exactly as
            # the function declares it, and the C++ harness passes a
            # `CFI_cdesc_t*` it established itself.
            #
            # The obvious alternative -- receive a pointer plus an extent, stage
            # them into a local ALLOCATABLE, call, and copy back -- cannot work:
            # the harness has to see the extent the callee CHOSE, and a copy-back
            # into a caller-sized buffer is exactly the shape that hides a
            # reallocation. It also would not be the same calling convention the
            # integration wrapper uses, so the harness would be measuring a
            # bridge that ships nowhere. See interface_gen.is_cfi_inout_array.
            dims = ", ".join(":" for _ in range(getattr(arg, "array_rank", 1) or 1))
            c_params.append(
                (f"        {_iso_c_for(fortran_type, kind_aliases)}, DIMENSION({dims}), "
                 f"ALLOCATABLE, INTENT(INOUT) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        elif arg.is_array and arg.is_assumed_shape:
            # Assumed-shape arrays: receive as assumed-size (*) + size parameter.
            # Pass explicit section to the Fortran function (which expects assumed-shape).
            #
            # The kind comes from the mapping, not from a literal. It used to be
            # hardcoded REAL(C_DOUBLE), so every REAL(ReKi) or REAL(SiKi)
            # argument -- REAL(4) in ROSCO -- got a REAL(8) dummy.
            c_type = _iso_c_for(fortran_type, kind_aliases)
            rank = getattr(arg, "array_rank", 1) or 1
            if rank == 1:
                c_params.append((f"        {c_type}, INTENT({intent}) :: {arg.name}(*)", arg.name))
                c_params.append((f"        INTEGER(C_INT), VALUE, INTENT(IN) :: n_{arg.name}", f"n_{arg.name}"))
                call_args.append(f"{arg.name}(1:n_{arg.name})")
            elif rank == 2:
                # A RANK-2 assumed-shape dummy took the rank-1 spelling above
                # until rosco-r2 unit #45: `zData(1:n_zData)` against
                # `zData(:,:)` is "Rank mismatch in argument 'zdata' (rank-2 and
                # rank-1)", and the two generators disagreed about the parameter
                # list as well -- `build_c_params` has always emitted
                # `n_<x>_rows` and `n_<x>_cols` for this argument, so the C++
                # side passed two extents into a bridge declaring one.
                #
                # The spelling is COPIED from `interface_gen._build_bridge_call_args`,
                # which has emitted exactly this RESHAPE for the kernel bridge
                # since before this cell was measured, rather than invented here:
                # two generators that disagree about a calling convention is the
                # defect this branch closes, and a second spelling would be a
                # second chance at it.
                if intent != "IN":
                    raise ValueError(
                        f"{arg.name}: a rank-2 assumed-shape dummy with "
                        f"INTENT({intent}) is REFUSED. The call below passes a "
                        f"RESHAPE, which is an expression and therefore a COPY, "
                        f"so anything the callee writes into it would be "
                        f"discarded silently. Only INTENT(IN) is measured.")
                c_params.append((f"        {c_type}, INTENT({intent}) :: {arg.name}(*)", arg.name))
                c_params.append((f"        INTEGER(C_INT), VALUE, INTENT(IN) :: n_{arg.name}_rows",
                                 f"n_{arg.name}_rows"))
                c_params.append((f"        INTEGER(C_INT), VALUE, INTENT(IN) :: n_{arg.name}_cols",
                                 f"n_{arg.name}_cols"))
                call_args.append(
                    f"RESHAPE({arg.name}(1:n_{arg.name}_rows*n_{arg.name}_cols), "
                    f"(/ n_{arg.name}_rows, n_{arg.name}_cols /))")
            else:
                raise ValueError(
                    f"{arg.name}: a rank-{rank} assumed-shape dummy is REFUSED. "
                    f"Rank 1 and rank 2 are the shapes this bridge has been "
                    f"measured on; a rank-{rank} dummy needs {rank} extents and "
                    f"no generator in this tool emits them.")
        elif arg.is_array:
            # Assumed-SIZE `avrSWAP(*)` and explicit-shape arrays. Neither is
            # assumed-shape, so both fell through to the SCALAR branches below
            # and the bridge declared a scalar where the function takes an
            # array. For `CableControl` that was a REAL(8) scalar against a
            # REAL(ReKi) assumed-size dummy -- a hard type mismatch that stopped
            # the bridge compiling, which is the good outcome; a matching kind
            # would have passed one element and read past it.
            c_type = _iso_c_for(fortran_type, kind_aliases)
            c_params.append((f"        {c_type}, INTENT({intent}) :: {arg.name}(*)", arg.name))
            call_args.append(arg.name)
        elif "INTEGER" in fortran_type:
            value_attr = ", VALUE" if intent == "IN" else ""
            c_params.append((f"        INTEGER(C_INT){value_attr}, INTENT({intent}) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        elif "COMPLEX" in fortran_type:
            # COMPLEX had no branch here at all, so the argument was named in the
            # parameter list and never declared -- "Symbol 'z' has no IMPLICIT
            # type". `type_map` learned COMPLEX when the replication found
            # COMPLEX(DbKi) narrowing to REAL(8) (ActiveWakeControl, #58); the
            # bridge did not, because no test-validate had ever run on a COMPLEX
            # argument. Half a fix, and the half that was missing stayed
            # invisible until the conformance fixture asked directly.
            value_attr = ", VALUE" if intent == "IN" else ""
            c_params.append((f"        {_iso_c_for(fortran_type, kind_aliases)}{value_attr}, "
                             f"INTENT({intent}) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        elif "REAL" in fortran_type:
            value_attr = ", VALUE" if intent == "IN" else ""
            c_params.append((f"        {_iso_c_for(fortran_type, kind_aliases)}{value_attr}, "
                             f"INTENT({intent}) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        elif "LOGICAL" in fortran_type:
            # The dummy is a default LOGICAL (kind 4). LOGICAL(C_BOOL) is kind
            # 1, and passing one to the other is a hard type mismatch, not a
            # silent narrowing -- the bridge simply would not compile. Cross as
            # INTEGER(C_INT), which is what type_map already uses everywhere
            # else in VIT, and convert through a local of the right kind. That
            # also lines the C side up with the translation's own signature,
            # which takes the flag as an int.
            value_attr = ", VALUE" if intent == "IN" else ""
            c_params.append((f"        INTEGER(C_INT){value_attr}, INTENT({intent}) :: {arg.name}", arg.name))
            if intent == "IN":
                local_decls.append(f"        LOGICAL :: {arg.name}_f")
                pre_call.append(f"        {arg.name}_f = ({arg.name} /= 0)")
                call_args.append(f"{arg.name}_f")
            else:
                local_decls.append(f"        LOGICAL :: {arg.name}_f")
                pre_call.append(f"        {arg.name}_f = ({arg.name} /= 0)")
                post_call.append(f"        {arg.name} = MERGE(1_C_INT, 0_C_INT, {arg.name}_f)")
                call_args.append(f"{arg.name}_f")
        else:
            c_params.append((f"        ! TODO: unsupported type {fortran_type}", arg.name))
            call_args.append(arg.name)

    # FUNCTION return type: add output parameter for the return value
    if sig.is_function:
        result_type_upper = (sig.result_type or "").upper()
        if sig.result_is_array and "CHARACTER" not in result_type_upper:
            # An ARRAY-valued FUNCTION RESULT. `interface_gen.result_is_out_param`
            # has said so since the campaign began and `build_c_params` appends
            # `<type>* <Func>_result`; this generator did not, so it declared the
            # result as a SCALAR and emitted `vit_result = identity(n)` --
            # *Incompatible ranks 0 and 2 in assignment*, which is a bridge that
            # does not compile rather than one that compares the wrong thing.
            #
            # Mirrors `interface_gen`: assumed-size on this side, the extent
            # compiled into neither, and the elements crossing in Fortran's own
            # array-element order. Ask BOTH generators before believing a
            # `bridge_feasible` -- they are two spellings of one ABI and this is
            # the second time they have disagreed.
            #
            # Narrow on purpose: a REAL or INTEGER element type, which is what
            # `build_c_params` maps to `double*`/`int*`. A LOGICAL or
            # derived-type array result is UNMEASURED and is refused with its
            # own reason rather than approximated.
            if "REAL" in result_type_upper:
                c_params.append(("        REAL(C_DOUBLE), INTENT(OUT) :: vit_result(*)",
                                 "vit_result"))
            elif "INTEGER" in result_type_upper:
                c_params.append(("        INTEGER(C_INT), INTENT(OUT) :: vit_result(*)",
                                 "vit_result"))
            else:
                c_params.append((f"        ! TODO: unsupported array return type "
                                 f"{sig.result_type}", "vit_result"))
        elif "LOGICAL" in result_type_upper:
            c_params.append((f"        LOGICAL(C_BOOL), INTENT(OUT) :: vit_result", "vit_result"))
        elif "INTEGER" in result_type_upper:
            c_params.append((f"        INTEGER(C_INT), INTENT(OUT) :: vit_result", "vit_result"))
        elif "REAL" in result_type_upper:
            c_params.append((f"        REAL(C_DOUBLE), INTENT(OUT) :: vit_result", "vit_result"))
        elif "CHARACTER" in result_type_upper:
            # Fortran CHARACTER results are fixed-width and blank-padded, so
            # they come back through a C_CHAR buffer the caller owns -- the
            # same shape the forward path uses. Staged through a local because
            # a CHARACTER(n) scalar cannot be sequence-associated with a
            # BIND(C) CHARACTER(KIND=C_CHAR) array dummy.
            c_params.append((f"        CHARACTER(KIND=C_CHAR), INTENT(OUT) :: vit_result(*)", "vit_result"))
        else:
            c_params.append((f"        ! TODO: unsupported return type {sig.result_type}", "vit_result"))

    # Build the bridge parameter names (for the SUBROUTINE declaration)
    param_names = [name for _, name in c_params]

    # THE TWO GENERATORS MUST AGREE ABOUT EXTENT ORDER, AND NOW THEY ARE ASKED.
    #
    # `build_c_params` defines the ABI: it is what `vit interface` compiles into
    # the shipped wrapper and what the translation's own prototype follows. This
    # bridge is a SECOND spelling of the same call, restated by hand, and the
    # two disagreed for an assumed-shape CHARACTER array -- `n_<x>` before
    # `len_<x>` there, `len_<x>` before `n_<x>` here. C linkage checks nothing,
    # so the transposition arrived as a wrong ANSWER: rosco-r2 unit #32
    # `FindLine`, 47 of 2358 differential cases, the reference iterating over
    # the element WIDTH because it had been handed the width as its count.
    #
    # Only the extents are compared, and only those BOTH lists carry: a
    # decomposed derived type puts names in this list that the ABI has no
    # counterpart for (it passes one `TYPE(C_PTR)`), and that difference is
    # deliberate rather than drift. What remains is exactly the quantity that
    # went wrong, and the error NAMES it (rosco-r2 unit #29's rule: an error
    # that names its parameter costs one pass, one that does not costs three).
    try:
        abi_names = [n for _, n in build_c_params(sig, kind_aliases, derived_types)]
    except Exception:                                    # pragma: no cover
        abi_names = None
    if abi_names:
        def _extents(names, keep):
            return [n for n in names
                    if (n.startswith("n_") or n.startswith("len_")) and n in keep]
        bridge_ext = set(n for n in param_names
                         if n.startswith("n_") or n.startswith("len_"))
        abi_ext = set(n for n in abi_names
                      if n.startswith("n_") or n.startswith("len_"))
        both = bridge_ext & abi_ext
        mine, theirs = _extents(param_names, both), _extents(abi_names, both)
        if mine != theirs:
            raise ValueError(
                f"{sig.name}: the test-validate bridge and build_c_params "
                f"disagree about extent ORDER -- bridge {mine}, ABI {theirs}. "
                f"The ABI is what ships; this bridge must follow it, or every "
                f"case is compared against a differently-shaped argument.")

    lines.extend(_wrapped_dummy_list(
        f"    SUBROUTINE {bridge_name}(", param_names, ") &"))
    lines.append(f"        BIND(C, NAME='{bridge_name}')")
    lines.append(f"        USE ISO_C_BINDING")
    # A copy-back that does not fit is REFUSED and REPORTED on ERROR_UNIT.
    # `_get_module_use_statements` is filtered to skip ISO_FORTRAN_ENV, so the
    # name has to be imported here or the generated bridge does not compile --
    # and the failure would land on the refusal branches, which are the branches
    # that most need to exist.
    if _bridge_reports_on_stderr(sig, kind_aliases, derived_types):
        lines.append(f"        USE, INTRINSIC :: ISO_FORTRAN_ENV, ONLY: ERROR_UNIT")
    if sig.module_name:
        lines.append(f"        USE {sig.module_name}")
    # USE modules that provide derived types.
    # Scan module-level USE statements from the source file instead of
    # guessing a naming convention (e.g., _Types).
    if any(arg.is_derived_type for arg in sig.args) and source_file:
        source_text = Path(source_file).read_text()
        # Module scope FIRST, then the procedure's own specification part. A
        # type may be imported at either, and a bridge that reads only the
        # first declares a dummy of a type nothing in its scope defines --
        # measured on ROSCO's `Filters.f90`, whose seven filter procedures each
        # carry `USE ROSCO_Types, ONLY : FilterParameters` inside the FUNCTION.
        module_uses = (_get_module_use_statements(source_text)
                       + _get_procedure_use_statements(source_text, sig.name))
        # Add all module-level USE statements — the bridge needs access
        # to all types the module imports (PRIVATE default hides re-exports).
        added = set()
        for use_stmt in module_uses:
            # Skip ISO_C_BINDING (already added) and the module itself
            use_upper = use_stmt.upper()
            if "ISO_C_BINDING" in use_upper or "ISO_FORTRAN_ENV" in use_upper:
                continue
            # Skip USE of the function's own module (already added above)
            use_module = use_upper.replace("::", " ").split()[1].strip().rstrip(",")
            if sig.module_name and use_module == sig.module_name.upper():
                continue
            if use_stmt not in added:
                lines.append(f"        {use_stmt}")
                added.add(use_stmt)
        lines.extend(_nested_type_use_statements(added, local_decls))
    lines.append(f"        IMPLICIT NONE")

    # Parameter declarations
    for decl, _ in c_params:
        lines.append(decl)

    # Local declarations
    for decl in local_decls:
        lines.append(decl)

    # Staging buffer for a CHARACTER-valued result
    if sig.is_function and "CHARACTER" in (sig.result_type or "").upper():
        lines.append(f"        {sig.result_type} :: vit_char_result")
        lines.append(f"        INTEGER :: vit_i_result")

    # Staging buffer for an ARRAY-valued result. Declared with the FUNCTION's
    # own DIMENSION, exactly as `interface_gen` declares the wrapper's result,
    # so the two spellings of one ABI cannot drift: an automatic array whose
    # extents are the dummies already in scope. The result is then flattened
    # into the assumed-size dummy in Fortran ARRAY-ELEMENT ORDER, which is the
    # order the C side lays the buffer out in.
    #
    # SIZE(), not a product of the declared extents: a DIMENSION(n, n) with
    # n < 0 is a zero-sized array, not an array of n*n elements, and writing
    # n*n of them would run off the caller's buffer.
    #
    # "THE DUMMIES ALREADY IN SCOPE" IS TRUE OF `DIMENSION(n, n)` AND FALSE OF
    # `DIMENSION(SIZE(x))`, and the difference is this bridge's own ABI rather
    # than the reference's. The original declares `x` assumed-shape (`x(:)`),
    # where `SIZE(x)` is exactly the extent; the BRIDGE declares it
    # assumed-size (`x(*)`), where gfortran refuses it outright --
    #
    #   Error: The upper bound in the last dimension must appear in the
    #          reference to the assumed size array 'x'
    #
    # -- so the text that is right in one is a bridge that does not compile in
    # the other. The extent it means is already a dummy here: `build_c_params`
    # emits `n_x` beside `x`, and the call below is `unwrap(x(1:n_x), ...)`.
    # So `SIZE(<arg>)` is rewritten to that parameter, and ONLY when the
    # parameter exists -- any other dimension text, and any `SIZE()` of
    # something that did not cross, is left exactly as it was. rosco-r2 unit
    # #26, `unwrap`; the fourth disagreement between VIT's two generators, and
    # the second on a RESULT.
    array_result = (sig.is_function and sig.result_is_array
                    and "CHARACTER" not in (sig.result_type or "").upper())
    if array_result and sig.result_dims:
        arr_type = _resolve_fortran_type(sig.result_type, kind_aliases)
        lines.append(f"        {arr_type}, "
                     f"DIMENSION({_bridge_result_dims(sig.result_dims, param_names)})"
                     f" :: vit_array_result")

    # Pre-call conversions
    for conv in pre_call:
        lines.append(conv)

    # Call the original function
    if sig.is_function:
        result_type_upper = (sig.result_type or "").upper()
        if array_result and sig.result_dims:
            lines.append(f"        vit_array_result = {func_name}({', '.join(call_args)})")
            lines.append(f"        vit_result(1:SIZE(vit_array_result)) = "
                         f"RESHAPE(vit_array_result, (/ SIZE(vit_array_result) /))")
        elif "LOGICAL" in result_type_upper:
            lines.append(f"        vit_result = LOGICAL({func_name}({', '.join(call_args)}), C_BOOL)")
        elif "CHARACTER" in result_type_upper:
            lines.append(f"        vit_char_result = {func_name}({', '.join(call_args)})")
            lines.append(f"        DO vit_i_result = 1, LEN(vit_char_result)")
            lines.append(f"            vit_result(vit_i_result) = vit_char_result(vit_i_result:vit_i_result)")
            lines.append(f"        END DO")
        else:
            lines.append(f"        vit_result = {func_name}({', '.join(call_args)})")
    else:
        lines.append(f"        CALL {func_name}({', '.join(call_args)})")

    # Post-call conversions
    for conv in post_call:
        lines.append(conv)

    lines.append(f"    END SUBROUTINE {bridge_name}")

    # Wrap anything still past the free-form line limit. Applied to the WHOLE
    # bridge rather than at each emission site on purpose: the dummy list was
    # only the longest offender, and a fix that names emission sites is a fix
    # that the next one escapes. See `_wrap_long_fortran_lines`.
    return "\n".join(_wrap_long_fortran_lines(lines))


_FORTRAN_RUNTIME = {
    "gfortran": "-lgfortran -lm",
    "flang": "-lflang -lm",
    "flang-new": "-lFortranRuntime -lFortranDecimal -lm",
    "ifort": "-lifcore -limf -lm",
    "ifx": "-lifcore -limf -lm",
    "nvfortran": "-lnvf -lm",
    "ftn": "-lgfortran -lm",       # Cray wrapper, usually over gfortran here
}


def fortran_runtime_for(compiler: str) -> str:
    """Link flags for a Fortran compiler's runtime, by compiler.

    The harness is linked with $(CXX), so nothing pulls the Fortran runtime in
    on its own. `-lgfortran -lm` was written as a literal even though the
    compiler is a vit.yaml field -- correct for exactly one toolchain, and
    wrong silently on the others, because the failure is an undefined symbol
    naming `_gfortran_*` that looks like a source problem.

    Unknown compilers fall back to gfortran's and say so at the top of the
    Makefile; override with `fortran.runtime_libs` in vit.yaml.
    """
    stem = Path(compiler).name.split()[0] if compiler else "gfortran"
    for known, libs in _FORTRAN_RUNTIME.items():
        if stem == known or stem.endswith("-" + known):
            return libs
    return _FORTRAN_RUNTIME["gfortran"] + f"   # guessed for {stem!r}; set fortran.runtime_libs"


_CALLEE_CALL = re.compile(r"\b(\w+)_c\s*\(")


def generate_callee_bridges(
    cpp_text: str,
    unit_name: str,
    source_dir: str,
    kind_aliases: Optional[dict] = None,
    derived_types: Optional[dict] = None,
) -> tuple[Optional[str], list[str], Optional[str], list[str]]:
    """Fortran BIND(C) bridges for every `<callee>_c` the translation calls.

    Returns (module source or None, notes, C declaration header or None,
    the names actually bridged). The last is what the Makefile must keep OUT
    of LIBS -- see `_parse_link_libraries`.

    THE HEADER IS NOT OPTIONAL DECORATION. A `<callee>_c` symbol the bridges
    define still has to be DECLARED on the C++ side, and the harness's test
    source includes the translation before anything the translation could
    include itself. Without it the build dies in the translation's own body --
    `'addtolist_c' was not declared in this scope` -- which reads like a defect
    in the translation and is not one. Measured at rosco-r2 unit #29, the first
    unit whose differential harness has a callee at all.

    WHY THIS EXISTS. `test-validate` produced a bridge for the unit under test
    and nothing else, so a translation that called another translated function
    -- `picontroller.cpp` calls `saturate_c` -- failed at link with an undefined
    reference. Only leaf functions could be validated this way, which is the
    opposite of what a campaign needs: the leaves are the easy ones.

    The bridges call the ORIGINAL FORTRAN callee, not the translated one. That
    matters for what the comparison means. Linking `saturate.cpp` instead would
    put the C++ `saturate` on one side and the Fortran `saturate` on the other,
    so a mismatch could come from either unit and a *pair* of compensating
    errors could cancel. Backing both sides with the same Fortran callee makes
    the harness a test of this unit alone.

    A callee whose Fortran source cannot be found, or whose bridge cannot be
    generated, is REPORTED and skipped -- the link then fails with a name, which
    is a better outcome than a harness that silently tests something else.
    """
    from vit.interface_gen import (generate_kernel_callee_bridge,
                                   generate_callee_declarations_header,
                                   bridge_char_len_constants)
    from vit.translate import _extract_function_text

    names = []
    for m in _CALLEE_CALL.finditer(cpp_text):
        n = m.group(1)
        if n.lower() != unit_name.lower() and n not in names:
            names.append(n)
    if not names:
        return None, [], None, []

    notes: list[str] = []
    bodies: list[str] = []
    bridged: list = []
    src_dir = Path(source_dir)
    sources = sorted(src_dir.glob("*.f90")) + sorted(src_dir.glob("*.F90"))
    for name in names:
        sig, found_in = None, None
        for f in sources:
            text = f.read_text(errors="replace")
            try:
                sig = parse_signature_from_source(text, name)
            except Exception:
                sig = None
            if sig is not None:
                found_in = text
                break
        if sig is None:
            notes.append(f"{name}_c: no Fortran definition found under {source_dir}; "
                         f"the link will fail on this symbol by name")
            continue
        # A CALLEE THAT IS ITSELF INTEGRATED MUST NOT BE BRIDGED -- THE BRIDGE
        # WOULD CALL ITSELF. Once `FindLine` is integrated its Fortran body is a
        # wrapper reading `CALL findline_c(...)`, so a Fortran-backed bridge
        # named `findline_c` closes a loop:
        #
        #     findline_c (this bridge) -> Fortran FindLine -> findline_c -> ...
        #
        # which is not a wrong answer but an immediate SIGSEGV on a blown stack,
        # with an empty stdout that reads as "the harness produced nothing".
        # Measured on rosco-r2 ParseInAry_Opt after FindLine, GetWords and
        # Int2LStr were integrated.
        #
        # The detector is the cycle itself rather than vit.yaml's `status:`,
        # because the wrapper is the thing that actually closes the loop and a
        # config field can disagree with the tree. Such a callee is left to the
        # production object -- `<callee>.cpp.o` defines `<callee>_c` and
        # terminates -- so it is deliberately NOT reported as bridged, and
        # `_parse_link_libraries` therefore keeps its object in LIBS.
        own = _extract_function_text(found_in, name) or ""
        if re.search(rf"\b{re.escape(name)}_c\s*\(", own, re.IGNORECASE):
            notes.append(f"{name}_c: already integrated -- its Fortran body calls "
                         f"{name}_c, so bridging it would recurse; linking "
                         f"{name.lower()}.cpp.o instead")
            continue
        try:
            body = generate_kernel_callee_bridge(sig, kind_aliases, derived_types)
        except Exception as e:
            notes.append(f"{name}_c: bridge not generated ({type(e).__name__}: {e})")
            continue
        # The kernel context supplies the callee's module; a standalone harness
        # does not, and without it gfortran reports "has no IMPLICIT type" --
        # which reads like a bridge bug rather than a missing USE. Inserted
        # after USE ISO_C_BINDING because Fortran requires every USE to precede
        # IMPLICIT NONE.
        # `parse_signature_from_source` parses a signature out of arbitrary text
        # and so cannot know the enclosing module. Take it from the file the
        # signature was found in.
        mod = getattr(sig, "module_name", None)
        if not mod and found_in:
            m = re.search(r"^\s*MODULE\s+(\w+)", found_in, re.MULTILINE | re.IGNORECASE)
            if m and m.group(1).upper() != "PROCEDURE":
                mod = m.group(1)
        if mod:
            # AND WITH THE CALLEE, ANY NAMED CONSTANT ITS OWN CHARACTER DUMMIES
            # ARE DECLARED WITH. `CHARACTER(MaxLineLength) :: Line` in ROSCO's
            # `FindLine` makes the bridge's staging local `CHARACTER(MaxLineLength)`
            # too, and `ONLY` is exactly the clause that would leave that name
            # out of scope -- "Symbol 'maxlinelength' has no IMPLICIT type",
            # which reads as a bridge bug rather than a missing import. rosco-r2
            # unit #54. See `interface_gen.bridge_char_len_constants`.
            only = ", ".join(
                [sig.name]
                + bridge_char_len_constants(sig, kind_aliases, derived_types))
            body = body.replace("USE ISO_C_BINDING",
                                f"USE ISO_C_BINDING\n        USE {mod}, ONLY : {only}", 1)
        else:
            notes.append(f"{name}_c: no module recorded for the callee; the bridge "
                         f"will not resolve it")
        bodies.append(body)
        bridged.append(sig)
        notes.append(f"{name}_c: bridged to Fortran {name}"
                     + (f" (USE {mod})" if mod else ""))

    if not bodies:
        return None, notes, None, []

    decls = generate_callee_declarations_header(bridged, kind_aliases, derived_types)

    header = [
        f"! VIT: callee bridges for the {unit_name} test-validate harness.",
        "! Each `<callee>_c` calls the ORIGINAL Fortran, so both sides of the",
        "! comparison share one callee implementation and a mismatch is",
        "! attributable to the unit under test.",
        "",
    ]
    return ("\n".join(header + bodies) + "\n", notes, decls,
            [s.name for s in bridged])


def generate_test_makefile(
    sig: FortranSignature,
    build_dir: str,
    source_dir: str,
    config_dir: str = ".",
    target_name: str = "",
    fortran_flags: str = "",
    fortran_compiler: str = "gfortran",
    cxx_compiler: str = "g++",
    fortran_runtime_libs: str = "",
    preprocessor_defines: Optional[list[str]] = None,
    shared_files: Optional[list[str]] = None,
    cpp_file: Optional[str] = None,
    patched_source: Optional[str] = None,
    callee_bridge: bool = False,
    bridged_callees: Optional[list[str]] = None,
) -> str:
    """Generate a Makefile for building the test-validate harness.

    Compiles the C++ test and Fortran bridge against the production build's
    prebuilt .mod files and libraries. If the function is PRIVATE, a patched
    copy of the source is compiled locally to produce a .mod that exports it.

    Args:
        sig: Parsed function signature
        build_dir: Absolute path to the production build directory
        source_dir: Absolute path to the project source directory
        config_dir: Absolute path to the directory containing vit.yaml
        target_name: CMake target name (for deriving library dependencies)
        fortran_flags: Fortran compiler flags from vit.yaml
        preprocessor_defines: List of preprocessor defines (e.g., ["OPENFAST_DOUBLE_PRECISION"])
        shared_files: Shared header/source paths (for -I includes)
        cpp_file: Path to the C++ translation file
        bridged_callees: callee names given a Fortran bridge; their production
            objects are kept out of LIBS, as is the unit's own
    """
    func_lower = sig.name.lower()
    defines = " ".join(f"-D{d}" for d in (preprocessor_defines or []))

    # Collect include paths for shared headers
    # shared_files paths are relative to config_dir (directory containing vit.yaml).
    include_dirs = set()
    if shared_files:
        for f in shared_files:
            p = Path(f)
            if not p.is_absolute():
                p = Path(config_dir) / f
            include_dirs.add(str(p.parent))
    # Include the current directory (vit_types.h is generated here by cmd_test_validate).
    include_dirs.add(".")

    cxx_includes = " ".join(f"-I{d}" for d in sorted(include_dirs))

    lines = []
    lines.append(f"# VIT test-validate Makefile for {sig.name}")
    lines.append(f"# Generated by: vit test-validate")
    lines.append(f"# Build against production libraries — no project rebuild needed.")
    lines.append("")
    lines.append(f"FC = {fortran_compiler}")
    lines.append(f"CXX = {cxx_compiler}")
    # Fortran flags: local .mod files first (for patched source), then production
    lines.append(f"FC_FLAGS = -fPIC -ffree-line-length-none {fortran_flags} {defines} \\")
    lines.append(f"           -I. -I{build_dir}/ftnmods -J.")
    lines.append(f"CXX_FLAGS = -O2 -fPIC -ffp-contract=off {cxx_includes}")
    lines.append("")
    # Derive library dependencies from CMake target's link.txt
    # The unit's own object is excluded unconditionally: the definition under
    # test comes from the included header, mutated or not.
    _exclude = tuple([sig.name] + list(bridged_callees or []))
    link_libs = (_parse_link_libraries(build_dir, target_name, _exclude)
                 if target_name else None)
    if link_libs:
        lines.append("# Libraries derived from CMake target (link order preserved)")
        lines.append(f"LIBS = {link_libs[0]} \\")
        for lib in link_libs[1:-1]:
            lines.append(f"       {lib} \\")
        lines.append(f"       {link_libs[-1]}")
    else:
        lines.append("# WARNING: Could not derive libraries from CMake target.")
        lines.append("# Fill in LIBS manually with the libraries needed for linking.")
        lines.append("LIBS =")
    # The harness is linked by $(CXX), so nothing pulls in the Fortran runtime.
    # Which runtime that is depends on the compiler, so it is derived rather
    # than written as a literal -- `-lgfortran` was hardcoded here, in a
    # generator whose Fortran compiler is a configuration field.
    # A target built as a Fortran shared library links it implicitly and the
    # omission never shows; the object-file fallback above links raw .o files,
    # and then every Fortran I/O, string or intrinsic call in the reference is
    # an undefined symbol at link time (ROSCO's ZeroMQInterface.f90 alone brings
    # in _gfortran_string_len_trim, _gfortran_concat_string and the transfer_*
    # family). Appended rather than prepended: it must resolve after the objects
    # that need it.
    lines.append(f"LIBS += {fortran_runtime_libs or fortran_runtime_for(fortran_compiler)}")
    lines.append("")

    # Copy translation .cpp to .hpp for inclusion
    if cpp_file:
        hpp_name = Path(cpp_file).stem + ".hpp"
        lines.append(f"{hpp_name}: {cpp_file}")
        lines.append(f"\tcp $< $@")
        lines.append("")
        test_deps = f"{func_lower}_test.cpp {hpp_name}"
    else:
        test_deps = f"{func_lower}_test.cpp"

    # If patched source exists, compile it locally for the .mod file
    # (needed when the function is PRIVATE in the module)
    link_objs = [f"{func_lower}_test.o", f"{func_lower}_bridge.o"]
    if callee_bridge:
        # Every `<callee>_c` the translation calls, wired to the ORIGINAL
        # Fortran. Both sides of the comparison then run the same callee, so a
        # difference is attributable to the unit under test rather than to some
        # other translation it happens to call.
        link_objs.append(f"{func_lower}_callees.o")
        lines.append("# Callee bridges: C++ `_c` entry points backed by Fortran originals")
        lines.append(f"{func_lower}_callees.o: {func_lower}_callees.f90")
        lines.append(f"\t$(FC) $(FC_FLAGS) -c $< -o $@")
        lines.append("")
    if patched_source:
        patched_name = Path(patched_source).name
        patched_obj = Path(patched_name).stem + ".o"
        link_objs.append(patched_obj)
        lines.append(f"# Patched source: function made PUBLIC for bridge access")
        lines.append(f"{patched_obj}: {patched_name}")
        lines.append(f"\t$(FC) $(FC_FLAGS) -c $< -o $@")
        lines.append("")
        # Bridge depends on patched source (for the .mod file)
        lines.append(f"{func_lower}_bridge.o: {func_lower}_bridge.f90 {patched_obj}")
        lines.append(f"\t$(FC) $(FC_FLAGS) -c {func_lower}_bridge.f90 -o $@")
    else:
        lines.append(f"{func_lower}_bridge.o: {func_lower}_bridge.f90")
        lines.append(f"\t$(FC) $(FC_FLAGS) -c $< -o $@")
    lines.append("")

    lines.append(f"test: {' '.join(link_objs)}")
    lines.append(f"\t$(CXX) -o $@ $^ $(LIBS)")
    lines.append("")
    lines.append(f"{func_lower}_test.o: {test_deps}")
    lines.append(f"\t$(CXX) $(CXX_FLAGS) -c {func_lower}_test.cpp -o $@")
    lines.append("")
    lines.append("run: test")
    lines.append("\t./test")
    lines.append("")
    lines.append("clean:")
    lines.append(f"\trm -f *.o test *.hpp *.mod")
    lines.append("")

    return "\n".join(lines)


def generate_test_harness_skeleton(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    cpp_file: Optional[str] = None,
    struct_headers: Optional[list[str]] = None,
    shared_files: Optional[list[str]] = None,
    derived_types: Optional[dict] = None,
) -> str:
    """Generate a C++ test harness skeleton for a validation function.

    The skeleton provides boilerplate (includes, main loop, result reporting).
    The LLM fills in the test implementations.

    Args:
        sig: Parsed Fortran function signature
        kind_aliases: Project kind mappings (e.g., {"ReKi": "REAL(8)"})
        cpp_file: Path to the C++ translation file
        struct_headers: Header files to include for struct definitions
        shared_files: Shared project files (e.g., vit_nwtc.h)

    Returns:
        C++ skeleton string
    """
    lines = []
    func_name = sig.name
    func_lower = func_name.lower()

    # Metadata header
    lines.append(f"// VIT Test-Validate Harness")
    lines.append(f"// Function: {func_name}")
    lines.append(f"// Generated by: vit test-validate")
    lines.append(f"//")
    lines.append(f"// This file tests every error branch in the validation function.")
    lines.append(f"// Each test constructs valid input, modifies one field to trigger")
    lines.append(f"// the error condition, and asserts the function reports an error.")
    lines.append("")

    # Includes
    lines.append("#include <cstdio>")
    lines.append("#include <cstring>")
    if struct_headers:
        for h in struct_headers:
            lines.append(f'#include "{h}"')
    if shared_files:
        for f in shared_files:
            name = Path(f).name
            lines.append(f'#include "{name}"')

    # Include the translation as a header
    if cpp_file:
        hpp_name = Path(cpp_file).stem + ".hpp"
        lines.append(f'#include "{hpp_name}"')
    lines.append("")

    # Test result type
    lines.append("// --- Test infrastructure ---")
    lines.append("")
    lines.append("struct TestResult {")
    lines.append("    const char* name;")
    lines.append("    bool passed;")
    lines.append("    const char* detail;")
    lines.append("};")
    lines.append("")

    # Function signatures (build from parsed sig)
    c_params = build_c_params(sig, kind_aliases or {}, derived_types)
    # build_c_params returns list of (c_type, name) tuples
    param_str = ", ".join(f"{ctype} {name}" for ctype, name in c_params)
    lines.append(f"// C++ function under test:")
    lines.append(f"// void {func_name}({param_str});")
    lines.append("")

    # Fortran bridge has a different signature: derived types as void* or
    # decomposed fields; CHARACTER args handled locally (not exposed to C++)
    bridge_params = []
    for arg in sig.args:
        if arg.is_derived_type:
            type_info = _has_allocatable_fields(arg, derived_types)
            if type_info is not None:
                # Same helper as the Fortran side, so the two agree by
                # construction rather than by both re-deriving the names.
                for fld, kind, rank, names in _decomposed_param_names(
                        arg, type_info, kind_aliases):
                    if kind == "alloc":
                        c_type = get_type_mapping(fld.fortran_type, kind_aliases).c_type
                        bridge_params.append(f"{c_type}* {names['ptr']}")
                        if rank == 1:
                            # A POINTER: the extent the callee chose comes back.
                            bridge_params.append(f"int* {names['n']}")
                            bridge_params.append(f"int {names['cap']}")
                        else:
                            bridge_params.append(f"int {names['rows']}")
                            bridge_params.append(f"int {names['cols']}")
                    elif kind == "nested":
                        bridge_params.append(f"void* {names['ptr']}")
                    elif kind == "char":
                        if rank:
                            bridge_params.append(f"char* {names['scalar']}")
                    elif kind == "defchar":
                        # `n` is a POINTER here: the callee may replace the
                        # string with a longer one, so its length comes back.
                        bridge_params.append(f"char* {names['buf']}")
                        bridge_params.append(f"int* {names['n']}")
                        bridge_params.append(f"int {names['cap']}")
                    elif kind == "fixed":
                        # By reference on the Fortran side, so a pointer here.
                        c_type = get_type_mapping(fld.fortran_type, kind_aliases).c_type
                        bridge_params.append(f"{c_type}* {names['scalar']}")
                    else:
                        c_type = decomposed_scalar_c_type(fld, kind_aliases)
                        star = "*" if decomposed_arg_is_out(arg) else ""
                        bridge_params.append(f"{c_type}{star} {names['scalar']}")
            else:
                bridge_params.append(f"void* {arg.name}_ptr")
        elif "CHARACTER" in arg.fortran_type.upper():
            # Buffer + length, matching the bridge above.
            bridge_params.append(f"char* {arg.name}_c")
            bridge_params.append(f"int len_{arg.name}")
            # An assumed-shape CHARACTER array also passes its element count.
            # Explicit shape does not: its extent is another dummy that is
            # already in the list.
            if getattr(arg, "is_array", False) and (
                    getattr(arg, "is_assumed_shape", False) or not arg.array_dims):
                bridge_params.append(f"int n_{arg.name}")
        else:
            # Find the matching C param from build_c_params, plus any size
            # parameter that travels with it. An assumed-shape array becomes
            # (ptr, n_ptr) on the C side and the bridge declares both, so
            # matching on the argument name alone drops the size and produces a
            # declaration that disagrees with the bridge it declares.
            for ctype, cname in c_params:
                if cname == arg.name or cname == f"n_{arg.name}":
                    bridge_params.append(f"{ctype} {cname}")
    # The bridge is always a SUBROUTINE, so a FUNCTION's result comes back
    # through a trailing output parameter. The loop above walks sig.args only,
    # which never includes the result.
    if sig.is_function:
        result_type_upper = (sig.result_type or "").upper()
        if "CHARACTER" in result_type_upper:
            bridge_params.append("char* vit_result")
        elif "LOGICAL" in result_type_upper:
            bridge_params.append("bool* vit_result")
        elif "INTEGER" in result_type_upper:
            bridge_params.append("int* vit_result")
        elif "REAL" in result_type_upper:
            bridge_params.append("double* vit_result")

    # The bridge needs a value for every derived-type argument -- it either
    # decomposes it or takes it as `void* <arg>_ptr`. `build_c_params` drops a
    # non-C-compatible one silently, and the two then describe the same
    # function with different arities: the skeleton declares a C++ call of N
    # arguments directly above an `#include` of a translation taking N+1.
    #
    # There is no false positive here, whatever the intent elsewhere. A genuine
    # passthrough must keep being dropped from a PRODUCTION call, and it is --
    # this check is local to test-validate, where the harness has to SUPPLY the
    # argument the bridge is about to pass. If the C++ call has no such
    # parameter, the harness cannot be built correctly, so refusing to emit one
    # is the only honest outcome.
    # Only when a registry was supplied. WITHOUT one `build_c_params` omits
    # every derived type and `_has_allocatable_fields` decomposes none, so both
    # sides are consistently degraded and there is no disagreement to report --
    # the absence is a fact about the call, not about the function.
    # `harness/vitbridge.py` draws the same line for the same reason.
    emitted = {name for _, name in c_params}
    unreachable = [(a, ty, why) for a, ty, why in
                   (dropped_derived_args(sig, kind_aliases, derived_types)
                    if derived_types else [])
                   if a not in emitted]
    if unreachable:
        detail = "\n".join(f"    {a} : {ty}\n        {why}"
                            for a, ty, why in unreachable)
        raise ValueError(
            f"{func_name}: the Fortran bridge passes {len(unreachable)} argument(s) "
            f"that build_c_params omits from the C++ call, so the two disagree "
            f"about this function's arity and nothing built from them can link:\n"
            f"{detail}")

    bridge_param_str = ", ".join(bridge_params)
    lines.append(f"// Fortran reference (via BIND(C) bridge):")
    lines.append(f'extern "C" void {func_lower}_f90({bridge_param_str});')
    lines.append("")

    # Placeholder sections for the LLM to fill in
    lines.append("// --- TODO: Implement make_valid_input() ---")
    lines.append("// Returns an input struct where ALL validation checks pass.")
    lines.append("// The LLM writes this based on the Fortran validation conditions.")
    lines.append("")
    lines.append("// --- TODO: Implement test functions ---")
    lines.append("// One test per error branch, plus test_happy_path().")
    lines.append("// Each test calls BOTH C++ and Fortran, asserts they agree.")
    lines.append("")

    # Main function
    lines.append("// --- Test runner ---")
    lines.append("")
    lines.append("int main() {")
    lines.append("    TestResult tests[] = {")
    lines.append("        // TODO: Add test function calls here")
    lines.append("        // test_happy_path(),")
    lines.append("        // test_InCol_Alfa_negative(),")
    lines.append("        // ...")
    lines.append("    };")
    lines.append("")
    lines.append("    int n = sizeof(tests) / sizeof(tests[0]);")
    lines.append("    int passed = 0, failed = 0, skipped = 0;")
    lines.append("    for (int i = 0; i < n; i++) {")
    lines.append('        if (tests[i].detail && strstr(tests[i].detail, "SKIPPED")) {')
    lines.append("            skipped++;")
    lines.append('            printf("  SKIP %s\\n", tests[i].name);')
    lines.append("        } else if (tests[i].passed) {")
    lines.append("            passed++;")
    lines.append('            printf("  PASS %s\\n", tests[i].name);')
    lines.append("        } else {")
    lines.append("            failed++;")
    lines.append('            printf("  FAIL %s: %s\\n", tests[i].name, tests[i].detail);')
    lines.append("        }")
    lines.append("    }")
    lines.append('    printf("\\n%d passed, %d failed, %d skipped\\n", passed, failed, skipped);')
    lines.append("    return failed > 0 ? 1 : 0;")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def generate_test_validate_prompt(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    source_file: Optional[str] = None,
    fortran_source_text: Optional[str] = None,
    cpp_translation_text: Optional[str] = None,
    skeleton_text: Optional[str] = None,
    derived_types: Optional[dict] = None,
) -> str:
    """Generate a prompt for LLM-assisted test writing.

    Assembles the Fortran source, C++ struct definitions, translation,
    and test harness skeleton into a structured prompt that guides the
    LLM to write branch-coverage tests.

    Args:
        sig: Parsed function signature
        kind_aliases: Project kind mappings
        source_file: Path to the Fortran source
        fortran_source_text: Full text of the validation function
        cpp_translation_text: Full text of the C++ translation
        skeleton_text: The test harness skeleton
        derived_types: Registry of derived types used in the signature

    Returns:
        Complete prompt string
    """
    sections = []

    sections.append(f"# Test-Validate: Write branch-coverage tests for `{sig.name}`\n")

    sections.append(
        "Write a complete C++ test file that exercises **every error branch** "
        "in the validation function below. The Fortran source shows the "
        "validation logic; the C++ translation shows how errors are reported "
        "in the implementation you are testing.\n"
    )

    # Fortran source
    if fortran_source_text:
        sections.append("## Fortran Source (validation logic)\n")
        sections.append(f"From `{source_file or 'unknown'}`:\n")
        sections.append("```fortran")
        sections.append(fortran_source_text.strip())
        sections.append("```\n")

    # C++ translation
    if cpp_translation_text:
        sections.append("## C++ Translation (function under test)\n")
        sections.append("```cpp")
        sections.append(cpp_translation_text.strip())
        sections.append("```\n")

    # C struct definitions
    if derived_types:
        from .derived_type_parser import (
            generate_c_struct_header, generate_view_struct_header,
            order_types_by_dependency,
        )
        struct_defs = []
        compatible = [info for info in derived_types.values()
                      if info.is_c_compatible or getattr(info, 'view_strategy', None) == 'view']
        for type_info in order_types_by_dependency(compatible):
            if getattr(type_info, 'view_strategy', None) == 'view':
                mappings = getattr(type_info, 'field_mappings', None) or []
                header = generate_view_struct_header(type_info, mappings, kind_aliases,
                                                     registry=derived_types)
            else:
                header = generate_c_struct_header(type_info, kind_aliases,
                                                   registry=derived_types,
                                                   emit_includes=False)
            struct_defs.append(header)
        if struct_defs:
            sections.append("## C Struct Definitions\n")
            sections.append("```c")
            sections.append("\n\n".join(d.strip() for d in struct_defs))
            sections.append("```\n")

    # Test harness skeleton
    if skeleton_text:
        sections.append("## Test Harness Skeleton\n")
        sections.append(
            "Fill in the TODO sections. Keep the `TestResult` type, "
            "`main()` runner, and include directives. Add your tests "
            "to the `tests[]` array.\n"
        )
        sections.append("```cpp")
        sections.append(skeleton_text.strip())
        sections.append("```\n")

    # Instructions
    func_lower = sig.name.lower()
    sections.append("## Instructions\n")
    sections.append(
        "Each test must call **both** the C++ function and the Fortran "
        "reference (via the `_f90` bridge declared in the skeleton) and "
        "assert they produce the **same** error status. This proves the "
        "translation agrees with the original Fortran on every branch — "
        "the same verification principle as KGen kernel comparison.\n"
    )
    sections.append(
        "1. Read the Fortran source to identify every condition that "
        "triggers an error.\n"
        "2. Read the C++ translation to understand how errors are "
        "reported (what values are set, what output parameters indicate "
        "errors).\n"
        "3. Implement `make_valid_input()`: return an input struct where "
        "ALL validation checks pass.\n"
        "4. Implement `test_happy_path()`: call both C++ and Fortran "
        "with valid input, assert both report no error.\n"
        "5. For each error branch, implement a test function:\n"
        "   - Start from `make_valid_input()`\n"
        "   - Modify the specific field(s) to trigger that error\n"
        f"   - Call C++ `{sig.name}(...)` → get error status\n"
        f"   - Call Fortran `{func_lower}_f90(...)` with the same input → "
        "get error status\n"
        "   - Assert both return the same error status (agreement)\n"
        "   - Assert the expected error condition (correctness)\n"
        "6. For conditional branches (only checked when a flag is set), "
        "also set the enabling flag in the test.\n"
        "7. Name each test after the field being validated "
        "(e.g., `test_InCol_Alfa_negative`, `test_AFTabMod_invalid`).\n"
        "8. Add all test functions to the `tests[]` array in `main()`.\n"
    )

    return "\n".join(sections)
