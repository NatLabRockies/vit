"""
Parse Fortran function/subroutine signatures from source files.

Extracts the information needed for deterministic interface generation:
- Function name
- Argument names and types
- Return type (for functions)
- INTENT attributes
"""

import ctypes
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


def _is_libc_symbol(name: str) -> bool:
    """Check if name is an exported symbol in the C library.

    Uses the dynamic linker's own resolution — catches exactly the
    symbols that would collide via PLT in a -fPIC shared library.
    """
    try:
        libc = ctypes.CDLL(None)  # default handle = all loaded libs
        getattr(libc, name)
        return True
    except (OSError, AttributeError):
        return False


class SubprogramKind(Enum):
    FUNCTION = "function"
    SUBROUTINE = "subroutine"


class IntentKind(Enum):
    IN = "IN"
    OUT = "OUT"
    INOUT = "INOUT"
    NONE = ""  # no explicit intent


@dataclass
class FortranArg:
    """A single argument to a Fortran function/subroutine."""
    name: str
    fortran_type: str        # e.g., "REAL(ReKi)" or "TYPE(ErrorVariables)"
    intent: IntentKind = IntentKind.NONE
    is_array: bool = False
    array_dims: Optional[str] = None  # e.g., "(:)" or "(3,3)"
    is_optional: bool = False
    is_allocatable: bool = False

    @property
    def is_derived_type(self) -> bool:
        """True if this argument is a derived type (TYPE(...))."""
        return self.fortran_type.upper().startswith("TYPE(")

    @property
    def derived_type_name(self) -> Optional[str]:
        """Extract the type name from TYPE(Foo) → 'Foo', or None if not derived."""
        if self.is_derived_type:
            return self.fortran_type[5:-1].strip()
        return None

    @property
    def is_assumed_shape(self) -> bool:
        """True if this is an assumed-shape array (has ':' in dims)."""
        return self.is_array and self.array_dims is not None and ":" in self.array_dims

    @property
    def array_rank(self) -> int:
        """Number of dimensions (0 for scalars, 1 for (:), 2 for (:,:), etc.)."""
        if not self.is_array or self.array_dims is None:
            return 0
        # Count comma-separated dimensions: "(:)" → 1, "(:,:)" → 2, "(3,3)" → 2
        return self.array_dims.count(",") + 1

    @property
    def is_assumed_size(self) -> bool:
        """True if this is an assumed-size array (declared with *)."""
        return self.is_array and self.array_dims is not None and "*" in self.array_dims


@dataclass
class FortranSignature:
    """Complete signature of a Fortran function or subroutine."""
    name: str
    kind: SubprogramKind
    args: list[FortranArg] = field(default_factory=list)
    result_name: Optional[str] = None       # RESULT(name) clause
    result_type: Optional[str] = None       # Return type for functions
    result_is_array: bool = False           # True if result is an array
    result_dims: Optional[str] = None       # e.g., "3,3" or "n, n"
    source_file: Optional[str] = None       # Where it was found
    module_name: Optional[str] = None       # Enclosing module, if any
    line_number: Optional[int] = None       # Line number in source
    use_statements: list[str] = field(default_factory=list)  # USE lines from function body

    @property
    def c_func_name(self) -> str:
        """The extern "C" function name (append _c suffix)."""
        return f"{self.name.lower()}_c"

    @property
    def cpp_impl_name(self) -> str:
        """C++ implementation function name.

        Preserves source casing from the Fortran declaration.
        Appends _impl if the exact name collides with a libc symbol
        (prevents PLT interposition in -fPIC shared libraries).
        """
        if _is_libc_symbol(self.name):
            return f"{self.name}_impl"
        return self.name

    @property
    def is_function(self) -> bool:
        return self.kind == SubprogramKind.FUNCTION

    @property
    def is_subroutine(self) -> bool:
        return self.kind == SubprogramKind.SUBROUTINE


