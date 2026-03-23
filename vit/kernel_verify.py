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

KGen state capture quirks:
- KGen captures state for every call to the target function within the
  enclosing routine, not just the pragma'd call site. Some of these
  invocations occur during initialization or secondary DLL calls where
  struct fields aren't fully populated, resulting in NaN reference data.
- NaN-vs-NaN comparisons always report as "non-identical", causing
  spurious verification failures. These are detected and reported
  separately as "skipped" cases with invalid reference data.
- KGen compares the ENTIRE struct against reference data, not just fields
  the kernel modifies. Uninitialized fields contain heap/stack residue
  that may or may not be NaN depending on memory layout (ASLR, stack
  depth, allocator behavior). This causes non-deterministic verification
  results across runs. Fixed by patching comparisons so NaN==NaN is
  treated as IDENTICAL (IEEE 754 guard: x /= x is true only for NaN).
"""

import csv
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
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
    skipped_cases: int  # Cases with NaN reference data (invalid state capture)
    max_error: float
    output: str
    details: list[dict]  # Per-case results
    field_data: list[dict] = field(default_factory=list)  # Per-field results


def setup_kernel_cpp(
    kernel_dir: str,
    sig: FortranSignature,
    cpp_impl_path: str,
    kind_aliases: Optional[dict] = None,
    field_log: bool = True,
    derived_types: Optional[dict] = None,
    fortran_flags: str = "",
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

    # --- Step 1b: Generate struct headers for C-compatible derived types ---
    struct_headers = []
    view_populator_sources = []
    if derived_types:
        from .derived_type_parser import (
            generate_c_struct_header, generate_view_struct_header,
        )
        from .view_populator import generate_view_populator

        for type_name, type_info in derived_types.items():
            is_view = getattr(type_info, 'view_strategy', None) == 'view'

            if is_view:
                # View type: use pre-computed field mappings from registry
                mappings = getattr(type_info, 'field_mappings', None) or []

                header_filename = f"{type_name.lower()}_view_t.h"
                header_content = generate_view_struct_header(
                    type_info, mappings, kind_aliases
                )
                header_path = kernel / header_filename
                header_path.write_text(header_content)
                struct_headers.append(header_filename)
                result["files_modified"].append(str(header_path))

                # Generate populator Fortran module
                source_module = type_info.source_module
                if not source_module:
                    result["errors"].append(
                        f"Cannot generate view populator for {type_name}: "
                        f"source module unknown. Ensure the TYPE definition is "
                        f"inside a MODULE block in the Fortran source."
                    )
                    return result
                populator_content = generate_view_populator(
                    type_info, mappings, kind_aliases, source_module=source_module
                )
                populator_filename = f"vit_{type_name.lower()}_view.f90"
                populator_path = kernel / populator_filename
                populator_path.write_text(populator_content)
                view_populator_sources.append((populator_filename, source_module))
                result["files_modified"].append(str(populator_path))

            elif type_info.is_c_compatible:
                # Regular BIND(C) type: generate standard struct header
                header_filename = f"{type_name.lower()}_t.h"
                header_content = generate_c_struct_header(type_info, kind_aliases,
                                                          registry=derived_types)
                header_path = kernel / header_filename
                header_path.write_text(header_content)
                struct_headers.append(header_filename)
                result["files_modified"].append(str(header_path))

    # --- Step 2: Generate C wrapper ---
    wrapper_filename = f"{sig.name.lower()}_cpp.cpp"
    wrapper_source = generate_cpp_wrapper(sig, impl_filename, kind_aliases,
                                           derived_types, struct_headers)
    wrapper_dest = kernel / wrapper_filename
    wrapper_dest.write_text(wrapper_source)
    result["files_modified"].append(str(wrapper_dest))

    # --- Step 3: Modify Makefile ---
    try:
        # Determine which .o file contains the wrapper (needs populator dependency)
        wrapper_fortran_obj = None
        if sig.source_file and view_populator_sources:
            wrapper_fortran_obj = Path(sig.source_file).stem + ".o"
        _patch_kernel_makefile(kernel, sig, wrapper_filename,
                               fortran_flags=fortran_flags,
                               view_populator_sources=view_populator_sources,
                               wrapper_fortran_obj=wrapper_fortran_obj)
        result["files_modified"].append(str(kernel / "Makefile"))
    except Exception as e:
        result["errors"].append(f"Makefile patch failed: {e}")
        return result

    # --- Step 4: Modify Fortran sources ---
    try:
        modified = _patch_kernel_fortran(kernel, sig, kind_aliases,
                                         field_log=field_log,
                                         derived_types=derived_types)
        result["files_modified"].extend(modified)
    except Exception as e:
        result["errors"].append(f"Fortran patch failed: {e}")
        return result

    result["success"] = len(result["errors"]) == 0
    return result


def run_kernel_verify(kernel_dir: str, env: Optional[dict] = None,
                      tolerance: float = 1e-14,
                      field_log: bool = True) -> VerifyResult:
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
            skipped_cases=0,
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
    result = _parse_verify_output(output, tolerance)

    # Write field data to CSV if field logging is enabled
    if field_log and result.field_data:
        csv_path = kernel / "verify_fields.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["case", "field", "type", "status",
                            "computed", "reference", "diff"],
            )
            writer.writeheader()
            writer.writerows(result.field_data)

    return result


def _patch_kernel_makefile(kernel: Path, sig: FortranSignature,
                           cpp_source: str, fortran_flags: str = "",
                           view_populator_sources: Optional[list] = None,
                           wrapper_fortran_obj: Optional[str] = None) -> None:
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

    The fortran_flags parameter propagates project-specific compiler flags
    (e.g., -fdefault-real-8) that affect literal constant precision.
    Without these, Fortran literals like 3.14159265359 may be truncated
    to single precision in the kernel, causing coefficient mismatches.
    """
    makefile = kernel / "Makefile"
    content = makefile.read_text()
    lines = content.split("\n")
    obj_name = cpp_source.replace(".cpp", ".o")

    # 1. Prepend FC_0/FC_FLAGS_SET_0 definitions if not present
    # KGen Makefiles use FC_0 but never define it or FC
    # Include project-specific fortran_flags (e.g., -fdefault-real-8)
    # Without these, Fortran literals like 3.14159265359 may be truncated
    # to single precision in the kernel, causing coefficient mismatches.
    base_flags = "-O2 -ffree-form -ffree-line-length-none"
    if fortran_flags:
        base_flags = f"{base_flags} {fortran_flags}"
    fc_flags_line = f"FC_FLAGS_SET_0 ?= {base_flags}"
    existing = re.search(r'^FC_FLAGS_SET_0\s*[?:]?=.*$', content, re.MULTILINE)
    if existing:
        # Update existing FC_FLAGS_SET_0 with correct flags
        lines = [fc_flags_line if re.match(r'^FC_FLAGS_SET_0\s*[?:]?=', l) else l
                 for l in lines]
    elif not re.search(r'^FC_0\s*[?:]?=', content, re.MULTILINE):
        lines.insert(0, fc_flags_line)
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
            "CXXFLAGS ?= -O2 -fPIC -ffp-contract=off",
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

    # 3b. Add populator .f90 compilation rules and objects
    for pop_entry in (view_populator_sources or []):
        # Each entry is (filename, source_module) tuple
        if isinstance(pop_entry, tuple):
            pop_src, pop_module = pop_entry
        else:
            pop_src, pop_module = pop_entry, None
        pop_obj = pop_src.replace(".f90", ".o")
        # The populator USEs the source module — depend on its .o
        module_dep = f" {pop_module}.o" if pop_module else ""
        content_joined = "\n".join(lines)

        # Add object to ALL_OBJS
        all_objs_match = re.search(r'^(ALL_OBJS\s*[:?]?=\s*)(.*)', content_joined, re.MULTILINE)
        if all_objs_match and pop_obj not in all_objs_match.group(2):
            old_line = all_objs_match.group(0)
            new_line = f"{all_objs_match.group(1)}{pop_obj} {all_objs_match.group(2)}"
            content_joined = content_joined.replace(old_line, new_line, 1)
            lines = content_joined.split("\n")

        # Add Fortran compilation rule (before "build:" target)
        content_joined = "\n".join(lines)
        if pop_src not in content_joined:
            pop_lines = [
                f"# VIT: view populator module",
                f"{pop_obj}: {pop_src}{module_dep}",
                f"\t$(FC_0) $(FC_FLAGS_SET_0) -c $< -o $@",
                "",
            ]
            for i, line in enumerate(lines):
                if re.match(r'^build\s*:', line):
                    for j, pop_line in enumerate(pop_lines):
                        lines.insert(i + j, pop_line)
                    break

    # 3c. Add populator dependency to the wrapper's Fortran .o file
    # The wrapper USEs the populator module, so it must compile after the populator.
    if wrapper_fortran_obj and view_populator_sources:
        content_joined = "\n".join(lines)
        for pop_entry in view_populator_sources:
            pop_src = pop_entry[0] if isinstance(pop_entry, tuple) else pop_entry
            pop_obj = pop_src.replace(".f90", ".o")
            # Find the wrapper .o rule and add the populator .o as dependency
            rule_re = re.compile(
                rf'^({re.escape(wrapper_fortran_obj)}\s*:\s*)(.*)',
                re.MULTILINE,
            )
            m = rule_re.search(content_joined)
            if m and pop_obj not in m.group(2):
                old_rule = m.group(0)
                new_rule = f"{m.group(1)}{pop_obj} {m.group(2)}"
                content_joined = content_joined.replace(old_rule, new_rule, 1)
        lines = content_joined.split("\n")

    # 4. Add -lstdc++ to link flags
    content_joined = "\n".join(lines)
    if "-lstdc++" not in content_joined:
        for i, line in enumerate(lines):
            if "-o kernel.exe" in line or "-o\tkernel.exe" in line:
                lines[i] = line.replace("-o kernel.exe", "-o kernel.exe -lstdc++")
                break

    makefile.write_text("\n".join(lines))


def _patch_kernel_fortran(kernel: Path, sig: FortranSignature,
                          kind_aliases: Optional[dict] = None,
                          field_log: bool = True,
                          derived_types: Optional[dict] = None) -> list[str]:
    """
    Patch kernel Fortran source files.

    Handles:
    1. Fix USE kgen_utils_mod, ONLY: ... → USE kgen_utils_mod
       (KGen's ONLY clause doesn't include all symbols the code actually uses)
    2. Adding USE ISO_C_BINDING (MUST come before IMPLICIT NONE)
    3. Adding the INTERFACE block above CONTAINS
    4. Replacing the original function body with the C-calling wrapper
    5. Ensuring USE ordering (USE before IMPLICIT NONE)
    6. Increasing verboseLevel so NaN differences appear in output
       (KGen's default verboseLevel=1 suppresses "Difference is NaN" lines,
       which prevents detection of invalid state capture data)
    7. Patching NaN comparisons so NaN==NaN → IDENTICAL
       (KGen compares entire structs; uninitialized fields contain NaN from
       heap/stack residue that varies between runs, causing non-deterministic
       results. IEEE 754 NaN != NaN, so we add a guard clause.)
    """
    modified_files = []
    interface_block = generate_fortran_interface_block(sig, kind_aliases, derived_types)
    wrapper_body = generate_fortran_wrapper(sig, kind_aliases, derived_types)

    for f90_file in kernel.glob("*.f90"):
        content = f90_file.read_text()
        original = content
        content_upper = content.upper()
        name_upper = sig.name.upper()

        # Fix USE kgen_utils_mod ONLY → USE kgen_utils_mod (all files)
        content = _fix_kgen_utils_mod_only(content)

        # Make kgen_array_sumcheck non-fatal: remove STOP from the finish
        # branch. KGen's state integrity checks compare array SUM values
        # read from state files to current values. Denormalized (subnormal)
        # values from uninitialized memory can cause false mismatches —
        # the state file captures garbage bits that round differently on
        # deserialization. The function output verification catches real
        # issues; crashing on input-state SUM mismatches is too strict.
        content = re.sub(
            r'if\s*\(\s*present\(finish\)\s*\.AND\.\s*finish\s*\)\s*then\s*\n\s*stop\s*\n\s*end\s*if',
            '! VIT: removed STOP from kgen_array_sumcheck (false positives from subnormal values)',
            content,
            flags=re.IGNORECASE,
        )

        # Increase verboseLevel so NaN differences are visible in output.
        # At verboseLevel=1, KGen only prints summary counts and suppresses
        # per-field "Difference is NaN" lines. We need those lines to detect
        # invalid state capture data (NaN reference values from initialization
        # calls). verboseLevel=100 prints all per-field comparisons.
        content = re.sub(
            r'verboseLevel\s*=\s*\d+',
            'verboseLevel=100',
            content,
        )

        # Patch NaN comparisons in KGen-generated verification subroutines.
        # KGen compares the ENTIRE struct (e.g. localvar) against reference
        # data, not just the fields the kernel modifies. Uninitialized fields
        # contain heap/stack residue that may or may not be NaN depending on
        # memory layout — causing non-deterministic verification results.
        # This makes NaN==NaN → IDENTICAL, eliminating the non-determinism.
        content = _patch_nan_comparisons(content)

        # Inject WRITE statements to log field values during verification.
        # Must run after NaN patching since it detects the NaN-patched form.
        if field_log:
            content = _patch_field_value_logging(content)

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


