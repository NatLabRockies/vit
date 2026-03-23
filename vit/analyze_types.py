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

from .confirm import ManualEdit
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
    manual_edits: list[ManualEdit] = field(default_factory=list)

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


# ---------------------------------------------------------------------------
# ALLOCATABLE size source scanning (Phase 4D)
# ---------------------------------------------------------------------------

@dataclass
class AllocatableFieldMapping:
    """Maps one ALLOCATABLE field to its runtime size source.

    Size classes:
        A — Size is a field on the same/another type (e.g., CntrPar%PC_GS_n)
        B — Size is a literal integer (e.g., 2)
        C — Size is a local variable or complex expression (needs EXIT_CONFIRM)
        D — Size is SIZE() of another field (e.g., SIZE(CntrPar%OL_Channels, DIM=1))
        E — Size is an array-indexed field (e.g., CntrPar%PerfTableSize(1))
    """
    field_name: str
    fortran_type: str        # e.g., "REAL(DbKi)"
    rank: int                # 1 or 2
    size_class: str          # "A", "B", "C", "D", "E"
    dim_sizes: list[str] = field(default_factory=list)  # per-dimension size expressions
    evidence: str = ""       # source line showing the ParseAry/ALLOCATE call
    needs_confirm: bool = False  # True for Type C


def scan_allocatable_sizes(
    source_dir: str,
    type_name: str,
    type_info: DerivedTypeInfo,
) -> list[AllocatableFieldMapping]:
    """
    Scan source files for ParseAry and ALLOCATE calls to map every
    ALLOCATABLE field in a type to its runtime size source.

    Searches all .f90/.F90 files in source_dir. For each ALLOCATABLE field
    in type_info, looks for:
    1. ParseAry calls: CALL ParseAry(..., Var%field, size_expr, ...)
    2. ALLOCATE calls: ALLOCATE(Var%field(dim1, dim2, ...))

    Args:
        source_dir: Root directory of Fortran sources
        type_name: Name of the derived type (e.g., "ControlParameters")
        type_info: Parsed type definition with field list

    Returns:
        List of AllocatableFieldMapping, one per ALLOCATABLE field found.
        Fields with no size source found are omitted.
    """
    source_path = Path(source_dir)
    alloc_fields = [
        f for f in type_info.fields if f.is_allocatable
    ]
    if not alloc_fields:
        return []

    # Read all source content once
    source_files = {}
    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            source_files[f90_file] = f90_file.read_text()

    mappings = []
    for af in alloc_fields:
        mapping = _scan_field_size(af, source_files)
        if mapping is not None:
            mappings.append(mapping)

    return mappings


