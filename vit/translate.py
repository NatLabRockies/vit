"""
Translation scaffold and prompt generation.

Generates C++ translation scaffolds (correct signature, metadata header,
placeholder body) and translation prompts for LLM-assisted translation.

The scaffold produces an implementation-only file matching the existing
translation pattern. The extern "C" wrapper is added at integration time
by _generate_standalone_cpp() in integrate.py.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .fortran_parser import FortranSignature
from .interface_gen import (assert_integration_bridgeable, build_c_params,
                            cfi_args, result_is_out_param)
from .type_map import get_type_mapping


def generate_translation_scaffold(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    source_file: Optional[str] = None,
    vit_version: str = "0.1.0",
    struct_headers: Optional[list[str]] = None,
    derived_types: Optional[dict] = None,
    shared_headers: Optional[list[str]] = None,
    fortran_flags: Optional[str] = None,
) -> str:
    """
    Generate a C++ translation scaffold with metadata header and correct signature.

    The scaffold has:
    - A metadata comment block (source file, function name, hash, version, status,
      and the flags the REFERENCE is compiled with)
    - #include directives for the type header and the campaign's shared headers
    - The correct C++ function signature (from build_c_params)
    - A placeholder body with TODO comment

    **`shared_headers` is why four translation tips no longer need to exist.**
    A campaign's shared headers -- ROSCO's `rosco_constants.h` and
    `vit_fortran_read.h`, AeroDyn's `vit_nwtc.h` -- were declared in `vit.yaml`
    and used everywhere EXCEPT here, so every translation had to be told in
    prose to include them. Four tips ("use Fortran's constants, not `M_PI`",
    "shared constants header", "use `vit_fortran_read.h`, not `scanf`",
    "`PathSep` is platform-selected") existed only because the scaffold did not
    say so itself. A scaffold that includes them makes all four unwritable.

    `fortran_flags` is in the metadata for the same reason: whether a bare
    Fortran literal is single or double precision depends on
    `-fdefault-real-8`, and a translator cannot know that from the source.

    Args:
        sig: Parsed Fortran function signature
        kind_aliases: Project-specific kind aliases (e.g., ReKi → REAL(8))
        source_file: Path to the Fortran source file containing the function
        vit_version: VIT version string for metadata
        struct_headers: List of type header filenames to #include

    Returns:
        Complete C++ scaffold file as a string
    """
    lines = []

    # Metadata header
    lines.append(_generate_metadata_header(sig, kind_aliases, source_file,
                                           vit_version, fortran_flags))
    lines.append("")

    # An ALLOCATABLE INTENT(INOUT) dummy arrives as a Fortran 2018 C descriptor,
    # so the body needs the interop header before anything else it includes.
    if cfi_args(sig):
        lines.append("#include <ISO_Fortran_binding.h>")
        lines.append("")

    # The type header, then the campaign's shared headers. Deduplicated and
    # order-stable: a scaffold that changes its include order between runs makes
    # every regeneration look like an edit.
    includes = list(struct_headers or []) + list(shared_headers or [])
    for h in dict.fromkeys(includes):
        lines.append(f'#include "{h}"')
    if includes:
        lines.append("")

    # Refuse before a human writes a body for a signature that cannot carry its
    # own effect. `vit translate` is the earliest point the cost is avoidable:
    # ROSCO's AddToList got a scaffold -- `void AddToList(int* list, int n_list,
    # int element)` -- under a header saying "Do not change the function
    # signature", and no body of that signature can reallocate the caller's
    # array. See interface_gen.assert_integration_bridgeable.
    assert_integration_bridgeable(sig, kind_aliases)

    # Generate C++ signature
    params = build_c_params(sig, kind_aliases, derived_types)
    ret_type = _get_c_return_type(sig, kind_aliases)
    func_name = sig.cpp_impl_name

    param_str = ", ".join(f"{p[0]} {p[1]}" for p in params)
    lines.append(f"{ret_type} {func_name}({param_str}) {{")
    lines.append(f"    // TODO: implement {sig.name}")
    if ret_type != "void":
        lines.append(f"    return 0;")
    lines.append(f"}}")
    lines.append("")

    return "\n".join(lines)


def generate_translation_prompt(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    source_file: Optional[str] = None,
    fortran_source_text: Optional[str] = None,
    scaffold_text: Optional[str] = None,
    call_site_info: Optional[str] = None,
    shared_header_paths: Optional[list] = None,
    derived_types: Optional[dict] = None,
    callee_declarations: Optional[list[str]] = None,
) -> str:
    """
    Generate a complete translation prompt for LLM-assisted translation.

    The prompt includes the original Fortran source, the generated scaffold,
    call site information, and any accumulated translation tips.

    Args:
        sig: Parsed Fortran function signature
        kind_aliases: Project-specific kind aliases
        source_file: Path to the Fortran source file
        fortran_source_text: The original Fortran function source (verbatim)
        scaffold_text: The generated C++ scaffold (verbatim)
        call_site_info: Call site file and line (e.g., "Controllers.f90:102")
        shared_header_paths: Shared headers to read the callable inventory from
        callee_declarations: List of C declaration strings for already-translated
            callees (e.g., ["double computeua360_cnoffset_c(...);"]).

    Returns:
        Complete prompt string ready to be sent to an LLM
    """
    sections = []

    sections.append(f"# Translate Fortran function `{sig.name}` to C++\n")

    # Original Fortran source
    if fortran_source_text:
        sections.append("## Original Fortran Source\n")
        sections.append(f"From `{source_file or 'unknown'}`:\n")
        sections.append("```fortran")
        sections.append(fortran_source_text.strip())
        sections.append("```\n")

    # Generated scaffold
    if scaffold_text:
        sections.append("## C++ Scaffold (correct signature — preserve exactly)\n")
        sections.append("```cpp")
        sections.append(scaffold_text.strip())
        sections.append("```\n")
        sections.append("Replace the `// TODO` placeholder with the algorithm. "
                        "Do not change the function signature.\n")

    # Call site info
    if call_site_info:
        sections.append(f"## Call Site\n")
        sections.append(f"Called from: `{call_site_info}`\n")

    # C struct definitions for derived types (in dependency order)
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
            sections.append("These struct types are passed by pointer to the C++ function:\n")
            sections.append("```c")
            sections.append("\n\n".join(d.strip() for d in struct_defs))
            sections.append("```\n")

    # Already-translated callee declarations
    if callee_declarations:
        sections.append("## Already-Translated Callees\n")
        sections.append(
            "This function calls the following already-translated functions. "
            "Call them directly using these C signatures — the declarations are "
            "provided automatically by VIT (do NOT add your own `extern \"C\"` "
            "or `#include` for these).\n"
        )
        sections.append("```c")
        sections.append("\n".join(callee_declarations))
        sections.append("```\n")

    # The C descriptor convention, for the dummies that actually use it.
    #
    # Generated from the signature rather than written as prose: the section
    # appears only when `build_c_params` emitted a `CFI_cdesc_t*`, so it cannot
    # describe a convention this scaffold does not use.
    cfi = cfi_args(sig)
    if cfi:
        names = ", ".join(f"`{a.name}`" for a in cfi)
        sections.append("## ALLOCATABLE INTENT(INOUT) arguments — C descriptors\n")
        sections.append(
            f"{names} arrives as `CFI_cdesc_t*` — the CALLER'S OWN Fortran "
            "descriptor, not a copy of its data pointer. That is what lets the "
            "body do what the Fortran does: replace both the data and the "
            "extent, as `allocate` + `move_alloc` does.\n"
            "\n"
            "```c\n"
            "d->base_addr == nullptr           // .NOT. ALLOCATED(x)\n"
            "d->dim[0].extent                  // SIZE(x)\n"
            "d->dim[0].lower_bound             // LBOUND(x, 1)\n"
            "CFI_index_t s = lb + i;\n"
            "*(T*)CFI_address(d, &s)           // x(s) -- SUBSCRIPTS ARE FORTRAN'S,\n"
            "                                  // so they start at lower_bound, not 0\n"
            "CFI_deallocate(d);                // DEALLOCATE(x)\n"
            "CFI_index_t lo = 1, hi = n;\n"
            "CFI_allocate(d, &lo, &hi, 0);     // ALLOCATE(x(1:n))\n"
            "```\n"
            "\n"
            "Copy the old contents out BEFORE `CFI_deallocate` — it frees the "
            "storage `CFI_address` was reading. `move_alloc` gives the result "
            "the SOURCE's bounds, and an `allocate(tmp(n))` source is `1:n`, so "
            "a Fortran `move_alloc` into this dummy leaves its lower bound 1 "
            "whatever the caller's was. Reproduce that; do not improve on it.\n"
        )

    # ALLOCATABLE field hints for view types
    if derived_types:
        alloc_hints = []
        for type_info in derived_types.values():
            if getattr(type_info, 'view_strategy', None) != 'view':
                continue
            alloc_fields = [f for f in type_info.fields if f.is_allocatable]
            if not alloc_fields:
                continue
            type_lines = [f"**{type_info.name}** (view type — ALLOCATABLE fields):"]
            for f in alloc_fields:
                dims = f"DIMENSION({f.array_dims})" if f.array_dims else "DIMENSION(:)"
                type_lines.append(f"- `{f.name}` — `{f.fortran_type}, {dims}`")
            alloc_hints.append("\n".join(type_lines))
        if alloc_hints:
            sections.append("## ALLOCATABLE Fields (may need pre-allocation)\n")
            sections.append(
                "If this function allocates or populates arrays in these types, "
                "the Fortran wrapper will need ALLOCATE statements before calling C++. "
                "The view struct exposes these as pointer+size pairs.\n"
            )
            sections.append("\n\n".join(alloc_hints))
            sections.append("")

    # Known-shape defects, GENERATED from the check registry.
    #
    # Derived, never hand-written: one line per active check, so the guidance
    # cannot describe a rule that is not enforced, and a check retired because
    # the tool made its defect impossible takes its line with it. That is the
    # property `TRANSLATION_TIPS.md` could not have -- it grew to 2,314 lines
    # and nothing in it was able to go red.
    from .checks import prompt_section
    sections.append(prompt_section())
    sections.append("")

    # What the shared headers provide, READ FROM THE HEADERS. Not a maintained
    # list -- a maintained list is a copy, and a copy drifts silently.
    if shared_header_paths:
        from .header_api import inventory
        inv = inventory(shared_header_paths)
        if inv:
            sections.append(inv)
            sections.append("")

    # General instruction
    sections.append("## Instructions\n")
    sections.append(
        "Consider the Fortran code carefully and be intentional about every "
        "translation choice. Pay attention to:\n"
        "- Array indexing (Fortran is 1-based, C++ is 0-based)\n"
        "- Fortran intrinsic functions and their C++ equivalents\n"
        "- Integer vs. floating-point division\n"
        "- Any implicit type conversions\n"
        "\n"
        "If anything is unclear or ambiguous, ask rather than assume."
    )
    sections.append("")

    return "\n".join(sections)


def _generate_metadata_header(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    source_file: Optional[str] = None,
    vit_version: str = "0.1.0",
    fortran_flags: Optional[str] = None,
) -> str:
    """Generate the metadata comment block for a translation file."""
    lines = []
    lines.append(f"// VIT Translation Scaffold")
    lines.append(f"// Function: {sig.name}")
    if source_file:
        lines.append(f"// Source: {Path(source_file).name}")
    if sig.module_name:
        lines.append(f"// Module: {sig.module_name}")

    # Fortran signature summary
    params = build_c_params(sig, kind_aliases)
    ret_type = _get_c_return_type(sig, kind_aliases)
    fortran_sig = _format_fortran_signature(sig)
    lines.append(f"// Fortran: {fortran_sig}")
    if fortran_flags:
        # `-fdefault-real-8` decides whether a bare literal in the reference is
        # single or double precision, and nothing in the source says so.
        lines.append(f"// Reference built with: {fortran_flags}")

    # Source hash (for detecting when the Fortran source changes)
    if source_file:
        try:
            source_text = Path(source_file).read_text()
            func_text = _extract_function_text(source_text, sig.name)
            if func_text:
                md5 = hashlib.md5(func_text.encode()).hexdigest()[:12]
                lines.append(f"// Source MD5: {md5}")
        except (OSError, IOError):
            pass

    lines.append(f"// VIT: {vit_version}")
    lines.append(f"// Status: unverified")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"// Generated: {timestamp}")

    return "\n".join(lines)


def _get_c_return_type(sig: FortranSignature, kind_aliases: Optional[dict] = None) -> str:
    """Get the C return type for a function signature."""
    if sig.is_subroutine:
        return "void"
    if result_is_out_param(sig, kind_aliases):
        return "void"
    if sig.result_type:
        mapping = get_type_mapping(sig.result_type, kind_aliases)
        return mapping.c_type
    raise ValueError(
        f"Cannot determine return type for function '{sig.name}'. "
        f"Ensure the Fortran parser captures the result type."
    )


def _format_fortran_signature(sig: FortranSignature) -> str:
    """Format a one-line Fortran signature summary for the metadata header."""
    kind_str = "FUNCTION" if sig.is_function else "SUBROUTINE"
    args = ", ".join(a.name for a in sig.args)
    result = ""
    if sig.result_name and sig.is_function:
        result = f" RESULT({sig.result_name})"
    return f"{kind_str} {sig.name}({args}){result}"


def _extract_function_text(source: str, function_name: str) -> Optional[str]:
    """Extract the raw text of a function/subroutine from source code."""
    import re
    lines = source.split("\n")
    name_upper = function_name.upper()
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]
        if start_idx is None:
            if re.search(rf'\b(FUNCTION|SUBROUTINE)\s+{name_upper}\s*\(', stripped):
                start_idx = i
        else:
            if re.search(rf'\bEND\s+(FUNCTION|SUBROUTINE)\b', stripped):
                end_idx = i + 1
                break

    if start_idx is not None and end_idx is not None:
        return "\n".join(lines[start_idx:end_idx])
    return None