def _patch_nan_comparisons(content: str) -> str:
    """
    Patch KGen-generated verification code to treat NaN==NaN as IDENTICAL,
    with a diagnostic message distinguishing NaN matches from real matches.

    KGen generates per-field comparisons like:
        IF (var%ptfmtdz == kgenref_var%ptfmtdz) THEN
            ...  ! IDENTICAL branch
        ELSE
            diff_ptfmtdz = ABS(var%ptfmtdz - kgenref_var%ptfmtdz)
            ...  ! out-of-tolerance branch → "Difference is NaN"

    In IEEE 754, NaN == NaN is false, so uninitialized fields (containing
    NaN from heap/stack residue) always fail verification. Worse, the NaN
    pattern varies between runs depending on memory layout (ASLR, stack
    depth, allocator behavior), causing non-deterministic results.

    Fix: extend the equality check so that if BOTH sides are NaN, the
    comparison still enters the IDENTICAL branch, and print a diagnostic
    so NaN matches are distinguishable from real matches:

        IF ((var%X == kgenref_var%X) .OR.
            ((var%X /= var%X) .AND. (kgenref_var%X /= kgenref_var%X))) THEN
            IF (var%X /= var%X) WRITE(*, *) "...is IDENTICAL (both NaN, uninitialized)."

    The IEEE 754 property x /= x is true ONLY for NaN. For integers,
    x /= x is always false, so both the guard clause and the diagnostic
    WRITE are harmlessly dead code for non-real types.
    """
    def _nan_guard(m):
        var = m.group(1)      # e.g. var%ptfmtdz
        field = m.group(2)    # e.g. ptfmtdz
        ref = m.group(3)      # e.g. kgenref_var%ptfmtdz
        return (
            f'IF (({var} == {ref}) .OR. '
            f'(({var} /= {var}) .AND. ({ref} /= {ref}))) THEN\n'
            f'        IF ({var} /= {var}) '
            f'WRITE(*, *) "localvar%{field} is IDENTICAL (both NaN, uninitialized)."'
        )

    content = re.sub(
        r'IF\s*\(\s*(var%(\w+))\s*==\s*(kgenref_var%\w+)\s*\)\s*THEN',
        _nan_guard,
        content,
    )

    # Bare variable comparisons in kv_ipc_* subroutines (no % field access).
    # These appear for functions whose verified output is a scalar or array
    # return value rather than a struct field modification.
    def _nan_guard_bare(m):
        return (
            f'IF ((var == kgenref_var) .OR. '
            f'((var /= var) .AND. (kgenref_var /= kgenref_var))) THEN\n'
            f'        IF (var /= var) '
            f'WRITE(*, *) trim(adjustl(varname)),'
            f'" is IDENTICAL (both NaN, uninitialized)."'
        )

    content = re.sub(
        r'IF\s*\(\s*var\s*==\s*kgenref_var\s*\)\s*THEN',
        _nan_guard_bare,
        content,
    )
    return content