def _scan_field_size(
    af: 'DerivedTypeField',
    source_files: dict,
) -> Optional[AllocatableFieldMapping]:
    """
    Scan all source files to find the size source for one ALLOCATABLE field.

    Checks ParseAry calls first (most common), then ALLOCATE calls.
    """
    field_name = af.name

    # --- Try ParseAry first ---
    # Two ParseAry call signatures in ROSCO:
    #   CALL ParseAry(FileLines, 'label', Var%field, size_expr, ...)
    #   CALL ParseAry(Unit, CurLine, 'label', Var%field, size_expr, ...)
    # In both cases, the field is followed by the size expression.
    # Use a general pattern: match Var%field anywhere in the call, capture the next arg.
    parseary_re = re.compile(
        r'CALL\s+ParseAry\s*\('
        r'.*?'                         # any preceding args
        r'\w+%' + re.escape(field_name) + r'\s*,'  # Var%field
        r'\s*([^,]+)',                 # next arg: size expression
        re.IGNORECASE,
    )

    for f90_file, content in source_files.items():
        for line_num, line in enumerate(content.split("\n"), 1):
            # Skip commented-out lines
            if line.lstrip().startswith("!"):
                continue
            m = parseary_re.search(line)
            if m:
                size_expr = m.group(1).strip()
                evidence = f"ParseAry(..., %{field_name}, {size_expr}, ...) in {f90_file.name}:{line_num}"
                size_class, needs_confirm = _classify_size_expr(size_expr)
                return AllocatableFieldMapping(
                    field_name=field_name,
                    fortran_type=af.fortran_type,
                    rank=_field_rank(af),
                    size_class=size_class,
                    dim_sizes=[size_expr],
                    evidence=evidence,
                    needs_confirm=needs_confirm,
                )

    # --- Try ALLOCATE ---
    # Pattern: ALLOCATE(Var%field(dim1)) or ALLOCATE(Var%field(dim1, dim2))
    # Use a pattern that handles one level of nested parens (e.g., PerfTableSize(2))
    allocate_re = re.compile(
        r'(?<!\!)\s*ALLOCATE\s*\(\s*\w+%' + re.escape(field_name) +
        r'\s*\(((?:[^()]*|\([^()]*\))*)\)\s*\)',
        re.IGNORECASE,
    )

    for f90_file, content in source_files.items():
        for line_num, line in enumerate(content.split("\n"), 1):
            # Skip commented-out lines
            stripped = line.lstrip()
            if stripped.startswith("!"):
                continue
            m = allocate_re.search(line)
            if m:
                dims_str = m.group(1).strip()
                dims = [d.strip() for d in dims_str.split(",")]
                evidence = f"ALLOCATE(%{field_name}({dims_str})) in {f90_file.name}:{line_num}"

                # Classify each dimension
                dim_classes = []
                any_confirm = False
                for d in dims:
                    sc, nc = _classify_size_expr(d)
                    dim_classes.append(sc)
                    if nc:
                        any_confirm = True

                # Overall class is the "hardest" dimension
                overall_class = _worst_class(dim_classes)

                return AllocatableFieldMapping(
                    field_name=field_name,
                    fortran_type=af.fortran_type,
                    rank=len(dims),
                    size_class=overall_class,
                    dim_sizes=dims,
                    evidence=evidence,
                    needs_confirm=any_confirm,
                )

    return None


def _classify_size_expr(expr: str) -> tuple[str, bool]:
    """
    Classify a size expression into a type (A-E) and whether it needs
    human confirmation.

    Returns:
        (size_class, needs_confirm)
    """
    expr = expr.strip()

    # Type B: literal integer
    if expr.isdigit():
        return ("B", False)

    # Type D: SIZE(Var%field) or SIZE(Var%field, DIM=N)
    if re.match(r'SIZE\s*\(', expr, re.IGNORECASE):
        return ("D", False)

    # Type E: Var%field(index) — array-indexed field reference
    if re.match(r'\w+%\w+\s*\(\s*\d+\s*\)', expr):
        return ("E", False)

    # Type A: Var%field — simple field reference (most common)
    if re.match(r'\w+%\w+$', expr):
        return ("A", False)

    # Type C: anything else (local variable, complex expression)
    return ("C", True)


def _field_rank(af: 'DerivedTypeField') -> int:
    """Determine the array rank of a field from its array_dims."""
    if not af.array_dims:
        return 0
    return af.array_dims.count(":") + af.array_dims.count(",")


def _worst_class(classes: list[str]) -> str:
    """Return the 'hardest' classification from a list (C > D > E > A > B)."""
    priority = {"C": 5, "D": 4, "E": 3, "A": 2, "B": 1}
    return max(classes, key=lambda c: priority.get(c, 0))


