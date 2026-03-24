"""
KGen extraction orchestration.

Automates:
1. Finding the call site for a target function
2. Inserting KGen pragmas around it
3. Generating include.ini for module discovery
4. Cleaning stale artifacts
5. Running KGen with the correct build/run commands
6. Injecting kgen_utils.f90 into the project build (KGen bug workaround)
7. Managing the resulting kernel/ and state/ directories

Encodes lessons learned from ROSCO debugging:
- KGen pragmas must use underscores: begin_callsite / end_callsite
- KGen pragmas must include the function name
- KGen strace often fails to discover -I/-J paths; include.ini must be pre-generated
- Stale strace.log causes KGen to reuse bad data; must clean before extraction
- Build must be clean so strace captures actual gfortran invocations
- The built library must be installed where the simulation loads it from
- KGen puts kernel/ and state/ in CWD, not relative to the source file
- Source files must be restored to original state on failure
- KGen instruments code with calls to kgen_utils functions but doesn't add
  kgen_utils.f90 to the project's build system. The instrumented shared library
  builds without error (unresolved symbols allowed) but fails silently at runtime.
  We wrap --cmd-build in a script that injects kgen_utils.f90 into the build.
- Editable pip installs (pip install -e .) can point to the wrong checkout,
  causing the simulation to load a different (uninstrumented) library.
"""

import os
import re
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Optional


def find_call_sites(source_dir: str, function_name: str,
                    file_pattern: str = "*.f90") -> list[tuple[str, int, str]]:
    """
    Find all call sites for a function in the source tree.

    Returns:
        List of (filepath, line_number, line_text) tuples
    """
    results = []
    name_upper = function_name.upper()
    source_path = Path(source_dir)

    for fpath in source_path.rglob(file_pattern):
        try:
            lines = fpath.read_text().split("\n")
        except (UnicodeDecodeError, PermissionError):
            continue

        for i, line in enumerate(lines):
            stripped = line.strip().upper()
            # Remove comments
            if "!" in stripped:
                stripped = stripped[:stripped.index("!")]

            # Look for function call (as expression) or CALL statement
            if re.search(rf'\b{name_upper}\s*\(', stripped):
                # Exclude the function definition itself
                if not re.match(rf'\s*(FUNCTION|SUBROUTINE)\s+{name_upper}', stripped):
                    results.append((str(fpath), i + 1, line.rstrip()))

    return results


def insert_kgen_pragma(filepath: str, line_number: int,
                       function_name: str = "",
                       output_path: Optional[str] = None) -> str:
    """
    Insert KGen extraction pragma around a specific line.

    KGen requires the format:
        !$kgen begin_callsite <function_name>
        <call site>
        !$kgen end_callsite

    Note: underscores not spaces, function name on the begin line.

    Args:
        filepath: Source file to modify
        line_number: 1-indexed line to wrap with pragma
        function_name: Name of the function being extracted (required by KGen)
        output_path: Where to write (defaults to overwriting filepath)

    Returns:
        Path to the modified file
    """
    lines = Path(filepath).read_text().split("\n")
    idx = line_number - 1  # Convert to 0-indexed

    if idx < 0 or idx >= len(lines):
        raise ValueError(f"Line {line_number} out of range (file has {len(lines)} lines)")

    # Find the statement boundaries (handle continuation lines)
    start_idx = idx
    end_idx = idx

    # Check if this line is a continuation of a previous line
    while start_idx > 0 and lines[start_idx - 1].rstrip().endswith("&"):
        start_idx -= 1

    # Check if this line continues on the next line
    while end_idx < len(lines) - 1 and lines[end_idx].rstrip().endswith("&"):
        end_idx += 1

    # Insert pragmas (KGen requires underscores and function name)
    indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    indent_str = " " * indent

    # Insert AFTER the end line, then BEFORE the start line (to preserve indices)
    lines.insert(end_idx + 1, f"{indent_str}!$kgen end_callsite")
    lines.insert(start_idx, f"{indent_str}!$kgen begin_callsite {function_name}")

    out_path = output_path or filepath
    Path(out_path).write_text("\n".join(lines))
    return out_path


def remove_kgen_pragma(filepath: str) -> None:
    """Remove KGen pragmas from a file."""
    lines = Path(filepath).read_text().split("\n")
    filtered = [l for l in lines if not l.strip().lower().startswith("!$kgen")]
    Path(filepath).write_text("\n".join(filtered))


