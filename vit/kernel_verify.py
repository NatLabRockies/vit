"""
KGen kernel verification orchestration.

This module handles the most friction-heavy part of the pipeline:
wiring a C++ translation into KGen's generated kernel build system
and running the verification.

Encodes all lessons learned from the ROSCO saturate experience:
- Makefile FC_0/FC_FLAGS_SET_0 variables
- USE ISO_C_BINDING must come before IMPLICIT NONE
- USE kgen_utils_mod must be present in extracted files
- C++ object compilation and -lstdc++ linking
- Verification output parsing
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .fortran_parser import FortranSignature
from .interface_gen import (
    generate_cpp_wrapper,
    generate_fortran_interface_block,
    generate_fortran_wrapper,
)


@dataclass
class VerifyResult:
    """Result of kernel verification."""
    success: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    max_error: float
    output: str
    details: list[dict]  # Per-case results


def setup_kernel_cpp(
    kernel_dir: str,
    sig: FortranSignature,
    cpp_impl_path: str,
    kind_aliases: Optional[dict] = None,
) -> dict:
    """
    Wire a C++ translation into a KGen kernel directory for verification.

    This is the automation of what we did manually for ROSCO saturate.

    Steps:
    1. Copy user's C++ implementation into kernel/
    2. Generate the C wrapper (extern "C") file
    3. Modify the kernel Makefile to compile C++ and link stdc++
    4. Modify kernel Fortran sources:
       a. Add USE ISO_C_BINDING (before IMPLICIT NONE)
       b. Add the INTERFACE block (above CONTAINS)
       c. Replace the original function body with the wrapper

    Args:
        kernel_dir: Path to KGen kernel/ directory
        sig: Parsed signature of the target function
        cpp_impl_path: Path to user's C++ implementation
        kind_aliases: Project kind aliases

    Returns:
        Dict with 'success', 'files_modified', 'errors'
    """
    result = {"success": False, "files_modified": [], "errors": []}
    kernel = Path(kernel_dir)

    # --- Step 1: Copy C++ implementation ---
    impl_filename = f"{sig.name.lower()}_impl.hpp"
    impl_dest = kernel / impl_filename
    shutil.copy2(cpp_impl_path, impl_dest)
    result["files_modified"].append(str(impl_dest))

    # --- Step 2: Generate C wrapper ---
    wrapper_filename = f"{sig.name.lower()}_cpp.cpp"
    wrapper_source = generate_cpp_wrapper(sig, impl_filename, kind_aliases)
    wrapper_dest = kernel / wrapper_filename
    wrapper_dest.write_text(wrapper_source)
    result["files_modified"].append(str(wrapper_dest))

    # --- Step 3: Modify Makefile ---
    try:
        _patch_kernel_makefile(kernel, sig, wrapper_filename)
        result["files_modified"].append(str(kernel / "Makefile"))
    except Exception as e:
        result["errors"].append(f"Makefile patch failed: {e}")
        return result

    # --- Step 4: Modify Fortran sources ---
    try:
        modified = _patch_kernel_fortran(kernel, sig, kind_aliases)
        result["files_modified"].extend(modified)
    except Exception as e:
        result["errors"].append(f"Fortran patch failed: {e}")
        return result

    result["success"] = len(result["errors"]) == 0
    return result


def run_kernel_verify(kernel_dir: str, env: Optional[dict] = None,
                      tolerance: float = 1e-14) -> VerifyResult:
    """
    Build and run the KGen kernel verification.

    Args:
        kernel_dir: Path to the (patched) kernel/ directory
        env: Additional environment variables
        tolerance: Maximum acceptable error

    Returns:
        VerifyResult with pass/fail details
    """
    kernel = Path(kernel_dir)

    # Clean first
    subprocess.run(
        "make clean",
        shell=True,
        cwd=str(kernel),
        capture_output=True,
    )

    # Build
    build_result = subprocess.run(
        "make build",
        shell=True,
        cwd=str(kernel),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )

    if build_result.returncode != 0:
        return VerifyResult(
            success=False,
            total_cases=0,
            passed_cases=0,
            failed_cases=0,
            max_error=float("inf"),
            output=f"BUILD FAILED:\n{build_result.stdout}\n{build_result.stderr}",
            details=[],
        )

    # Run
    run_result = subprocess.run(
        "make run",
        shell=True,
        cwd=str(kernel),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )

    output = run_result.stdout + run_result.stderr

    # Parse verification output
    return _parse_verify_output(output, tolerance)


def _patch_kernel_makefile(kernel: Path, sig: FortranSignature,
                           cpp_source: str) -> None:
    """
    Patch the kernel Makefile to:
    1. Define FC_0 and FC_FLAGS_SET_0 (KGen bug workaround)
    2. Add C++ compilation rule
    3. Add C++ object to ALL_OBJS
    4. Add -lstdc++ to link flags
    """
    makefile = kernel / "Makefile"
    content = makefile.read_text()
    obj_name = cpp_source.replace(".cpp", ".o")

    # 1. Add FC_0/FC_FLAGS_SET_0 if not present
    if "FC_0" not in content:
        # Insert after the first FC definition
        content = re.sub(
            r'(FC\s*[?:]?=\s*.+)',
            r'\1\nFC_0 ?= $(FC)\nFC_FLAGS_SET_0 ?= $(FC_FLAGS)',
            content,
            count=1,
        )

    # 2. Add CXX variable and C++ compilation rule
    if "CXX" not in content:
        # Add before the first target
        cxx_block = f"""