def format_allocatable_scan_report(
    type_name: str,
    mappings: list[AllocatableFieldMapping],
    unmapped_fields: list[str],
) -> str:
    """
    Format the allocatable scan results as a human-readable report.

    Args:
        type_name: Name of the type scanned
        mappings: Successfully mapped fields
        unmapped_fields: ALLOCATABLE fields with no size source found

    Returns:
        Formatted report string
    """
    lines = []
    lines.append(f"ALLOCATABLE Size Scan: {type_name}")
    lines.append("=" * 60)
    lines.append(f"  Mapped:   {len(mappings)}")
    lines.append(f"  Unmapped: {len(unmapped_fields)}")
    lines.append("")

    # Group by class
    by_class = {}
    for m in mappings:
        by_class.setdefault(m.size_class, []).append(m)

    class_labels = {
        "A": "Type A — field reference (fully automatable)",
        "B": "Type B — literal constant (trivial)",
        "C": "Type C — local variable (needs confirmation)",
        "D": "Type D — SIZE() of another field (automatable)",
        "E": "Type E — array-indexed field (automatable)",
    }

    for cls in ["A", "B", "D", "E", "C"]:
        fields = by_class.get(cls, [])
        if not fields:
            continue
        lines.append(f"{class_labels[cls]}")
        lines.append("-" * 60)
        for m in sorted(fields, key=lambda x: x.field_name):
            dims_str = " x ".join(m.dim_sizes)
            rank_str = f"{m.rank}D" if m.rank > 1 else "1D"
            lines.append(f"  {m.field_name} ({rank_str}): {dims_str}")
            lines.append(f"    {m.evidence}")
        lines.append("")

    if unmapped_fields:
        lines.append("Unmapped (no ParseAry/ALLOCATE found)")
        lines.append("-" * 60)
        for name in sorted(unmapped_fields):
            lines.append(f"  {name}")
        lines.append("")

    return "\n".join(lines)


