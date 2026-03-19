"""
Build-verify-fix loop for Fortran source modifications.

After VIT modifies Fortran source (e.g., fixing CHARACTER(:), ALLOCATABLE),
downstream code may fail to compile. This module:

1. Runs the project's build command
2. Parses gfortran compiler errors
3. Applies fixes from a registry of known error patterns
4. Retries until the build succeeds or no more fixes can be applied

The fix registry is extensible — new patterns are added as VIT's type system
work encounters new downstream consequences.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class CompilerError:
    """A single error from gfortran output."""
    file: str
    line: int
    column: int
    severity: str       # "Error", "Warning", "Fatal Error"
    message: str
    source_line: str    # The source code line that errored
    raw_text: str       # Full multi-line error block


@dataclass
class ErrorFix:
    """A fix applied to resolve a compiler error."""
    error: CompilerError
    file: str
    line: int
    original_line: str
    fixed_line: str
    pattern_name: str
    description: str


@dataclass
class BuildFixResult:
    """Result of a build-and-fix cycle."""
    success: bool
    build_attempts: int
    fixes_applied: list[ErrorFix] = field(default_factory=list)
    remaining_errors: list[CompilerError] = field(default_factory=list)
    build_output: str = ""
    files_modified: list[str] = field(default_factory=list)


@dataclass
class FixPattern:
    """A registered pattern for automatically fixing compiler errors."""
    name: str
    description: str
    matcher: Callable[[CompilerError], bool]
    fixer: Callable[[CompilerError, str], Optional[ErrorFix]]


def parse_gfortran_errors(output: str) -> list[CompilerError]:
    """
    Parse gfortran error output into structured CompilerError objects.

    Handles the gfortran error format:
        file.f90:line:col:

           line | source code
                |     1
        Error: message text

    Also handles errors wrapped in cmake build output.
    """
    errors = []
    lines = output.split("\n")
    i = 0

    while i < len(lines):
        # Look for location line: file.f90:line:col:
        m = re.match(r'^(.+\.(?:[fF]90|[fF])):(\d+):(\d+):\s*$', lines[i])
        if m:
            file_path = m.group(1)
            line_num = int(m.group(2))
            col_num = int(m.group(3))

            block_start = i
            i += 1
            source_line = ""

            # Scan forward for the Error:/Warning: line
            while i < len(lines):
                # Capture source line (has line number + pipe)
                src_match = re.match(r'\s*\d+\s*\|(.+)', lines[i])
                if src_match:
                    source_line = src_match.group(1).strip()

                # Check for severity line
                sev_match = re.match(
                    r'^(Error|Warning|Fatal Error):\s*(.+)', lines[i]
                )
                if sev_match:
                    raw_text = "\n".join(lines[block_start:i + 1])
                    errors.append(CompilerError(
                        file=file_path,
                        line=line_num,
                        column=col_num,
                        severity=sev_match.group(1),
                        message=sev_match.group(2),
                        source_line=source_line,
                        raw_text=raw_text,
                    ))
                    break

                # Stop scanning if we hit another location line or end of output
                if re.match(r'^.+\.(?:[fF]90|[fF]):\d+:\d+:\s*$', lines[i]):
                    break

                i += 1
        i += 1

    return errors


def build_and_fix(
    build_cmd: str,
    source_dir: str,
    max_iterations: int = 5,
    fix_registry: Optional[list[FixPattern]] = None,
    dry_run: bool = False,
    compiler: str = "gfortran",
) -> BuildFixResult:
    """
    Build the project, parse errors, apply known fixes, and retry.

    Args:
        build_cmd: Shell command to build (from VitConfig.build_cmd)
        source_dir: Root directory of Fortran sources
        max_iterations: Maximum build-fix cycles
        fix_registry: Fix patterns to try. Defaults to DEFAULT_FIX_REGISTRY.
        dry_run: If True, report fixes without applying them
        compiler: Fortran compiler name (only "gfortran" is supported)
    """
    if compiler.lower() != "gfortran":
        import sys
        print(
            f"WARNING: build_fixer error parser only supports gfortran "
            f"(configured compiler: {compiler}). "
            f"Compiler errors may not be parsed correctly.",
            file=sys.stderr,
        )
    registry = fix_registry if fix_registry is not None else DEFAULT_FIX_REGISTRY
    all_fixes: list[ErrorFix] = []
    files_modified: set[str] = set()
    build_output = ""

    for attempt in range(1, max_iterations + 1):
        proc = subprocess.run(
            build_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        build_output = proc.stdout + "\n" + proc.stderr

        if proc.returncode == 0:
            return BuildFixResult(
                success=True,
                build_attempts=attempt,
                fixes_applied=all_fixes,
                build_output=build_output,
                files_modified=sorted(files_modified),
            )

        # Parse errors
        errors = parse_gfortran_errors(build_output)
        if not errors:
            return BuildFixResult(
                success=False,
                build_attempts=attempt,
                fixes_applied=all_fixes,
                build_output=build_output,
                files_modified=sorted(files_modified),
            )

        # Try to fix each error
        fixes_this_round: list[ErrorFix] = []
        fixed_locations: set[tuple[str, int]] = set()

        for error in errors:
            loc = (error.file, error.line)
            if loc in fixed_locations:
                continue
            for pattern in registry:
                if pattern.matcher(error):
                    file_content = Path(error.file).read_text()
                    fix = pattern.fixer(error, file_content)
                    if fix:
                        fixes_this_round.append(fix)
                        fixed_locations.add(loc)
                        break

        if not fixes_this_round:
            return BuildFixResult(
                success=False,
                build_attempts=attempt,
                fixes_applied=all_fixes,
                remaining_errors=errors,
                build_output=build_output,
                files_modified=sorted(files_modified),
            )

        # Apply fixes
        if not dry_run:
            _apply_fixes(fixes_this_round)

        all_fixes.extend(fixes_this_round)
        for fix in fixes_this_round:
            files_modified.add(fix.file)

    # Exhausted max_iterations
    errors = parse_gfortran_errors(build_output)
    return BuildFixResult(
        success=False,
        build_attempts=max_iterations,
        fixes_applied=all_fixes,
        remaining_errors=errors,
        build_output=build_output,
        files_modified=sorted(files_modified),
    )


def _apply_fixes(fixes: list[ErrorFix]) -> None:
    """Apply fixes to source files. Groups by file, applies in reverse line order."""
    fixes_by_file: dict[str, list[ErrorFix]] = {}
    for fix in fixes:
        fixes_by_file.setdefault(fix.file, []).append(fix)

    for filepath, file_fixes in fixes_by_file.items():
        content = Path(filepath).read_text()
        file_lines = content.split("\n")
        for fix in sorted(file_fixes, key=lambda f: f.line, reverse=True):
            idx = fix.line - 1
            if 0 <= idx < len(file_lines):
                file_lines[idx] = fix.fixed_line
        Path(filepath).write_text("\n".join(file_lines))


# ---------------------------------------------------------------------------
# Fix pattern: TRANSFER shape mismatch
# ---------------------------------------------------------------------------

def _match_transfer_shape_mismatch(error: CompilerError) -> bool:
    """Match gfortran 'Different shape for array assignment' errors."""
    return (
        error.severity.lower() in ("error", "fatal error")
        and "different shape" in error.message.lower()
        and "array assignment" in error.message.lower()
    )


def _fix_transfer_shape_mismatch(
    error: CompilerError, file_content: str
) -> Optional[ErrorFix]:
    """
    Fix TRANSFER(C_NULL_CHAR, array_var) → array_var = C_NULL_CHAR.

    When CHARACTER(:), ALLOCATABLE becomes CHARACTER(N), downstream code
    using TRANSFER(C_NULL_CHAR, mold_var) to initialize a character array
    breaks because the TRANSFER result has shape (1) but the array has
    shape (N). Scalar broadcast assignment fills the entire array correctly.
    """
    file_lines = file_content.split("\n")
    idx = error.line - 1
    if idx < 0 or idx >= len(file_lines):
        return None

    line = file_lines[idx]

    # Match: VAR = TRANSFER( C_NULL_CHAR, VAR )
    # The variable name appears on both sides of TRANSFER
    m = re.match(
        r'^(\s*)(\w+)(\s*=\s*)TRANSFER\s*\(\s*C_NULL_CHAR\s*,\s*'
        r'\2\s*\)(.*)',
        line,
        re.IGNORECASE,
    )
    if not m:
        return None

    indent = m.group(1)
    var_name = m.group(2)
    equals = m.group(3)
    trailing = m.group(4).rstrip()

    fixed_line = f"{indent}{var_name}{equals}C_NULL_CHAR{trailing}"

    return ErrorFix(
        error=error,
        file=error.file,
        line=error.line,
        original_line=line,
        fixed_line=fixed_line,
        pattern_name="transfer_shape_mismatch",
        description=(
            f"Replace TRANSFER(C_NULL_CHAR, {var_name}) with "
            f"{var_name} = C_NULL_CHAR (scalar broadcast)"
        ),
    )


# ---------------------------------------------------------------------------
# Fix pattern: LOGICAL/INTEGER type mismatch
# ---------------------------------------------------------------------------

def _match_logical_type_mismatch(error: CompilerError) -> bool:
    """Match gfortran 'Type mismatch' errors involving LOGICAL/INTEGER."""
    return (
        error.severity.lower() in ("error", "fatal error")
        and "type mismatch" in error.message.lower()
        and "logical" in error.message.lower()
    )


def _fix_logical_type_mismatch(
    error: CompilerError, file_content: str
) -> Optional[ErrorFix]:
    """
    Fix LOGICAL/INTEGER type mismatch by wrapping the argument with (/= 0).

    When a LOGICAL field is converted to INTEGER(C_INT), call sites that
    pass it to functions expecting LOGICAL need the expression wrapped
    with (/= 0) to produce a LOGICAL from the integer comparison.
    """
    file_lines = file_content.split("\n")
    idx = error.line - 1
    if idx < 0 or idx >= len(file_lines):
        return None

    line = file_lines[idx]
    col = error.column - 1  # 0-indexed

    # Search for word%word pattern near the error column
    best_match = None
    best_start = None
    for start in range(max(0, col - 20), min(len(line), col + 20)):
        m = re.match(r'(\w+%\w+)', line[start:])
        if m:
            if best_match is None or abs(start - col) < abs(best_start - col):
                best_match = m.group(1)
                best_start = start

    if best_match is None:
        return None

    expr = best_match
    start = best_start

    # Don't wrap if already wrapped
    after = line[start + len(expr):].lstrip()
    if after.startswith('/= 0') or after.startswith('== 0'):
        return None

    new_line = line[:start] + '(' + expr + ' /= 0)' + line[start + len(expr):]

    return ErrorFix(
        error=error,
        file=error.file,
        line=error.line,
        original_line=line,
        fixed_line=new_line,
        pattern_name="logical_type_mismatch",
        description=(
            f"Wrap {expr} with (/= 0) for LOGICAL/INTEGER type mismatch"
        ),
    )


# ---------------------------------------------------------------------------
# Default fix registry
# ---------------------------------------------------------------------------

DEFAULT_FIX_REGISTRY = [
    FixPattern(
        name="transfer_shape_mismatch",
        description=(
            "Fix TRANSFER(C_NULL_CHAR, array) shape mismatch "
            "after CHARACTER(:) → CHARACTER(N)"
        ),
        matcher=_match_transfer_shape_mismatch,
        fixer=_fix_transfer_shape_mismatch,
    ),
    FixPattern(
        name="logical_type_mismatch",
        description=(
            "Fix LOGICAL/INTEGER type mismatch by wrapping with (/= 0) "
            "after LOGICAL → INTEGER(C_INT)"
        ),
        matcher=_match_logical_type_mismatch,
        fixer=_fix_logical_type_mismatch,
    ),
]
