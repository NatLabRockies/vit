"""
Parse Fortran derived type definitions (TYPE blocks).

Extracts field information and checks BIND(C) compatibility for
determining whether a derived type can be mapped to a C struct
or must be handled as a passthrough argument.

Also provides auto-discovery (searching source directories for type
definitions), registry building (scanning function signatures), and
C struct header generation for BIND(C)-compatible types.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
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
    source_file: Optional[str] = None    # Absolute path to file containing this type

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

    def is_c_compatible_recursive(self, registry: dict) -> bool:
        """
        Recursively check C compatibility using a type registry.

        Like is_c_compatible, but for TYPE(X) fields, looks up X
        in the registry and checks its compatibility recursively.
        Unknown nested types are conservatively treated as incompatible.

        Args:
            registry: Dict mapping type_name → DerivedTypeInfo
        """
        if self.is_bind_c:
            return True
        for f in self.fields:
            if f.is_allocatable or f.is_pointer:
                return False
            if f.fortran_type.upper().startswith("TYPE("):
                nested_name = f.fortran_type[5:-1].strip()
                nested_info = None
                for k, v in registry.items():
                    if k.upper() == nested_name.upper():
                        nested_info = v
                        break
                if nested_info is None:
                    return False
                if not nested_info.is_c_compatible_recursive(registry):
                    return False
        return True


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


def _fortran_dims_to_c(dims: str) -> str:
    """Convert Fortran dimension spec to C array bracket syntax.

    "1024"  → "[1024]"
    "3,3"   → "[3][3]"
    "3,1"   → "[3][1]"
    """
    parts = [d.strip() for d in dims.split(",")]
    return "".join(f"[{p}]" for p in parts)


def find_and_parse_derived_type(type_name: str, source_dir: str) -> Optional[DerivedTypeInfo]:
    """
    Search a source directory for a TYPE definition and parse it.

    Globs all .f90/.F90 files in the directory tree.

    Args:
        type_name: Name of the derived type to find (case-insensitive)
        source_dir: Root directory to search

    Returns:
        DerivedTypeInfo if found, None otherwise
    """
    source_path = Path(source_dir)
    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            source = f90_file.read_text()
            info = parse_derived_type(source, type_name)
            if info:
                info.source_file = str(f90_file.resolve())
                return info
    return None


def build_derived_type_registry(sig, source_dir: str) -> dict:
    """
    Build a registry of derived types used in a function signature.

    Scans sig.args for TYPE(...) arguments, auto-parses each type
    definition from the source directory.

    Args:
        sig: FortranSignature (or any object with .args list of FortranArg)
        source_dir: Root directory to search for type definitions

    Returns:
        Dict mapping type_name → DerivedTypeInfo for all found types
    """
    registry = {}
    for arg in sig.args:
        if arg.is_derived_type:
            type_name = arg.derived_type_name
            if type_name and type_name.upper() not in {k.upper() for k in registry}:
                info = find_and_parse_derived_type(type_name, source_dir)
                if info:
                    registry[type_name] = info
    return registry


def generate_c_struct_header(type_info: DerivedTypeInfo,
                              kind_aliases: Optional[dict] = None,
                              registry: Optional[dict] = None,
                              emit_includes: bool = True) -> str:
    """
    Generate a C typedef struct header from a parsed derived type.

    Works for C-compatible types: primitives, fixed-size arrays,
    CHARACTER(N), and nested TYPE(X) fields (when X is in the registry
    and itself C-compatible).

    Args:
        type_info: Parsed derived type definition
        kind_aliases: Project-specific kind aliases for type resolution
        registry: Dict mapping type_name → DerivedTypeInfo for nested types
        emit_includes: If True, emit #include for nested type headers
                       (for separate-file mode in kernel_verify).
                       If False, skip includes (for inline mode in
                       integrate/translate where caller renders types
                       in dependency order).

    Returns:
        Complete C header file content with include guard
    """
    from .type_map import get_type_mapping

    guard = f"{type_info.name.upper()}_T_H"
    lines = []
    lines.append(f"// Auto-generated C struct for Fortran TYPE({type_info.name})")
    lines.append(f"// Generated by VIT (Verified Incremental Translator)")

    # Check for multi-dimensional arrays → add column-major note
    has_multidim = any(
        f.is_array and f.array_dims and "," in f.array_dims
        for f in type_info.fields
    )
    if has_multidim:
        lines.append(f"// Note: Fortran uses column-major order.")
        lines.append(f"//   Fortran P(i,j) maps to C P[j-1][i-1].")

    lines.append("")
    lines.append(f"#ifndef {guard}")
    lines.append(f"#define {guard}")
    lines.append("")

    # Emit #include for nested types
    if emit_includes and registry:
        for f in type_info.fields:
            if f.fortran_type.upper().startswith("TYPE("):
                nested_name = f.fortran_type[5:-1].strip()
                for k in registry:
                    if k.upper() == nested_name.upper():
                        lines.append(f'#include "{nested_name.lower()}_t.h"')
                        break
        # Add blank line after includes if any were emitted
        if any(line.startswith('#include') for line in lines):
            lines.append("")

    lines.append(f"typedef struct {{")

    for f in type_info.fields:
        # Handle nested TYPE(X) fields
        if f.fortran_type.upper().startswith("TYPE("):
            nested_name = f.fortran_type[5:-1].strip()
            lines.append(f"    {nested_name.lower()}_t {f.name};")
            continue

        mapping = get_type_mapping(f.fortran_type, kind_aliases)
        if mapping.character_length:
            lines.append(f"    {mapping.c_type} {f.name}[{mapping.character_length}];")
        elif f.is_array and f.array_dims:
            c_dims = _fortran_dims_to_c(f.array_dims)
            lines.append(f"    {mapping.c_type} {f.name}{c_dims};")
        else:
            lines.append(f"    {mapping.c_type} {f.name};")

    lines.append(f"}} {type_info.name.lower()}_t;")
    lines.append("")
    lines.append(f"#endif // {guard}")
    lines.append("")

    return "\n".join(lines)


def scan_all_derived_types(source_dir: str) -> list[DerivedTypeInfo]:
    """
    Scan a source directory and find every TYPE definition.

    Globs all .f90/.F90 files, finds TYPE definition start lines,
    parses each type, and returns a list of all discovered types.

    Args:
        source_dir: Root directory to search

    Returns:
        List of DerivedTypeInfo for every TYPE found
    """
    source_path = Path(source_dir)
    results = []
    seen_names = set()

    # Regex to find TYPE definition start lines
    type_start_re = re.compile(
        r'^\s*TYPE\b'
        r'(?:\s*,\s*(?:BIND\s*\(\s*C\s*\)|PUBLIC|PRIVATE|ABSTRACT|EXTENDS\s*\([^)]*\)))*'
        r'\s*(?:::)?\s*'
        r'(\w+)\s*$',
        re.IGNORECASE,
    )

    for pattern in ("**/*.f90", "**/*.F90"):
        for f90_file in sorted(source_path.glob(pattern)):
            source = f90_file.read_text()
            for line in source.split("\n"):
                # Strip comments
                stripped = line.strip()
                if "!" in stripped:
                    stripped = stripped[:stripped.index("!")]
                stripped = stripped.strip()

                m = type_start_re.match(stripped)
                if m:
                    type_name = m.group(1)
                    # Skip false positives (TYPE used as function keyword)
                    if type_name.upper() in ("IS", "GUARD"):
                        continue
                    name_upper = type_name.upper()
                    if name_upper not in seen_names:
                        info = parse_derived_type(source, type_name)
                        if info:
                            info.source_file = str(f90_file.resolve())
                            results.append(info)
                            seen_names.add(name_upper)

    return results


@dataclass
class FieldBlocker:
    """A field that prevents a derived type from being C-compatible."""
    field_name: str
    fortran_type: str
    reason: str          # e.g., "character_allocatable", "allocatable", "pointer"
    fix_suggestion: str  # Human-readable fix suggestion


def classify_field_blockers(type_info: DerivedTypeInfo,
                            kind_aliases: Optional[dict] = None,
                            registry: Optional[dict] = None) -> list[FieldBlocker]:
    """
    Identify which fields prevent a derived type from being C-compatible.

    Args:
        type_info: Parsed derived type definition
        kind_aliases: Project-specific kind aliases for type resolution
        registry: Optional dict mapping type_name → DerivedTypeInfo.
                  When provided, nested TYPE(X) fields are only reported
                  as blockers if X is NOT C-compatible in the registry.

    Returns:
        List of FieldBlocker for each incompatible field
    """
    from .type_map import normalize_fortran_type

    blockers = []
    for f in type_info.fields:
        normalized = normalize_fortran_type(f.fortran_type, kind_aliases).upper()

        # CHARACTER(:), ALLOCATABLE — special case
        if normalized.startswith("CHARACTER") and f.is_allocatable:
            blockers.append(FieldBlocker(
                field_name=f.name,
                fortran_type=f.fortran_type,
                reason="character_allocatable",
                fix_suggestion=(
                    f"Convert to fixed-length: CHARACTER(1024) :: {f.name}\n"
                    f"  Run: vit analyze-types --fix character "
                    f"--type {type_info.name} --field {f.name} --apply"
                ),
            ))
        elif f.is_allocatable:
            blockers.append(FieldBlocker(
                field_name=f.name,
                fortran_type=f.fortran_type,
                reason="allocatable",
                fix_suggestion=f"Remove ALLOCATABLE from {f.name} or use fixed-size array",
            ))
        elif f.is_pointer:
            blockers.append(FieldBlocker(
                field_name=f.name,
                fortran_type=f.fortran_type,
                reason="pointer",
                fix_suggestion=f"Replace POINTER with fixed-size array or scalar for {f.name}",
            ))
        elif normalized.startswith("TYPE("):
            nested_name = normalized[5:-1].strip()
            # With registry: only block if nested type is NOT compatible
            if registry:
                nested_info = None
                for k, v in registry.items():
                    if k.upper() == nested_name:
                        nested_info = v
                        break
                if nested_info is not None and nested_info.is_c_compatible_recursive(registry):
                    continue  # Compatible nested type — not a blocker
            blockers.append(FieldBlocker(
                field_name=f.name,
                fortran_type=f.fortran_type,
                reason="nested_type",
                fix_suggestion=f"Nested derived type {f.fortran_type} must also be C-compatible",
            ))
        elif normalized.startswith("COMPLEX"):
            blockers.append(FieldBlocker(
                field_name=f.name,
                fortran_type=f.fortran_type,
                reason="complex",
                fix_suggestion=f"COMPLEX fields require manual struct layout",
            ))

    return blockers


def order_types_by_dependency(types: list[DerivedTypeInfo]) -> list[DerivedTypeInfo]:
    """
    Topological sort: leaf types first, types with nested dependencies last.

    Types that have TYPE(X) fields depend on X. This ensures headers are
    emitted in an order where every dependency is defined before it's used.

    Args:
        types: List of DerivedTypeInfo to sort

    Returns:
        Sorted list (leaf types first)

    Raises:
        ValueError: If a dependency cycle is detected
    """
    name_map = {t.name.upper(): t for t in types}

    # Build adjacency: type → set of dependencies (nested TYPE names)
    deps: dict[str, set[str]] = {}
    for t in types:
        key = t.name.upper()
        deps[key] = set()
        for f in t.fields:
            if f.fortran_type.upper().startswith("TYPE("):
                nested = f.fortran_type[5:-1].strip().upper()
                if nested in name_map:
                    deps[key].add(nested)

    # Kahn's algorithm for topological sort
    in_degree = {k: 0 for k in deps}
    for k, d in deps.items():
        for dep in d:
            if dep in in_degree:
                in_degree[dep] = in_degree.get(dep, 0)  # ensure exists
                # dep is depended ON by k — but we want leaf-first,
                # so k depends on dep → k has higher order
                pass

    # Recompute: in_degree[X] = number of types that X depends on
    # Actually for topo sort: edges go from dependency to dependent.
    # We want: if A depends on B, B comes first.
    # So edge B → A, and in_degree counts incoming edges.
    in_degree = {k: len(d) for k, d in deps.items()}
    # Reverse adjacency: for each dep, which types depend on it
    rev: dict[str, list[str]] = {k: [] for k in deps}
    for k, d in deps.items():
        for dep in d:
            if dep in rev:
                rev[dep].append(k)

    queue = [k for k, deg in in_degree.items() if deg == 0]
    result = []
    while queue:
        # Sort for deterministic output
        queue.sort()
        node = queue.pop(0)
        result.append(name_map[node])
        for dependent in rev.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(types):
        sorted_names = {t.name.upper() for t in result}
        cycle_types = [t.name for t in types if t.name.upper() not in sorted_names]
        raise ValueError(f"Dependency cycle detected among types: {cycle_types}")

    return result
