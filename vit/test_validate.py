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
from typing import Optional

from .fortran_parser import FortranSignature, parse_signature_from_source
from .interface_gen import build_c_params
from .type_map import get_type_mapping


def _parse_link_libraries(build_dir: str, target_name: str) -> Optional[list[str]]:
    """Parse CMake link.txt to extract library dependencies for a target.

    Searches for the target's link.txt in the build directory, parses the
    linker command, and returns all library references (.a paths and -l flags).

    Args:
        build_dir: Absolute path to the build directory
        target_name: CMake target name (e.g., "aerodyn_driver")

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


def _decomposed_param_names(arg, type_info):
    """Ordered, collision-free bridge parameter names for a decomposed type.

    Returns a list of (field, kind, rank, names) where `kind` is one of
    "alloc" / "nested" / "scalar" and `names` maps a role ("ptr", "n", "rows",
    "cols", "scalar") to the parameter name to emit.

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
        if fld.is_allocatable:
            rank = fld.array_dims.count(",") + 1 if fld.array_dims else 1
            names = {"ptr": uniq(f"{base}_ptr")}
            if rank == 1:
                names["n"] = uniq(f"{base}_n")
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
    spec = _decomposed_param_names(arg, type_info)
    # A deferred-length CHARACTER field has no length to declare, so it cannot
    # cross. Collected and reported rather than dropped in silence.
    skipped_char: list[str] = []

    for fld, kind, rank, names in spec:
        if kind == "alloc":
            c_params.append((f"        TYPE(C_PTR), VALUE :: {names['ptr']}", names["ptr"]))
            if rank == 1:
                c_params.append((
                    f"        INTEGER(C_INT), VALUE :: {names['n']}", names["n"]))
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
        elif kind == "fixed":
            iso_type = get_type_mapping(fld.fortran_type, kind_aliases).iso_c_type
            # By reference, with the declared bounds: C passes the buffer, every
            # element crosses, and INTENT(INOUT/OUT) can copy back.
            c_params.append((f"        {iso_type} :: {names['scalar']}({rank})",
                             names["scalar"]))
        else:
            iso_type = get_type_mapping(fld.fortran_type, kind_aliases).iso_c_type
            c_params.append((f"        {iso_type}, VALUE :: {names['scalar']}", names["scalar"]))

    # Local: real Fortran type (not POINTER)
    local_decls.append(f"        TYPE({type_info.name}) :: {local_var}")

    # Temp pointers and loop variables for ALLOCATABLE fields
    for fld in type_info.fields:
        if not fld.is_allocatable:
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
                p_ptr, p_n = names["ptr"], names["n"]
                pre_call.append(f"        IF (C_ASSOCIATED({p_ptr})) THEN")
                pre_call.append(f"            ALLOCATE({local_var}%{fld.name}({p_n}))")
                pre_call.append(
                    f"            CALL C_F_POINTER({p_ptr}, {tmp}, [{p_n}])")
                pre_call.append(
                    f"            {local_var}%{fld.name}(1:{p_n}) = {tmp}(1:{p_n})")
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
    intent_text = str(getattr(getattr(arg, "intent", None), "value", None)
                      or getattr(getattr(arg, "intent", None), "name", None)
                      or getattr(arg, "intent", "") or "").upper()
    if "OUT" in intent_text:
        for fld, kind, rank, names in spec:
            if kind == "fixed":
                post_call.append(f"        {names['scalar']} = {local_var}%{fld.name}")
            elif kind == "nested":
                tmp = f"vit_nested_{arg.name}_{fld.name}"
                post_call.append(f"        IF (C_ASSOCIATED({names['ptr']})) "
                                 f"{tmp} = {local_var}%{fld.name}")
            elif kind == "char" and rank:
                ci = f"vit_ci_{arg.name}_{fld.name}"
                post_call.append(f"        DO {ci} = 1, {rank}")
                post_call.append(f"            {names['scalar']}({ci}) = "
                                 f"{local_var}%{fld.name}({ci}:{ci})")
                post_call.append(f"        END DO")

    # Post-call: DEALLOCATE ALLOCATABLEs
    for fld in type_info.fields:
        if fld.is_allocatable:
            post_call.append(
                f"        IF (ALLOCATED({local_var}%{fld.name})) "
                f"DEALLOCATE({local_var}%{fld.name})")

    call_args.append(local_var)


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
        elif "CHARACTER" in fortran_type:
            # CHARACTER is not directly C-interoperable, so it crosses as a
            # C_CHAR buffer plus its length and is staged through a local
            # CHARACTER(len) that the Fortran function can actually take.
            #
            # This used to declare an uninitialized CHARACTER(8192) local and
            # pass THAT, exposing no C-side parameter at all: the bridge
            # compiled, ran, and tested nothing. Any function whose input is a
            # CHARACTER argument was silently unverifiable.
            c_params.append(
                (f"        CHARACTER(KIND=C_CHAR), INTENT({intent}) :: {arg.name}_c(*)",
                 f"{arg.name}_c"))
            c_params.append(
                (f"        INTEGER(C_INT), VALUE, INTENT(IN) :: len_{arg.name}",
                 f"len_{arg.name}"))
            # A CHARACTER ARRAY dummy has a second, independent size. Dropping
            # it produced a bridge declaring a CHARACTER SCALAR where the
            # function takes an array -- which does not even match the
            # function's interface, so the whole class was unbridgeable.
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
            if count_expr:
                local_decls.append(
                    f"        CHARACTER(len_{arg.name}) :: {arg.name}({count_expr})")
                local_decls.append(f"        INTEGER :: vit_i_{arg.name}, vit_j_{arg.name}")
                pre_call.append(f"        DO vit_j_{arg.name} = 1, {count_expr}")
                pre_call.append(f"            DO vit_i_{arg.name} = 1, len_{arg.name}")
                pre_call.append(f"                {arg.name}(vit_j_{arg.name})(vit_i_{arg.name}:vit_i_{arg.name}) = &")
                pre_call.append(f"                    {arg.name}_c((vit_j_{arg.name} - 1) * len_{arg.name} + vit_i_{arg.name})")
                pre_call.append(f"            END DO")
                pre_call.append(f"        END DO")
                if intent in ("INOUT", "OUT"):
                    post_call.append(f"        DO vit_j_{arg.name} = 1, {count_expr}")
                    post_call.append(f"            DO vit_i_{arg.name} = 1, len_{arg.name}")
                    post_call.append(f"                {arg.name}_c((vit_j_{arg.name} - 1) * len_{arg.name} + vit_i_{arg.name}) = &")
                    post_call.append(f"                    {arg.name}(vit_j_{arg.name})(vit_i_{arg.name}:vit_i_{arg.name})")
                    post_call.append(f"            END DO")
                    post_call.append(f"        END DO")
            else:
                local_decls.append(f"        CHARACTER(len_{arg.name}) :: {arg.name}")
                local_decls.append(f"        INTEGER :: vit_i_{arg.name}")
                pre_call.append(f"        DO vit_i_{arg.name} = 1, len_{arg.name}")
                pre_call.append(f"            {arg.name}(vit_i_{arg.name}:vit_i_{arg.name}) = {arg.name}_c(vit_i_{arg.name})")
                pre_call.append(f"        END DO")
                if intent in ("INOUT", "OUT"):
                    post_call.append(f"        DO vit_i_{arg.name} = 1, len_{arg.name}")
                    post_call.append(f"            {arg.name}_c(vit_i_{arg.name}) = {arg.name}(vit_i_{arg.name}:vit_i_{arg.name})")
                    post_call.append(f"        END DO")
            call_args.append(arg.name)
        elif arg.is_array and arg.is_assumed_shape:
            # Assumed-shape arrays: receive as assumed-size (*) + size parameter.
            # Pass explicit section to the Fortran function (which expects assumed-shape).
            #
            # The kind comes from the mapping, not from a literal. It used to be
            # hardcoded REAL(C_DOUBLE), so every REAL(ReKi) or REAL(SiKi)
            # argument -- REAL(4) in ROSCO -- got a REAL(8) dummy.
            c_type = _iso_c_for(fortran_type, kind_aliases)
            c_params.append((f"        {c_type}, INTENT({intent}) :: {arg.name}(*)", arg.name))
            c_params.append((f"        INTEGER(C_INT), VALUE, INTENT(IN) :: n_{arg.name}", f"n_{arg.name}"))
            call_args.append(f"{arg.name}(1:n_{arg.name})")
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
        if "LOGICAL" in result_type_upper:
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

    lines.append(f"    SUBROUTINE {bridge_name}({', '.join(param_names)}) &")
    lines.append(f"        BIND(C, NAME='{bridge_name}')")
    lines.append(f"        USE ISO_C_BINDING")
    if sig.module_name:
        lines.append(f"        USE {sig.module_name}")
    # USE modules that provide derived types.
    # Scan module-level USE statements from the source file instead of
    # guessing a naming convention (e.g., _Types).
    if any(arg.is_derived_type for arg in sig.args) and source_file:
        source_text = Path(source_file).read_text()
        module_uses = _get_module_use_statements(source_text)
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

    # Pre-call conversions
    for conv in pre_call:
        lines.append(conv)

    # Call the original function
    if sig.is_function:
        result_type_upper = (sig.result_type or "").upper()
        if "LOGICAL" in result_type_upper:
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

    return "\n".join(lines)


