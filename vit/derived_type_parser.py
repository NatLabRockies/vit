"""
Parse Fortran derived type definitions (TYPE blocks).

Extracts field information and checks BIND(C) compatibility for
determining whether a derived type can be mapped to a C struct
or must be handled as a passthrough argument.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DerivedTypeField:
    """A single field within a Fortran derived type."""
    name: str
    fortran_type: str
    is_allocatable: bool = False
    is_pointer: bool = False
    is_array: bool = False
    array_dims: Optional[str] = None


@dataclass
class DerivedTypeInfo:
    """Parsed information about a Fortran derived type definition."""
    name: str
    fields: list[DerivedTypeField] = field(default_factory=list)
    is_bind_c: bool = False           # TYPE, BIND(C) :: Name
    source_module: Optional[str] = None  # Which module defines it

    @property
    def is_c_compatible(self) -> bool:
        """
        Can be mapped to a C struct.

        True if explicitly BIND(C), or if all fields are primitive
        (no ALLOCATABLE, no POINTER, no nested derived types).
        """
        if self.is_bind_c:
            return True
        return all(
            not f.is_allocatable
            and not f.is_pointer
            and not f.fortran_type.upper().startswith("TYPE(")
            for f in self.fields
        )


def parse_derived_type(source: str, type_name: str) -> Optional[DerivedTypeInfo]:
    """
    Find and parse a TYPE :: type_name definition in Fortran source text.

    Args:
        source: Fortran source code (full file or module)
        type_name: Name of the derived type to find (case-insensitive)

    Returns:
        DerivedTypeInfo if found, None otherwise
    """
    lines = source.split("\n")
    name_upper = type_name.upper()

    # Find the TYPE definition line
    # Patterns: TYPE :: Name, TYPE, BIND(C) :: Name, TYPE Name
    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*BIND\s*\(\s*C\s*\))?'  # optional BIND(C)
        r'(?:\s*,\s*\w+)*'                   # optional other attrs
        r'\s*(?:::)?\s*'
        r'(' + re.escape(name_upper) + r')\s*$',
        re.IGNORECASE,
    )

    start_idx = None
    is_bind_c = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Remove comments
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]
        stripped = stripped.strip()

        m = type_start_re.match(stripped)
        if m:
            start_idx = i
            is_bind_c = bool(re.search(r'BIND\s*\(\s*C\s*\)', stripped, re.IGNORECASE))
            break

    if start_idx is None:
        return None

    # Parse fields until END TYPE
    fields = []
    end_type_re = re.compile(r'^\s*END\s+TYPE\b', re.IGNORECASE)

    for i in range(start_idx + 1, len(lines)):
        line = lines[i].strip()
        line_upper = line.upper()

        # Remove comments
        if "!" in line:
            comment_idx = line.index("!")
            line = line[:comment_idx].strip()
            line_upper = line.upper()

        if not line_upper:
            continue

        if end_type_re.match(line_upper):
            break

        # Skip SEQUENCE, PRIVATE, CONTAINS, PROCEDURE lines
        if line_upper in ("SEQUENCE", "PRIVATE", "PUBLIC") or \
           line_upper.startswith("CONTAINS") or \
           line_upper.startswith("PROCEDURE"):
            continue

        # Parse field declaration
        parsed = _parse_field_declaration(line, line_upper)
        if parsed:
            fields.extend(parsed)

    # Try to find enclosing module
    source_module = None
    for i in range(start_idx, -1, -1):
        m = re.match(r'\s*MODULE\s+(\w+)', lines[i], re.IGNORECASE)
        if m and m.group(1).upper() not in ("PROCEDURE", "FUNCTION", "SUBROUTINE"):
            source_module = m.group(1)
            break

    return DerivedTypeInfo(
        name=type_name,
        fields=fields,
        is_bind_c=is_bind_c,
        source_module=source_module,
    )


def _parse_field_declaration(line: str, line_upper: str) -> Optional[list[DerivedTypeField]]:
    """
    Parse a field declaration inside a TYPE block.

    Returns list of DerivedTypeField or None if not a valid declaration.
    """
    # Match type declarations
    type_patterns = [
        r'^(REAL(?:\s*\([^)]*\))?)',
        r'^(INTEGER(?:\s*\([^)]*\))?)',
        r'^(DOUBLE\s+PRECISION)',
        r'^(LOGICAL(?:\s*\([^)]*\))?)',
        r'^(CHARACTER(?:\s*\([^)]*\))?)',
        r'^(COMPLEX(?:\s*\([^)]*\))?)',
        r'^(TYPE\s*\([^)]*\))',
    ]

    type_str = None
    rest = None

    for pattern in type_patterns:
        m = re.match(pattern, line_upper)
        if m:
            type_str = line[:m.end(1)].strip()
            rest_upper = line_upper[m.end():].strip()
            rest_orig = line[m.end():].strip()
            break

    if type_str is None:
        return None

    # Parse attributes (use uppercase for matching)
    is_allocatable = False
    is_pointer = False
    has_dimension = False
    dim_spec = None

    if "::" in rest_upper:
        attr_part, _ = rest_upper.split("::", 1)
        _, var_part = rest_orig.split("::", 1)
    else:
        attr_part = ""
        var_part = rest_orig

    attr_part = attr_part.strip().lstrip(",").strip()
    if attr_part:
        attrs = attr_part.upper()
        if "ALLOCATABLE" in attrs:
            is_allocatable = True
        if "POINTER" in attrs:
            is_pointer = True
        dim_match = re.search(r'DIMENSION\s*\(([^)]*)\)', attrs)
        if dim_match:
            has_dimension = True
            dim_spec = dim_match.group(1)

    # Parse variable names
    results = []
    var_part = var_part.strip()

    for var_decl in _split_commas(var_part):
        var_decl = var_decl.strip()
        if not var_decl:
            continue

        local_dims = dim_spec
        local_is_array = has_dimension

        paren_match = re.match(r'(\w+)\s*\(([^)]*)\)', var_decl)
        if paren_match:
            var_name = paren_match.group(1)
            local_dims = paren_match.group(2)
            local_is_array = True
        else:
            name_match = re.match(r'(\w+)', var_decl)
            if name_match:
                var_name = name_match.group(1)
            else:
                continue

        results.append(DerivedTypeField(
            name=var_name,
            fortran_type=type_str,
            is_allocatable=is_allocatable,
            is_pointer=is_pointer,
            is_array=local_is_array,
            array_dims=local_dims,
        ))

    return results if results else None


def _split_commas(s: str) -> list[str]:
    """Split on commas, respecting parentheses."""
    parts = []
    depth = 0
    current = ""
    for ch in s:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts
