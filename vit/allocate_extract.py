"""
Extract ALLOCATE information from Fortran source code.

Scans a function body for ALLOCATE statements and helper-mediated
allocations (e.g., CALL ParseAry), producing structured AllocationInfo
records that describe which fields are allocated, with what dimensions,
and whether the size expressions are safe to evaluate in a C++ wrapper.

Used by VIT's wrapper generation to pre-allocate ALLOCATABLE arrays
before passing view structs across the Fortran/C++ boundary.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AllocationInfo:
    """Describes a single ALLOCATABLE field allocation found in Fortran source.

    Attributes:
        field_name: The field being allocated (e.g., "Cp_mat").
        target_arg: The function argument that owns the field (e.g., "PerfData").
        size_exprs: Dimension size expressions, one per rank
            (e.g., ["CntrPar%PerfTableSize(2)", "CntrPar%PerfTableSize(1)"]).
        category: Safety classification for wrapper generation:
            - "safe": all size expressions come from different arguments or literals
            - "same_arg": at least one size expression references the same argument
              as target_arg (circular dependency risk)
            - "local": at least one size expression is a local variable
        source: How the allocation was discovered:
            - "direct" for explicit ALLOCATE statements
            - "helper:<name>" for helper-mediated allocations (e.g., "helper:ParseAry")
            - "copy_call:<routine>" for Registry Copy calls
        init_value: Default initialization value for the allocated array
            ("0.0D0" for REAL, "0" for INTEGER, "0.0D0" as fallback).
        guard_expr: Fortran IF condition that guards this allocation, or None
            if the allocation is unconditional.
        copy_call: Full CALL statement for Registry Copy allocations, including
            the loop wrapper if present (e.g., "DO i=1,SIZE(OtherState%xdot)\\n...")
        error_msg: Error message string from the original SetErrStat call after
            this allocation, if detected.
    """
    field_name: str
    target_arg: str
    size_exprs: list[str] = field(default_factory=list)
    category: str = "safe"
    source: str = "direct"
    init_value: str = "0.0D0"
    guard_expr: Optional[str] = None
    copy_call: Optional[str] = None
    error_msg: Optional[str] = None


def extract_allocations(
    function_name: str,
    source_text: str,
    arg_names: list[str],
    allocatable_helpers: list[dict],
) -> list[AllocationInfo]:
    """Extract allocation information from a Fortran function body.

    Scans the function body for:
    1. Direct ALLOCATE statements: ``ALLOCATE(Var%field(dim1, dim2, ...))``
    2. Helper-mediated allocations: ``CALL ParseAry(..., Var%field, size, ...)``

    Only allocations targeting function arguments (listed in ``arg_names``)
    are returned. Local variable allocations are skipped.

    Args:
        function_name: Name of the Fortran SUBROUTINE or FUNCTION to scan.
        source_text: Full Fortran source file text.
        arg_names: Names of the function's arguments (e.g., ["CntrPar", "PerfData"]).
        allocatable_helpers: Helper definitions from vit.yaml, each a dict with
            keys "name", "array_arg", "size_arg" (1-indexed positions).

    Returns:
        List of AllocationInfo records, one per unique field allocation.
        If the same field is allocated more than once, only the first is returned.
    """
    body = _extract_function_body(function_name, source_text)
    if body is None:
        return []

    # Normalize: join continuation lines, strip comments
    body = _join_continuations(body)

    # Build case-insensitive lookup for arg names
    arg_names_upper = {name.upper() for name in arg_names}

    results: list[AllocationInfo] = []
    seen_fields: set[tuple[str, str]] = set()  # (target_arg_upper, field_name_upper)

    # 1. Scan for direct ALLOCATE statements
    direct = _scan_direct_allocates(body, arg_names_upper)
    for info in direct:
        key = (info.target_arg.upper(), info.field_name.upper())
        if key not in seen_fields:
            seen_fields.add(key)
            results.append(info)

    # 2. Scan for helper-mediated allocations
    for helper in allocatable_helpers:
        helper_results = _scan_helper_allocates(body, helper, arg_names_upper)
        for info in helper_results:
            key = (info.target_arg.upper(), info.field_name.upper())
            if key not in seen_fields:
                seen_fields.add(key)
                results.append(info)

    # 3. Scan for Registry Copy calls (allocation via deep copy)
    copy_results = _scan_copy_allocates(body, arg_names_upper)
    for info in copy_results:
        key = (info.target_arg.upper(), info.field_name.upper())
        if key not in seen_fields:
            seen_fields.add(key)
            results.append(info)

    # 4. Classify each allocation
    for info in results:
        if info.source.startswith("copy_call"):
            continue  # Copy calls are always safe (src is a different arg)
        info.category = _classify_allocation(info, arg_names_upper)

    return results


def _extract_function_body(function_name: str, source_text: str) -> Optional[str]:
    """Extract the body of a SUBROUTINE or FUNCTION from source text.

    Returns the text between the declaration line and the matching
    END SUBROUTINE/FUNCTION line, or None if not found.
    """
    lines = source_text.split("\n")
    name_upper = function_name.upper()

    body_start = None
    body_end = None

    # Find the start: SUBROUTINE name(...) or FUNCTION name(...)
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Remove inline comments
        comment_pos = _find_comment_pos(stripped)
        if comment_pos >= 0:
            stripped = stripped[:comment_pos]
        stripped_upper = stripped.upper()

        if re.search(rf'\b(?:SUBROUTINE|FUNCTION)\s+{name_upper}\s*\(', stripped_upper):
            body_start = i + 1
            break

    if body_start is None:
        return None

    # Find the end: END SUBROUTINE name or END FUNCTION name
    # Also accept just END SUBROUTINE / END FUNCTION (without the name)
    for i in range(body_start, len(lines)):
        stripped = lines[i].strip()
        comment_pos = _find_comment_pos(stripped)
        if comment_pos >= 0:
            stripped = stripped[:comment_pos]
        stripped_upper = stripped.upper()

        # Match END SUBROUTINE [name] or END FUNCTION [name]
        if re.match(rf'\s*END\s+(?:SUBROUTINE|FUNCTION)\b', stripped_upper):
            body_end = i
            break

    if body_end is None:
        # Fallback: use rest of file
        body_end = len(lines)

    return "\n".join(lines[body_start:body_end])


def _find_comment_pos(line: str) -> int:
    """Find the position of a Fortran comment marker (!) outside of strings.

    Returns -1 if no comment found.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '!' and not in_single and not in_double:
            return i
    return -1


