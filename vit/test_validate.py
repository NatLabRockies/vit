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
from pathlib import Path
from typing import Optional

from .fortran_parser import FortranSignature
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


def generate_fortran_bridge(
    sig: FortranSignature,
    kind_aliases: Optional[dict] = None,
    source_file: Optional[str] = None,
) -> str:
    """Generate a BIND(C) Fortran bridge for calling the original function from C++.

    The bridge allows the test harness to call the original Fortran validation
    function for comparison against the C++ translation. Named <name>_f90 to
    distinguish from the C++ function.

    Args:
        sig: Parsed Fortran function signature
        kind_aliases: Project kind mappings
        source_file: Path to the Fortran source file (for extracting module USE statements)

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
        intent = arg.intent.value if arg.intent else "INOUT"

        if arg.is_derived_type:
            type_name = arg.derived_type_name
            # Receive as C_PTR, convert via C_F_POINTER
            c_params.append((f"        TYPE(C_PTR), VALUE :: {arg.name}_ptr", f"{arg.name}_ptr"))
            local_decls.append(f"        TYPE({type_name}), POINTER :: {arg.name}")
            pre_call.append(f"        CALL C_F_POINTER({arg.name}_ptr, {arg.name})")
            call_args.append(arg.name)
        elif "CHARACTER" in fortran_type:
            # CHARACTER is not C-interoperable — handle locally in the bridge.
            # No C-side parameter; only ErrStat is returned to C++.
            local_decls.append(f"        CHARACTER(8192) :: {arg.name}  ! Local buffer (not compared across language boundary)")
            call_args.append(arg.name)
        elif "INTEGER" in fortran_type:
            c_params.append((f"        INTEGER(C_INT), INTENT({intent}) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        elif "REAL" in fortran_type:
            c_params.append((f"        REAL(C_DOUBLE), INTENT({intent}) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        elif "LOGICAL" in fortran_type:
            c_params.append((f"        LOGICAL(C_BOOL), INTENT({intent}) :: {arg.name}", arg.name))
            call_args.append(arg.name)
        else:
            c_params.append((f"        ! TODO: unsupported type {fortran_type}", arg.name))
            call_args.append(arg.name)

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

    # Pre-call conversions
    for conv in pre_call:
        lines.append(conv)

    # Call the original function
    lines.append(f"        CALL {func_name}({', '.join(call_args)})")

    # Post-call conversions
    for conv in post_call:
        lines.append(conv)

    lines.append(f"    END SUBROUTINE {bridge_name}")

    return "\n".join(lines)


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

    # Fortran bridge has a different signature: derived types as void*,
    # CHARACTER args handled locally in the bridge (not exposed to C++)
    bridge_params = []
    for arg in sig.args:
        if arg.is_derived_type:
            bridge_params.append(f"void* {arg.name}_ptr")
        elif "CHARACTER" in arg.fortran_type.upper():
            continue  # CHARACTER handled locally in bridge, not passed from C++
        else:
            # Find the matching C param from build_c_params
            for ctype, cname in c_params:
                if cname == arg.name:
                    bridge_params.append(f"{ctype} {cname}")
                    break
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
