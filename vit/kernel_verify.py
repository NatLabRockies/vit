"""
KGen kernel verification orchestration.

This module handles the most friction-heavy part of the pipeline:
wiring a C++ translation into KGen's generated kernel build system
and running the verification.

Encodes all lessons learned from the ROSCO saturate experience:

Makefile fixes:
- FC_0/FC_FLAGS_SET_0 must be defined (KGen uses these but never defines them)
- C++ object must be in ALL_OBJS (not just have a compilation rule)
- -lstdc++ must be in the link command
- kernel.exe must be chmod +x after linking (some systems need this)

Fortran fixes:
- USE ISO_C_BINDING must come before IMPLICIT NONE
- USE kgen_utils_mod must NOT use ONLY clause (KGen doesn't import all needed symbols)
- INTERFACE block goes above CONTAINS
- Function body replaced with C-calling wrapper
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

    Steps:
    1. Copy user's C++ implementation into kernel/
    2. Generate the C wrapper (extern "C") file
    3. Modify the kernel Makefile to compile C++ and link stdc++
    4. Modify kernel Fortran sources:
       a. Fix USE kgen_utils_mod ONLY → USE kgen_utils_mod (remove ONLY)
       b. Add USE ISO_C_BINDING (before IMPLICIT NONE)
       c. Add the INTERFACE block (above CONTAINS)
       d. Replace the original function body with the wrapper

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
    run_env = {**os.environ, **(env or {})}

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
        env=run_env,
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

    # chmod +x kernel.exe — some systems/filesystems don't set execute bit
    kernel_exe = kernel / "kernel.exe"
    if kernel_exe.exists():
        kernel_exe.chmod(kernel_exe.stat().st_mode | 0o111)

    # Run
    run_result = subprocess.run(
        "./kernel.exe",
        shell=True,
        cwd=str(kernel),
        capture_output=True,
        text=True,
        env=run_env,
    )

    output = run_result.stdout + run_result.stderr

    # Parse verification output
    return _parse_verify_output(output, tolerance)


def _patch_kernel_makefile(kernel: Path, sig: FortranSignature,
                           cpp_source: str) -> None:
    """
    Patch the kernel Makefile to:
    1. Define FC_0 and FC_FLAGS_SET_0 (KGen bug: uses but never defines these)
    2. Add C++ compilation rule
    3. Add C++ object to ALL_OBJS
    4. Add -lstdc++ to link flags

    The KGen-generated Makefile looks like:
        # Makefile for KGEN-generated kernel
        ALL_OBJS := A.o B.o ...
        build: ${ALL_OBJS}
            ${FC_0} ${FC_FLAGS_SET_0} -o kernel.exe $^
        ...
        A.o: A.f90 ...
            ${FC_0} ${FC_FLAGS_SET_0} -c -o $@ $<

    Note: FC_0 and FC_FLAGS_SET_0 are USED but NEVER DEFINED.
    There is no FC variable either. We must prepend definitions.
    """
    makefile = kernel / "Makefile"
    content = makefile.read_text()
    lines = content.split("\n")
    obj_name = cpp_source.replace(".cpp", ".o")

    # 1. Prepend FC_0/FC_FLAGS_SET_0 definitions if not present
    # KGen Makefiles use FC_0 but never define it or FC
    if not re.search(r'^FC_0\s*[?:]?=', content, re.MULTILINE):
        lines.insert(0, "FC_FLAGS_SET_0 ?= -O2 -ffree-form -ffree-line-length-none")
        lines.insert(0, "FC_0 ?= gfortran")

    # 2. Add C++ object to ALL_OBJS FIRST (before checking obj_name in content)
    # This must happen before adding the CXX rule, because the CXX rule
    # also contains obj_name and would fool the "obj_name not in content" check
    content_joined = "\n".join(lines)
    all_objs_match = re.search(r'^(ALL_OBJS\s*[:?]?=\s*)(.*)', content_joined, re.MULTILINE)
    if all_objs_match and obj_name not in all_objs_match.group(2):
        old_line = all_objs_match.group(0)
        new_line = f"{all_objs_match.group(1)}{obj_name} {all_objs_match.group(2)}"
        content_joined = content_joined.replace(old_line, new_line, 1)
        lines = content_joined.split("\n")

    # 3. Add CXX variable and C++ compilation rule
    content_joined = "\n".join(lines)
    if "CXX" not in content_joined:
        cxx_lines = [
            "# VIT: C++ compilation support",
            "CXX ?= g++",
            "CXXFLAGS ?= -O2 -fPIC",
            "",
            f"{obj_name}: {cpp_source}",
            f"\t$(CXX) $(CXXFLAGS) -c $< -o $@",
            "",
        ]
        # Insert before "build:" target
        for i, line in enumerate(lines):
            if re.match(r'^build\s*:', line):
                for j, cxx_line in enumerate(cxx_lines):
                    lines.insert(i + j, cxx_line)
                break

    # 4. Add -lstdc++ to link flags
    content_joined = "\n".join(lines)
    if "-lstdc++" not in content_joined:
        for i, line in enumerate(lines):
            if "-o kernel.exe" in line or "-o\tkernel.exe" in line:
                lines[i] = line.replace("-o kernel.exe", "-o kernel.exe -lstdc++")
                break

    makefile.write_text("\n".join(lines))


def _patch_kernel_fortran(kernel: Path, sig: FortranSignature,
                          kind_aliases: Optional[dict] = None) -> list[str]:
    """
    Patch kernel Fortran source files.

    Handles:
    1. Fix USE kgen_utils_mod, ONLY: ... → USE kgen_utils_mod
       (KGen's ONLY clause doesn't include all symbols the code actually uses)
    2. Adding USE ISO_C_BINDING (MUST come before IMPLICIT NONE)
    3. Adding the INTERFACE block above CONTAINS
    4. Replacing the original function body with the C-calling wrapper
    5. Ensuring USE ordering (USE before IMPLICIT NONE)
    """
    modified_files = []
    interface_block = generate_fortran_interface_block(sig, kind_aliases)
    wrapper_body = generate_fortran_wrapper(sig, kind_aliases)

    for f90_file in kernel.glob("*.f90"):
        content = f90_file.read_text()
        original = content
        content_upper = content.upper()
        name_upper = sig.name.upper()

        # Fix USE kgen_utils_mod ONLY → USE kgen_utils_mod (all files)
        content = _fix_kgen_utils_mod_only(content)

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
        if "ISO_C_BINDING" not in content.upper():
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


def _fix_kgen_utils_mod_only(content: str) -> str:
    """
    Remove ONLY clause from USE kgen_utils_mod statements.

    KGen generates:
        USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck
    but the code also references kgen_verboselevel, kgen_init_verify,
    and other symbols not in the ONLY list. Fix by removing the restriction.
    """
    return re.sub(
        r'(USE\s+kgen_utils_mod)\s*,\s*ONLY\s*:[^\n]*',
        r'\1',
        content,
        flags=re.IGNORECASE
    )


def _fix_use_ordering(content: str) -> str:
    """
    Ensure USE statements come before IMPLICIT NONE.

    This is a Fortran syntax requirement. USE statements must appear
    before IMPLICIT NONE in the same scoping unit.
    """
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line_upper = lines[i].strip().upper()

        # If we find IMPLICIT NONE, check if subsequent USE statements
        # need to be moved before it
        if line_upper.startswith("IMPLICIT NONE") or line_upper == "IMPLICIT NONE":
            j = i + 1
            use_lines_to_move = []
            while j < len(lines):
                next_upper = lines[j].strip().upper()
                if next_upper.startswith("USE "):
                    use_lines_to_move.append(lines[j])
                    lines.pop(j)
                elif not next_upper or next_upper.startswith("!"):
                    j += 1  # Skip blank lines and comments
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
            if not stripped.startswith("END"):
                start_idx = i
                break

    if start_idx is None:
        return content

    # Find function end
    end_idx = None
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip().upper()
        if re.match(rf'\s*END\s+{end_keyword}\b', stripped):
            end_idx = i
            break

    if end_idx is None:
        return content

    # Replace the function body (start to end inclusive) with wrapper
    new_lines = lines[:start_idx] + wrapper_body.split("\n") + lines[end_idx + 1:]
    return "\n".join(new_lines)


def _parse_verify_output(output: str, tolerance: float) -> VerifyResult:
    """
    Parse KGen kernel verification output.

    KGen output has two sections we need to parse:

    Per-case results like:
        Verification PASSED with saturate.0.0.1

    Summary like:
        Total number of verification cases  :     1
        Number of verification-passed cases :     1

    We prefer the summary values when present; per-case counting is fallback.
    """
    per_case_total = 0
    per_case_passed = 0
    per_case_failed = 0
    summary_total = None
    summary_passed = None
    max_error = 0.0
    details = []

    for line in output.split("\n"):
        line_stripped = line.strip()
        line_upper = line_stripped.upper()

        # Parse summary lines FIRST (these are authoritative)
        total_match = re.search(
            r'Total number of verification cases\s*:\s*(\d+)', line)
        if total_match:
            summary_total = int(total_match.group(1))
            continue

        passed_match = re.search(
            r'Number of verification-passed cases\s*:\s*(\d+)', line)
        if passed_match:
            summary_passed = int(passed_match.group(1))
            continue

        # Skip summary/count lines — don't count them as per-case results
        if re.search(r'Number of\s+(non-)?identical', line, re.IGNORECASE):
            continue

        # Per-case results: "Verification PASSED with <name>"
        # or "kernel: <name>: PASSED verification"
        if re.search(r'Verification\s+PASSED\s+with\b', line, re.IGNORECASE):
            per_case_total += 1
            per_case_passed += 1
            details.append({"status": "PASSED", "line": line_stripped})

        elif re.search(r'Verification\s+FAILED', line, re.IGNORECASE):
            per_case_total += 1
            per_case_failed += 1
            details.append({"status": "FAILED", "line": line_stripped})

            err_match = re.search(
                r'error\s*[:=]\s*([\d.eE+-]+)', line, re.IGNORECASE)
            if err_match:
                try:
                    max_error = max(max_error, float(err_match.group(1)))
                except ValueError:
                    pass

        elif re.search(r'PASSED\s+verification\b', line, re.IGNORECASE):
            # "kernel: saturate: PASSED verification" — this is a summary echo
            # Don't double-count if we already have per-case data
            if per_case_total == 0:
                per_case_total += 1
                per_case_passed += 1
                details.append({"status": "PASSED", "line": line_stripped})

        elif re.search(r'FAILED\s+verification\b', line, re.IGNORECASE):
            if per_case_total == 0:
                per_case_total += 1
                per_case_failed += 1
                details.append({"status": "FAILED", "line": line_stripped})

    # Use summary values if available, otherwise fall back to per-case counts
    total = summary_total if summary_total is not None else per_case_total
    passed = summary_passed if summary_passed is not None else per_case_passed
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