def _join_continuations(text: str) -> str:
    """Join Fortran continuation lines (& at end of line).

    Removes comment-only lines and joins lines connected by &.
    Preserves line structure for non-continuation lines.
    """
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip comment-only lines
        if stripped.startswith("!"):
            i += 1
            continue

        # Remove inline comments (but not inside strings)
        comment_pos = _find_comment_pos(stripped)
        if comment_pos >= 0:
            stripped = stripped[:comment_pos].rstrip()

        # Check for continuation: line ends with &
        while stripped.endswith("&"):
            stripped = stripped[:-1].rstrip()
            i += 1
            if i >= len(lines):
                break
            next_line = lines[i].strip()
            if next_line.startswith("!"):
                continue
            # Remove inline comments from continuation line
            cpos = _find_comment_pos(next_line)
            if cpos >= 0:
                next_line = next_line[:cpos].rstrip()
            # Strip leading & from continuation
            if next_line.startswith("&"):
                next_line = next_line[1:].lstrip()
            stripped = stripped + " " + next_line

        result.append(stripped)
        i += 1

    return "\n".join(result)


def _find_guard_for_line(lines: list[str], target_line_idx: int) -> Optional[str]:
    """Find the IF condition that guards the ALLOCATE at the given line index.

    Walks backwards from the ALLOCATE line to find the nearest enclosing
    block IF statement. Returns the condition expression or None.
    """
    depth = 0
    for i in range(target_line_idx - 1, -1, -1):
        upper = lines[i].strip().upper()
        if re.match(r'END\s*IF\b', upper):
            depth += 1
        elif re.match(r'IF\s*\(.*\)\s*THEN\b', upper, re.IGNORECASE):
            if depth == 0:
                m = re.match(r'IF\s*\((.*)\)\s*THEN\b', lines[i].strip(), re.IGNORECASE)
                if m:
                    return m.group(1).strip()
                return None
            depth -= 1
    return None