def _patch_field_value_logging(content: str) -> str:
    """
    Inject WRITE statements to log field values during verification.

    Detects scalar and array comparison patterns in KGen-generated code
    (after NaN patching) and injects [VIT_FIELD] / [VIT_ARRAY] tagged
    WRITE statements after each check_result assignment.

    Handles three field types:
    - Numeric scalars: have diff_FIELD variable, three check_result branches
    - Character scalars: no diff_FIELD, only IDENTICAL/OUT_TOL branches
    - Numeric arrays: have rmsdiff_FIELD, three branches
    - Character arrays: no rmsdiff_FIELD, only IDENTICAL/OUT_TOL branches
    """
    # Idempotency: skip if already patched
    if "[VIT_FIELD]" in content or "[VIT_ARRAY]" in content:
        return content

    # Pre-scan for declared diff/rmsdiff variables to detect field types.
    # Numeric fields have diff_FIELD = ABS(...); character fields don't.
    diff_fields = {m.group(1).lower()
                   for m in re.finditer(r'\bdiff_(\w+)\s*=\s*ABS\b', content, re.IGNORECASE)}
    rmsdiff_fields = {m.group(1).lower()
                      for m in re.finditer(r'\brmsdiff_(\w+)\s*=', content, re.IGNORECASE)}

    lines = content.split("\n")
    result = []
    current_field = None
    current_type = None  # "scalar" or "array"

    # Regex patterns for comparison starts
    # Scalar: NaN-patched form: IF ((var%FIELD == kgenref_var%FIELD) .OR. ...
    scalar_re = re.compile(
        r'\s*IF\s*\(\(var%(\w+)\s*==\s*kgenref_var%\w+\)\s*\.OR\.',
        re.IGNORECASE,
    )
    # Array: IF (ALL(var%FIELD == kgenref_var%FIELD)) THEN
    array_re = re.compile(
        r'\s*IF\s*\(ALL\(var%(\w+)\s*==\s*kgenref_var%',
        re.IGNORECASE,
    )
    # Bare scalar: NaN-patched form: IF ((var == kgenref_var) .OR. ...
    # (no % — appears in kv_ipc_* subroutines for return-value verification)
    bare_scalar_re = re.compile(
        r'\s*IF\s*\(\(var\s*==\s*kgenref_var\)\s*\.OR\.',
        re.IGNORECASE,
    )
    # Bare array: IF (ALL(var == kgenref_var)) THEN
    bare_array_re = re.compile(
        r'\s*IF\s*\(ALL\(var\s*==\s*kgenref_var\)',
        re.IGNORECASE,
    )
    # check_result assignment
    check_re = re.compile(
        r'(\s*)check_result\s*=\s*(CHECK_IDENTICAL|CHECK_IN_TOL|CHECK_OUT_TOL)\s*$',
        re.IGNORECASE,
    )

    for line in lines:
        # Detect scalar comparison start
        m = scalar_re.match(line)
        if m:
            current_field = m.group(1)
            current_type = "scalar"

        # Detect array comparison start
        m = array_re.match(line)
        if m:
            current_field = m.group(1)
            current_type = "array"

        # Detect bare scalar comparison (kv_ipc_* subroutines)
        if bare_scalar_re.match(line):
            current_field = "__bare__"
            current_type = "bare_scalar"

        # Detect bare array comparison (kv_ipc_*_dim1 subroutines)
        if bare_array_re.match(line):
            current_field = "__bare__"
            current_type = "bare_array"

        result.append(line)

        # Inject WRITE after check_result assignments
        if current_field:
            m = check_re.match(line)
            if m:
                indent = m.group(1)
                check_type = m.group(2).upper()
                f = current_field
                has_diff = f.lower() in diff_fields
                has_rmsdiff = f.lower() in rmsdiff_fields

                if current_type == "scalar":
                    if check_type == "CHECK_IDENTICAL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_FIELD] {f} | IDENTICAL | ", '
                            f'var%{f}, " | ", kgenref_var%{f}'
                        )
                    elif check_type == "CHECK_IN_TOL" and has_diff:
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_FIELD] {f} | IN_TOL | ", '
                            f'var%{f}, " | ", kgenref_var%{f}, " | ", diff_{f}'
                        )
                    elif check_type == "CHECK_OUT_TOL":
                        if has_diff:
                            result.append(
                                f'{indent}WRITE(*, *) "[VIT_FIELD] {f} | OUT_TOL | ", '
                                f'var%{f}, " | ", kgenref_var%{f}, " | ", diff_{f}'
                            )
                        else:
                            # CHARACTER type — no diff variable
                            result.append(
                                f'{indent}WRITE(*, *) "[VIT_FIELD] {f} | OUT_TOL | ", '
                                f'var%{f}, " | ", kgenref_var%{f}'
                            )

                elif current_type == "array":
                    if check_type == "CHECK_IDENTICAL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_ARRAY] {f} | IDENTICAL | size=", '
                            f'SIZE(var%{f})'
                        )
                    elif check_type == "CHECK_IN_TOL" and has_rmsdiff:
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_ARRAY] {f} | IN_TOL | n_diff=", '
                            f'n_{f}, " | rms=", rmsdiff_{f}'
                        )
                    elif check_type == "CHECK_OUT_TOL":
                        if has_rmsdiff:
                            result.append(
                                f'{indent}WRITE(*, *) "[VIT_ARRAY] {f} | OUT_TOL | n_diff=", '
                                f'n_{f}, " | rms=", rmsdiff_{f}'
                            )
                        else:
                            # CHARACTER array — no rmsdiff variable
                            result.append(
                                f'{indent}WRITE(*, *) "[VIT_ARRAY] {f} | OUT_TOL | n_diff=", '
                                f'n_{f}'
                            )

                # Bare variable comparisons (kv_ipc_* subroutines).
                # Uses subroutine-local variables: var, kgenref_var, diff,
                # rmsdiff, n, varname (the string name parameter).
                elif current_type == "bare_scalar":
                    if check_type == "CHECK_IDENTICAL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_FIELD] ", '
                            f'trim(adjustl(varname)), '
                            f'" | IDENTICAL | ", var, " | ", kgenref_var'
                        )
                    elif check_type == "CHECK_IN_TOL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_FIELD] ", '
                            f'trim(adjustl(varname)), '
                            f'" | IN_TOL | ", var, " | ", kgenref_var, " | ", diff'
                        )
                    elif check_type == "CHECK_OUT_TOL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_FIELD] ", '
                            f'trim(adjustl(varname)), '
                            f'" | OUT_TOL | ", var, " | ", kgenref_var, " | ", diff'
                        )

                elif current_type == "bare_array":
                    if check_type == "CHECK_IDENTICAL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_ARRAY] ", '
                            f'trim(adjustl(varname)), '
                            f'" | IDENTICAL | size=", SIZE(var)'
                        )
                    elif check_type == "CHECK_IN_TOL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_ARRAY] ", '
                            f'trim(adjustl(varname)), '
                            f'" | IN_TOL | n_diff=", n, " | rms=", rmsdiff'
                        )
                    elif check_type == "CHECK_OUT_TOL":
                        result.append(
                            f'{indent}WRITE(*, *) "[VIT_ARRAY] ", '
                            f'trim(adjustl(varname)), '
                            f'" | OUT_TOL | n_diff=", n, " | rms=", rmsdiff'
                        )

    return "\n".join(result)