_CALLEE_CALL = re.compile(r"\b(\w+)_c\s*\(")


def generate_callee_bridges(
    cpp_text: str,
    unit_name: str,
    source_dir: str,
    kind_aliases: Optional[dict] = None,
    derived_types: Optional[dict] = None,
) -> tuple[Optional[str], list[str]]:
    """Fortran BIND(C) bridges for every `<callee>_c` the translation calls.

    Returns (module source or None, notes).

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
    from vit.interface_gen import generate_kernel_callee_bridge

    names = []
    for m in _CALLEE_CALL.finditer(cpp_text):
        n = m.group(1)
        if n.lower() != unit_name.lower() and n not in names:
            names.append(n)
    if not names:
        return None, []

    notes: list[str] = []
    bodies: list[str] = []
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
            body = body.replace("USE ISO_C_BINDING",
                                f"USE ISO_C_BINDING\n        USE {mod}, ONLY : {sig.name}", 1)
        else:
            notes.append(f"{name}_c: no module recorded for the callee; the bridge "
                         f"will not resolve it")
        bodies.append(body)
        notes.append(f"{name}_c: bridged to Fortran {name}"
                     + (f" (USE {mod})" if mod else ""))

    if not bodies:
        return None, notes

    header = [
        f"! VIT: callee bridges for the {unit_name} test-validate harness.",
        "! Each `<callee>_c` calls the ORIGINAL Fortran, so both sides of the",
        "! comparison share one callee implementation and a mismatch is",
        "! attributable to the unit under test.",
        "",
    ]
    return "\n".join(header + bodies) + "\n", notes


def generate_test_makefile(
    sig: FortranSignature,
    build_dir: str,
    source_dir: str,
    config_dir: str = ".",
    target_name: str = "",
    fortran_flags: str = "",
    preprocessor_defines: Optional[list[str]] = None,
    shared_files: Optional[list[str]] = None,
    cpp_file: Optional[str] = None,
    patched_source: Optional[str] = None,
    callee_bridge: bool = False,
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
    lines.append("FC = gfortran")
    lines.append("CXX = g++")
    # Fortran flags: local .mod files first (for patched source), then production
    lines.append(f"FC_FLAGS = -fPIC -ffree-line-length-none {fortran_flags} {defines} \\")
    lines.append(f"           -I. -I{build_dir}/ftnmods -J.")
    lines.append(f"CXX_FLAGS = -O2 -fPIC -ffp-contract=off {cxx_includes}")
    lines.append("")
    # Derive library dependencies from CMake target's link.txt
    link_libs = _parse_link_libraries(build_dir, target_name) if target_name else None
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
    # A target built as a Fortran shared library links it implicitly and the
    # omission never shows; the object-file fallback above links raw .o files,
    # and then every Fortran I/O, string or intrinsic call in the reference is
    # an undefined symbol at link time (ROSCO's ZeroMQInterface.f90 alone brings
    # in _gfortran_string_len_trim, _gfortran_concat_string and the transfer_*
    # family). Appended rather than prepended: it must resolve after the objects
    # that need it.
    lines.append("LIBS += -lgfortran -lm")
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
                for fld, kind, rank, names in _decomposed_param_names(arg, type_info):
                    if kind == "alloc":
                        c_type = get_type_mapping(fld.fortran_type, kind_aliases).c_type
                        bridge_params.append(f"{c_type}* {names['ptr']}")
                        if rank == 1:
                            bridge_params.append(f"int {names['n']}")
                        else:
                            bridge_params.append(f"int {names['rows']}")
                            bridge_params.append(f"int {names['cols']}")
                    elif kind == "nested":
                        bridge_params.append(f"void* {names['ptr']}")
                    elif kind == "char":
                        if rank:
                            bridge_params.append(f"char* {names['scalar']}")
                    elif kind == "fixed":
                        # By reference on the Fortran side, so a pointer here.
                        c_type = get_type_mapping(fld.fortran_type, kind_aliases).c_type
                        bridge_params.append(f"{c_type}* {names['scalar']}")
                    else:
                        c_type = get_type_mapping(fld.fortran_type, kind_aliases).c_type
                        bridge_params.append(f"{c_type} {names['scalar']}")
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
