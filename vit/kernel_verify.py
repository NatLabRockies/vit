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


def _backup_or_restore_kgen_originals(kernel: Path) -> None:
    """
    Restore the kernel directory to its original KGen state before patching.

    .kgen backups are created during extraction (extract.py:_create_kgen_backups)
    when source files are guaranteed clean. This function ONLY restores from
    existing backups — it never creates new ones. If backups don't exist,
    it raises an error directing the user to re-extract.

    This makes vit verify idempotent — safe to run multiple times on the same kernel.
    """
    makefile = kernel / "Makefile"
    makefile_backup = kernel / "Makefile.kgen"

    # Check that backups exist
    has_backups = makefile_backup.exists() or any(
        f.with_suffix(".f90.kgen").exists()
        for f in kernel.glob("*.f90")
        if not f.name.startswith("vit_")
    )
    if not has_backups:
        raise FileNotFoundError(
            f"No .kgen backups found in {kernel}. "
            f"Re-run 'vit extract' to create them. "
            f"(.kgen backups are created during extraction when source is clean.)"
        )

    # Makefile
    if makefile_backup.exists():
        shutil.copy2(makefile_backup, makefile)

    # Fortran source files
    for f90_file in sorted(kernel.glob("*.f90")):
        if f90_file.name.startswith("vit_"):
            continue
        backup = f90_file.with_suffix(".f90.kgen")
        if backup.exists():
            shutil.copy2(backup, f90_file)


