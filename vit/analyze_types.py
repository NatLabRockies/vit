"""
Analyze derived types for C compatibility and apply automated fixes.

Provides the `vit analyze-types` command: scans all Fortran TYPE definitions,
classifies which fields block C compatibility, and offers automated fixes
(currently: CHARACTER(:), ALLOCATABLE → CHARACTER(N)).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .derived_type_parser import (
    DerivedTypeInfo,
    FieldBlocker,
    classify_field_blockers,
    scan_all_derived_types,
)


@dataclass
class TypeAnalysis:
    """Analysis result for a single derived type."""
    type_info: DerivedTypeInfo
    blockers: list[FieldBlocker]
    source_file: Optional[str]

    @property
    def is_compatible(self) -> bool:
        return len(self.blockers) == 0


def analyze_all_types(source_dir: str,
                      kind_aliases: Optional[dict] = None) -> list['TypeAnalysis']:
    """
    Scan a source directory and analyze all derived types for C compatibility.

    Args:
        source_dir: Root directory to search for Fortran sources
        kind_aliases: Project-specific kind aliases

    Returns:
        List of TypeAnalysis, one per discovered type
    """
    all_types = scan_all_derived_types(source_dir)
    # Build a registry so classify_field_blockers can resolve nested types
    registry = {ti.name: ti for ti in all_types}
    analyses = []
    for ti in all_types:
        blockers = classify_field_blockers(ti, kind_aliases, registry=registry)
        analyses.append(TypeAnalysis(
            type_info=ti,
            blockers=blockers,
            source_file=ti.source_file,
        ))
    return analyses


def format_analysis_report(analyses: list[TypeAnalysis],
                           verbose: bool = False) -> str:
    """
    Format the type analysis as a human-readable report.

    Args:
        analyses: List of TypeAnalysis from analyze_all_types()
        verbose: If True, show field-level details for all types

    Returns:
        Formatted report string
    """
    compatible = [a for a in analyses if a.is_compatible]
    incompatible = [a for a in analyses if not a.is_compatible]

    lines = []
    lines.append(f"Derived Type Analysis")
    lines.append(f"{'=' * 50}")
    lines.append(f"Total types:      {len(analyses)}")
    lines.append(f"C-compatible:     {len(compatible)}")
    lines.append(f"Not compatible:   {len(incompatible)}")
    lines.append("")

    if incompatible:
        lines.append(f"Incompatible Types")
        lines.append(f"{'-' * 50}")
        for a in sorted(incompatible, key=lambda x: x.type_info.name):
            src = a.source_file or "unknown"
            lines.append(f"  {a.type_info.name}  ({src})")
            for b in a.blockers:
                lines.append(f"    {b.field_name}: {b.reason}")
                lines.append(f"      {b.fix_suggestion}")
            lines.append("")

    if verbose and compatible:
        lines.append(f"Compatible Types")
        lines.append(f"{'-' * 50}")
        for a in sorted(compatible, key=lambda x: x.type_info.name):
            src = a.source_file or "unknown"
            n_fields = len(a.type_info.fields)
            lines.append(f"  {a.type_info.name}  ({n_fields} fields, {src})")
        lines.append("")

    return "\n".join(lines)


@dataclass
class FixResult:
    """Result of an automated fix operation."""
    files_modified: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def infer_character_length(source_dir: str, type_name: str,
                           field_name: str) -> Optional[tuple[int, str]]:
    """
    Try to infer the intended length of a CHARACTER(:), ALLOCATABLE field
    by searching for ALLOCATE calls in the source directory.

    Searches both active and commented-out ALLOCATE lines.

    Args:
        source_dir: Root directory of Fortran sources
        type_name: Name of the derived type (e.g., "ErrorVariables")
        field_name: Name of the CHARACTER field (e.g., "ErrMsg")

    Returns:
        (inferred_length, evidence_string) if ALLOCATE found and evaluable,
        (None, evidence_string) if ALLOCATE found but expression not evaluable,
        or None if no ALLOCATE found at all
    """
    source_path = Path(source_dir)

    # Match: ALLOCATE(Var%field_name(size_expr))
    # Also match commented-out: ! ALLOCATE(Var%field_name(size_expr))
    alloc_re = re.compile(
        rf'^\s*!?\s*ALLOCATE\s*\(\s*\w+%{re.escape(field_name)}\s*\(([^)]+)\)\s*\)',
        re.IGNORECASE,
    )

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            content = f90_file.read_text()
            for line_num, line in enumerate(content.split("\n"), 1):
                m = alloc_re.match(line.strip())
                if m:
                    size_expr = m.group(1).strip()
                    rel_path = f90_file.name
                    evidence = f"ALLOCATE(%{field_name}({size_expr})) in {rel_path}:{line_num}"
                    length = _evaluate_size_expression(size_expr, content)
                    return (length, evidence)

    return None


def _evaluate_size_expression(expr: str, source_content: str) -> Optional[int]:
    """
    Try to evaluate a simple Fortran size expression to an integer.

    Handles:
    - Literal integers: "1024" → 1024
    - Simple subtraction: "N-1" where N is a PARAMETER → N-1
    - Simple addition: "N+1" → N+1
    """
    expr = expr.strip()

    # Literal integer
    if expr.isdigit():
        return int(expr)

    # variable - N or variable + N
    m = re.match(r'^(\w+)\s*([+-])\s*(\d+)$', expr)
    if m:
        var_name = m.group(1)
        op = m.group(2)
        offset = int(m.group(3))
        var_value = _find_parameter_value(var_name, source_content)
        if var_value is not None:
            return var_value + offset if op == "+" else var_value - offset

    # Single variable
    if re.match(r'^\w+$', expr):
        var_value = _find_parameter_value(expr, source_content)
        if var_value is not None:
            return var_value

    return None


def _find_parameter_value(var_name: str, source_content: str) -> Optional[int]:
    """
    Search source for a PARAMETER declaration or simple assignment of a variable.

    Matches:
    - INTEGER, PARAMETER :: var = 1025
    - INTEGER(4), PARAMETER :: var = 1025
    """
    param_re = re.compile(
        rf'^\s*INTEGER(?:\s*\([^)]*\))?\s*,\s*PARAMETER\s*::\s*'
        rf'{re.escape(var_name)}\s*=\s*(\d+)',
        re.IGNORECASE | re.MULTILINE,
    )
    m = param_re.search(source_content)
    if m:
        return int(m.group(1))
    return None


def fix_character_allocatable(source_dir: str, type_name: str,
                              field_name: str, fixed_length: int = 1024,
                              dry_run: bool = True) -> FixResult:
    """
    Fix CHARACTER(:), ALLOCATABLE fields by converting to CHARACTER(N).

    Two-pass fix:
      Pass 1: In the TYPE definition, rewrite the field declaration.
      Pass 2: In all source files, comment out ALLOCATE/DEALLOCATE of the field.

    Args:
        source_dir: Root directory of Fortran sources
        type_name: Name of the derived type (e.g., "ErrorVariables")
        field_name: Name of the CHARACTER field (e.g., "ErrMsg")
        fixed_length: Fixed length to use (default 1024)
        dry_run: If True, report changes without modifying files

    Returns:
        FixResult with details of changes made
    """
    result = FixResult()
    source_path = Path(source_dir)

    # Pass 1: Find and rewrite the TYPE definition
    type_file = _find_type_definition_file(source_path, type_name)
    if not type_file:
        result.errors.append(f"Could not find TYPE {type_name} in {source_dir}")
        return result

    content = type_file.read_text()
    new_content, changed = _rewrite_character_field(
        content, type_name, field_name, fixed_length
    )
    if changed:
        action = f"Rewrite {field_name} in TYPE {type_name}: "
        action += f"CHARACTER(:), ALLOCATABLE → CHARACTER({fixed_length})"
        result.changes.append(f"{type_file}: {action}")
        if not dry_run:
            type_file.write_text(new_content)
            result.files_modified.append(str(type_file))
    else:
        result.errors.append(
            f"Could not find CHARACTER(:), ALLOCATABLE :: {field_name} "
            f"in TYPE {type_name} ({type_file})"
        )
        return result

    # Pass 2: Comment out ALLOCATE/DEALLOCATE of field
    # Pattern: ALLOCATE(Var%field...) or DEALLOCATE(Var%field...)
    alloc_re = re.compile(
        rf'^\s*(ALLOCATE|DEALLOCATE)\s*\([^)]*%{re.escape(field_name)}\b[^)]*\)',
        re.IGNORECASE,
    )

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            file_content = f90_file.read_text()
            file_lines = file_content.split("\n")
            modified = False

            for i, line in enumerate(file_lines):
                stripped = line.lstrip()
                # Skip already-commented lines
                if stripped.startswith("!"):
                    continue
                if alloc_re.match(stripped):
                    # Comment out the line, preserving indentation
                    indent = line[:len(line) - len(stripped)]
                    file_lines[i] = f"{indent}! {stripped}  ! VIT: removed (fixed-length CHARACTER)"
                    result.changes.append(
                        f"{f90_file}: Commented out: {stripped.strip()}"
                    )
                    modified = True

            if modified:
                if not dry_run:
                    f90_file.write_text("\n".join(file_lines))
                    if str(f90_file) not in result.files_modified:
                        result.files_modified.append(str(f90_file))

    return result


def _find_type_definition_file(source_path: Path, type_name: str) -> Optional[Path]:
    """Find the file containing a TYPE definition."""
    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE))*'
        r'\s*(?:::)?\s*'
        + re.escape(type_name.upper()) + r'\s*$',
        re.IGNORECASE,
    )
    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            content = f90_file.read_text()
            for line in content.split("\n"):
                stripped = line.strip()
                if "!" in stripped:
                    stripped = stripped[:stripped.index("!")]
                stripped = stripped.strip()
                if type_start_re.match(stripped):
                    return f90_file
    return None


def fix_logical_field(source_dir: str, type_name: str,
                      field_name: str, dry_run: bool = True) -> FixResult:
    """
    Fix LOGICAL fields by converting to INTEGER(C_INT).

    Two-pass fix:
      Pass 1: In the TYPE definition, rewrite LOGICAL :: field → INTEGER(C_INT) :: field
      Pass 2: In all source files, rewrite %field references by context:
        - Assignment .True./.False. → 1/0
        - IF conditions → add /= 0
        - .NOT. → (field == 0)
        - Function arguments → wrap with (field /= 0)
        - Binary I/O → no change
    """
    result = FixResult()
    source_path = Path(source_dir)

    # Pass 1: Find and rewrite the TYPE definition
    type_file = _find_type_definition_file(source_path, type_name)
    if not type_file:
        result.errors.append(f"Could not find TYPE {type_name} in {source_dir}")
        return result

    content = type_file.read_text()
    new_content, changed = _rewrite_logical_field(content, type_name, field_name)
    if changed:
        result.changes.append(
            f"{type_file}: LOGICAL :: {field_name} → INTEGER(C_INT) :: {field_name}"
        )
        if not dry_run:
            type_file.write_text(new_content)
            result.files_modified.append(str(type_file))
    else:
        result.errors.append(
            f"Could not find LOGICAL :: {field_name} in TYPE {type_name} ({type_file})"
        )
        return result

    # Pass 2: Rewrite all %field_name references in source files
    field_re = re.compile(r'%' + re.escape(field_name) + r'\b', re.IGNORECASE)

    # Context detection patterns
    io_re = re.compile(r'^\s*(WRITE|READ)\s*\(', re.IGNORECASE)
    assign_true_re = re.compile(
        r'(\w+%' + re.escape(field_name) + r'\s*=\s*)\.True\.',
        re.IGNORECASE,
    )
    assign_false_re = re.compile(
        r'(\w+%' + re.escape(field_name) + r'\s*=\s*)\.False\.',
        re.IGNORECASE,
    )
    if_bare_re = re.compile(
        r'((?:ELSE\s*)?IF\s*\(\s*)(\w+%' + re.escape(field_name) + r')(\s*\))',
        re.IGNORECASE,
    )
    not_re = re.compile(
        r'\.NOT\.\s*(\w+%' + re.escape(field_name) + r')\b',
        re.IGNORECASE,
    )
    arg_re = re.compile(
        r'(\w+%' + re.escape(field_name) + r')\b',
        re.IGNORECASE,
    )

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            file_content = f90_file.read_text()
            if not field_re.search(file_content):
                continue

            file_lines = file_content.split("\n")
            modified = False

            for i, line in enumerate(file_lines):
                if not field_re.search(line):
                    continue

                stripped = line.lstrip()
                # Skip already-commented lines
                if stripped.startswith("!"):
                    continue

                # Binary I/O — no change needed
                if io_re.match(stripped):
                    continue

                original_line = line

                # Assignment .True. → 1
                if assign_true_re.search(line):
                    line = assign_true_re.sub(r'\g<1>1', line)
                    file_lines[i] = line
                    result.changes.append(
                        f"{f90_file}: .True. → 1: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # Assignment .False. → 0
                if assign_false_re.search(line):
                    line = assign_false_re.sub(r'\g<1>0', line)
                    file_lines[i] = line
                    result.changes.append(
                        f"{f90_file}: .False. → 0: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # IF/ELSEIF bare condition
                if if_bare_re.search(line):
                    line = if_bare_re.sub(r'\1\2 /= 0\3', line)
                    file_lines[i] = line
                    result.changes.append(
                        f"{f90_file}: IF condition → /= 0: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # .NOT. pattern
                if not_re.search(line):
                    line = not_re.sub(r'(\1 == 0)', line)
                    file_lines[i] = line
                    result.changes.append(
                        f"{f90_file}: .NOT. → (== 0): {original_line.strip()}"
                    )
                    modified = True
                    continue

                # Function argument — wrap with (/= 0)
                new_line = arg_re.sub(r'(\1 /= 0)', line)
                if new_line != line:
                    file_lines[i] = new_line
                    result.changes.append(
                        f"{f90_file}: Argument wrap → (/= 0): {original_line.strip()}"
                    )
                    modified = True

            if modified and not dry_run:
                f90_file.write_text("\n".join(file_lines))
                if str(f90_file) not in result.files_modified:
                    result.files_modified.append(str(f90_file))

    return result


def _rewrite_logical_field(content: str, type_name: str,
                           field_name: str) -> tuple[str, bool]:
    """
    Rewrite a LOGICAL field to INTEGER(C_INT) in a TYPE block.

    Returns (new_content, changed).
    """
    lines = content.split("\n")
    name_upper = type_name.upper()
    field_upper = field_name.upper()

    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE))*'
        r'\s*(?:::)?\s*'
        + re.escape(name_upper) + r'\s*$',
        re.IGNORECASE,
    )
    end_type_re = re.compile(r'^\s*END\s+TYPE\b', re.IGNORECASE)

    in_type = False
    changed = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        clean = stripped
        if "!" in clean:
            clean = clean[:clean.index("!")]
        clean = clean.strip()

        if not in_type:
            if type_start_re.match(clean):
                in_type = True
            continue

        if end_type_re.match(clean.upper()):
            break

        # Look for LOGICAL :: field_name
        line_upper = line.upper()
        if field_upper in line_upper and "LOGICAL" in line_upper:
            indent = line[:len(line) - len(line.lstrip())]
            comment = ""
            if "!" in stripped:
                comment_idx = stripped.index("!")
                comment = " " + stripped[comment_idx:]

            lines[i] = (
                f"{indent}INTEGER(C_INT)"
                f"                :: {field_name}"
                f"{comment}"
            )
            changed = True
            break

    return "\n".join(lines), changed


def fix_complex_field(source_dir: str, type_name: str,
                      field_name: str, dry_run: bool = True) -> FixResult:
    """
    Fix COMPLEX fields by splitting into REAL re/im pairs.

    Pass 1: In the TYPE definition, replace COMPLEX :: field(dims)
            with REAL :: field_re(dims) + REAL :: field_im(dims)
    Pass 2: Rewrite all %field references:
        - Zero init → set both _re and _im
        - EXP(complexI * expr) → COS/SIN decomposition
        - REAL(field) → field_re
        - Binary I/O → split into _re and _im per element
        - Output data → use _re
    """
    result = FixResult()
    source_path = Path(source_dir)

    # Pass 1: Find and rewrite the TYPE definition
    type_file = _find_type_definition_file(source_path, type_name)
    if not type_file:
        result.errors.append(f"Could not find TYPE {type_name} in {source_dir}")
        return result

    content = type_file.read_text()
    new_content, changed = _rewrite_complex_field(content, type_name, field_name)
    if changed:
        result.changes.append(
            f"{type_file}: COMPLEX :: {field_name} → REAL :: {field_name}_re + {field_name}_im"
        )
        if not dry_run:
            type_file.write_text(new_content)
            result.files_modified.append(str(type_file))
    else:
        result.errors.append(
            f"Could not find COMPLEX :: {field_name} in TYPE {type_name} ({type_file})"
        )
        return result

    # Pass 2: Rewrite all %field_name references
    field_re = re.compile(r'%' + re.escape(field_name) + r'\b', re.IGNORECASE)

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            file_content = f90_file.read_text()
            if not field_re.search(file_content):
                continue

            file_lines = file_content.split("\n")
            modified = False
            insertions = []  # (line_index, new_line_text) to insert AFTER

            for i, line in enumerate(file_lines):
                if not field_re.search(line):
                    continue

                stripped = line.lstrip()
                if stripped.startswith("!"):
                    continue

                original_line = line
                indent = line[:len(line) - len(stripped)]

                # REAL() extraction: REAL(var%field(K)) → var%field_re(K)
                real_re = re.compile(
                    r'REAL\s*\((\w+%' + re.escape(field_name) + r'(?:\([^)]*\))?)\)',
                    re.IGNORECASE,
                )
                if real_re.search(line):
                    line = real_re.sub(
                        lambda m: m.group(1).replace(
                            field_name, field_name + '_re'
                        ) if field_name in m.group(1) else m.group(0),
                        line,
                    )
                    # Case-preserving replacement
                    line = re.sub(
                        r'REAL\s*\((\w+)%' + re.escape(field_name) + r'(_re)(\([^)]*\))?\)',
                        r'\1%' + field_name + r'_re\3',
                        line, flags=re.IGNORECASE,
                    )
                    file_lines[i] = line
                    result.changes.append(f"{f90_file}: REAL() → _re: {original_line.strip()}")
                    modified = True
                    continue

                # Euler formula: var%field(K) = var%field(K) + amp * EXP(complexI * (expr))
                # The inner expr may contain nested parens like AWC_angle(K)
                euler_re = re.compile(
                    r'(\w+%' + re.escape(field_name) + r'\((\w+)\))\s*=\s*'
                    r'(\w+%' + re.escape(field_name) + r'\(\2\))\s*\+\s*'
                    r'(\w+)\s*\*\s*EXP\s*\(\s*\w+\s*\*\s*'
                    r'\(([^()]*(?:\([^()]*\))[^()]*)\)\s*\)',
                    re.IGNORECASE,
                )
                m_euler = euler_re.search(line)
                if m_euler:
                    var_idx = m_euler.group(2)      # K
                    amp = m_euler.group(4)           # amp
                    angle_expr = m_euler.group(5)    # AWC_angle(K)
                    # Get the variable prefix (e.g., LocalVar)
                    prefix_match = re.match(r'(\w+)%', m_euler.group(1))
                    var_prefix = prefix_match.group(1) if prefix_match else ""

                    re_line = (
                        f"{indent}{var_prefix}%{field_name}_re({var_idx}) = "
                        f"{var_prefix}%{field_name}_re({var_idx}) + "
                        f"{amp} * COS({angle_expr})"
                    )
                    im_line = (
                        f"{indent}{var_prefix}%{field_name}_im({var_idx}) = "
                        f"{var_prefix}%{field_name}_im({var_idx}) + "
                        f"{amp} * SIN({angle_expr})"
                    )
                    file_lines[i] = re_line
                    insertions.append((i, im_line))
                    result.changes.append(f"{f90_file}: Euler → COS/SIN: {original_line.strip()}")
                    modified = True
                    continue

                # Zero initialization: var%field = 0.0D0
                zero_init_re = re.compile(
                    r'(\w+%' + re.escape(field_name) + r')\s*=\s*(0\.0\w*)',
                    re.IGNORECASE,
                )
                m_zero = zero_init_re.search(line)
                if m_zero and 'EXP' not in line.upper():
                    var_expr = m_zero.group(1)
                    zero_val = m_zero.group(2)
                    re_line = line.replace(var_expr, var_expr.replace(
                        field_name, field_name + '_re'
                    ))
                    im_line = line.replace(var_expr, var_expr.replace(
                        field_name, field_name + '_im'
                    ))
                    file_lines[i] = re_line
                    insertions.append((i, im_line))
                    result.changes.append(
                        f"{f90_file}: Zero init → _re + _im: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # Binary I/O per element: WRITE/READ ... var%field(N)
                io_re = re.compile(
                    r'^(\s*(?:WRITE|READ)\s*\([^)]*\))\s*'
                    r'(\w+%' + re.escape(field_name) + r'\((\w+)\))',
                    re.IGNORECASE,
                )
                m_io = io_re.match(line)
                if m_io:
                    io_prefix = m_io.group(1)
                    var_expr = m_io.group(2)
                    re_line = f"{io_prefix} {var_expr.replace(field_name, field_name + '_re')}"
                    im_line = f"{io_prefix} {var_expr.replace(field_name, field_name + '_im')}"
                    file_lines[i] = re_line
                    insertions.append((i, im_line))
                    result.changes.append(
                        f"{f90_file}: I/O split → _re + _im: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # Output data assignment: LocalVarOutData(N) = var%field(K)
                # Just use _re
                outdata_re = re.compile(
                    r'(\w+%' + re.escape(field_name) + r'(?:\([^)]*\))?)',
                    re.IGNORECASE,
                )
                new_line = outdata_re.sub(
                    lambda m: m.group(0).replace(field_name, field_name + '_re')
                    if field_name.lower() in m.group(0).lower() else m.group(0),
                    line,
                )
                if new_line != line:
                    file_lines[i] = new_line
                    result.changes.append(
                        f"{f90_file}: → _re: {original_line.strip()}"
                    )
                    modified = True

            # Apply insertions in reverse order to preserve line numbers
            for insert_after, new_line_text in reversed(insertions):
                file_lines.insert(insert_after + 1, new_line_text)

            if modified and not dry_run:
                f90_file.write_text("\n".join(file_lines))
                if str(f90_file) not in result.files_modified:
                    result.files_modified.append(str(f90_file))

    # Remove complexI declaration if no longer needed
    _remove_complexi_decl(source_path, field_name, result, dry_run)

    return result


def _remove_complexi_decl(source_path: Path, field_name: str,
                          result: FixResult, dry_run: bool) -> None:
    """Remove COMPLEX :: complexI = (0.0, 1.0) local declaration if present."""
    complexi_re = re.compile(
        r'^\s*COMPLEX\s*\([^)]*\)\s*::\s*complexI\s*=\s*\(.*\)',
        re.IGNORECASE | re.MULTILINE,
    )
    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            content = f90_file.read_text()
            if not complexi_re.search(content):
                continue
            lines = content.split("\n")
            modified = False
            for i, line in enumerate(lines):
                if complexi_re.match(line):
                    indent = line[:len(line) - len(line.lstrip())]
                    lines[i] = f"{indent}! {line.lstrip()}  ! VIT: removed (complex split)"
                    result.changes.append(
                        f"{f90_file}: Commented out complexI declaration"
                    )
                    modified = True
            if modified and not dry_run:
                f90_file.write_text("\n".join(lines))
                if str(f90_file) not in result.files_modified:
                    result.files_modified.append(str(f90_file))


def _rewrite_complex_field(content: str, type_name: str,
                           field_name: str) -> tuple[str, bool]:
    """
    Rewrite a COMPLEX field to two REAL fields (_re, _im) in a TYPE block.

    Returns (new_content, changed).
    """
    lines = content.split("\n")
    name_upper = type_name.upper()
    field_upper = field_name.upper()

    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE))*'
        r'\s*(?:::)?\s*'
        + re.escape(name_upper) + r'\s*$',
        re.IGNORECASE,
    )
    end_type_re = re.compile(r'^\s*END\s+TYPE\b', re.IGNORECASE)

    in_type = False
    changed = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        clean = stripped
        if "!" in clean:
            clean = clean[:clean.index("!")]
        clean = clean.strip()

        if not in_type:
            if type_start_re.match(clean):
                in_type = True
            continue

        if end_type_re.match(clean.upper()):
            break

        # Look for COMPLEX :: field_name or COMPLEX(kind) :: field_name
        line_upper = line.upper()
        if field_upper in line_upper and "COMPLEX" in line_upper:
            indent = line[:len(line) - len(line.lstrip())]
            comment = ""
            if "!" in stripped:
                comment_idx = stripped.index("!")
                comment = " " + stripped[comment_idx:]

            # Extract the kind: COMPLEX(DbKi) → REAL(DbKi)
            kind_match = re.search(r'COMPLEX\s*\(([^)]+)\)', line, re.IGNORECASE)
            kind_str = kind_match.group(1) if kind_match else "8"

            # Extract array dims if present
            dims_match = re.search(
                re.escape(field_name) + r'\s*\(([^)]+)\)', line, re.IGNORECASE
            )
            dims_str = f"({dims_match.group(1)})" if dims_match else ""

            re_line = (
                f"{indent}REAL({kind_str})"
                f"                 :: {field_name}_re{dims_str}"
                f"         {comment}".rstrip()
            )
            im_line = (
                f"{indent}REAL({kind_str})"
                f"                 :: {field_name}_im{dims_str}"
                f"         ! Imaginary part"
            )
            lines[i] = re_line
            lines.insert(i + 1, im_line)
            changed = True
            break

    return "\n".join(lines), changed


def fix_character_array_allocatable(source_dir: str, type_name: str,
                                    field_name: str, fixed_length: int = 1024,
                                    dry_run: bool = True) -> FixResult:
    """
    Fix CHARACTER, DIMENSION(:), ALLOCATABLE fields by converting to CHARACTER :: field(N).

    Two-pass fix:
      Pass 1: In the TYPE definition, rewrite the field declaration.
      Pass 2: In all source files, comment out ALLOCATE/DEALLOCATE of the field.
    """
    result = FixResult()
    source_path = Path(source_dir)

    # Pass 1: Find and rewrite the TYPE definition
    type_file = _find_type_definition_file(source_path, type_name)
    if not type_file:
        result.errors.append(f"Could not find TYPE {type_name} in {source_dir}")
        return result

    content = type_file.read_text()
    new_content, changed = _rewrite_character_array_field(
        content, type_name, field_name, fixed_length,
    )
    if changed:
        result.changes.append(
            f"{type_file}: CHARACTER, DIMENSION(:), ALLOCATABLE :: {field_name} "
            f"→ CHARACTER :: {field_name}({fixed_length})"
        )
        if not dry_run:
            type_file.write_text(new_content)
            result.files_modified.append(str(type_file))
    else:
        result.errors.append(
            f"Could not find CHARACTER, DIMENSION(:), ALLOCATABLE :: {field_name} "
            f"in TYPE {type_name} ({type_file})"
        )
        return result

    # Pass 2: Comment out ALLOCATE/DEALLOCATE of field
    alloc_re = re.compile(
        rf'^\s*(ALLOCATE|DEALLOCATE)\s*\([^)]*%{re.escape(field_name)}\b[^)]*\)',
        re.IGNORECASE,
    )

    for pat in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pat)):
            file_content = f90_file.read_text()
            file_lines = file_content.split("\n")
            modified = False

            for i, line in enumerate(file_lines):
                stripped = line.lstrip()
                if stripped.startswith("!"):
                    continue
                if alloc_re.match(stripped):
                    indent_str = line[:len(line) - len(stripped)]
                    file_lines[i] = (
                        f"{indent_str}! {stripped}  "
                        f"! VIT: removed (fixed-length CHARACTER array)"
                    )
                    result.changes.append(
                        f"{f90_file}: Commented out: {stripped.strip()}"
                    )
                    modified = True

            if modified:
                if not dry_run:
                    f90_file.write_text("\n".join(file_lines))
                    if str(f90_file) not in result.files_modified:
                        result.files_modified.append(str(f90_file))

    return result


def _rewrite_character_array_field(content: str, type_name: str,
                                   field_name: str,
                                   fixed_length: int) -> tuple[str, bool]:
    """
    Rewrite CHARACTER, DIMENSION(:), ALLOCATABLE :: field
    to CHARACTER :: field(N) in a TYPE block.

    Returns (new_content, changed).
    """
    lines = content.split("\n")
    name_upper = type_name.upper()
    field_upper = field_name.upper()

    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE))*'
        r'\s*(?:::)?\s*'
        + re.escape(name_upper) + r'\s*$',
        re.IGNORECASE,
    )
    end_type_re = re.compile(r'^\s*END\s+TYPE\b', re.IGNORECASE)

    in_type = False
    changed = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        clean = stripped
        if "!" in clean:
            clean = clean[:clean.index("!")]
        clean = clean.strip()

        if not in_type:
            if type_start_re.match(clean):
                in_type = True
            continue

        if end_type_re.match(clean.upper()):
            break

        # Look for CHARACTER, DIMENSION(:), ALLOCATABLE :: field_name
        line_upper = line.upper()
        if (field_upper in line_upper
                and "CHARACTER" in line_upper
                and "ALLOCATABLE" in line_upper):
            indent = line[:len(line) - len(line.lstrip())]
            comment = ""
            if "!" in stripped:
                comment_idx = stripped.index("!")
                comment = " " + stripped[comment_idx:]

            lines[i] = (
                f"{indent}CHARACTER"
                f"                         :: {field_name}({fixed_length})"
                f"{comment}"
            )
            changed = True
            break

    return "\n".join(lines), changed


def fix_bind_c(source_dir: str, type_name: str,
               dry_run: bool = True) -> FixResult:
    """
    Add BIND(C) attribute to a TYPE and remove default initializers.

    1. Add BIND(C) to TYPE declaration
    2. Remove default initializers (e.g., = 0) from fields — BIND(C)
       types cannot have them in Fortran. SAVE'd module variables get
       zero-initialized by the runtime, so explicit = 0 is redundant.
    """
    result = FixResult()
    source_path = Path(source_dir)

    type_file = _find_type_definition_file(source_path, type_name)
    if not type_file:
        result.errors.append(f"Could not find TYPE {type_name} in {source_dir}")
        return result

    content = type_file.read_text()
    lines = content.split("\n")
    name_upper = type_name.upper()

    type_start_re = re.compile(
        r'^(\s*TYPE\b)'
        r'((?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE))*)'
        r'(\s*(?:::)?\s*)'
        + re.escape(name_upper) + r'\s*$',
        re.IGNORECASE,
    )
    end_type_re = re.compile(r'^\s*END\s+TYPE\b', re.IGNORECASE)

    in_type = False
    type_start_idx = None
    modified = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        clean = stripped
        if "!" in clean:
            clean = clean[:clean.index("!")]
        clean = clean.strip()

        if not in_type:
            m = type_start_re.match(clean)
            if m:
                in_type = True
                type_start_idx = i
                # Add BIND(C) if not already present
                if not re.search(r'BIND\s*\(\s*C\s*\)', line, re.IGNORECASE):
                    # Insert BIND(C) after TYPE
                    indent = line[:len(line) - len(line.lstrip())]
                    # Reconstruct: TYPE, BIND(C), PUBLIC :: Name
                    attrs = m.group(2).strip()
                    new_attrs = ", BIND(C)" + (attrs if attrs else "")
                    # Find the existing attrs portion
                    lines[i] = re.sub(
                        r'(TYPE\b)((?:\s*,\s*(?:PUBLIC|PRIVATE))*)'
                        r'(\s*(?:::)?\s*' + re.escape(type_name) + r')',
                        r'\1, BIND(C)\2\3',
                        lines[i],
                        flags=re.IGNORECASE,
                    )
                    result.changes.append(
                        f"{type_file}: Added BIND(C) to TYPE {type_name}"
                    )
                    modified = True
            continue

        if end_type_re.match(clean.upper()):
            break

        # Remove default initializers: field = value → field
        # Match: TYPE :: name = value (with optional trailing comment)
        init_re = re.compile(
            r'^(\s*\w+(?:\s*\([^)]*\))?\s*(?:,\s*\w+)*\s*::\s*\w+(?:\s*\([^)]*\))?)\s*=\s*[^!]+',
            re.IGNORECASE,
        )
        if '=' in line and '::' in line:
            # Split at :: to get the part after (field_name = value)
            before_colons, after_colons = line.split('::', 1)
            # Check if there's a = in the field part (not in the type spec)
            field_part = after_colons
            # Strip trailing comment
            comment = ""
            if "!" in field_part:
                comment_idx = field_part.index("!")
                comment = field_part[comment_idx:]
                field_part = field_part[:comment_idx]

            if '=' in field_part:
                # Remove the = value part
                field_name_part = field_part.split('=')[0].rstrip()
                new_line = f"{before_colons}:: {field_name_part.strip()}"
                if comment:
                    # Pad to align comment
                    new_line = new_line.ljust(len(before_colons) + 2 + 30) + comment
                lines[i] = new_line
                result.changes.append(
                    f"{type_file}: Removed default initializer: {line.strip()}"
                )
                modified = True

    if modified and not dry_run:
        type_file.write_text("\n".join(lines))
        result.files_modified.append(str(type_file))

    return result


def infer_character_array_length(source_dir: str, type_name: str,
                                 field_name: str) -> Optional[tuple[int, str]]:
    """
    Try to infer the intended size of a CHARACTER DIMENSION(:) ALLOCATABLE field
    by searching for ALLOCATE calls.

    Similar to infer_character_length() but for CHARACTER array fields.
    """
    source_path = Path(source_dir)

    # Match: ALLOCATE(Var%field_name(size_expr))
    # Also match commented-out versions
    alloc_re = re.compile(
        rf'^\s*!?\s*ALLOCATE\s*\(\s*\w+%{re.escape(field_name)}\s*\(([^)]+)\)\s*\)',
        re.IGNORECASE,
    )

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            content = f90_file.read_text()
            for line_num, line in enumerate(content.split("\n"), 1):
                m = alloc_re.match(line.strip())
                if m:
                    size_expr = m.group(1).strip()
                    rel_path = f90_file.name
                    evidence = (
                        f"ALLOCATE(%{field_name}({size_expr})) "
                        f"in {rel_path}:{line_num}"
                    )
                    length = _evaluate_size_expression(size_expr, content)
                    return (length, evidence)

    return None


def _rewrite_character_field(content: str, type_name: str,
                             field_name: str, fixed_length: int) -> tuple[str, bool]:
    """
    Rewrite a CHARACTER(:), ALLOCATABLE field to CHARACTER(N) in a TYPE block.

    Returns (new_content, changed).
    """
    lines = content.split("\n")
    name_upper = type_name.upper()
    field_upper = field_name.upper()

    # Find the TYPE block
    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE))*'
        r'\s*(?:::)?\s*'
        + re.escape(name_upper) + r'\s*$',
        re.IGNORECASE,
    )
    end_type_re = re.compile(r'^\s*END\s+TYPE\b', re.IGNORECASE)

    in_type = False
    changed = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        clean = stripped
        if "!" in clean:
            clean = clean[:clean.index("!")]
        clean = clean.strip()

        if not in_type:
            if type_start_re.match(clean):
                in_type = True
            continue

        if end_type_re.match(clean.upper()):
            break

        # Look for CHARACTER(:), ALLOCATABLE :: field_name
        line_upper = line.upper()
        if (field_upper in line_upper
                and "CHARACTER" in line_upper
                and "ALLOCATABLE" in line_upper):
            # Rewrite: preserve indentation and trailing comment
            indent = line[:len(line) - len(line.lstrip())]
            comment = ""
            if "!" in stripped:
                comment_idx = stripped.index("!")
                comment = " " + stripped[comment_idx:]

            lines[i] = (
                f"{indent}CHARACTER({fixed_length})"
                f"                :: {field_name}"
                f"{comment}"
            )
            changed = True
            break

    return "\n".join(lines), changed