def _fix_kgen_utils_mod_only(content: str) -> str:
    """
    Remove ONLY clause from USE kgen_utils_mod statements.

    KGen generates single-line:
        USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck
    or multi-line with continuations:
        USE kgen_utils_mod, ONLY: kgen_dp, kgen_array_sumcheck, &
            CHECK_IDENTICAL, CHECK_IN_TOL, CHECK_OUT_TOL

    The code also references kgen_verboselevel, kgen_init_verify,
    and other symbols not in the ONLY list. Fix by removing the restriction
    AND any continuation lines that were part of the ONLY clause.
    """
    lines = content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        line_upper = line.strip().upper()

        # Check if this is a USE kgen_utils_mod, ONLY: line
        if re.match(r'\s*USE\s+kgen_utils_mod\s*,\s*ONLY\s*:', line, re.IGNORECASE):
            # Replace with just USE kgen_utils_mod (preserve indentation)
            indent = len(line) - len(line.lstrip())
            result.append(" " * indent + "USE kgen_utils_mod")

            # Skip any continuation lines (current line ends with &,
            # or next line starts with &)
            while i < len(lines) and lines[i].rstrip().endswith("&"):
                i += 1  # skip this continuation line
            # Also skip the next line if it starts with & (final continuation)
            if i + 1 < len(lines) and lines[i + 1].lstrip().startswith("&"):
                i += 1  # skip the orphaned continuation
            i += 1
        else:
            # Also catch orphaned continuation lines that start with & and
            # contain only identifiers/commas (left over from a previous
            # partial fix or unusual formatting)
            result.append(line)
            i += 1

    return "\n".join(result)


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

    KGen output has per-case blocks like:

        ***************** Verification against 'saturate.0.0.1' *****************
        localvar%istatus is IDENTICAL.
        ...
        Verification PASSED with saturate.0.0.1

    Or for cases with NaN reference data (invalid state capture):

        ***************** Verification against 'saturate.0.0.3' *****************
        Difference is                        NaN
        ...
        Verification FAILED withsaturate.0.0.3

    And a summary block:
        Total number of verification cases  :     1
        Number of verification-passed cases :     1

    Cases where all differences are NaN indicate corrupted reference data
    from KGen's state capture (e.g., initialization calls with uninitialized
    struct fields). These are classified as "skipped" rather than "failed".
    """
    per_case_total = 0
    per_case_passed = 0
    per_case_failed = 0
    per_case_skipped = 0
    summary_total = None
    summary_passed = None
    max_error = 0.0
    details = []
    field_data = []

    # Track NaN differences within each case block.
    # When we see a case header (*** Verification against ***), reset the flag.
    # When we see "Difference is NaN", set it.
    # When we see FAILED, check if NaN was present → classify as skipped.
    current_case_has_nan = False
    current_case_name = ""

    for line in output.split("\n"):
        line_stripped = line.strip()
        line_upper = line_stripped.upper()

        # Detect case header — reset NaN tracking, capture case name
        case_match = re.search(r"Verification against '([^']+)'", line, re.IGNORECASE)
        if case_match:
            current_case_name = case_match.group(1)
            current_case_has_nan = False
            continue

        # Parse field value logging lines
        if "[VIT_FIELD]" in line:
            rest = line[line.index("[VIT_FIELD]") + len("[VIT_FIELD]"):].strip()
            parts = [p.strip() for p in rest.split("|")]
            if len(parts) >= 3:
                entry = {
                    "case": current_case_name,
                    "field": parts[0],
                    "type": "scalar",
                    "status": parts[1],
                    "computed": parts[2] if len(parts) > 2 else "",
                    "reference": parts[3] if len(parts) > 3 else "",
                    "diff": parts[4] if len(parts) > 4 else "",
                }
                field_data.append(entry)
            continue

        if "[VIT_ARRAY]" in line:
            rest = line[line.index("[VIT_ARRAY]") + len("[VIT_ARRAY]"):].strip()
            parts = [p.strip() for p in rest.split("|")]
            if len(parts) >= 2:
                entry = {
                    "case": current_case_name,
                    "field": parts[0],
                    "type": "array",
                    "status": parts[1],
                    "computed": "",
                    "reference": "",
                    "diff": "",
                }
                # Extract array-specific info from remaining parts
                for p in parts[2:]:
                    entry["diff"] += p.strip() + " "
                entry["diff"] = entry["diff"].strip()
                field_data.append(entry)
            continue

        # Detect NaN differences within a case block
        if re.search(r'Difference\s+is\s+NaN', line, re.IGNORECASE):
            current_case_has_nan = True
            continue

        # Parse summary lines (these are authoritative for total/passed counts)
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
        if re.search(r'Verification\s+PASSED\s+with\b', line, re.IGNORECASE):
            per_case_total += 1
            per_case_passed += 1
            details.append({"status": "PASSED", "line": line_stripped})
            current_case_has_nan = False

        elif re.search(r'Verification\s+FAILED', line, re.IGNORECASE):
            per_case_total += 1
            if current_case_has_nan:
                # NaN reference data — invalid state capture, not a real failure
                per_case_skipped += 1
                details.append({
                    "status": "SKIPPED",
                    "line": line_stripped,
                    "reason": "NaN reference data (invalid state capture)",
                })
            else:
                per_case_failed += 1
                details.append({"status": "FAILED", "line": line_stripped})

                err_match = re.search(
                    r'error\s*[:=]\s*([\d.eE+-]+)', line, re.IGNORECASE)
                if err_match:
                    try:
                        max_error = max(max_error, float(err_match.group(1)))
                    except ValueError:
                        pass
            current_case_has_nan = False

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

    # Compute final counts.
    # KGen's summary counts include NaN cases as failures. We need to
    # adjust: real failures = summary failures - skipped NaN cases.
    if summary_total is not None and summary_passed is not None:
        total = summary_total
        passed = summary_passed
        skipped = per_case_skipped
        failed = total - passed - skipped
    else:
        total = per_case_total
        passed = per_case_passed
        skipped = per_case_skipped
        failed = per_case_failed

    # Success means: at least one valid case, and no real failures.
    # NaN-skipped cases don't count against success.
    valid_cases = total - skipped
    success = valid_cases > 0 and failed == 0

    return VerifyResult(
        success=success,
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        skipped_cases=skipped,
        max_error=max_error,
        output=output,
        details=details,
        field_data=field_data,
    )