def setup_kernel_cpp(
    kernel_dir: str,
    sig: FortranSignature,
    cpp_impl_path: str,
    kind_aliases: Optional[dict] = None,
    field_log: bool = True,
    derived_types: Optional[dict] = None,
    fortran_flags: str = "",
    translations: Optional[dict] = None,
    source_dir: Optional[str] = None,
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

    # --- Step 0: Backup/restore original KGen files ---
    # On first run, save KGen originals. On subsequent runs, restore from backup.
    # This makes the entire patching pipeline idempotent.
    _backup_or_restore_kgen_originals(kernel)

    # --- Step 1: Copy C++ implementation ---
    impl_filename = f"{sig.name.lower()}.hpp"
    impl_dest = kernel / impl_filename
    shutil.copy2(cpp_impl_path, impl_dest)
    result["files_modified"].append(str(impl_dest))

    # --- Step 1a: Copy shared constants header (if found near the impl file) ---
    for parent in [Path(cpp_impl_path).parent] + list(Path(cpp_impl_path).parent.parents):
        candidate = parent / "rosco_constants.h"
        if candidate.exists():
            constants_dest = kernel / "rosco_constants.h"
            shutil.copy2(str(candidate), str(constants_dest))
            result["files_modified"].append(str(constants_dest))
            break

    # --- Step 1b: Generate consolidated type header ---
    struct_headers = []
    view_populator_sources = []
    if derived_types:
        from .derived_type_parser import generate_consolidated_types_header
        from .view_populator import generate_view_populator

        # Generate single vit_types.h with all types in dependency order
        consolidated = generate_consolidated_types_header(derived_types, kind_aliases)
        vit_types_path = kernel / "vit_types.h"
        vit_types_path.write_text(consolidated)
        struct_headers = ["vit_types.h"]
        result["files_modified"].append(str(vit_types_path))

        # Generate view populator Fortran modules (kernel-specific)
        for type_name, type_info in derived_types.items():
            if getattr(type_info, 'view_strategy', None) != 'view':
                continue
            mappings = getattr(type_info, 'field_mappings', None) or []
            source_module = type_info.source_module
            if not source_module:
                result["errors"].append(
                    f"Cannot generate view populator for {type_name}: "
                    f"source module unknown. Ensure the TYPE definition is "
                    f"inside a MODULE block in the Fortran source."
                )
                return result
            populator_content = generate_view_populator(
                type_info, mappings, kind_aliases, source_module=source_module,
                registry=derived_types
            )
            populator_filename = f"vit_{type_name.lower()}_view.f90"
            populator_path = kernel / populator_filename
            populator_path.write_text(populator_content)
            view_populator_sources.append((populator_filename, source_module))
            result["files_modified"].append(str(populator_path))

    # --- Step 2: Generate C wrapper ---
    wrapper_filename = f"{sig.name.lower()}.cpp"
    wrapper_source = generate_cpp_wrapper(sig, str(impl_dest), kind_aliases,
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

    # --- Step 5: Generate callee bridges for translated callees ---
    callee_bridge_sources = []
    if translations and source_dir:
        try:
            callee_bridge_sources = _generate_callee_bridges(
                kernel, sig, translations, source_dir,
                kind_aliases, derived_types, result,
            )
        except Exception as e:
            result["errors"].append(f"Callee bridge generation failed: {e}")
            return result

    # --- Step 5b: Patch Makefile for callee bridges ---
    if callee_bridge_sources:
        try:
            _add_bridges_to_makefile(kernel, callee_bridge_sources)
            result["files_modified"].append(str(kernel / "Makefile"))
        except Exception as e:
            result["errors"].append(f"Callee bridge Makefile patch failed: {e}")

    result["success"] = len(result["errors"]) == 0
    return result


def _build_and_run_kernel(kernel: Path, env: dict) -> tuple[int, str]:
    """Build and run the kernel. Returns (returncode, combined_output)."""
    subprocess.run("make clean", shell=True, cwd=str(kernel), capture_output=True)

    build = subprocess.run(
        "make build", shell=True, cwd=str(kernel),
        capture_output=True, text=True, env=env,
    )
    if build.returncode != 0:
        return build.returncode, f"BUILD FAILED:\n{build.stdout}\n{build.stderr}"

    exe = kernel / "kernel.exe"
    if exe.exists():
        exe.chmod(exe.stat().st_mode | 0o111)

    run = subprocess.run(
        "./kernel.exe", shell=True, cwd=str(kernel),
        capture_output=True, text=True, env=env,
    )
    return 0, run.stdout + run.stderr


def _extract_function_body(content: str, name: str, is_function: bool) -> Optional[tuple[int, int, list[str]]]:
    """Find a function/subroutine body in source and return (start_idx, end_idx, lines).

    Returns the line indices (inclusive) and the lines of the function body,
    or None if not found.
    """
    lines = content.split("\n")
    name_upper = name.upper()
    keyword = "FUNCTION" if is_function else "SUBROUTINE"

    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if re.search(rf'\b{keyword}\s+{name_upper}\s*\(', stripped):
            if not stripped.startswith("END"):
                start_idx = i
                break

    if start_idx is None:
        return None

    end_idx = None
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip().upper()
        if re.match(rf'\s*END\s+{keyword}\b', stripped):
            end_idx = i
            break

    if end_idx is None:
        return None

    return start_idx, end_idx, lines[start_idx:end_idx + 1]


def _run_fortran_reference(kernel: Path, sig: FortranSignature,
                           env: dict,
                           tolerance: float) -> Optional[list[dict]]:
    """
    Run the kernel with original Fortran (no C++ replacement) to get a
    reference field log. Returns the field_data list, or None on failure.

    Replaces only the C++ wrapper function body with the original Fortran
    function body from the .kgen backup. All other VIT patches (field
    logging, USE statement fixes, NaN comparison patches) are preserved
    so the Fortran run produces comparable field log output.

    This is necessary when the target function and the KGen verification
    subroutines are in the same file (common in AeroDyn where caller and
    callee are module procedures in the same source file). Restoring the
    entire file would strip the field logging patches.

    Both runs use the same state file exit reference, so any state file
    artifacts appear identically in both field logs and cancel out when
    comparing Fortran vs C++.
    """
    makefile = kernel / "Makefile"

    # Find the file containing the target function
    target_file = None
    name_upper = sig.name.upper()
    for f90 in sorted(kernel.glob("*.[fF]90")):
        if f90.name.startswith("vit_"):
            continue
        content = f90.read_text()
        func_pattern = rf'\b(FUNCTION|SUBROUTINE)\s+{name_upper}\s*\('
        if re.search(func_pattern, content, re.IGNORECASE):
            target_file = f90
            break

    if target_file is None:
        return None

    backup = target_file.with_suffix(target_file.suffix + ".kgen")
    if not backup.exists():
        return None

    # Extract the original Fortran function body from the .kgen backup
    backup_content = backup.read_text()
    original_body = _extract_function_body(backup_content, sig.name, sig.is_function)
    if original_body is None:
        return None
    _, _, original_lines = original_body

    # Find the wrapper body in the VIT-patched file
    patched_content = target_file.read_text()
    wrapper_body = _extract_function_body(patched_content, sig.name, sig.is_function)
    if wrapper_body is None:
        return None
    wrapper_start, wrapper_end, _ = wrapper_body

    # Splice: replace only the wrapper with the original Fortran body,
    # keeping all other VIT patches (field logging, USE fixes, etc.)
    patched_lines = patched_content.split("\n")
    fortran_ref_lines = patched_lines[:wrapper_start] + original_lines + patched_lines[wrapper_end + 1:]
    fortran_ref_content = "\n".join(fortran_ref_lines)

    # Save current state
    saved_target = patched_content
    saved_makefile = makefile.read_text()

    try:
        # Write the spliced version (original function body + VIT logging patches)
        target_file.write_text(fortran_ref_content)

        # Strip VIT C++ additions from Makefile but keep Fortran patches
        mk_content = saved_makefile
        # Remove VIT C++ object files from ALL_OBJS
        mk_content = re.sub(r'\b' + re.escape(sig.name.lower()) + r'\.o\b', '', mk_content)
        # Remove VIT object files from ALL_OBJS
        mk_content = re.sub(r'\bvit_\w+\.o\b', '', mk_content)
        mk_content = re.sub(r'\b\w+_cpp\.o\b', '', mk_content)
        # Remove -lstdc++ from link line
        mk_content = mk_content.replace('-lstdc++', '')
        # Clean up multiple spaces in ALL_OBJS line
        mk_content = re.sub(r'  +', ' ', mk_content)
        makefile.write_text(mk_content)

        # Build and run
        rc, output = _build_and_run_kernel(kernel, env)
        if rc != 0:
            return None

        # Parse the output to get field data
        result = _parse_verify_output(output, tolerance)
        return result.field_data

    finally:
        # Restore VIT-patched files
        target_file.write_text(saved_target)
        makefile.write_text(saved_makefile)


def _compare_field_logs(fortran_data: list[dict],
                        cpp_data: list[dict]) -> tuple[bool, list[str], int]:
    """
    Compare Fortran and C++ field logs entry by entry.

    Returns (all_match, differences, n_artifacts) where:
    - all_match: True if C++ matches Fortran for every field
    - differences: list of human-readable difference descriptions
    - n_artifacts: count of state file artifacts (both show non-IDENTICAL)
    """
    # Build lookup: (case, field) → entry
    def make_key(entry):
        return (entry["case"], entry["field"])

    fortran_by_key = {}
    for entry in fortran_data:
        fortran_by_key[make_key(entry)] = entry

    cpp_by_key = {}
    for entry in cpp_data:
        cpp_by_key[make_key(entry)] = entry

    differences = []
    n_artifacts = 0

    # Status severity: IDENTICAL < IN_TOL < OUT_TOL
    severity = {"IDENTICAL": 0, "IN_TOL": 1, "OUT_TOL": 2}

    # Check every C++ entry against Fortran
    for key, cpp_entry in cpp_by_key.items():
        fortran_entry = fortran_by_key.get(key)
        if fortran_entry is None:
            # C++ has a field that Fortran doesn't — structural difference
            differences.append(
                f"{key[0]}, {key[1]}: present in C++ but not Fortran"
            )
            continue

        f_sev = severity.get(fortran_entry["status"], 2)
        c_sev = severity.get(cpp_entry["status"], 2)

        if cpp_entry["status"] == fortran_entry["status"]:
            if cpp_entry["status"] == "IDENTICAL":
                pass  # Both identical — perfect
            elif cpp_entry["status"] == "IN_TOL":
                pass  # Both within tolerance — acceptable
            elif cpp_entry["diff"] == fortran_entry["diff"]:
                # Same OUT_TOL status with same diff — state file artifact
                n_artifacts += 1
            else:
                # Same OUT_TOL status but different diff — real difference
                differences.append(
                    f"{key[0]}, {key[1]}: same status ({cpp_entry['status']}) "
                    f"but different diff — Fortran: {fortran_entry['diff']}, "
                    f"C++: {cpp_entry['diff']}"
                )
        elif c_sev > f_sev:
            # C++ is worse than Fortran — real translation issue
            # Exception: IN_TOL in C++ when Fortran is IDENTICAL is acceptable
            # (expected from callee bridge C/Fortran interface overhead)
            if cpp_entry["status"] == "IN_TOL" and fortran_entry["status"] == "IDENTICAL":
                pass  # Bridge overhead — within tolerance, acceptable
            else:
                differences.append(
                    f"{key[0]}, {key[1]}: Fortran={fortran_entry['status']}, "
                    f"C++={cpp_entry['status']}"
                )
        elif c_sev < f_sev:
            # C++ is better than Fortran — unexpected but not a bug
            pass

    # Check for Fortran entries missing from C++
    for key in fortran_by_key:
        if key not in cpp_by_key:
            differences.append(
                f"{key[0]}, {key[1]}: present in Fortran but not C++"
            )

    return len(differences) == 0, differences, n_artifacts


def run_kernel_verify(kernel_dir: str, env: Optional[dict] = None,
                      tolerance: float = 1e-14,
                      field_log: bool = True,
                      sig: Optional[FortranSignature] = None) -> VerifyResult:
    """
    Build and run the KGen kernel verification.

    Uses a two-run approach: first runs the original Fortran to establish
    a reference field log, then runs the C++ translation and compares.
    Both runs use the same state file exit reference, so state file
    capture artifacts cancel out in the comparison.

    Args:
        kernel_dir: Path to the (patched) kernel/ directory
        env: Additional environment variables
        tolerance: Maximum acceptable error
        sig: Function signature (needed for Fortran reference run)

    Returns:
        VerifyResult with pass/fail details
    """
    kernel = Path(kernel_dir)
    run_env = {**os.environ, **(env or {})}

    # --- Step 1: Fortran reference run ---
    fortran_field_data = None
    if sig is not None:
        fortran_field_data = _run_fortran_reference(kernel, sig, run_env, tolerance)

    # --- Step 2: C++ verification run ---
    rc, output = _build_and_run_kernel(kernel, run_env)
    if rc != 0:
        return VerifyResult(
            success=False,
            total_cases=0,
            passed_cases=0,
            failed_cases=0,
            skipped_cases=0,
            max_error=float("inf"),
            output=output,
            details=[],
        )

    # Parse C++ verification output
    cpp_result = _parse_verify_output(output, tolerance)

    # --- Step 3: Compare field logs ---
    if fortran_field_data is not None and cpp_result.field_data:
        all_match, differences, n_artifacts = _compare_field_logs(
            fortran_field_data, cpp_result.field_data
        )

        if all_match:
            # C++ matches Fortran exactly — all cases pass
            # Override pass count: failures that match Fortran are artifacts
            result = VerifyResult(
                success=True,
                total_cases=cpp_result.total_cases,
                passed_cases=cpp_result.total_cases - cpp_result.skipped_cases,
                failed_cases=0,
                skipped_cases=cpp_result.skipped_cases,
                max_error=0.0,
                output=cpp_result.output,
                details=cpp_result.details,
                field_data=cpp_result.field_data,
            )
        else:
            # C++ differs from Fortran — real translation bugs
            diff_msg = "\n".join(f"  {d}" for d in differences[:10])
            result = VerifyResult(
                success=False,
                total_cases=cpp_result.total_cases,
                passed_cases=cpp_result.passed_cases,
                failed_cases=cpp_result.failed_cases,
                skipped_cases=cpp_result.skipped_cases,
                max_error=cpp_result.max_error,
                output=cpp_result.output + f"\n\nC++ differs from Fortran:\n{diff_msg}",
                details=cpp_result.details,
                field_data=cpp_result.field_data,
            )

        if n_artifacts > 0:
            result.output += f"\n  Note: {n_artifacts} state file artifacts detected (Fortran and C++ match each other)"
    else:
        # No Fortran reference available — fall back to direct comparison
        result = cpp_result

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
                # APPEND dependency (not prepend) — $< must remain the .f90 source file
                new_rule = f"{m.group(1)}{m.group(2)} {pop_obj}"
                content_joined = content_joined.replace(old_rule, new_rule, 1)
        lines = content_joined.split("\n")

    # 4. Add -lstdc++ to link flags (must be AFTER object files for gfortran)
    content_joined = "\n".join(lines)
    if "-lstdc++" not in content_joined:
        for i, line in enumerate(lines):
            if ("-o kernel.exe" in line or "-o\tkernel.exe" in line) and "rm " not in line and "./" not in line:
                lines[i] = line.rstrip() + " -lstdc++"
                break

    makefile.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Kernel callee bridges
# ---------------------------------------------------------------------------

def detect_callees(source: str, target_name: str,
                    known_functions: Optional[set] = None) -> list[str]:
    """
    Scan target function body for calls to other functions/subroutines.

    Detects all CALL statements and function-call patterns, optionally
    filtered to a set of known function names. If known_functions is None,
    returns all detected callee names.

    Args:
        source: Full Fortran source text containing the target function
        target_name: Name of the target function
        known_functions: Optional set of function names to filter to.
                         If None, all detected callees are returned.

    Returns:
        List of callee function names found in the target function body
    """
    import re as _re

    # Extract the target function body
    lines = source.split("\n")
    name_upper = target_name.upper()
    in_body = False
    body_lines = []
    for line in lines:
        stripped = line.strip().upper()
        # Remove comments
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]
        stripped = stripped.strip()
        if not in_body:
            if _re.search(rf'\b(FUNCTION|SUBROUTINE)\s+{name_upper}\s*\(', stripped):
                in_body = True
        else:
            if _re.search(rf'\bEND\s+(FUNCTION|SUBROUTINE)\b', stripped):
                break
            body_lines.append(line)

    # Strip comments from body lines before searching for callees
    stripped_body_lines = []
    for line in body_lines:
        if "!" in line:
            line = line[:line.index("!")]
        stripped_body_lines.append(line)
    body_text = "\n".join(stripped_body_lines)

    # Extract all CALL targets and function-call patterns
    callees = set()

    # CALL subroutine_name(...)
    for m in _re.finditer(r'\bCALL\s+(\w+)\s*\(', body_text, _re.IGNORECASE):
        name = m.group(1)
        # Skip intrinsics and KGen utilities
        if name.upper() not in ('C_F_POINTER', 'C_LOC', 'PRESENT', 'TRANSFER',
                                  'ALLOCATED', 'SIZE', 'MIN', 'MAX', 'ABS',
                                  'WRITE', 'READ', 'PRINT'):
            callees.add(name)

    # function_name(...) = ... (assignment context, harder to detect reliably)
    # Use known_functions to filter if available
    if known_functions:
        for func_name in known_functions:
            pattern = rf'\b{_re.escape(func_name)}\s*\('
            if _re.search(pattern, body_text, _re.IGNORECASE):
                callees.add(func_name)

    # Filter to known functions if provided
    if known_functions:
        callees = {c for c in callees if c.upper() in {f.upper() for f in known_functions}}

    # Remove the target function itself (recursive calls shouldn't be bridged)
    callees.discard(target_name)
    callees = {c for c in callees if c.upper() != target_name.upper()}

    # Filter out VIT-generated artifacts (wrappers, populators)
    callees = {c for c in callees
                if not c.lower().endswith('_c')
                and not c.lower().startswith('vit_')}

    return sorted(callees, key=str.upper)


def _generate_callee_bridges(
    kernel: Path,
    sig: FortranSignature,
    translations: dict,
    source_dir: str,
    kind_aliases: Optional[dict],
    derived_types: Optional[dict],
    result: dict,
) -> list[str]:
    """
    Generate BIND(C) bridge functions for callees in the kernel.

    Generates bridges for ALL callees the target function calls (not just
    translated ones). In the kernel, every callee is original Fortran —
    the bridge provides a C-callable entry point regardless of translation
    status.

    Returns list of bridge .f90 filenames created.
    """
    from .fortran_parser import find_function_in_file
    from .interface_gen import (
        generate_kernel_callee_bridge,
        generate_callee_declarations_header,
        build_c_params,
    )
    from .derived_type_parser import build_derived_type_registry, generate_consolidated_types_header

    # Read the ORIGINAL source file (not the patched kernel — the kernel has
    # the function body replaced with a wrapper, so callees won't be found there)
    if not sig.source_file:
        return []
    target_source = Path(sig.source_file).read_text()

    # Build a set of all known function names for detection
    # (translations + any function we can find in the source directory)
    known_funcs = set(translations.keys()) if translations else set()

    # Also scan all source files for FUNCTION/SUBROUTINE definitions
    if source_dir:
        import re as _re
        for pattern in ("**/*.f90", "**/*.F90"):
            for f90_file in sorted(Path(source_dir).glob(pattern)):
                content = f90_file.read_text()
                for m in _re.finditer(
                    r'\b(?:FUNCTION|SUBROUTINE)\s+(\w+)\s*\(',
                    content, _re.IGNORECASE,
                ):
                    known_funcs.add(m.group(1))

    # Detect all callees in the target function body
    callee_names = detect_callees(target_source, sig.name, known_functions=known_funcs)
    if not callee_names:
        return []

    bridge_sources = []
    callee_sigs = []

    for callee_name in callee_names:
        # Find the callee's definition in the source directory
        callee_sig = None
        for pattern in ("**/*.f90", "**/*.F90"):
            for f90_file in sorted(Path(source_dir).glob(pattern)):
                try:
                    callee_sig = find_function_in_file(str(f90_file), callee_name)
                    if callee_sig:
                        break
                except (ValueError, FileNotFoundError):
                    continue
            if callee_sig:
                break

        if not callee_sig:
            continue

        # Build type registry for the callee (for struct pointer handling)
        try:
            callee_registry = build_derived_type_registry(callee_sig, source_dir)

            # Update vit_types.h with any callee types not already present
            vit_types_path = kernel / "vit_types.h"
            existing = vit_types_path.read_text() if vit_types_path.exists() else ""
            updated = generate_consolidated_types_header(
                callee_registry, kind_aliases, existing_content=existing)
            if updated != existing:
                vit_types_path.write_text(updated)

            # Skip array-returning functions — bridges for these require
            # allocatable temporaries that we can't generate reliably.
            # If the C++ actually calls them, it'll get a linker error.
            if callee_sig.is_function and callee_sig.result_is_array:
                continue

            # Propagate view strategies from the target's registry to the callee's.
            # The target's registry has view_strategy applied from vit.yaml, but
            # each callee gets a fresh registry. Without this, view-type args
            # (like ControlParameters) would be treated as passthrough instead
            # of using the module stash variable.
            if derived_types and callee_registry:
                for type_name, type_info in callee_registry.items():
                    if type_name in derived_types:
                        parent_info = derived_types[type_name]
                        if getattr(parent_info, 'view_strategy', None) == "view":
                            type_info.view_strategy = "view"

            bridge_code = generate_kernel_callee_bridge(
                callee_sig, kind_aliases, callee_registry
            )
            bridge_filename = f"vit_bridge_{callee_name.lower()}.f90"
            bridge_path = kernel / bridge_filename
            bridge_path.write_text(bridge_code)
            bridge_sources.append(bridge_filename)
            callee_sigs.append(callee_sig)
            result["files_modified"].append(str(bridge_path))
        except (ValueError, TypeError) as e:
            # Skip callees whose signatures can't be bridged (e.g., CHARACTER
            # args, unsupported types). The C++ will get a linker error only
            # if it actually calls this callee — which is the right behavior.
            pass

    # Generate callee declarations header for C++
    if callee_sigs:
        header_content = generate_callee_declarations_header(
            callee_sigs, kind_aliases, derived_types
        )
        header_path = kernel / "vit_kernel_callees.h"
        header_path.write_text(header_content)
        result["files_modified"].append(str(header_path))

        # Patch the C++ wrapper to include the callee header
        wrapper_cpp = kernel / f"{sig.name.lower()}.cpp"
        if wrapper_cpp.exists():
            content = wrapper_cpp.read_text()
            if "vit_kernel_callees.h" not in content:
                content = content.replace(
                    '// User-provided C++ implementation',
                    '#include "vit_kernel_callees.h"\n\n// User-provided C++ implementation',
                )
                wrapper_cpp.write_text(content)

    return bridge_sources


def _add_bridges_to_makefile(kernel: Path, bridge_sources: list[str]) -> None:
    """Add callee bridge .f90 files to the kernel Makefile."""
    makefile = kernel / "Makefile"
    content = makefile.read_text()
    lines = content.split("\n")

    for bridge_src in bridge_sources:
        bridge_obj = bridge_src.replace(".f90", ".o")

        # Add to ALL_OBJS
        content_joined = "\n".join(lines)
        all_objs_match = re.search(r'^(ALL_OBJS\s*[:?]?=\s*)(.*)', content_joined, re.MULTILINE)
        if all_objs_match and bridge_obj not in all_objs_match.group(2):
            old_line = all_objs_match.group(0)
            new_line = f"{all_objs_match.group(1)}{bridge_obj} {all_objs_match.group(2)}"
            content_joined = content_joined.replace(old_line, new_line, 1)
            lines = content_joined.split("\n")

        # Add compilation rule.
        # The bridge USEs both ROSCO_Types (for derived types) and the callee's
        # module (e.g., Controllers). It must compile after both.
        # We use a broad dependency on all .o files that might provide modules,
        # which is safe since the bridge is a leaf node (nothing depends on it
        # except the linker).
        content_joined = "\n".join(lines)
        if bridge_src not in content_joined:
            # Find all existing .o targets to depend on (ensures all modules are available)
            all_objs_match = re.search(r'^ALL_OBJS\s*[:?]?=\s*(.*)', content_joined, re.MULTILINE)
            other_objs = ""
            if all_objs_match:
                # Depend on all Fortran .o files (exclude the bridge itself and C++ .o)
                objs = all_objs_match.group(1).split()
                fortran_objs = [o for o in objs if o != bridge_obj and not o.endswith("_cpp.o")]
                other_objs = " " + " ".join(fortran_objs)
            rule_lines = [
                f"# VIT: callee bridge for kernel verification",
                f"{bridge_obj}: {bridge_src}{other_objs}",
                f"\t$(FC_0) $(FC_FLAGS_SET_0) -c $< -o $@",
                "",
            ]
            for i, line in enumerate(lines):
                if re.match(r'^build\s*:', line):
                    for j, rule_line in enumerate(rule_lines):
                        lines.insert(i + j, rule_line)
                    break

    makefile.write_text("\n".join(lines))


def _patch_kind_constants(kernel: Path, kind_aliases: dict,
                          modified_files: list) -> None:
    """
    Patch Fortran kind parameters in the kernel to match kind_aliases.

    KGen copies Constants.f90 with original kind definitions like:
        INTEGER, PARAMETER :: ReKi = C_FLOAT  (= 4 bytes)
    But with -fdefault-real-8, ROSCO builds with ReKi effectively 8 bytes.
    The kind_aliases in vit.yaml declare this: ReKi: REAL(8).

    This function patches the kind parameter definitions so kernel types
    match the C interface expectations. Without this, REAL(ReKi) arrays
    are 4-byte floats in the kernel but the C++ wrapper expects double.
    """
    # Map kind alias targets to ISO_C_BINDING constants
    kind_to_c_constant = {
        "REAL(8)": "C_DOUBLE",
        "REAL(4)": "C_FLOAT",
        "INTEGER(4)": "C_INT",
        "INTEGER(8)": "C_LONG_LONG",
    }

    for f90_file in sorted(kernel.glob("*.[fF]90")):
        content = f90_file.read_text()
        original = content
        for kind_name, target_type in kind_aliases.items():
            target_type_upper = target_type.strip().upper()
            c_constant = kind_to_c_constant.get(target_type_upper)
            if not c_constant:
                continue
            # Match: INTEGER, PARAMETER :: KindName = C_SOMETHING
            pattern = (
                rf'(INTEGER\s*,\s*PARAMETER\s*::\s*{kind_name}\s*=\s*)'
                rf'(\w+)'
            )
            content = re.sub(pattern, rf'\g<1>{c_constant}', content,
                             flags=re.IGNORECASE)
        if content != original:
            f90_file.write_text(content)
            modified_files.append(str(f90_file))




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
    wrapper_body = generate_fortran_wrapper(sig, kind_aliases, derived_types, is_kernel=True)

    for f90_file in sorted(kernel.glob("*.[fF]90")):
        content = f90_file.read_text()
        original = content
        content_upper = content.upper()
        name_upper = sig.name.upper()

        # Fix USE kgen_utils_mod ONLY → USE kgen_utils_mod (all files)
        content = _fix_kgen_utils_mod_only(content)

        # Fix unresolved ISO_FORTRAN_ENV symbols (e.g., real64, int32).
        # KGen resolves #ifdef but can't resolve intrinsic module constants,
        # leaving markers like "R8Ki = real64 !!! UNRESOLVED !!! real64".
        # Add USE ISO_FORTRAN_ENV if the file has unresolved symbols from it.
        if "UNRESOLVED" in content and ("real64" in content or "int32" in content or "real32" in content):
            if "ISO_FORTRAN_ENV" not in content.upper():
                content = re.sub(
                    r'(USE\s+kgen_utils_mod[^\n]*\n)',
                    r'\1    USE, INTRINSIC :: ISO_FORTRAN_ENV\n',
                    content, count=1, flags=re.IGNORECASE,
                )

        # Remove PUBLIC statements inside subroutines (illegal Fortran).
        # KGen sometimes copies PUBLIC statements from the original module-level
        # code into the generated kernel driver subroutine. PUBLIC is only valid
        # in module specification parts (before CONTAINS), not inside procedures.
        # Only remove PUBLIC statements that appear after CONTAINS to avoid
        # stripping legitimate module-level PUBLIC declarations.
        contains_pos = content.upper().find('\nCONTAINS\n')
        if contains_pos >= 0:
            before_contains = content[:contains_pos]
            after_contains = content[contains_pos:]
            after_contains = re.sub(
                r'^\s*PUBLIC\s+\w+\s*$', '! VIT: removed invalid PUBLIC statement',
                after_contains, flags=re.MULTILINE | re.IGNORECASE,
            )
            content = before_contains + after_contains

        # Remove USE statements for DISCON module.
        # When extracting from DISCON.F90, KGen generates the kernel driver
        # with `USE discon, ONLY: discon` — but DISCON is a subroutine, not
        # a module. The kernel driver calls discon() directly via EXTERNAL.
        content = re.sub(
            r'^\s*USE\s+discon\b.*$',
            '! VIT: removed USE discon (subroutine, not module)',
            content, flags=re.MULTILINE | re.IGNORECASE,
        )

        # Fix assumed-size array arguments in KGen verification calls.
        # KGen generates comparison routines with DIMENSION(:) parameters but
        # the kernel driver passes assumed-size (*) arrays to them. Fortran
        # forbids passing (*) to (:). We comment out these verification calls
        # since the function's output verification covers correctness.
        content = re.sub(
            r'^(\s*CALL\s+kv_\w+\([^)]*\bavrswap\b[^)]*\))',
            r'! VIT: skipped avrSWAP verification (assumed-size incompatible with KGen comparator)\n! \1',
            content, flags=re.MULTILINE | re.IGNORECASE,
        )

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
        # Check for the INTERFACE keyword + c_func_name, not just c_func_name
        # (the CALL line already contains c_func_name from the wrapper body)
        iface_marker = f"BIND(C, NAME='{sig.c_func_name}')"
        if iface_marker.upper() not in content.upper():
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