def parse_signature_from_source(source: str, function_name: str) -> FortranSignature:
    """
    Parse a Fortran function/subroutine signature from source code.

    This does not require a full Fortran parser — it uses regex patterns
    to extract the signature line and subsequent type declarations.

    Args:
        source: Fortran source code (full file or relevant excerpt)
        function_name: Name of the function to find

    Returns:
        FortranSignature with all arguments and types resolved
    """
    lines = source.split("\n")
    name_upper = function_name.upper()

    # Find the FUNCTION or SUBROUTINE line
    sig_line = ""
    sig_line_num = -1
    kind = None

    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        # Remove comments
        if "!" in stripped:
            stripped = stripped[:stripped.index("!")]

        # Match FUNCTION name(...) or SUBROUTINE name(...)
        func_match = re.search(
            rf'\bFUNCTION\s+{name_upper}\s*\(', stripped
        )
        sub_match = re.search(
            rf'\bSUBROUTINE\s+{name_upper}\s*\(', stripped
        )

        if func_match:
            kind = SubprogramKind.FUNCTION
        elif sub_match:
            kind = SubprogramKind.SUBROUTINE
        else:
            continue

        # Collect continuation lines (Fortran & continuation)
        sig_line = line.strip()
        sig_line_num = i
        # Handle continuation: if line ends with & or next line starts with &
        j = i
        while "&" in sig_line.rstrip().rstrip("!").rstrip():
            j += 1
            if j >= len(lines):
                break
            next_line = lines[j].strip()
            if next_line.startswith("&"):
                next_line = next_line[1:]
            sig_line = sig_line.rstrip().rstrip("&").rstrip() + " " + next_line
        break

    if kind is None:
        raise ValueError(f"Could not find FUNCTION or SUBROUTINE '{function_name}' in source")

    # Extract actual function name casing from the source (not from CLI arg)
    source_name_match = re.search(
        r'(?:FUNCTION|SUBROUTINE)\s+(\w+)\s*\(', sig_line, re.IGNORECASE
    )
    source_name = source_name_match.group(1) if source_name_match else function_name

    # Parse argument list from signature line
    sig_upper = sig_line.upper()
    paren_start = sig_upper.index("(", sig_upper.index(name_upper))
    # Find matching closing paren
    depth = 0
    paren_end = paren_start
    for ci in range(paren_start, len(sig_upper)):
        if sig_upper[ci] == "(":
            depth += 1
        elif sig_upper[ci] == ")":
            depth -= 1
            if depth == 0:
                paren_end = ci
                break

    arg_str = sig_line[paren_start + 1:paren_end].strip()
    arg_names = [a.strip() for a in arg_str.split(",") if a.strip()]

    # Check for RESULT clause (functions)
    result_name = None
    result_match = re.search(r'RESULT\s*\(\s*(\w+)\s*\)', sig_upper[paren_end:])
    if result_match:
        # Extract from original sig_line to preserve case
        result_name = sig_line[paren_end + result_match.start(1):paren_end + result_match.end(1)]

    # Now scan subsequent lines for type declarations of the arguments
    # Also look for the result variable's type
    args = {name.upper(): FortranArg(name=name, fortran_type="UNKNOWN") for name in arg_names}
    result_type = None
    result_is_array = False
    result_dims = None

    # Determine which names to look for
    names_to_find = set(a.upper() for a in arg_names)
    if result_name:
        names_to_find.add(result_name.upper())
    elif kind == SubprogramKind.FUNCTION:
        names_to_find.add(name_upper)

    # Scan from the line after the signature to END FUNCTION/SUBROUTINE
    end_pattern = re.compile(rf'\bEND\s+(FUNCTION|SUBROUTINE)\b', re.IGNORECASE)
    contains_pattern = re.compile(r'\bCONTAINS\b', re.IGNORECASE)
    use_statements = []

    for i in range(sig_line_num + 1, len(lines)):
        line = lines[i].strip()
        line_upper = line.upper()

        # Remove comments
        if "!" in line_upper:
            line_upper = line_upper[:line_upper.index("!")]
            line = line[:line.index("!")]

        if not line_upper:
            continue

        # Stop at END FUNCTION/SUBROUTINE or CONTAINS
        if end_pattern.search(line_upper) or contains_pattern.search(line_upper):
            break

        # Capture USE statements from the function body
        if line_upper.startswith("USE "):
            use_statements.append(lines[i].strip())

        # Try to parse as a type declaration
        decl = _parse_type_declaration(line, line_upper, names_to_find)
        if decl:
            for var_name, var_type, intent, is_array, dims, is_optional, is_allocatable in decl:
                var_upper = var_name.upper()
                if var_upper in args:
                    args[var_upper].fortran_type = var_type
                    args[var_upper].intent = intent
                    args[var_upper].is_array = is_array
                    args[var_upper].array_dims = dims
                    args[var_upper].is_optional = is_optional
                    args[var_upper].is_allocatable = is_allocatable
                elif var_upper == (result_name or function_name).upper():
                    result_type = var_type
                    result_is_array = is_array
                    result_dims = dims

    # Build ordered arg list
    ordered_args = [args[name.upper()] for name in arg_names]

    # If result type not found from explicit declaration, check for prefix type
    if result_type is None and kind == SubprogramKind.FUNCTION:
        prefix_match = re.match(
            r'((?:REAL|INTEGER|DOUBLE\s+PRECISION|LOGICAL|CHARACTER)'
            r'(?:\s*\([^)]*\))?)\s+FUNCTION',
            sig_upper
        )
        if prefix_match:
            # Extract from original sig_line to preserve case
            result_type = sig_line[prefix_match.start(1):prefix_match.end(1)].strip()

    return FortranSignature(
        name=source_name,
        kind=kind,
        args=ordered_args,
        result_name=result_name,
        result_type=result_type,
        result_is_array=result_is_array,
        result_dims=result_dims,
        line_number=sig_line_num + 1,  # 1-indexed
        use_statements=use_statements,
    )