def generate_include_ini(
    source_dirs: list[str],
    mod_dirs: list[str],
    output_path: str = "include.ini",
) -> str:
    """
    Generate KGen include.ini with module search paths.

    KGen's strace-based module discovery is unreliable — it often fails to
    capture -I/-J flags from cmake/make child processes. This function
    generates an include.ini that explicitly lists all directories KGen
    should search for .mod files and Fortran source files.

    The format is:
        [include]
        /path/to/ftnmods=
        /path/to/src=

    Args:
        source_dirs: Directories containing Fortran source files
        mod_dirs: Directories containing .mod files (e.g., build/ftnmods)
        output_path: Where to write include.ini

    Returns:
        Path to the generated file
    """
    lines = ["[include]"]

    # Add .mod directories first (higher priority)
    for d in mod_dirs:
        d = str(Path(d).resolve())
        if os.path.isdir(d):
            lines.append(f"{d}=")

    # Add source directories
    for d in source_dirs:
        d = str(Path(d).resolve())
        if os.path.isdir(d):
            lines.append(f"{d}=")

    Path(output_path).write_text("\n".join(lines) + "\n")
    return output_path


def discover_source_dirs(source_dir: str) -> list[str]:
    """
    Find all directories containing Fortran source files under source_dir.

    Returns absolute paths.
    """
    dirs = set()
    source_path = Path(source_dir).resolve()
    if not source_path.exists():
        return []
    for pattern in ["**/*.f90", "**/*.F90", "**/*.f", "**/*.F"]:
        for fpath in source_path.glob(pattern):
            dirs.add(str(fpath.parent))
    return sorted(dirs)


def discover_mod_dirs(build_dir: str) -> list[str]:
    """
    Find all directories containing .mod files under build_dir.

    Returns absolute paths.
    """
    dirs = set()
    build_path = Path(build_dir).resolve()
    if build_path.exists():
        for fpath in build_path.rglob("*.mod"):
            dirs.add(str(fpath.parent))
    return sorted(dirs)


def clean_kgen_artifacts(working_dir: str = ".") -> list[str]:
    """
    Remove stale KGen artifacts that cause problems on re-extraction.

    KGen reuses strace.log if it exists, which means it won't re-trace
    the build. Since the include paths may have been wrong in the stale
    strace, we must remove it so KGen generates a fresh one.

    Returns:
        List of files removed
    """
    removed = []
    for artifact in ["strace.log", "kgen.log", "model.ini"]:
        path = os.path.join(working_dir, artifact)
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)

    # Remove elapsedtime/ directory — KGen stores ETime profiling data here
    # and reuses it across extractions, which can embed the wrong function
    # name in state capture instrumented code.
    etime_dir = os.path.join(working_dir, "elapsedtime")
    if os.path.isdir(etime_dir):
        shutil.rmtree(etime_dir)
        removed.append(etime_dir)

    # Note: .kgen_org files (KGen's instrumented source backups) are
    # cleaned in run_kgen_extract() which knows the source_dir.

    # Don't remove include.ini — we generate it ourselves and want KGen to reuse it
    # Don't remove kernel/ or state/ — user may want to keep previous data

    return removed


