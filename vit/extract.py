"""
KGen extraction orchestration.

Automates:
1. Finding the call site for a target function
2. Inserting KGen pragmas around it
3. Generating include.ini for module discovery
4. Cleaning stale artifacts
5. Running KGen with the correct build/run commands
6. Managing the resulting kernel/ and state/ directories

Encodes lessons learned from ROSCO debugging:
- KGen pragmas must use underscores: begin_callsite / end_callsite
- KGen pragmas must include the function name
- KGen strace often fails to discover -I/-J paths; include.ini must be pre-generated
- Stale strace.log causes KGen to reuse bad data; must clean before extraction
- Build must be clean so strace captures actual gfortran invocations
- The built library must be installed where the simulation loads it from
- KGen puts kernel/ and state/ in CWD, not relative to the source file
- Source files must be restored to original state on failure
"""

import os
import re
import shutil
import subprocess
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
    for artifact in ["strace.log", "kgen.log"]:
        path = os.path.join(working_dir, artifact)
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)

    # Don't remove include.ini — we generate it ourselves and want KGen to reuse it
    # Don't remove kernel/ or state/ — user may want to keep previous data

    return removed


def build_kgen_build_cmd(build_cmd: str, install_cmd: str = "") -> str:
    """
    Combine build and install commands for KGen's --cmd-build.

    KGen's state Makefile runs the build command, then runs the simulation.
    But the built library must be installed where the simulation expects it.

    Example: ROSCO build produces rosco/controller/build/libdiscon.so
    but the simulation loads rosco/lib/libdiscon.so. The install_cmd
    copies the library to the right place.

    Args:
        build_cmd: The base build command (e.g., "make -C build")
        install_cmd: Post-build command (e.g., "cp build/lib.so install/lib.so")

    Returns:
        Combined command string for --cmd-build
    """
    if install_cmd:
        return f"{build_cmd} && {install_cmd}"
    return build_cmd


def generate_kgen_command(
    source_file: str,
    build_cmd: str,
    run_cmd: str,
    clean_cmd: str = "",
    kgen_path: str = "kgen",
    output_dir: Optional[str] = None,
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
    output_dir: Optional[str] = None,
    env: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """
    Run KGen extraction with all necessary setup.

    This handles:
    1. Cleaning stale artifacts (strace.log, kgen.log)
    2. Ensuring the build is clean so strace captures compiler invocations
    3. Generating include.ini with proper module/source paths
    4. Combining build + install commands
    5. Running KGen
    6. Locating the generated kernel/ and state/ directories

    Returns:
        Dict with keys: 'success', 'kernel_dir', 'state_dir', 'cmd', 'output',
                        'include_ini', 'cleaned', 'state_cases'
    """
    # Combine build + install
    effective_build_cmd = build_kgen_build_cmd(build_cmd, install_cmd)

    cmd = generate_kgen_command(
        source_file=source_file,
        build_cmd=effective_build_cmd,
        run_cmd=run_cmd,
        clean_cmd=clean_cmd,
        kgen_path=kgen_path,
        output_dir=output_dir,
    )

    result = {
        "success": False,
        "kernel_dir": None,
        "state_dir": None,
        "cmd": cmd,
        "output": "",
        "include_ini": None,
        "cleaned": [],
        "state_cases": 0,
    }

    if dry_run:
        result["output"] = f"[DRY RUN] Would execute:\n  {cmd}"
        return result

    # Step 1: Clean stale artifacts
    result["cleaned"] = clean_kgen_artifacts()

    # Step 2: Run clean command to force full rebuild for strace
    if clean_cmd:
        subprocess.run(clean_cmd, shell=True, capture_output=True)

    # Step 3: Generate include.ini with proper paths
    if source_dir or build_dir:
        src_dirs = discover_source_dirs(source_dir) if source_dir else []
        mod_dirs = discover_mod_dirs(build_dir) if build_dir else []
        if src_dirs or mod_dirs:
            ini_path = generate_include_ini(src_dirs, mod_dirs)
            result["include_ini"] = ini_path

    # Step 4: Build first so .mod files exist for include.ini paths
    # (KGen needs .mod files to resolve USE statements, and they may
    # have been removed by the clean step above)
    if build_cmd:
        subprocess.run(build_cmd, shell=True, capture_output=True)
        # Regenerate include.ini now that .mod files exist
        if source_dir or build_dir:
            src_dirs = discover_source_dirs(source_dir) if source_dir else []
            mod_dirs = discover_mod_dirs(build_dir) if build_dir else []
            if src_dirs or mod_dirs:
                ini_path = generate_include_ini(src_dirs, mod_dirs)
                result["include_ini"] = ini_path

    # Step 5: Run KGen
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

        # Step 6: Locate output directories
        # KGen places kernel/ and state/ in CWD, not relative to source file
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
                )

    except subprocess.TimeoutExpired:
        result["output"] = "ERROR: KGen extraction timed out after 1200 seconds"
    except Exception as e:
        result["output"] = f"ERROR: {e}"

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