def _parse_type_declaration(line: str, line_upper: str, names_to_find: set):
    """
    Parse a Fortran type declaration line.

    Returns list of (var_name, type, intent, is_array, dims, is_optional,
    is_allocatable) tuples
    for any variables that are in names_to_find, or None if not a type decl.
    """
    # Match type declarations: TYPE[(...)] [, attrs] :: var1, var2
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
    rest_original = None

    for pattern in type_patterns:
        m = re.match(pattern, line_upper)
        if m:
            # Extract type string from ORIGINAL line to preserve case (e.g., DbKi not DBKI)
            type_str = line.strip()[m.start(1):m.end(1)].strip()
            rest = line_upper[m.end():].strip()
            rest_original = line.strip()[m.end():].strip()
            break

    if type_str is None:
        return None

    # Normalize "TYPE (Foo)" → "TYPE(Foo)" — remove optional whitespace
    # so downstream code can use startswith("TYPE(") consistently
    if type_str.upper().startswith("TYPE") and "(" in type_str:
        type_str = re.sub(r'^(TYPE)\s*\(', r'\1(', type_str, flags=re.IGNORECASE)

    # Parse attributes between type and ::
    intent = IntentKind.NONE
    is_optional = False
    is_allocatable = False
    has_dimension = False
    dim_spec = None

    if "::" in rest:
        attr_part, var_part = rest.split("::", 1)
        attr_part_original = rest_original.split("::", 1)[0]
    else:
        # No :: — the rest is variable names (older Fortran style)
        attr_part = ""
        attr_part_original = ""
        var_part = rest

    # Parse attributes (use uppercase for matching, original case for extraction)
    attr_part = attr_part.strip().lstrip(",").strip()
    attr_part_original = attr_part_original.strip().lstrip(",").strip()
    if attr_part:
        attrs = attr_part.upper()
        intent_match = re.search(r'INTENT\s*\(\s*(IN|OUT|INOUT)\s*\)', attrs)
        if intent_match:
            intent = IntentKind(intent_match.group(1))
        if "OPTIONAL" in attrs:
            is_optional = True
        # ALLOCATABLE on a dummy matters: an INTENT(OUT) ALLOCATABLE arrives
        # unallocated and the callee is what decides its extent. See the
        # allocate-on-return convention in interface_gen.
        if re.search(r'\bALLOCATABLE\b', attrs):
            is_allocatable = True
        dim_start = re.search(r'DIMENSION\s*\(', attrs)
        if dim_start:
            has_dimension = True
            # Balanced-paren extraction: find content between DIMENSION( and its matching )
            open_pos = dim_start.end() - 1  # position of the '('
            depth = 0
            for k in range(open_pos, len(attrs)):
                if attrs[k] == '(':
                    depth += 1
                elif attrs[k] == ')':
                    depth -= 1
                    if depth == 0:
                        # Extract from original-case attr_part to preserve variable casing
                        dim_spec = attr_part_original[open_pos + 1:k]
                        break

    # Parse variable names
    results = []
    var_part = var_part.strip()

    # Split on commas, but respect parentheses (for array dimensions)
    var_names = _split_respecting_parens(var_part)

    for var_decl in var_names:
        var_decl = var_decl.strip()
        if not var_decl:
            continue

        # Check for inline array dimensions: var(3,3) or var(size(alpha))
        var_name = var_decl
        local_dims = dim_spec
        local_is_array = has_dimension

        # Use balanced-paren matching (not [^)]*) to handle nested parens
        # like Cn(size(alpha))
        paren_start_match = re.match(r'(\w+)\s*\(', var_decl)
        if paren_start_match:
            var_name = paren_start_match.group(1)
            open_pos = paren_start_match.end() - 1  # position of '('
            depth = 0
            close_pos = open_pos
            for k in range(open_pos, len(var_decl)):
                if var_decl[k] == '(':
                    depth += 1
                elif var_decl[k] == ')':
                    depth -= 1
                    if depth == 0:
                        close_pos = k
                        break
            local_dims = var_decl[open_pos + 1:close_pos]
            local_is_array = True
        else:
            var_name = re.match(r'(\w+)', var_decl)
            if var_name:
                var_name = var_name.group(1)
            else:
                continue

        if var_name.upper() in names_to_find:
            # Use the original case from the source for the type
            # Find the type in the original line
            orig_type = line[:len(type_str)]  # rough — use the actual matched span
            results.append((var_name, type_str, intent, local_is_array, local_dims,
                            is_optional, is_allocatable))

    return results if results else None


def _split_respecting_parens(s: str) -> list[str]:
    """Split string on commas, but not inside parentheses."""
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


def find_function_in_file(filepath: str, function_name: str) -> FortranSignature:
    """
    Find and parse a function/subroutine signature from a Fortran file.

    Args:
        filepath: Path to the .f90/.F90 file
        function_name: Name of the function/subroutine

    Returns:
        FortranSignature with source_file set
    """
    source = Path(filepath).read_text()
    sig = parse_signature_from_source(source, function_name)
    sig.source_file = str(filepath)

    sig.module_name = enclosing_module(source, function_name)
    return sig


def enclosing_module(source: str, procedure_name: str) -> Optional[str]:
    """The module a procedure is actually declared in.

    This used to take the FIRST `MODULE` statement in the file and stop. In a
    file holding one module that is the same answer; in a file holding two it is
    the wrong one, and the failure is quiet in a way that is hard to trace: the
    generated bridge emits `USE <the-wrong-module>`, so the procedure it calls
    has no visible interface. A SUBROUTINE still compiles -- Fortran permits an
    implicit interface -- and only a FUNCTION fails, with "has no IMPLICIT
    type", which reads like a problem in the function rather than in the USE
    statement above it.

    Multi-module files are ordinary: a types module beside the module that uses
    it is the commonest layout in scientific Fortran. ROSCO happens to put one
    module per file, which is why this survived 52 translated procedures.

    Tracks module scope rather than guessing: push on MODULE, pop on END MODULE,
    and report whatever is open when the procedure is declared. Returns None for
    a procedure that is not inside a module at all, which is a real case and not
    an error.
    """
    decl = re.compile(
        r'^\s*(?:[A-Za-z0-9_()\s*]*?\s)?(?:SUBROUTINE|FUNCTION)\s+'
        + re.escape(procedure_name) + r'\s*[(\s]', re.IGNORECASE)
    open_mod = re.compile(r'^\s*MODULE\s+(\w+)\s*$', re.IGNORECASE)
    end_mod = re.compile(r'^\s*END\s*MODULE\b', re.IGNORECASE)

    stack: list[str] = []
    for raw in source.split("\n"):
        line = raw.split("!", 1)[0]
        if end_mod.match(line):
            if stack:
                stack.pop()
            continue
        m = open_mod.match(line)
        if m and m.group(1).upper() not in ("PROCEDURE", "FUNCTION", "SUBROUTINE"):
            stack.append(m.group(1))
            continue
        if decl.match(line) and not line.strip().upper().startswith("END"):
            return stack[-1] if stack else None
    return stack[-1] if stack else None

_PROC_OPEN = re.compile(
    r'^\s*(?:[A-Za-z0-9_()\s*,=:]*?\s)?(?:SUBROUTINE|FUNCTION)\s+(\w+)', re.IGNORECASE)
_PROC_END = re.compile(r'^\s*END\s*(?:SUBROUTINE|FUNCTION)\b', re.IGNORECASE)
_PROGRAM_OPEN = re.compile(r'^\s*PROGRAM\s+(\w+)', re.IGNORECASE)
_PROGRAM_END = re.compile(r'^\s*END\s*PROGRAM\b', re.IGNORECASE)


def enclosing_procedure(source: str, line_no: int) -> Optional[str]:
    """The procedure (or main program) that line `line_no` (1-based) is in.

    Tracks scope the way enclosing_module does: push on a SUBROUTINE, FUNCTION
    or PROGRAM statement, pop on its END statement, and report what is open at
    the line, so a line in an internal procedure names the internal one and a
    line after it names its host again. Returns None for a line outside any
    procedure, such as a module's specification part.

    A procedure closed by a bare `END`, without SUBROUTINE or FUNCTION, is not
    recognised as closed.
    """
    stack: list[str] = []
    for i, raw in enumerate(source.split("\n"), 1):
        line = raw.split("!", 1)[0]
        if _PROC_END.match(line) or _PROGRAM_END.match(line):
            if i == line_no:
                return stack[-1] if stack else None
            if stack:
                stack.pop()
            continue
        m = _PROC_OPEN.match(line) or _PROGRAM_OPEN.match(line)
        if m and not line.strip().upper().startswith("END"):
            stack.append(m.group(1))
        if i == line_no:
            return stack[-1] if stack else None
    return None
