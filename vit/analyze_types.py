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
    analyses = []
    for ti in all_types:
        blockers = classify_field_blockers(ti, kind_aliases)
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
