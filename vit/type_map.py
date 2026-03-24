"""
Deterministic Fortran ↔ C/C++ type mapping.

This is the mechanical core that makes interface generation a non-LLM task.
Every Fortran type has exactly one C equivalent for ISO_C_BINDING interop.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TypeMapping:
    """A complete mapping between Fortran and C/C++ types."""
    fortran_type: str          # e.g., "REAL(ReKi)" or "TYPE(ErrorVariables)"
    fortran_kind: str          # e.g., "ReKi"
    c_kind: str                # e.g., "C_DOUBLE"
    c_type: str                # e.g., "double" or "ErrorVariables"
    iso_c_type: str            # e.g., "REAL(C_DOUBLE)" or "TYPE(C_PTR)"
    byte_size: int             # e.g., 8
    is_derived: bool = False   # True for TYPE(...) args
    is_c_compatible: bool = True  # False → passthrough (not included in C interface)
    is_view_type: bool = False   # True for pointer+size view pattern (ALLOCATABLE types)
    character_length: Optional[int] = None  # For CHARACTER(N), the fixed length N


# Standard Fortran kind → C mappings
# These cover the types used in ROSCO and most scientific Fortran codes
FORTRAN_TO_C = {
    # Real types
    "REAL": TypeMapping("REAL", "", "C_FLOAT", "float", "REAL(C_FLOAT)", 4),
    "REAL(4)": TypeMapping("REAL(4)", "4", "C_FLOAT", "float", "REAL(C_FLOAT)", 4),
    "REAL(8)": TypeMapping("REAL(8)", "8", "C_DOUBLE", "double", "REAL(C_DOUBLE)", 8),
    "REAL(16)": TypeMapping("REAL(16)", "16", "C_LONG_DOUBLE", "long double", "REAL(C_LONG_DOUBLE)", 16),
    "DOUBLE PRECISION": TypeMapping("DOUBLE PRECISION", "", "C_DOUBLE", "double", "REAL(C_DOUBLE)", 8),

    # Integer types
    "INTEGER": TypeMapping("INTEGER", "", "C_INT", "int", "INTEGER(C_INT)", 4),
    "INTEGER(4)": TypeMapping("INTEGER(4)", "4", "C_INT", "int", "INTEGER(C_INT)", 4),
    "INTEGER(8)": TypeMapping("INTEGER(8)", "8", "C_LONG_LONG", "long long", "INTEGER(C_LONG_LONG)", 8),
    "INTEGER(2)": TypeMapping("INTEGER(2)", "2", "C_SHORT", "short", "INTEGER(C_SHORT)", 2),
    "INTEGER(1)": TypeMapping("INTEGER(1)", "1", "C_SIGNED_CHAR", "signed char", "INTEGER(C_SIGNED_CHAR)", 1),

    # Logical
    "LOGICAL": TypeMapping("LOGICAL", "", "C_BOOL", "int", "LOGICAL(C_BOOL)", 4),
    "LOGICAL(4)": TypeMapping("LOGICAL(4)", "4", "C_BOOL", "int", "LOGICAL(C_BOOL)", 4),

    # Character (single)
    "CHARACTER": TypeMapping("CHARACTER", "", "C_CHAR", "char", "CHARACTER(C_CHAR)", 1),
}

# Project-specific kind aliases (configurable per-project)
# These map symbolic kind names to their underlying numeric kinds
DEFAULT_KIND_ALIASES = {
    # NWTC Library / OpenFAST / ROSCO kinds
    "ReKi": "REAL(4)",       # Real kind (C_FLOAT = single precision)
    "DbKi": "REAL(8)",       # Double kind
    "SiKi": "REAL(4)",       # Single kind
    "IntKi": "INTEGER(4)",   # Integer kind
    "B1Ki": "INTEGER(1)",    # Byte kind

    # ISO_C_BINDING kind names
    "C_INT": "INTEGER(4)",
    "C_FLOAT": "REAL(4)",
    "C_DOUBLE": "REAL(8)",

    # Common Fortran kinds
    "dp": "REAL(8)",
    "sp": "REAL(4)",
    "qp": "REAL(16)",
    "i4": "INTEGER(4)",
    "i8": "INTEGER(8)",

    # Selected kinds (common patterns)
    "SELECTED_REAL_KIND(15)": "REAL(8)",
    "SELECTED_REAL_KIND(6)": "REAL(4)",
    "SELECTED_INT_KIND(9)": "INTEGER(4)",
    "SELECTED_INT_KIND(18)": "INTEGER(8)",
}


def normalize_fortran_type(type_str: str, kind_aliases: Optional[dict] = None) -> str:
    """
    Normalize a Fortran type declaration to a canonical form.

    Examples:
        "REAL(ReKi)" → "REAL(8)"  (with default aliases)
        "real(kind=8)" → "REAL(8)"
        "DOUBLE PRECISION" → "DOUBLE PRECISION"
        "INTEGER(IntKi)" → "INTEGER(4)"
    """
    aliases = {**DEFAULT_KIND_ALIASES, **(kind_aliases or {})}
    t = type_str.strip().upper()

    # Derived types pass through unchanged (no kind resolution needed)
    if t.startswith("TYPE("):
        return t

    # Handle "kind=" prefix: REAL(KIND=8) → REAL(8)
    t = t.replace("KIND=", "")

    # Extract base type and kind parameter
    if "(" in t:
        base = t[:t.index("(")].strip()
        kind = t[t.index("(") + 1:t.rindex(")")].strip()

        # Resolve kind alias (case-insensitive lookup)
        for alias_key, alias_val in aliases.items():
            if kind.upper() == alias_key.upper():
                return alias_val

        # Already numeric
        return f"{base}({kind})"

    return t


def get_type_mapping(fortran_type: str, kind_aliases: Optional[dict] = None,
                     derived_types: Optional[dict] = None) -> TypeMapping:
    """
    Get the C/C++ type mapping for a Fortran type.

    Args:
        fortran_type: Fortran type string, e.g., "REAL(ReKi)", "INTEGER(IntKi)",
                      or "TYPE(ErrorVariables)"
        kind_aliases: Project-specific kind name → numeric kind mapping
        derived_types: Registry of known derived types (name → DerivedTypeInfo).
                       If provided and the type is TYPE(...), uses this to
                       determine C compatibility. If not provided or type not
                       found, defaults to passthrough (non-C-compatible).
                       Types with view_strategy="view" are treated as C-compatible
                       with is_view_type=True.

    Returns:
        TypeMapping with all the C/ISO_C_BINDING equivalents

    Raises:
        ValueError if the type cannot be mapped (primitive types only)
    """
    aliases = {**DEFAULT_KIND_ALIASES, **(kind_aliases or {})}
    normalized = normalize_fortran_type(fortran_type, aliases)

    # Handle CHARACTER(N) where N is numeric — fixed-length character string
    if normalized.startswith("CHARACTER("):
        inner = normalized[10:-1].strip()
        if inner.isdigit():
            length = int(inner)
            return TypeMapping(
                fortran_type=fortran_type.strip(),
                fortran_kind=str(length),
                c_kind="C_CHAR",
                c_type="char",
                iso_c_type=f"CHARACTER(LEN={length})",
                byte_size=length,
                character_length=length,
            )
        # CHARACTER(:) or CHARACTER(*) — cannot map directly
        raise ValueError(
            f"Cannot map Fortran type '{fortran_type}' (normalized: '{normalized}'). "
            f"Deferred-length CHARACTER(:) is not C-compatible. "
            f"Use 'vit analyze-types --fix character' to convert to fixed-length."
        )

    # Handle derived types: TYPE(...)
    if normalized.startswith("TYPE("):
        type_name = normalized[5:-1].strip()
        if derived_types and type_name.upper() in {k.upper() for k in derived_types}:
            # Look up case-insensitively
            info = next(v for k, v in derived_types.items()
                        if k.upper() == type_name.upper())
            return get_derived_type_mapping(fortran_type.strip(), info)
        # Unknown derived type → passthrough
        return TypeMapping(
            fortran_type=fortran_type.strip(),
            fortran_kind="",
            c_kind="",
            c_type=type_name,
            iso_c_type="TYPE(C_PTR)",
            byte_size=0,
            is_derived=True,
            is_c_compatible=False,
        )

    if normalized in FORTRAN_TO_C:
        mapping = FORTRAN_TO_C[normalized]
        # Return a copy with the original fortran_type preserved
        return TypeMapping(
            fortran_type=fortran_type.strip(),
            fortran_kind=mapping.fortran_kind,
            c_kind=mapping.c_kind,
            c_type=mapping.c_type,
            iso_c_type=mapping.iso_c_type,
            byte_size=mapping.byte_size,
        )

    raise ValueError(
        f"Cannot map Fortran type '{fortran_type}' (normalized: '{normalized}'). "
        f"Known types: {list(FORTRAN_TO_C.keys())}. "
        f"Add a kind_alias mapping if this is a project-specific kind."
    )


def get_derived_type_mapping(fortran_type: str, type_info) -> TypeMapping:
    """
    Create a TypeMapping for a parsed derived type.

    Args:
        fortran_type: Original Fortran type string, e.g., "TYPE(ErrorVariables)"
        type_info: DerivedTypeInfo from derived_type_parser

    Returns:
        TypeMapping with is_derived=True and is_c_compatible set appropriately.
        If type_info.view_strategy == "view", the type is treated as C-compatible
        with is_view_type=True, even if it has ALLOCATABLE fields.
    """
    is_view = (getattr(type_info, 'view_strategy', None) == "view")
    return TypeMapping(
        fortran_type=fortran_type,
        fortran_kind="",
        c_kind="",
        c_type=type_info.name,
        iso_c_type="TYPE(C_PTR)",
        byte_size=0,
        is_derived=True,
        is_c_compatible=type_info.is_c_compatible or is_view,
        is_view_type=is_view,
    )