# VIT: C++ compilation support
CXX ?= g++
CXXFLAGS ?= -O2 -fPIC

{obj_name}: {cpp_source}
\t$(CXX) $(CXXFLAGS) -c $< -o $@
"""
        # Insert before "all:" or "build:" target
        content = re.sub(
            r'^((?:all|build)\s*:)',
            cxx_block + r'\n\1',
            content,
            count=1,
            flags=re.MULTILINE,
        )

    # 3. Add C++ object to ALL_OBJS (or the object list for the link step)
    if obj_name not in content:
        # Find ALL_OBJS or the link command and add our object
        if "ALL_OBJS" in content:
            content = re.sub(
                r'(ALL_OBJS\s*[+:]?=.*)',
                rf'\1 {obj_name}',
                content,
                count=1,
            )
        else:
            # Try to find the link command (typically uses $(FC) ... -o kernel.exe)
            content = re.sub(
                r'(\$\(FC\).*?-o\s+\S+)',
                rf'\1 {obj_name}',
                content,
                count=1,
            )

    # 4. Add -lstdc++ to link flags
    if "-lstdc++" not in content:
        # Add to the link command
        content = re.sub(
            r'(-o\s+kernel\.exe)',
            rf'\1 -lstdc++',
            content,
        )
        # Also try LDFLAGS
        if "LDFLAGS" in content:
            content = re.sub(
                r'(LDFLAGS\s*[+:]?=.*)',
                r'\1 -lstdc++',
                content,
                count=1,
            )

    makefile.write_text(content)


def _patch_kernel_fortran(kernel: Path, sig: FortranSignature,
                          kind_aliases: Optional[dict] = None) -> list[str]:
    """
    Patch kernel Fortran source files.

    Handles:
    1. Adding USE ISO_C_BINDING (MUST come before IMPLICIT NONE)
    2. Adding the INTERFACE block above CONTAINS
    3. Replacing the original function body with the C-calling wrapper
    4. Ensuring USE kgen_utils_mod is present where needed
    """
    modified_files = []
    interface_block = generate_fortran_interface_block(sig, kind_aliases)
    wrapper_body = generate_fortran_wrapper(sig, kind_aliases)

    for f90_file in kernel.glob("*.f90"):
        content = f90_file.read_text()
        original = content
        content_upper = content.upper()
        name_upper = sig.name.upper()

        # Check if this file contains our function
        func_pattern = rf'\b(FUNCTION|SUBROUTINE)\s+{name_upper}\s*\('
        if not re.search(func_pattern, content_upper):
            # Even files that don't contain our function may need USE fixes
            content = _fix_use_ordering(content)
            if content != original:
                f90_file.write_text(content)
                modified_files.append(str(f90_file))
            continue

        # This file contains our function — apply all patches

        # 1. Add USE ISO_C_BINDING to module level (before IMPLICIT NONE)
        if "ISO_C_BINDING" not in content_upper:
            content = _add_use_iso_c_binding(content)

        # 2. Add INTERFACE block above CONTAINS
        if sig.c_func_name.upper() not in content.upper():
            content = _add_interface_block(content, interface_block)

        # 3. Replace function body with wrapper
        content = _replace_function_body(content, sig, wrapper_body)

        # 4. Fix USE ordering everywhere
        content = _fix_use_ordering(content)

        if content != original:
            f90_file.write_text(content)
            modified_files.append(str(f90_file))

    return modified_files


def _fix_use_ordering(content: str) -> str:
    """
    Ensure USE ISO_C_BINDING comes before IMPLICIT NONE.

    This is a Fortran syntax requirement that caused kernel build failures.
    USE statements must come before IMPLICIT NONE.
    """
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line_upper = lines[i].strip().upper()

        # If we find IMPLICIT NONE, check if the next USE statements
        # need to be moved before it
        if line_upper.startswith("IMPLICIT NONE") or line_upper == "IMPLICIT NONE":
            # Look ahead for USE statements that should come before
            j = i + 1
            use_lines_to_move = []
            while j < len(lines):
                next_upper = lines[j].strip().upper()
                if next_upper.startswith("USE "):
                    use_lines_to_move.append(lines[j])
                    lines.pop(j)
                else:
                    break

            # Insert USE lines before IMPLICIT NONE
            for k, use_line in enumerate(use_lines_to_move):
                lines.insert(i + k, use_line)
            i += len(use_lines_to_move)

        i += 1

    return "\n".join(lines)


def _add_use_iso_c_binding(content: str) -> str:
    """Add USE ISO_C_BINDING at module level, before first IMPLICIT NONE."""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if stripped.startswith("IMPLICIT NONE") or stripped == "IMPLICIT NONE":
            indent = len(line) - len(line.lstrip())
            lines.insert(i, " " * indent + "USE ISO_C_BINDING")
            break
    return "\n".join(lines)


def _add_interface_block(content: str, interface_block: str) -> str:
    """Add INTERFACE block above the CONTAINS statement."""
    lines = content.split("\n")

    # Find CONTAINS
    for i, line in enumerate(lines):
        if line.strip().upper() == "CONTAINS":
            # Insert interface block before CONTAINS
            lines.insert(i, "")
            lines.insert(i, interface_block)
            lines.insert(i, "")
            return "\n".join(lines)

    # No CONTAINS found — add before END MODULE
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r'\s*END\s+MODULE', lines[i], re.IGNORECASE):
            lines.insert(i, "")
            lines.insert(i, "CONTAINS")
            lines.insert(i, "")
            lines.insert(i, interface_block)
            lines.insert(i, "")
            return "\n".join(lines)

    # Fallback: append
    lines.append("")
    lines.append(interface_block)
    return "\n".join(lines)


def _replace_function_body(content: str, sig: FortranSignature,
                           wrapper_body: str) -> str:
    """
    Replace the original function body with the C-calling wrapper.

    Finds the function definition and replaces it entirely.
    """
    lines = content.split("\n")
    name_upper = sig.name.upper()
    end_keyword = "FUNCTION" if sig.is_function else "SUBROUTINE"

    # Find function start
    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if re.search(rf'\b{end_keyword}\s+{name_upper}\s*\(', stripped):
            # Make sure this isn't the END line
            if not stripped.startswith("END"):
                start_idx = i
                break

    if start_idx is None:
        return content  # Function not found, return unchanged

    # Find function end
    end_idx = None
    depth = 0
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip().upper()
        if re.match(rf'\s*END\s+{end_keyword}\b', stripped):
            end_idx = i
            break

    if end_idx is None:
        return content  # Could not find END, return unchanged

    # Replace the function body (start to end inclusive) with wrapper
    new_lines = lines[:start_idx] + wrapper_body.split("\n") + lines[end_idx + 1:]
    return "\n".join(new_lines)


def _parse_verify_output(output: str, tolerance: float) -> VerifyResult:
    """
    Parse KGen kernel verification output.

    KGen output typically contains lines like:
        verification PASSED with tolerance 1E-14
    or per-variable verification results.
    """
    total = 0
    passed = 0
    failed = 0
    max_error = 0.0
    details = []

    for line in output.split("\n"):
        line_upper = line.strip().upper()

        # Count verification results
        if "PASSED" in line_upper and "VERIFICATION" in line_upper:
            total += 1
            passed += 1
            details.append({"status": "PASSED", "line": line.strip()})

        elif "FAILED" in line_upper and "VERIFICATION" in line_upper:
            total += 1
            failed += 1
            details.append({"status": "FAILED", "line": line.strip()})

            # Try to extract error value
            err_match = re.search(r'error\s*[:=]\s*([\d.eE+-]+)', line, re.IGNORECASE)
            if err_match:
                try:
                    err_val = float(err_match.group(1))
                    max_error = max(max_error, err_val)
                except ValueError:
                    pass

        # Also look for summary lines
        summary_match = re.search(
            r'(\d+)\s+(?:out of|/)\s+(\d+)\s+(?:passed|PASSED)',
            line
        )
        if summary_match:
            passed = int(summary_match.group(1))
            total = int(summary_match.group(2))
            failed = total - passed

    success = total > 0 and failed == 0

    return VerifyResult(
        success=success,
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        max_error=max_error,
        output=output,
        details=details,
    )