def _find_error_msg(lines: list[str], alloc_line_idx: int) -> Optional[str]:
    """Find the SetErrStat error message after an ALLOCATE, if present."""
    for i in range(alloc_line_idx + 1, min(alloc_line_idx + 5, len(lines))):
        line = lines[i].strip()
        m = re.search(r'SetErrStat\s*\(\s*ErrID_Fatal\s*,\s*["\']([^"\']+)["\']', line, re.IGNORECASE)
        if m:
            return m.group(1)
        if re.match(r'\bALLOCATE\b', line, re.IGNORECASE):
            break
    return None


def _scan_direct_allocates(
    body: str,
    arg_names_upper: set[str],
) -> list[AllocationInfo]:
    """Scan function body for direct ALLOCATE(Var%field(...)) statements.

    Handles:
    - Single and multi-dimensional allocations
    - Nested parentheses in dimension expressions (e.g., PerfTableSize(2))
    - ALLOCATE with STAT= clause (ignored, not treated as a dimension)
    - Multiple ALLOCATE targets separated by commas (rare but legal)
    - Enclosing IF guards (conditional allocations)
    - SetErrStat error messages after ALLOCATE
    """
    results = []
    lines = body.split("\n")

    for line_idx, line in enumerate(lines):
        line_upper = line.strip().upper()
        if not re.match(r'\bALLOCATE\s*\(', line_upper):
            continue

        # Find the full ALLOCATE content (may span this single normalized line)
        match = re.search(r'\bALLOCATE\s*\(', line, re.IGNORECASE)
        if not match:
            continue
        start = match.end() - 1
        content = _extract_balanced_parens(line, start)
        if content is None:
            continue

        content = _remove_stat_clause(content)
        targets = _split_allocate_targets(content)

        guard = _find_guard_for_line(lines, line_idx)
        error_msg = _find_error_msg(lines, line_idx)

        for target in targets:
            target = target.strip()
            if not target:
                continue

            m = re.match(r'(\w+)\s*%\s*(\w+)\s*\(', target, re.IGNORECASE)
            if not m:
                continue

            var_name = m.group(1)
            field_name = m.group(2)

            if var_name.upper() not in arg_names_upper:
                continue

            dims_start = target.index("(", m.start(2))
            dims_content = _extract_balanced_parens(target, dims_start)
            if dims_content is None:
                continue

            size_exprs = _split_call_args(dims_content)
            size_exprs = [s.strip() for s in size_exprs if s.strip()]

            results.append(AllocationInfo(
                field_name=field_name,
                target_arg=var_name,
                size_exprs=size_exprs,
                source="direct",
                guard_expr=guard,
                error_msg=error_msg,
            ))

    return results


def _scan_helper_allocates(
    body: str,
    helper: dict,
    arg_names_upper: set[str],
) -> list[AllocationInfo]:
    """Scan function body for helper-mediated allocations.

    For each CALL to the helper subroutine, extracts the array argument
    and size argument at the configured positions.

    Args:
        body: Normalized function body text.
        helper: Dict with "name", "array_arg" (1-indexed), "size_arg" (1-indexed).
        arg_names_upper: Set of uppercase argument names.

    Returns:
        List of AllocationInfo records found via this helper.
    """
    results = []
    helper_name = helper["name"]
    array_pos = helper["array_arg"] - 1  # Convert to 0-indexed
    size_pos = helper["size_arg"] - 1

    # Find CALL helper_name(...)
    pattern = re.compile(
        rf'\bCALL\s+{re.escape(helper_name)}\s*\(',
        re.IGNORECASE,
    )

    for match in pattern.finditer(body):
        paren_start = match.end() - 1
        args_content = _extract_balanced_parens(body, paren_start)
        if args_content is None:
            continue

        args = _split_call_args(args_content)

        if array_pos >= len(args) or size_pos >= len(args):
            continue

        array_arg = args[array_pos].strip()
        size_arg = args[size_pos].strip()

        # Check if array_arg matches Var%field pattern
        m = re.match(r'(\w+)\s*%\s*(\w+)$', array_arg, re.IGNORECASE)
        if not m:
            continue

        var_name = m.group(1)
        field_name = m.group(2)

        if var_name.upper() not in arg_names_upper:
            continue

        results.append(AllocationInfo(
            field_name=field_name,
            target_arg=var_name,
            size_exprs=[size_arg],
            source=f"helper:{helper_name}",
        ))

    return results