def fix_character_allocatable(source_dir: str, type_name: str,
                              field_name: str, fixed_length: int = 1024,
                              dry_run: bool = True) -> FixResult:
    """
    Fix CHARACTER ALLOCATABLE fields by converting to fixed-length.

    Handles both forms:
      - CHARACTER(:), ALLOCATABLE :: field  (deferred-length scalar)
      - CHARACTER, DIMENSION(:), ALLOCATABLE :: field  (array of single chars)

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

    # Try character array first (more specific pattern):
    # CHARACTER, DIMENSION(:), ALLOCATABLE :: field
    new_content, changed = _rewrite_character_array_field(
        content, type_name, field_name, fixed_length
    )
    if changed:
        action = f"Rewrite {field_name} in TYPE {type_name}: "
        action += (
            f"CHARACTER, DIMENSION(:), ALLOCATABLE "
            f"→ CHARACTER :: {field_name}({fixed_length})"
        )
        result.changes.append(f"{type_file}: {action}")
    else:
        # Try deferred-length scalar: CHARACTER(:), ALLOCATABLE :: field
        new_content, changed = _rewrite_character_field(
            content, type_name, field_name, fixed_length
        )
        if changed:
            action = f"Rewrite {field_name} in TYPE {type_name}: "
            action += f"CHARACTER(:), ALLOCATABLE → CHARACTER({fixed_length})"
            result.changes.append(f"{type_file}: {action}")

    if changed:
        if not dry_run:
            type_file.write_text(new_content)
            result.files_modified.append(str(type_file))
    else:
        result.errors.append(
            f"Could not find CHARACTER ALLOCATABLE :: {field_name} "
            f"in TYPE {type_name} ({type_file})"
        )
        return result

    # Pass 2: Comment out ALLOCATE/DEALLOCATE of field
    # Pattern: ALLOCATE(Var%field...) or DEALLOCATE(Var%field...)
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
                # Skip already-commented lines
                if stripped.startswith("!"):
                    continue
                if alloc_re.match(stripped):
                    # Comment out the line, preserving indentation
                    indent_str = line[:len(line) - len(stripped)]
                    file_lines[i] = (
                        f"{indent_str}! {stripped}  "
                        f"! VIT: removed (fixed-length CHARACTER)"
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
                      field_name: str, dry_run: bool = True,
                      rewrites: Optional[dict[str, str]] = None) -> FixResult:
    """
    Fix COMPLEX fields by splitting into REAL re/im pairs.

    Scan-first approach:
      1. Scan all %field references and classify as auto or manual
      2. If manual lines exist and no --rewrite covers them → populate
         result.manual_edits and return (caller exits 11)
      3. If all manual lines are covered by rewrites (or none exist) →
         apply everything (type def + auto-rewrites + provided rewrites)

    Auto-rewrite patterns:
        - REAL(field) → field_re
        - AIMAG(field) → field_im
        - Zero/constant init → set both _re and _im
        - Binary I/O → split into _re and _im per element
        - Simple reference → _re
    Manual (needs --rewrite):
        - Lines with complex intrinsics (EXP, CMPLX, CONJG, etc.)

    Args:
        rewrites: Dict mapping "filename:line" → replacement line(s).
                  Multiple replacement lines are separated by \\n.
    """
    result = FixResult()
    source_path = Path(source_dir)
    rewrites = rewrites or {}

    # Verify the TYPE definition exists before scanning
    type_file = _find_type_definition_file(source_path, type_name)
    if not type_file:
        result.errors.append(f"Could not find TYPE {type_name} in {source_dir}")
        return result

    content = type_file.read_text()
    _, can_rewrite = _rewrite_complex_field(content, type_name, field_name)
    if not can_rewrite:
        result.errors.append(
            f"Could not find COMPLEX :: {field_name} in TYPE {type_name} ({type_file})"
        )
        return result

    # Intrinsics that signal expressions VIT can't auto-rewrite
    _complex_intrinsics_re = re.compile(
        r'\b(EXP|CMPLX|CONJG|ABS|SQRT|LOG)\s*\(',
        re.IGNORECASE,
    )

    # --- Phase 1: Scan all references and classify ---
    field_re = re.compile(r'%' + re.escape(field_name) + r'\b', re.IGNORECASE)
    unresolved_manual: list[ManualEdit] = []

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            file_content = f90_file.read_text()
            if not field_re.search(file_content):
                continue
            for i, line in enumerate(file_content.split("\n")):
                if not field_re.search(line):
                    continue
                stripped = line.lstrip()
                if stripped.startswith("!"):
                    continue
                if _complex_intrinsics_re.search(line):
                    key = f"{f90_file.name}:{i+1}"
                    if key not in rewrites:
                        unresolved_manual.append(ManualEdit(
                            file=str(f90_file),
                            line=i + 1,
                            reason=f"complex intrinsic: {_complex_intrinsics_re.search(line).group(1)}",
                            source_text=line.strip(),
                            cli_override=f"--rewrite '{key}=<replacement>'",
                        ))

    # If unresolved manual lines exist, return them — caller exits 11
    if unresolved_manual:
        result.manual_edits = unresolved_manual
        return result

    # --- Phase 2: All references resolved — apply changes ---

    # Rewrite the TYPE definition
    new_content, _ = _rewrite_complex_field(content, type_name, field_name)
    result.changes.append(
        f"{type_file}: COMPLEX :: {field_name} → REAL :: {field_name}_re + {field_name}_im"
    )
    if not dry_run:
        type_file.write_text(new_content)
        result.files_modified.append(str(type_file))

    # Rewrite all %field_name references
    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            file_content = f90_file.read_text()
            if not field_re.search(file_content):
                continue

            file_lines = file_content.split("\n")
            modified = False
            insertions = []

            for i, line in enumerate(file_lines):
                if not field_re.search(line):
                    continue

                stripped = line.lstrip()
                if stripped.startswith("!"):
                    continue

                original_line = line

                # Check if this line has a user-provided rewrite
                key = f"{f90_file.name}:{i+1}"
                if key in rewrites:
                    rewrite_lines = rewrites[key].split("\\n")
                    file_lines[i] = rewrite_lines[0]
                    for extra in rewrite_lines[1:]:
                        insertions.append((i, extra))
                    result.changes.append(
                        f"{f90_file}: Manual rewrite applied: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # --- Safe auto-rewrites ---

                # REAL() extraction: REAL(var%field...) → var%field_re...
                real_extract_re = re.compile(
                    r'REAL\s*\((\w+%' + re.escape(field_name)
                    + r'(?:\([^)]*\))?)\)',
                    re.IGNORECASE,
                )
                if real_extract_re.search(line):
                    def _real_to_re(m):
                        inner = m.group(1)
                        return re.sub(
                            re.escape(field_name), field_name + '_re',
                            inner, count=1, flags=re.IGNORECASE,
                        )
                    line = real_extract_re.sub(_real_to_re, line)
                    file_lines[i] = line
                    result.changes.append(
                        f"{f90_file}: REAL() → _re: {original_line.strip()}"
                    )
                    modified = True
                    continue

                # AIMAG() extraction: AIMAG(var%field...) → var%field_im...
                aimag_re = re.compile(
                    r'AIMAG\s*\((\w+%' + re.escape(field_name)
                    + r'(?:\([^)]*\))?)\)',
                    re.IGNORECASE,
                )
                if aimag_re.search(line):
                    def _aimag_to_im(m):
                        inner = m.group(1)
                        return re.sub(
                            re.escape(field_name), field_name + '_im',
                            inner, count=1, flags=re.IGNORECASE,
                        )
                    line = aimag_re.sub(_aimag_to_im, line)
                    file_lines[i] = line
                    result.changes.append(
                        f"{f90_file}: AIMAG() → _im: {original_line.strip()}"
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
                    re_line = (
                        f"{io_prefix} "
                        f"{var_expr.replace(field_name, field_name + '_re')}"
                    )
                    im_line = (
                        f"{io_prefix} "
                        f"{var_expr.replace(field_name, field_name + '_im')}"
                    )
                    file_lines[i] = re_line
                    insertions.append((i, im_line))
                    result.changes.append(
                        f"{f90_file}: I/O split → _re + _im: "
                        f"{original_line.strip()}"
                    )
                    modified = True
                    continue

                # Zero/constant initialization: var%field = 0.0D0
                zero_init_re = re.compile(
                    r'(\w+%' + re.escape(field_name) + r'(?:\([^)]*\))?)'
                    r'\s*=\s*([0-9]+\.?[0-9]*\w*)',
                    re.IGNORECASE,
                )
                m_zero = zero_init_re.search(line)
                if m_zero and not _complex_intrinsics_re.search(line):
                    re_line = re.sub(
                        re.escape(field_name), field_name + '_re',
                        line, count=1, flags=re.IGNORECASE,
                    )
                    im_line = re.sub(
                        re.escape(field_name), field_name + '_im',
                        line, count=1, flags=re.IGNORECASE,
                    )
                    file_lines[i] = re_line
                    insertions.append((i, im_line))
                    result.changes.append(
                        f"{f90_file}: Constant init → _re + _im: "
                        f"{original_line.strip()}"
                    )
                    modified = True
                    continue

                # Fallback: simple substitution → _re
                ref_re = re.compile(
                    r'(\w+%' + re.escape(field_name) + r'(?:\([^)]*\))?)',
                    re.IGNORECASE,
                )
                new_line = ref_re.sub(
                    lambda m: re.sub(
                        re.escape(field_name), field_name + '_re',
                        m.group(0), count=1, flags=re.IGNORECASE,
                    ),
                    line,
                )
                if new_line != line:
                    file_lines[i] = new_line
                    result.changes.append(
                        f"{f90_file}: → _re: {original_line.strip()}"
                    )
                    modified = True

            # Apply insertions in reverse order
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
        # Must have DIMENSION to distinguish from CHARACTER(:), ALLOCATABLE
        line_upper = line.upper()
        if (field_upper in line_upper
                and "CHARACTER" in line_upper
                and "DIMENSION" in line_upper
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
