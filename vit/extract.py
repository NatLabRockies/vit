"""
KGen extraction orchestration.

Automates:
1. Finding the call site for a target function
2. Inserting KGen pragmas around it
3. Running KGen with the correct build/run commands
4. Managing the resulting kernel/ and state/ directories
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
    filtered = [l for l in lines if not l.strip().startswith("!$kgen")]
    Path(filepath).write_text("\n".join(filtered))


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

    This encodes the lessons learned from ROSCO extraction:
    - Proper quoting of build/run commands
    - Clean command for reproducible extraction

    Args:
        source_file: Path to the Fortran file with KGen pragma
        build_cmd: Command to build the project
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
    kgen_path: str = "kgen",
    output_dir: Optional[str] = None,
    env: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """
    Run KGen extraction.

    Returns:
        Dict with keys: 'success', 'kernel_dir', 'state_dir', 'cmd', 'output'
    """
    cmd = generate_kgen_command(
        source_file=source_file,
        build_cmd=build_cmd,
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
    }

    if dry_run:
        result["output"] = f"[DRY RUN] Would execute: {cmd}"
        return result

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
            timeout=600,
        )
        result["output"] = proc.stdout + proc.stderr

        # Check for kernel/ and state/ directories
        base_dir = output_dir or str(Path(source_file).parent)
        kernel_dir = os.path.join(base_dir, "kernel")
        state_dir = os.path.join(base_dir, "state")

        if os.path.isdir(kernel_dir):
            result["kernel_dir"] = kernel_dir
            result["success"] = True
        if os.path.isdir(state_dir):
            result["state_dir"] = state_dir

    except subprocess.TimeoutExpired:
        result["output"] = "ERROR: KGen extraction timed out after 600 seconds"
    except Exception as e:
        result["output"] = f"ERROR: {e}"

    return result


def count_kernel_cases(state_dir: str) -> int:
    """Count the number of KGen verification cases in a state directory."""
    if not os.path.isdir(state_dir):
        return 0

    # KGen state files are typically named like 0.0.1, 0.0.2, etc.
    # or in subdirectories
    count = 0
    for item in os.listdir(state_dir):
        item_path = os.path.join(state_dir, item)
        if os.path.isdir(item_path):
            # Count files in subdirectories
            count += len([f for f in os.listdir(item_path) if not f.startswith(".")])
        elif re.match(r'\d+\.\d+\.\d+', item):
            count += 1

    return max(count, 0)