def _scan_copy_allocates(
    body: str,
    arg_names_upper: set[str],
) -> list[AllocationInfo]:
    """Scan for CALL statements that initialize INTENT(OUT) argument fields.

    Detects any CALL where an argument matches ``arg%field(...)`` and ``arg``
    is one of the function's arguments. These calls typically allocate or
    populate fields as a side effect (e.g., deep-copy routines) and must be
    preserved in the Fortran wrapper before the C++ call.
    """
    results = []
    lines = body.split("\n")

    for line_idx, line in enumerate(lines):
        m = re.match(
            r'\s*(?:CALL\s+)(\w+)\s*\(',
            line, re.IGNORECASE,
        )
        if not m:
            continue

        routine = m.group(1)

        # Check if any argument is arg%field where arg is a function argument
        call_start = line.upper().index(routine.upper()) + len(routine)
        paren_start = line.index("(", call_start)
        call_content = _extract_balanced_parens(line, paren_start)
        if call_content is None:
            continue

        args = _split_call_args(call_content)
        dst_arg = None
        dst_field = None
        for arg_text in args:
            arg_m = re.match(r'\s*(\w+)\s*%\s*(\w+)', arg_text.strip())
            if arg_m and arg_m.group(1).upper() in arg_names_upper:
                dst_arg = arg_m.group(1)
                dst_field = arg_m.group(2)
                # Don't break — take the LAST match (destination is typically
                # after source in copy-style calls)

        if dst_arg is None:
            continue

        guard = _find_guard_for_line(lines, line_idx)

        loop_line = None
        for i in range(line_idx - 1, max(line_idx - 3, -1), -1):
            upper = lines[i].strip().upper()
            if re.match(r'DO\s+\w+\s*=', upper):
                loop_line = lines[i].strip()
                break

        copy_call = line.strip()
        if loop_line:
            copy_call = f"{loop_line}\n            {copy_call}\n        END DO"

        results.append(AllocationInfo(
            field_name=f"{dst_field}(*)",
            target_arg=dst_arg,
            size_exprs=[],
            category="safe",
            source=f"copy_call:{routine}",
            guard_expr=guard,
            copy_call=copy_call,
        ))

    return results


def _classify_allocation(
    info: AllocationInfo,
    arg_names_upper: set[str],
) -> str:
    """Classify an allocation based on its size expression dependencies.

    Classification rules:
    - "safe": all size expressions are literals or reference arguments
      other than target_arg
    - "same_arg": at least one size expression references the same argument
      as target_arg (circular dependency risk)
    - "local": at least one size expression references a local variable
      (not an argument field and not a literal)

    A literal integer (e.g., "3", "2") is always safe regardless of target_arg.
    An expression like "SIZE(CntrPar%OL_Channels,DIM=1)" that references an
    argument is classified based on which argument it references.
    """
    target_upper = info.target_arg.upper()
    has_local = False
    has_same_arg = False

    for expr in info.size_exprs:
        expr_class = _classify_size_expr(expr, target_upper, arg_names_upper)
        if expr_class == "local":
            has_local = True
        elif expr_class == "same_arg":
            has_same_arg = True
        # "safe" doesn't set any flag

    # Priority: local > same_arg > safe
    if has_local:
        return "local"
    if has_same_arg:
        return "same_arg"
    return "safe"


