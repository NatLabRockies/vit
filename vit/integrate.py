"""
Codebase integration — generate patches for the real project.

After KGen verification passes, this module generates the actual changes
needed to integrate the C++ function into the project's build system.

For CMake projects (like ROSCO), this means:
1. Adding the C++ source file
2. Modifying the Fortran module file (interface block + wrapper)
3. Updating CMakeLists.txt (enable CXX, add source, link stdc++)
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .fortran_parser import FortranSignature
from .interface_gen import (
    generate_fortran_interface_block,
    generate_fortran_wrapper,
    build_c_params,
)


@dataclass
class IntegrationPatch:
    """A single file modification for codebase integration."""
    filepath: str           # File to modify
    description: str        # Human-readable description
    action: str             # "create", "modify", "insert"
    content: str            # New content (for create) or patch description
    original_snippet: Optional[str] = None  # For modify: what to replace
    replacement_snippet: Optional[str] = None


@dataclass
class IntegrationPlan:
    """Complete plan for integrating a verified C++ function."""
    function_name: str
    patches: list[IntegrationPatch]
    summary: str

    def to_markdown(self) -> str:
        """Generate a human-readable integration plan."""
        lines = [
            f"# Integration Plan: {self.function_name}",
            "",
            self.summary,
            "",
            f"## Changes Required ({len(self.patches)} files)",
            "",
        ]

        for i, patch in enumerate(self.patches, 1):
            lines.append(f"### {i}. {patch.description}")
            lines.append(f"**File:** `{patch.filepath}`")
            lines.append(f"**Action:** {patch.action}")
            lines.append("")

            if patch.action == "create":
                lines.append("```")
                lines.append(patch.content)
                lines.append("```")
            elif patch.action == "modify" and patch.original_snippet:
                lines.append("**Replace:**")
                lines.append("```fortran")
                lines.append(patch.original_snippet)
                lines.append("```")
                lines.append("**With:**")
                lines.append("```fortran")
                lines.append(patch.replacement_snippet or "")
                lines.append("```")
            else:
                lines.append("```")
                lines.append(patch.content)
                lines.append("```")

            lines.append("")

        return "\n".join(lines)


def generate_integration_plan(
    sig: FortranSignature,
    cpp_impl_path: str,
    source_file: str,
    cmake_file: Optional[str] = None,
    src_dir: Optional[str] = None,
    target_name: str = "discon",
    kind_aliases: Optional[dict] = None,
    derived_types: Optional[dict] = None,
) -> IntegrationPlan:
    """
    Generate a complete integration plan for a verified C++ function.

    Args:
        sig: Parsed function signature
        cpp_impl_path: Path to the user's C++ implementation
        source_file: Fortran source file containing the original function
        cmake_file: Path to CMakeLists.txt (if using CMake)
        src_dir: Directory where C++ source should be placed
        target_name: CMake target name
        kind_aliases: Project kind aliases

    Returns:
        IntegrationPlan with all required changes
    """
    patches = []
    src_dir = src_dir or os.path.dirname(source_file)

    # 1. Create the C++ wrapper source file
    cpp_wrapper_name = f"{sig.name.lower()}_cpp.cpp"
    cpp_wrapper_path = os.path.join(src_dir, cpp_wrapper_name)
    impl_filename = f"{sig.name.lower()}_impl.hpp"

    # Read user's C++ implementation
    cpp_impl_content = Path(cpp_impl_path).read_text()

    # Generate the complete C++ file (impl + wrapper)
    cpp_complete = _generate_standalone_cpp(sig, cpp_impl_content, kind_aliases,
                                             derived_types)

    patches.append(IntegrationPatch(
        filepath=cpp_wrapper_path,
        description=f"Create C++ source file with extern \"C\" wrapper",
        action="create",
        content=cpp_complete,
    ))

    # 2. Modify the Fortran source file
    fortran_patches = _generate_fortran_patches(
        sig, source_file, kind_aliases, derived_types
    )
    patches.extend(fortran_patches)

    # 3. Update CMakeLists.txt
    if cmake_file:
        cmake_patches = _generate_cmake_patches(
            cmake_file, cpp_wrapper_name, target_name
        )
        patches.extend(cmake_patches)

    summary = (
        f"Integration of C++ {sig.name}() requires {len(patches)} changes: "
        f"add C++ source file, modify {os.path.basename(source_file)} "
        f"(add USE ISO_C_BINDING, interface block, and wrapper function)"
    )
    if cmake_file:
        summary += f", and update {os.path.basename(cmake_file)} (enable CXX, add source, link stdc++)"

    # 4. Update shared C header for translated functions
    header_path = os.path.join(src_dir, "vit_translated.h")
    c_decl = _generate_c_declaration(sig, kind_aliases, derived_types)
    patches.append(IntegrationPatch(
        filepath=header_path,
        description=f"Add {sig.c_func_name} declaration to shared C header",
        action="append_header",
        content=c_decl,
    ))

    return IntegrationPlan(
        function_name=sig.name,
        patches=patches,
        summary=summary,
    )


def apply_integration_plan(plan: IntegrationPlan, dry_run: bool = False) -> dict:
    """
    Apply an integration plan to the actual codebase.

    Args:
        plan: The integration plan to apply
        dry_run: If True, only report what would be done

    Returns:
        Dict with 'success', 'applied', 'errors'
    """
    result = {"success": True, "applied": [], "errors": []}

    for patch in plan.patches:
        try:
            if patch.action == "create":
                if dry_run:
                    result["applied"].append(f"[DRY RUN] Would create: {patch.filepath}")
                else:
                    Path(patch.filepath).parent.mkdir(parents=True, exist_ok=True)
                    Path(patch.filepath).write_text(patch.content)
                    result["applied"].append(f"Created: {patch.filepath}")

            elif patch.action == "modify":
                if dry_run:
                    result["applied"].append(f"[DRY RUN] Would modify: {patch.filepath}")
                else:
                    content = Path(patch.filepath).read_text()
                    if patch.original_snippet and patch.replacement_snippet:
                        if patch.original_snippet in content:
                            content = content.replace(
                                patch.original_snippet,
                                patch.replacement_snippet,
                                1
                            )
                            Path(patch.filepath).write_text(content)
                            result["applied"].append(f"Modified: {patch.filepath}")
                        else:
                            result["errors"].append(
                                f"Could not find expected text in {patch.filepath}"
                            )
                            result["success"] = False

            elif patch.action == "append_header":
                if dry_run:
                    result["applied"].append(
                        f"[DRY RUN] Would update header: {patch.filepath}"
                    )
                else:
                    _apply_header_patch(patch)
                    result["applied"].append(f"Updated header: {patch.filepath}")

            elif patch.action == "insert":
                if dry_run:
                    result["applied"].append(f"[DRY RUN] Would insert into: {patch.filepath}")
                else:
                    content = Path(patch.filepath).read_text()
                    # Content field describes where and what to insert
                    # This is handled by the specific patch generator
                    result["applied"].append(f"Insert into: {patch.filepath} (manual review needed)")

        except Exception as e:
            result["errors"].append(f"{patch.filepath}: {e}")
            result["success"] = False

    return result


def _apply_header_patch(patch: IntegrationPatch) -> None:
    """
    Create or update the shared C header for translated functions.

    Creates the file with an include guard if it doesn't exist.
    Appends the declaration if it's not already present (idempotent).
    """
    header_path = Path(patch.filepath)
    guard = "VIT_TRANSLATED_H"
    declaration = patch.content

    if header_path.exists():
        content = header_path.read_text()
        # Skip if this declaration is already present
        if declaration in content:
            return
        # Insert before the closing #ifdef __cplusplus / } / #endif block
        # (i.e., inside the extern "C" scope)
        closing_marker = "\n#ifdef __cplusplus\n}\n#endif"
        if closing_marker in content:
            content = content.replace(
                closing_marker,
                f"{declaration}\n{closing_marker}",
            )
        else:
            # Fallback: insert before #endif guard
            content = content.replace(
                f"\n#endif // {guard}\n",
                f"{declaration}\n\n#endif // {guard}\n",
            )
        header_path.write_text(content)
    else:
        header_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "// Shared header for VIT-translated function entry points.",
            "// Auto-generated by `vit integrate --apply`. Do not edit manually.",
            "//",
            "// C++ translations #include this header to call other translated",
            "// functions directly via their _c entry points, bypassing the",
            "// Fortran wrapper layer.",
            "",
            f"#ifndef {guard}",
            f"#define {guard}",
            "",
            "#ifdef __cplusplus",
            'extern "C" {',
            "#endif",
            "",
            declaration,
            "",
            "#ifdef __cplusplus",
            "}",
            "#endif",
            "",
            f"#endif // {guard}",
            "",
        ]
        header_path.write_text("\n".join(lines))


def _generate_c_declaration(sig: FortranSignature,
                            kind_aliases: Optional[dict] = None,
                            derived_types: Optional[dict] = None) -> str:
    """
    Generate the C function declaration for a translated function's _c entry point.

    Returns a single line like:
        double saturate_c(double inputValue, double minValue, double maxValue);
    """
    from .type_map import get_type_mapping

    c_func_name = sig.c_func_name
    c_params = build_c_params(sig, kind_aliases, derived_types)
    param_str = ", ".join(f"{p[0]} {p[1]}" for p in c_params)

    if sig.is_function and not sig.result_is_array:
        ret_mapping = get_type_mapping(
            sig.result_type or "REAL(8)", kind_aliases, derived_types
        )
        return f"{ret_mapping.c_type} {c_func_name}({param_str});"
    else:
        return f"void {c_func_name}({param_str});"


def _generate_standalone_cpp(sig: FortranSignature, impl_content: str,
                              kind_aliases: Optional[dict] = None,
                              derived_types: Optional[dict] = None) -> str:
    """
    Generate a standalone C++ file with implementation + extern "C" wrapper.

    Skips passthrough args (non-C-compatible derived types).
    Adds size parameters for assumed-shape arrays.
    """
    from .type_map import get_type_mapping

    lines = []
    lines.append(f"// C++ implementation of Fortran function: {sig.name}")
    lines.append(f"// Generated by VIT (Verified Incremental Translator)")
    lines.append(f"// After verification: {sig.name} kernel PASSED")
    lines.append("")
    lines.append("#include <algorithm>")
    lines.append("#include <cmath>")
    lines.append("")

    # Embed struct typedefs for C-compatible derived types (in dependency order)
    if derived_types:
        from .derived_type_parser import generate_c_struct_header, order_types_by_dependency
        compatible = [info for info in derived_types.values() if info.is_c_compatible]
        for type_info in order_types_by_dependency(compatible):
            header = generate_c_struct_header(type_info, kind_aliases,
                                               registry=derived_types,
                                               emit_includes=False)
            lines.append(header.rstrip())
            lines.append("")

    # Include the user's implementation
    lines.append("// --- User-provided C++ implementation ---")
    lines.append(impl_content.rstrip())
    lines.append("")

    # Generate extern "C" wrapper using shared param builder
    lines.append("// --- Fortran-callable C wrapper ---")
    c_func_name = sig.c_func_name
    c_params = build_c_params(sig, kind_aliases, derived_types)

    param_str = ", ".join(f"{p[0]} {p[1]}" for p in c_params)
    arg_str = ", ".join(p[1] for p in c_params)

    if sig.is_function and not sig.result_is_array:
        ret_mapping = get_type_mapping(sig.result_type or "REAL(8)", kind_aliases, derived_types)

        lines.append(f'extern "C" {{')
        lines.append(f"    {ret_mapping.c_type} {c_func_name}({param_str}) {{")
        lines.append(f"        return {sig.name.lower()}({arg_str});")
        lines.append(f"    }}")
        lines.append(f"}}")
    else:  # Subroutine or array-valued function
        lines.append(f'extern "C" {{')
        lines.append(f"    void {c_func_name}({param_str}) {{")
        lines.append(f"        {sig.name.lower()}({arg_str});")
        lines.append(f"    }}")
        lines.append(f"}}")

    lines.append("")
    return "\n".join(lines)


def _generate_fortran_patches(sig: FortranSignature, source_file: str,
                               kind_aliases: Optional[dict] = None,
                               derived_types: Optional[dict] = None) -> list[IntegrationPatch]:
    """Generate patches for the Fortran source file."""
    patches = []
    content = Path(source_file).read_text()
    lines = content.split("\n")
    name_upper = sig.name.upper()

    # Patch 1: Add USE ISO_C_BINDING before first IMPLICIT NONE
    if "ISO_C_BINDING" not in content.upper():
        for i, line in enumerate(lines):
            if line.strip().upper().startswith("IMPLICIT NONE"):
                indent = len(line) - len(line.lstrip())
                patches.append(IntegrationPatch(
                    filepath=source_file,
                    description="Add USE ISO_C_BINDING before IMPLICIT NONE",
                    action="modify",
                    content="",
                    original_snippet=line,
                    replacement_snippet=" " * indent + "USE ISO_C_BINDING\n" + line,
                ))
                break

    # Patch 2: Add INTERFACE block above CONTAINS
    interface_block = generate_fortran_interface_block(sig, kind_aliases, derived_types)
    for i, line in enumerate(lines):
        if line.strip().upper() == "CONTAINS":
            patches.append(IntegrationPatch(
                filepath=source_file,
                description=f"Add INTERFACE block for {sig.c_func_name} above CONTAINS",
                action="modify",
                content=interface_block,
                original_snippet=line,
                replacement_snippet=f"\n{interface_block}\n\n{line}",
            ))
            break

    # Patch 3: Replace function body with wrapper
    wrapper = generate_fortran_wrapper(sig, kind_aliases, derived_types)
    end_keyword = "FUNCTION" if sig.is_function else "SUBROUTINE"

    # Find the original function body
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if re.search(rf'\b{end_keyword}\s+{name_upper}\s*\(', stripped):
            if not stripped.startswith("END"):
                start_idx = i
        if start_idx is not None and re.match(rf'\s*END\s+{end_keyword}\b', stripped):
            end_idx = i
            break

    if start_idx is not None and end_idx is not None:
        original_func = "\n".join(lines[start_idx:end_idx + 1])
        patches.append(IntegrationPatch(
            filepath=source_file,
            description=f"Replace {sig.name} body with C++-calling wrapper",
            action="modify",
            content="",
            original_snippet=original_func,
            replacement_snippet=wrapper,
        ))

    return patches


def _generate_cmake_patches(cmake_file: str, cpp_source_name: str,
                             target_name: str) -> list[IntegrationPatch]:
    """Generate patches for CMakeLists.txt."""
    content = Path(cmake_file).read_text()
    patches = []

    # Enable CXX language
    if "enable_language(CXX)" not in content and "enable_language( CXX" not in content:
        # Find project() or cmake_minimum_required() to add after
        for pattern in [r'(project\s*\([^)]*\))', r'(cmake_minimum_required\s*\([^)]*\))']:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                patches.append(IntegrationPatch(
                    filepath=cmake_file,
                    description="Enable CXX language in CMake",
                    action="modify",
                    content="",
                    original_snippet=match.group(0),
                    replacement_snippet=match.group(0) + "\nenable_language(CXX)",
                ))
                break

    # Add C++ source to target
    if cpp_source_name not in content:
        sources_match = re.search(
            r'set\s*\(\s*SOURCES\b(.*?)(^\))',
            content, re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if sources_match:
            block_body = sources_match.group(1)
            # Find the last non-blank, non-comment line in the block
            source_lines = [l for l in block_body.strip().split('\n')
                            if l.strip() and not l.strip().startswith('#')]
            if source_lines:
                last_entry = source_lines[-1]
                patches.append(IntegrationPatch(
                    filepath=cmake_file,
                    description=f"Add {cpp_source_name} to source list",
                    action="modify",
                    content="",
                    original_snippet=last_entry,
                    replacement_snippet=f"{last_entry}\n    src/{cpp_source_name}",
                ))

    # Link stdc++
    if "stdc++" not in content:
        lib_match = re.search(
            rf'(add_library\s*\(\s*{re.escape(target_name)}\s+SHARED\s+\$\{{SOURCES\}}\s*\))',
            content,
        )
        if lib_match:
            patches.append(IntegrationPatch(
                filepath=cmake_file,
                description="Link stdc++ library",
                action="modify",
                content="",
                original_snippet=lib_match.group(0),
                replacement_snippet=f"{lib_match.group(0)}\ntarget_link_libraries({target_name} PRIVATE stdc++)",
            ))

    return patches
