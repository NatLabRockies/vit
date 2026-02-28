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
    fortran_type: str          # e.g., "REAL(ReKi)"
    fortran_kind: str          # e.g., "ReKi"
    c_kind: str                # e.g., "C_DOUBLE"
    c_type: str                # e.g., "double"
    iso_c_type: str            # e.g., "REAL(C_DOUBLE)"
    byte_size: int             # e.g., 8


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
    "ReKi": "REAL(8)",       # Real kind (typically double precision)
    "DbKi": "REAL(8)",       # Double kind
    "SiKi": "REAL(4)",       # Single kind
    "IntKi": "INTEGER(4)",   # Integer kind
    "B1Ki": "INTEGER(1)",    # Byte kind

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
    aliases = kind_aliases or DEFAULT_KIND_ALIASES
    t = type_str.strip().upper()

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


def get_type_mapping(fortran_type: str, kind_aliases: Optional[dict] = None) -> TypeMapping:
    """
    Get the C/C++ type mapping for a Fortran type.

    Args:
        fortran_type: Fortran type string, e.g., "REAL(ReKi)", "INTEGER(IntKi)"
        kind_aliases: Project-specific kind name → numeric kind mapping

    Returns:
        TypeMapping with all the C/ISO_C_BINDING equivalents

    Raises:
        ValueError if the type cannot be mapped
    """
    normalized = normalize_fortran_type(fortran_type, kind_aliases)

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