def _classify_size_expr(
    expr: str,
    target_upper: str,
    arg_names_upper: set[str],
) -> str:
    """Classify a single size expression.

    Returns "safe", "same_arg", or "local".
    """
    expr_stripped = expr.strip()

    # Literal integer: always safe
    if re.match(r'^\d+$', expr_stripped):
        return "safe"

    # Find all Arg%field references in the expression
    # This handles both simple "CntrPar%PC_GS_n" and complex
    # "SIZE(CntrPar%OL_Channels,DIM=1)" patterns
    arg_refs = re.findall(r'(\w+)\s*%\s*\w+', expr_stripped, re.IGNORECASE)

    if not arg_refs:
        # No Arg%field pattern found — it's a local variable or intrinsic call
        # with no argument reference. Could be something like "NumLines" or
        # "N_OL_Cables" — these are local variables.
        # Exception: bare intrinsic calls on argument fields are caught by the
        # arg_refs check above (SIZE(CntrPar%X) contains "CntrPar%X").
        return "local"

    for ref in arg_refs:
        ref_upper = ref.upper()
        if ref_upper == target_upper:
            return "same_arg"
        if ref_upper not in arg_names_upper:
            # References something that isn't a function argument — local/module var
            return "local"

    # All references are to arguments other than target
    return "safe"


def _split_call_args(call_text: str) -> list[str]:
    """Split a parenthesized argument list, handling nested parentheses.

    Given the content between outer parentheses (not including them),
    splits on commas at depth 0.

    Example:
        "CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)"
        → ["CntrPar%PerfTableSize(2)", "CntrPar%PerfTableSize(1)"]

        "FileLines, 'PC_GS_angles', CntrPar%PC_GS_angles, CntrPar%PC_GS_n, accINFILE(1), ErrVar"
        → ["FileLines", "'PC_GS_angles'", "CntrPar%PC_GS_angles", "CntrPar%PC_GS_n", "accINFILE(1)", "ErrVar"]
    """
    parts = []
    depth = 0
    current = ""
    in_single = False
    in_double = False

    for ch in call_text:
        if ch == "'" and not in_double:
            in_single = not in_single
            current += ch
        elif ch == '"' and not in_single:
            in_double = not in_double
            current += ch
        elif in_single or in_double:
            current += ch
        elif ch == "(":
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


def _extract_balanced_parens(text: str, start: int) -> Optional[str]:
    """Extract content inside balanced parentheses starting at position ``start``.

    ``text[start]`` must be ``(``. Returns the content between the outermost
    parentheses (excluding them), or None if unbalanced.
    """
    if start >= len(text) or text[start] != "(":
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]

    return None  # Unbalanced


def _remove_stat_clause(allocate_content: str) -> str:
    """Remove STAT= clause from ALLOCATE content.

    In ``ALLOCATE(arr(n), STAT=err)``, the STAT= clause appears as a
    top-level comma-separated item. We remove it to avoid treating
    ``STAT=err`` as an allocation target.

    Also handles SOURCE=, MOLD=, and ERRMSG= clauses.
    """
    parts = _split_call_args(allocate_content)
    filtered = []
    for part in parts:
        stripped = part.strip().upper()
        # Skip STAT=, SOURCE=, MOLD=, ERRMSG= clauses
        if re.match(r'(STAT|SOURCE|MOLD|ERRMSG)\s*=', stripped):
            continue
        filtered.append(part)
    return ", ".join(filtered)


def _split_allocate_targets(content: str) -> list[str]:
    """Split ALLOCATE content into individual allocation targets.

    ALLOCATE can have multiple targets: ``ALLOCATE(a(n), b(m))``.
    Uses parenthesis-aware splitting: commas inside ``Var%field(dim1, dim2)``
    are at depth >= 1 and are not split on. Commas between targets
    (``a(n), b(m)``) are at depth 0 and become split points.
    """
    return _split_call_args(content)