def check_editable_installs(project_dir: str, package_name: str = "") -> list[str]:
    """
    Check for editable pip installs that might load the wrong library.

    When a project is installed with `pip install -e .`, Python's import
    system follows .egg-link files to the original source directory. If the
    user has multiple checkouts (e.g., /project/original and /project/testing),
    the editable install may point to the wrong one — meaning the simulation
    loads an uninstrumented library instead of the KGen-instrumented one.

    This checks for common indicators and returns warnings.

    Args:
        project_dir: The project directory we WANT to be active
        package_name: Optional package name to check (e.g., "rosco")

    Returns:
        List of warning messages (empty if no issues detected)
    """
    warnings_list = []
    project_dir = str(Path(project_dir).resolve())

    # Check if there's a pip editable install pointing elsewhere
    try:
        result = subprocess.run(
            ["pip", "show", "-f", package_name] if package_name else ["pip", "list", "--editable"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout

        if package_name:
            # Look for "Editable project location:" or "Location:"
            for line in output.split("\n"):
                if "location:" in line.lower():
                    location = line.split(":", 1)[1].strip()
                    location_resolved = str(Path(location).resolve())
                    if location_resolved != project_dir:
                        warnings_list.append(
                            f"WARNING: Editable install for '{package_name}' points to:\n"
                            f"  {location_resolved}\n"
                            f"but VIT is working in:\n"
                            f"  {project_dir}\n"
                            f"The simulation may load the WRONG (uninstrumented) library.\n"
                            f"Fix: cd {project_dir} && pip install -e ."
                        )
        else:
            # Check if any editable installs point outside project_dir
            for line in output.split("\n"):
                if project_dir not in line and "editable" in line.lower():
                    warnings_list.append(
                        f"WARNING: Found editable install not in project dir: {line.strip()}"
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # pip not available, skip check

    return warnings_list


def generate_state_capture_build_script(
    build_cmd: str,
    install_cmd: str = "",
    cmake_file: str = "",
    source_dir: str = "",
    kernel_dir: str = "kernel",
    assumed_size_arrays: dict = None,
) -> str:
    """
    Generate a shell script that wraps the build command for state capture.

    KGen instruments source code with calls to kgen_utils functions
    (kgen_init_vars, kgen_check_save, etc.) but does NOT add kgen_utils.f90
    to the project's build system. On Linux, shared libraries allow unresolved
    symbols at link time, so the build succeeds — but state capture fails
    silently at runtime because the kgen functions are missing.

    This script:
    1. Copies kgen_utils.f90 from kernel/ into the source tree
    2. Patches CMakeLists.txt (or Makefile) to compile it
    3. Runs the actual build
    4. Runs the install step (if any)
    5. Cleans up the patches (so the source tree is restored)

    The script is written to a temp file and returned as a command.

    Args:
        build_cmd: Original build command
        install_cmd: Optional post-build install command
        cmake_file: Path to CMakeLists.txt (for cmake projects)
        source_dir: Directory containing Fortran source files
        kernel_dir: Where KGen puts kernel/ (for finding kgen_utils.f90)

    Returns:
        Path to the generated script
    """
    # Find where kgen_utils.f90 will be
    kgen_utils_src = os.path.join(os.path.abspath(kernel_dir), "kgen_utils.f90")

    # Determine target location for kgen_utils.f90
    if source_dir:
        kgen_utils_dest = os.path.join(os.path.abspath(source_dir), "kgen_utils.f90")
    else:
        kgen_utils_dest = os.path.join(os.getcwd(), "kgen_utils.f90")

    cmake_file_abs = os.path.abspath(cmake_file) if cmake_file else ""

    script_lines = [
        "#!/bin/bash",
        "set -e",
        "",
        "# VIT: State capture build wrapper",
        "# Injects kgen_utils.f90 into the project build so KGen's",
        "# instrumented code can capture state data at runtime.",
        "",
        f'KGEN_UTILS_SRC="{kgen_utils_src}"',
        f'KGEN_UTILS_DEST="{kgen_utils_dest}"',
        f'CMAKE_FILE="{cmake_file_abs}"',
        "",
        "# Step 1: Copy kgen_utils.f90 into source tree (if it exists)",
        'if [ -f "$KGEN_UTILS_SRC" ]; then',
        '    cp "$KGEN_UTILS_SRC" "$KGEN_UTILS_DEST"',
        '    echo "VIT: Copied kgen_utils.f90 to source tree"',
        "",
    ]

    # Step 2: Patch CMakeLists.txt if cmake project
    if cmake_file_abs:
        script_lines.extend([
            '    # Patch CMakeLists.txt to include kgen_utils.f90 (only once)',
            '    if [ -f "$CMAKE_FILE" ]; then',
            '        # Backup only if no backup exists (wrapper may run multiple times)',
            '        if [ ! -f "${CMAKE_FILE}.vit_backup" ]; then',
            '            cp "$CMAKE_FILE" "${CMAKE_FILE}.vit_backup"',
            '        fi',
            '        # Add kgen_utils.f90 only if not already present',
            '        if ! grep -q "kgen_utils.f90" "$CMAKE_FILE"; then',
            r"""            sed -i '0,/\(src\/[A-Za-z_]*\.f90\)/s//\1\n    src\/kgen_utils.f90/' "$CMAKE_FILE" """,
            '            echo "VIT: Patched CMakeLists.txt to include kgen_utils.f90"',
            '        fi',
            "    fi",
        ])

    script_lines.extend([
        "fi",
        "",
    ])

    # Step 2b: Patch assumed-size arrays in instrumented source.
    # KGen generates write subroutines with DIMENSION(:) (assumed-shape)
    # parameters, but assumed-size (*) arrays can't be passed to assumed-shape.
    # Fix: replace bare array names with explicit sections at kw_* call sites.
    if assumed_size_arrays and source_dir:
        script_lines.append("# Step 2b: Patch assumed-size arrays in instrumented source")
        source_dir_abs = os.path.abspath(source_dir)
        for varname, size in assumed_size_arrays.items():
            varname_lower = varname.lower()
            # Replace (varname, kgen_unit with (varname(1:N), kgen_unit in kw_* calls
            # Case-insensitive because KGen lowercases variable names
            sed_pattern = f"s/({varname_lower}, kgen_unit/({varname_lower}(1:{size}), kgen_unit/gI"
            script_lines.extend([
                f'for f90 in "{source_dir_abs}"/*.f90 "{source_dir_abs}"/*.F90; do',
                f'    [ -f "$f90" ] || continue',
                f"    sed -i '{sed_pattern}' \"$f90\"",
                f'done',
                f'echo "VIT: Patched assumed-size array {varname} with size {size}"',
            ])
        script_lines.append("")

    # Step 2c: DLL SAVE variable persistence workaround.
    # KGen's profiling phase loads the DLL (setting SAVE variables including
    # AlreadyInitialized=1, kgen_isstop, kgen_invoke, kgen_islast, etc.).
    # If the DLL isn't truly unloaded before the state capture phase (common
    # with ctypes/dlclose), ALL SAVE variables persist. This causes:
    #   1. ROSCO's SetParameters rejects reinitialization (aviFAIL=-1)
    #   2. KGen's invocation counter and stop flags are stale
    # Fix: Reset AlreadyInitialized before ReadAvrSWAP, and deallocate
    # KGen's SAVE arrays before the allocation check so they reinitialize.
    if source_dir:
        source_dir_abs = os.path.abspath(source_dir)
        script_lines.extend([
            "# Step 2c: Reset SAVE variables (DLL persistence workaround)",
            f'for f90 in "{source_dir_abs}"/*.f90 "{source_dir_abs}"/*.F90; do',
            '    [ -f "$f90" ] || continue',
            "    # Reset ROSCO's AlreadyInitialized flag",
            "    sed -i '/CALL ReadAvrSWAP/i\\    LocalVar%AlreadyInitialized = 0' \"$f90\"",
            "    # Deallocate KGen's SAVE arrays on init call only (iStatus=0).",
            "    # Anchored on the KGen init check (exists in ALL instrumented files,",
            "    # not just DISCON.F90). Only runs on iStatus=0 to avoid resetting the",
            "    # invocation counter on every call.",
            "    sed -i '/IF (.NOT. ALLOCATED(kgen_isstop))/i\\        IF (NINT(avrSWAP(1)) == 0 .AND. ALLOCATED(kgen_isstop)) THEN\\n            DEALLOCATE(kgen_isstop, kgen_issave, kgen_islast, kgen_ischecked)\\n            DEALLOCATE(kgen_invoke, kgen_last_invoke, kgen_filepath, kgen_lockpath)\\n        END IF' \"$f90\"",
            'done',
            'echo "VIT: Patched DLL SAVE variable reset in instrumented source"',
            "",
        ])

    script_lines.extend([
        "# Step 3: Run the actual build",
        f"{build_cmd}",
        "BUILD_RC=$?",
        "",
    ])

    # Step 4: Install
    if install_cmd:
        script_lines.extend([
            "# Step 4: Install",
            f'if [ $BUILD_RC -eq 0 ]; then',
            f"    {install_cmd}",
            "fi",
            "",
        ])

    # Step 5: Clean up (always, even on failure)
    script_lines.extend([
        "# Step 5: Clean up injected files (restore source tree)",
        '# NOTE: We do NOT clean up here because KGen may need to rebuild',
        '# multiple times. Cleanup happens after state capture is complete.',
        "",
        "exit $BUILD_RC",
    ])

    # Write script
    script_path = os.path.join(os.getcwd(), ".vit_build_wrapper.sh")
    Path(script_path).write_text("\n".join(script_lines) + "\n")
    os.chmod(script_path, 0o755)

    return script_path


def cleanup_kgen_utils_injection(
    cmake_file: str = "",
    source_dir: str = "",
) -> list[str]:
    """
    Remove kgen_utils.f90 injection after state capture is complete.

    Restores CMakeLists.txt from backup and removes the copied kgen_utils.f90.

    Returns:
        List of files cleaned up
    """
    cleaned = []

    # Restore CMakeLists.txt
    if cmake_file:
        backup = cmake_file + ".vit_backup"
        if os.path.exists(backup):
            shutil.move(backup, cmake_file)
            cleaned.append(f"Restored {cmake_file}")

    # Remove injected kgen_utils.f90
    if source_dir:
        injected = os.path.join(source_dir, "kgen_utils.f90")
        if os.path.exists(injected):
            os.remove(injected)
            cleaned.append(f"Removed {injected}")

    # Remove wrapper script
    wrapper = os.path.join(os.getcwd(), ".vit_build_wrapper.sh")
    if os.path.exists(wrapper):
        os.remove(wrapper)
        cleaned.append(f"Removed {wrapper}")

    return cleaned


def generate_kgen_command(
    source_file: str,
    build_cmd: str,
    run_cmd: str,
    clean_cmd: str = "",
    kgen_path: str = "kgen",
    output_dir: Optional[str] = None,
    invocation: str = "",
) -> str:
    """
    Generate the kgen command line.

    Args:
        source_file: Path to the Fortran file with KGen pragma
        build_cmd: Command to build the project (may include install step)
        run_cmd: Command to run the simulation
        clean_cmd: Command to clean build artifacts
        kgen_path: Path to KGen executable
        output_dir: Where to put kernel/ and state/ directories
        invocation: KGen invocation spec: "MPI_rank:OMP_thread:start-end"
                    e.g., "0:0:1-20" captures calls 1-20 on rank 0 thread 0

    Returns:
        Shell command string
    """
    parts = [kgen_path, source_file]

    parts.append(f"--cmd-build '{build_cmd}'")
    parts.append(f"--cmd-run '{run_cmd}'")

    if clean_cmd:
        parts.append(f"--cmd-clean '{clean_cmd}'")

    if output_dir:
        parts.append(f"--outdir {output_dir}")

    if invocation:
        parts.append(f"--invocation {invocation}")

    return " ".join(parts)


def run_kgen_extract(
    source_file: str,
    build_cmd: str,
    run_cmd: str,
    clean_cmd: str = "",
    install_cmd: str = "",
    kgen_path: str = "kgen",
    source_dir: str = "",
    build_dir: str = "",
    cmake_file: str = "",
    output_dir: Optional[str] = None,
    package_name: str = "",
    invocation: str = "",
    assumed_size_arrays: Optional[dict] = None,
    env: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """
    Run KGen extraction with all necessary setup.

    This handles:
    1. Checking for editable pip install path conflicts
    2. Cleaning stale artifacts (strace.log, kgen.log)
    3. Pre-building so .mod files exist for include.ini generation
    4. Generating include.ini with proper module/source paths
    5. Generating a build wrapper script that injects kgen_utils.f90
    6. Running KGen
    7. Cleaning up injected files (restoring source tree)
    8. Locating the generated kernel/ and state/ directories

    Returns:
        Dict with keys: 'success', 'kernel_dir', 'state_dir', 'cmd', 'output',
                        'include_ini', 'cleaned', 'state_cases', 'warnings'
    """
    result = {
        "success": False,
        "kernel_dir": None,
        "state_dir": None,
        "cmd": "",
        "output": "",
        "include_ini": None,
        "cleaned": [],
        "state_cases": 0,
        "warnings": [],
    }

    # Step 1: Check for editable install conflicts
    if package_name or source_dir:
        project_dir = source_dir or os.path.dirname(os.path.abspath(source_file))
        install_warnings = check_editable_installs(project_dir, package_name)
        result["warnings"].extend(install_warnings)
        for w in install_warnings:
            warnings.warn(w, UserWarning, stacklevel=2)

    # Step 2: Clean stale artifacts
    result["cleaned"] = clean_kgen_artifacts()

    # Clean stale .kgen_org files (KGen's instrumented source backups).
    # These may contain pragmas from a previous extraction that would
    # contaminate this extraction — KGen reads all source files and
    # processes all pragmas it finds, including in .kgen_org files
    # that get restored to the source directory during state capture.
    if source_dir:
        for kgen_org in sorted(Path(source_dir).glob("*.kgen_org")):
            kgen_org.unlink()
            result["cleaned"].append(str(kgen_org))

    # Step 3: Run clean command to force full rebuild for strace
    if clean_cmd:
        subprocess.run(clean_cmd, shell=True, capture_output=True)

    # Step 4: Pre-build so .mod files exist for include.ini generation
    # KGen needs .mod files to resolve USE statements. They may have been
    # removed by the clean step above.
    if build_cmd:
        subprocess.run(build_cmd, shell=True, capture_output=True)

    # Step 5: Generate include.ini with proper paths
    if source_dir or build_dir:
        src_dirs = discover_source_dirs(source_dir) if source_dir else []
        mod_dirs = discover_mod_dirs(build_dir) if build_dir else []
        if src_dirs or mod_dirs:
            ini_path = generate_include_ini(src_dirs, mod_dirs)
            result["include_ini"] = ini_path

    # Step 6: Generate build wrapper script that injects kgen_utils.f90
    # into the project build (KGen bug workaround)
    wrapper_script = generate_state_capture_build_script(
        build_cmd=build_cmd,
        install_cmd=install_cmd,
        cmake_file=cmake_file,
        source_dir=source_dir,
        kernel_dir=output_dir or "kernel",
        assumed_size_arrays=assumed_size_arrays,
    )
    effective_build_cmd = f"bash {wrapper_script}"

    cmd = generate_kgen_command(
        source_file=source_file,
        build_cmd=effective_build_cmd,
        run_cmd=run_cmd,
        clean_cmd=clean_cmd,
        kgen_path=kgen_path,
        output_dir=output_dir,
        invocation=invocation,
    )
    result["cmd"] = cmd

    if dry_run:
        result["output"] = f"[DRY RUN] Would execute:\n  {cmd}"
        result["output"] += f"\n\nBuild wrapper script:\n{Path(wrapper_script).read_text()}"
        return result

    # Step 7: Run KGen
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
            timeout=1200,  # 20 minutes — extraction can be slow
        )
        result["output"] = proc.stdout + proc.stderr

        # Step 8: Locate output directories
        cwd = output_dir or "."
        for candidate_base in [cwd, str(Path(source_file).parent)]:
            kernel_dir = os.path.join(candidate_base, "kernel")
            state_dir = os.path.join(candidate_base, "state")

            if os.path.isdir(kernel_dir) and result["kernel_dir"] is None:
                result["kernel_dir"] = kernel_dir
            if os.path.isdir(state_dir) and result["state_dir"] is None:
                result["state_dir"] = state_dir

        # Count state data files (in kernel dir — that's where KGen puts them)
        if result["kernel_dir"]:
            result["state_cases"] = count_kernel_cases(result["kernel_dir"])
            result["success"] = True

            if result["state_cases"] == 0:
                result["output"] += (
                    "\n\nWARNING: Kernel generated but no state data captured.\n"
                    "Possible causes:\n"
                    "  1. The simulation didn't run (check run_cmd)\n"
                    "  2. The instrumented library wasn't installed where the simulation\n"
                    "     loads it from. Set install_cmd in vit.yaml to copy the built\n"
                    "     library to the install location.\n"
                    "  3. The call site wasn't reached during the simulation run.\n"
                    "  4. kgen_utils.f90 wasn't compiled into the shared library.\n"
                    "     Check that the build wrapper script ran correctly.\n"
                    "  5. An editable pip install (pip install -e .) may be pointing\n"
                    "     to a different checkout, causing the simulation to load an\n"
                    "     uninstrumented library. Run: pip show <package> and check\n"
                    "     'Editable project location' matches your working directory.\n"
                )

    except subprocess.TimeoutExpired:
        result["output"] = "ERROR: KGen extraction timed out after 1200 seconds"
    except Exception as e:
        result["output"] = f"ERROR: {e}"
    finally:
        # Step 9: Clean up kgen_utils.f90 injection (restore source tree)
        cleanup_result = cleanup_kgen_utils_injection(
            cmake_file=cmake_file,
            source_dir=source_dir,
        )
        if cleanup_result:
            result["cleaned"].extend(cleanup_result)

    return result


def count_kernel_cases(kernel_dir: str) -> int:
    """
    Count the number of KGen state data files in a directory.

    KGen state files are named like: <function>.0.0.1, <function>.0.0.2, etc.
    """
    if not os.path.isdir(kernel_dir):
        return 0

    count = 0
    for item in os.listdir(kernel_dir):
        # Match pattern: name.N.N.N (e.g., saturate.0.0.1)
        if re.match(r'.+\.\d+\.\d+\.\d+$', item):
            count += 1

    return count