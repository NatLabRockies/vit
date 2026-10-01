"""
KGen extraction orchestration.

Automates:
1. Finding the call site for a target function
2. Inserting KGen pragmas around it
3. Generating include.ini for module discovery
4. Cleaning stale artifacts
5. Running KGen with the correct build/run commands
6. Wrapping --cmd-build in a script that prepares the instrumented tree
   (see build_state_capture_wrapper -- nine steps, not one)
7. Managing the resulting kernel/ and state/ directories

Encodes lessons learned from ROSCO debugging:
- KGen pragmas must use underscores: begin_callsite / end_callsite
- KGen pragmas must include the function name
- KGen strace often fails to discover -I/-J paths; include.ini must be pre-generated
- Stale strace.log causes KGen to reuse bad data; must clean before extraction
- Build must be clean so strace captures actual gfortran invocations
- The built library must be installed where the simulation loads it from
- KGen puts kernel/ and state/ in CWD, not relative to the source file
- Source files must be restored to original state on failure
- KGen instruments code with calls to kgen_utils functions but doesn't add
  kgen_utils.f90 to the project's build system. The instrumented shared library
  builds without error (unresolved symbols allowed) but fails silently at runtime.
  We wrap --cmd-build in a script that injects kgen_utils.f90 into the build.
- Editable pip installs (pip install -e .) can point to the wrong checkout,
  causing the simulation to load a different (uninstrumented) library.
"""

import os
import re
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Optional

from vit.confirm import Assumption, print_assumption


def _create_kgen_backups(kernel: Path) -> None:
    """Create .kgen backups of clean kernel files after extraction.

    Called immediately after successful KGen extraction, when kernel .f90
    files are guaranteed clean (no VIT patches applied). These backups are
    the authoritative clean state used by vit verify to restore files
    between verification runs.
    """
    for f90_file in sorted(kernel.glob("*.f90")):
        if f90_file.name.startswith("vit_"):
            continue
        backup = f90_file.with_suffix(".f90.kgen")
        shutil.copy2(f90_file, backup)

    makefile = kernel / "Makefile"
    if makefile.exists():
        shutil.copy2(makefile, kernel / "Makefile.kgen")


def fix_cross_module_instrumentation(
    state_dir: str, cmake_file: str, source_dir: str
) -> list[str]:
    """
    Detect and fix cross-module KGen instrumentation.

    KGen traces USE dependency chains and instruments source files in every
    module it encounters — not just the build target. When a file outside
    the cmake target's source tree is instrumented, its kw_* subroutines
    won't be compiled, causing unresolved symbol errors.

    This function transplants kw_* subroutines from cross-module instrumented
    files into the correct module file within the build target. Both files
    define the same Fortran MODULE (same module name, same module-scope
    variables), so the subroutine works in either location.

    Called from the build wrapper AFTER KGen's state Makefile 'replace' step
    has copied instrumented source to the source tree, and BEFORE the cmake
    build.

    Args:
        state_dir: Path to KGen's state/ directory (contains Makefile)
        cmake_file: Path to the cmake target's CMakeLists.txt
        source_dir: Root source directory for searching module files

    Returns:
        List of human-readable descriptions of fixes applied
    """
    fixes = []
    state_makefile = os.path.join(state_dir, "Makefile")
    if not os.path.exists(state_makefile):
        return fixes

    # Determine the cmake target's source directory
    cmake_dir = os.path.dirname(os.path.abspath(cmake_file))

    # Parse the state Makefile to find all files KGen instrumented
    instrumented_files = _parse_state_makefile_replace_files(state_makefile)

    for abs_path in instrumented_files:
        # Check if this file is outside the cmake target's directory tree
        if os.path.abspath(abs_path).startswith(cmake_dir + os.sep):
            continue  # In-target file, no fix needed

        # Cross-module file detected
        cross_module_path = abs_path
        module_name = _extract_module_name(cross_module_path)
        if not module_name:
            continue

        public_lines, subroutine_blocks = _extract_kw_subroutines(
            cross_module_path
        )
        if not subroutine_blocks:
            continue  # No kw_* subroutines to transplant

        # Find the in-target file that defines the same module
        target_file = _find_module_file_in_dir(
            module_name, source_dir, exclude_path=cross_module_path
        )
        if not target_file:
            fixes.append(
                f"WARNING: Cross-module file {os.path.basename(cross_module_path)} "
                f"defines MODULE {module_name} but no matching file found in "
                f"source tree under {source_dir}"
            )
            continue

        # Transplant the kw_* subroutines
        _inject_kw_into_module(target_file, public_lines, subroutine_blocks)

        sub_names = [
            re.search(r'SUBROUTINE\s+(\w+)', block, re.IGNORECASE).group(1)
            for block in subroutine_blocks
            if re.search(r'SUBROUTINE\s+(\w+)', block, re.IGNORECASE)
        ]
        fixes.append(
            f"Transplanted {', '.join(sub_names)} from "
            f"{os.path.basename(cross_module_path)} to "
            f"{os.path.basename(target_file)}"
        )

    return fixes


def _parse_state_makefile_replace_files(makefile_path: str) -> list[str]:
    """Parse state/Makefile 'replace' target to get instrumented file paths.

    The replace target has lines like:
        cp -f AirfoilInfo.f90 /workspace/openfast/modules/aerodyn/src/AirfoilInfo.f90

    Returns the list of absolute destination paths.
    """
    content = Path(makefile_path).read_text()
    files = []
    in_replace = False
    for line in content.split("\n"):
        raw = line  # Preserve original indentation for detection
        stripped = line.strip()
        if stripped.startswith("replace:"):
            in_replace = True
            continue
        if in_replace:
            # Makefile recipe lines start with a tab. A non-tab, non-empty
            # line indicates the next target.
            if raw and not raw[0] in ('\t', ' ') and not raw.startswith('#'):
                break  # Next target
            # Match: cp -f <local> <abs_path>
            m = re.match(r'cp\s+-f\s+\S+\s+(\S+)', stripped)
            if m:
                files.append(m.group(1))
    return files


def _extract_module_name(filepath: str) -> Optional[str]:
    """Extract the MODULE name from a Fortran file.

    Looks for 'MODULE name' (not 'MODULE PROCEDURE' or 'MODULE FUNCTION').
    """
    try:
        content = Path(filepath).read_text()
    except (FileNotFoundError, PermissionError):
        return None
    for line in content.split("\n"):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("!"):
            continue
        m = re.match(
            r'MODULE\s+(\w+)\s*$', stripped, re.IGNORECASE
        )
        if m:
            name = m.group(1)
            # Exclude 'MODULE PROCEDURE' etc.
            if name.upper() not in ("PROCEDURE", "FUNCTION", "SUBROUTINE"):
                return name
    return None


def _extract_kw_subroutines(
    filepath: str,
) -> tuple[list[str], list[str]]:
    """Extract kw_* PUBLIC declarations and subroutine bodies.

    Returns:
        (public_lines, subroutine_blocks) where:
        - public_lines: lines like '   PUBLIC kw_externs_in_nwtc_base'
        - subroutine_blocks: complete subroutine text blocks
    """
    try:
        content = Path(filepath).read_text()
    except (FileNotFoundError, PermissionError):
        return [], []

    lines = content.split("\n")
    public_lines = []
    subroutine_blocks = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Capture PUBLIC kw_* declarations
        if re.match(r'PUBLIC\s+kw_', stripped, re.IGNORECASE):
            public_lines.append(lines[i])
            i += 1
            continue

        # Capture SUBROUTINE kw_* ... END SUBROUTINE kw_*
        m = re.match(r'SUBROUTINE\s+(kw_\w+)', stripped, re.IGNORECASE)
        if m:
            sub_name = m.group(1)
            block_lines = [lines[i]]
            i += 1
            # Collect until END SUBROUTINE
            while i < len(lines):
                block_lines.append(lines[i])
                if re.match(
                    rf'\s*END\s+SUBROUTINE\s+{re.escape(sub_name)}\s*$',
                    lines[i].strip(), re.IGNORECASE,
                ):
                    break
                i += 1
            subroutine_blocks.append("\n".join(block_lines))
            i += 1
            continue

        i += 1

    return public_lines, subroutine_blocks


def _find_module_file_in_dir(
    module_name: str, search_dir: str, exclude_path: str = ""
) -> Optional[str]:
    """Find the file defining MODULE <name> within search_dir.

    Recursively searches for .f90/.F90 files containing 'MODULE <name>'.
    Excludes the file at exclude_path (the cross-module file itself).
    """
    exclude_abs = os.path.abspath(exclude_path) if exclude_path else ""
    search_path = Path(search_dir).resolve()

    for pattern in ["**/*.f90", "**/*.F90"]:
        for fpath in search_path.glob(pattern):
            if str(fpath.resolve()) == exclude_abs:
                continue
            try:
                content = fpath.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("!"):
                    continue
                m = re.match(
                    r'MODULE\s+(\w+)\s*$', stripped, re.IGNORECASE
                )
                if m and m.group(1).upper() == module_name.upper():
                    return str(fpath)
    return None


def _inject_kw_into_module(
    target_file: str,
    public_lines: list[str],
    subroutine_blocks: list[str],
):
    """Inject kw_* PUBLIC declarations and subroutines into a module file.

    - PUBLIC declarations are added after existing PUBLIC/IMPLICIT lines
    - Subroutine bodies are added before END MODULE
    - If no CONTAINS section exists, one is added
    """
    content = Path(target_file).read_text()
    lines = content.split("\n")

    # Find insertion points
    end_module_idx = None
    contains_idx = None
    last_public_or_implicit_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if stripped.startswith("!"):
            continue
        if re.match(r'END\s+MODULE\b', stripped):
            end_module_idx = i
        if stripped == "CONTAINS":
            contains_idx = i
        if re.match(r'(PUBLIC|PRIVATE|IMPLICIT)\b', stripped):
            last_public_or_implicit_idx = i

    if end_module_idx is None:
        return  # Can't find END MODULE — bail

    # Insert PUBLIC declarations after the last PUBLIC/IMPLICIT line
    if public_lines and last_public_or_implicit_idx is not None:
        insert_idx = last_public_or_implicit_idx + 1
        for pl in public_lines:
            lines.insert(insert_idx, pl)
            insert_idx += 1
            # Adjust indices
            if contains_idx is not None and contains_idx >= last_public_or_implicit_idx:
                contains_idx += 1
            end_module_idx += 1

    # Insert subroutines before END MODULE
    if subroutine_blocks:
        # Add CONTAINS if not present
        if contains_idx is None:
            lines.insert(end_module_idx, "")
            lines.insert(end_module_idx + 1, "CONTAINS")
            end_module_idx += 2

        # Insert subroutine blocks before END MODULE
        insert_lines = []
        for block in subroutine_blocks:
            insert_lines.append("")
            insert_lines.extend(block.split("\n"))

        for j, sl in enumerate(insert_lines):
            lines.insert(end_module_idx + j, sl)

    Path(target_file).write_text("\n".join(lines))


def find_call_sites(source_dir: str, function_name: str,
                    file_pattern: str = "*.f90") -> list[tuple[str, int, str]]:
    """
    Find all call sites for a function in the source tree.

    Returns:
        List of (filepath, line_number, line_text) tuples
    """
    results = []
    source_path = Path(source_dir)

    for fpath in source_path.rglob(file_pattern):
        try:
            lines = fpath.read_text().split("\n")
        except (UnicodeDecodeError, PermissionError):
            continue

        for i, line in enumerate(lines):
            if line_calls(line, function_name):
                results.append((str(fpath), i + 1, line.rstrip()))

    return results


def line_calls(line: str, function_name: str) -> bool:
    """Whether one source line calls `function_name`, by the rule `vit find` uses.

    Outside a comment, a call is a CALL statement naming it, with or without an
    argument list, or the name followed by `(` in an expression. The
    procedure's own SUBROUTINE or FUNCTION statement is not a call, including
    one with a prefix such as `REAL(ReKi) FUNCTION` or `PURE SUBROUTINE`. One
    line at a time: a function reference whose name and `(` are on different
    continuation lines is not found.
    """
    name = re.escape(function_name.upper())
    stripped = line.strip().upper()
    if "!" in stripped:
        stripped = stripped[:stripped.index("!")]
    if not (re.search(rf'\bCALL\s+{name}\b', stripped)
            or re.search(rf'\b{name}\s*\(', stripped)):
        return False
    return not _defines_procedure(name).match(stripped)


def _defines_procedure(name: str):
    """A SUBROUTINE or FUNCTION statement for `name`, prefixes allowed."""
    return re.compile(rf'^(?:[A-Z0-9_()\s*,=:]*?\s)?(?:FUNCTION|SUBROUTINE)\s+{name}\b')


# Construct openers, and the END keyword that closes each. Matched against the
# logical statement with comments stripped and whitespace collapsed.
_BLOCK_OPENERS = [
    (re.compile(r"^(?:\w+\s*:\s*)?IF\b.*\bTHEN$"), "IF"),
    (re.compile(r"^(?:\w+\s*:\s*)?DO\b"), "DO"),
    (re.compile(r"^(?:\w+\s*:\s*)?SELECT\s*(?:CASE|TYPE)\b"), "SELECT"),
    (re.compile(r"^(?:\w+\s*:\s*)?WHERE\s*\(.*\)$"), "WHERE"),
    (re.compile(r"^(?:\w+\s*:\s*)?FORALL\s*\(.*\)$"), "FORALL"),
    (re.compile(r"^(?:\w+\s*:\s*)?ASSOCIATE\b"), "ASSOCIATE"),
    (re.compile(r"^(?:\w+\s*:\s*)?BLOCK$"), "BLOCK"),
]

_END_PATTERNS = {
    "IF": re.compile(r"^END\s*IF\b"),
    "DO": re.compile(r"^END\s*DO\b"),
    "SELECT": re.compile(r"^END\s*SELECT\b"),
    "WHERE": re.compile(r"^END\s*WHERE\b"),
    "FORALL": re.compile(r"^END\s*FORALL\b"),
    "ASSOCIATE": re.compile(r"^END\s*ASSOCIATE\b"),
    "BLOCK": re.compile(r"^END\s*BLOCK\b"),
}


def _logical_stmt(lines: list, idx: int) -> str:
    """
    Normalize one source line into a comparable statement: strip a trailing
    comment, drop a trailing continuation marker, collapse whitespace, uppercase.

    Comment stripping is quote-aware, so a '!' inside a string literal is kept.
    """
    raw = lines[idx]
    out = []
    quote = None
    for ch in raw:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "!":
            break
        else:
            out.append(ch)
    text = "".join(out).strip()
    if text.endswith("&"):
        text = text[:-1].strip()
    return re.sub(r"\s+", " ", text).upper()


def _joined_stmt(lines: list, start_idx: int, end_idx: int) -> str:
    """Join a continued statement into one normalized line."""
    return re.sub(
        r"\s+", " ",
        " ".join(_logical_stmt(lines, i) for i in range(start_idx, end_idx + 1))
    ).strip()


def _extend_to_block_end(lines: list, start_idx: int, end_idx: int) -> int:
    """
    If the statement at start_idx..end_idx opens a block construct, return the
    index of its matching END statement. Otherwise return end_idx unchanged.

    Nested constructs of any kind are counted, so the returned END is the one
    that actually closes this construct. ELSE / ELSE IF / CASE / TYPE IS are
    continuations, not openers, and do not affect the depth.

    An unterminated construct (no matching END before end of file) returns
    end_idx unchanged rather than raising -- the caller's existing behaviour is
    then no worse than before this function existed.
    """
    stmt = _joined_stmt(lines, start_idx, end_idx)

    kind = None
    for pattern, name in _BLOCK_OPENERS:
        if pattern.match(stmt):
            kind = name
            break
    if kind is None:
        return end_idx

    # A DO with a numeric label terminates on that label, not END DO. Rare in
    # modern code and not handled; leave it alone rather than guess.
    if kind == "DO" and re.match(r"^(?:\w+\s*:\s*)?DO\s+\d+\b", stmt):
        return end_idx

    end_pattern = _END_PATTERNS[kind]
    depth = 1
    i = end_idx + 1
    while i < len(lines):
        # Skip continuation lines: only consider statement starts.
        if i > 0 and _logical_stmt(lines, i - 1).endswith("&"):
            i += 1
            continue
        current = _logical_stmt(lines, i)
        if not current:
            i += 1
            continue

        if end_pattern.match(current):
            depth -= 1
            if depth == 0:
                return i
        else:
            for pattern, name in _BLOCK_OPENERS:
                if name == kind and pattern.match(current):
                    depth += 1
                    break
        i += 1

    return end_idx


def insert_kgen_pragma(filepath: str, line_number: int,
                       function_name: str = "",
                       output_path: Optional[str] = None) -> str:
    """
    Insert KGen extraction pragma around a specific line.

    KGen requires the format:
        !$kgen begin_callsite <function_name>
        <call site>
        !$kgen end_callsite

    Note: underscores not spaces, function name on the begin line.

    Args:
        filepath: Source file to modify
        line_number: 1-indexed line to wrap with pragma
        function_name: Name of the function being extracted (required by KGen)
        output_path: Where to write (defaults to overwriting filepath)

    Returns:
        Path to the modified file
    """
    lines = Path(filepath).read_text().split("\n")
    idx = line_number - 1  # Convert to 0-indexed

    if idx < 0 or idx >= len(lines):
        raise ValueError(f"Line {line_number} out of range (file has {len(lines)} lines)")

    # Find the statement boundaries (handle continuation lines)
    start_idx = idx
    end_idx = idx

    # Check if this line is a continuation of a previous line
    while start_idx > 0 and lines[start_idx - 1].rstrip().endswith("&"):
        start_idx -= 1

    # Check if this line continues on the next line
    while end_idx < len(lines) - 1 and lines[end_idx].rstrip().endswith("&"):
        end_idx += 1

    # If the statement OPENS A BLOCK, extend to its terminator.
    #
    # A call embedded in a construct header -- most often
    # `IF (cond .AND. .NOT. f(x)) THEN` -- is a legitimate call site, but
    # wrapping only the header puts an unterminated IF inside the callsite
    # region. KGen then emits a malformed kernel: the construct's body is
    # dropped and its END statement is re-emitted out of order. The failure
    # surfaces as a Fortran syntax error in the generated kernel, far from its
    # cause.
    end_idx = _extend_to_block_end(lines, start_idx, end_idx)

    # Insert pragmas (KGen requires underscores and function name)
    indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    indent_str = " " * indent

    # Insert AFTER the end line, then BEFORE the start line (to preserve indices)
    lines.insert(end_idx + 1, f"{indent_str}!$kgen end_callsite")
    lines.insert(start_idx, f"{indent_str}!$kgen begin_callsite {function_name}")

    out_path = output_path or filepath
    Path(out_path).write_text("\n".join(lines))
    return out_path


def remove_kgen_pragma(filepath: str) -> bool:
    """Remove KGen pragma lines from a file; return whether any were removed.

    A file with no pragma is not rewritten, so its timestamp is unchanged and
    the build does not recompile it. Bytes that are not UTF-8 pass through.
    """
    raw = Path(filepath).read_bytes()
    if b"!$kgen" not in raw.lower():
        return False
    lines = raw.decode("utf-8", errors="surrogateescape").split("\n")
    filtered = [l for l in lines if not l.strip().lower().startswith("!$kgen")]
    if len(filtered) == len(lines):
        return False
    Path(filepath).write_bytes("\n".join(filtered).encode("utf-8", errors="surrogateescape"))
    return True


def _project_files(source_dir: str, patterns: tuple) -> list[Path]:
    """Files under source_dir, at any depth, matching any of `patterns`.

    The whole tree, because that is what KGen searches: include.ini lists
    every directory under source_dir that holds Fortran. Extraction's own
    output directories, kernel/ and state/ in the current directory, are
    skipped; they hold KGen's copies, not the project's sources.
    """
    root = Path(source_dir)
    if not root.is_dir():
        return []
    outputs = [(Path.cwd() / d).resolve() for d in ("kernel", "state")]
    found = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            resolved = path.resolve()
            if any(resolved == o or o in resolved.parents for o in outputs):
                continue
            found.add(path)
    return sorted(found)


def sweep_stale_pragmas(source_dir: str) -> list[str]:
    """Remove KGen pragmas left in any Fortran source under source_dir.

    KGen acts on every pragma in the sources it reads, so one left by an
    interrupted extraction redirects the next. This used to look only at the
    top level of source_dir, while KGen searches all of it.
    """
    return [str(p) for p in _project_files(source_dir, ("*.f90", "*.F90"))
            if remove_kgen_pragma(str(p))]


def remove_stale_kgen_org(source_dir: str) -> list[str]:
    """Delete KGen's `.kgen_org` source backups anywhere under source_dir."""
    removed = []
    for path in _project_files(source_dir, ("*.kgen_org",)):
        path.unlink()
        removed.append(str(path))
    return removed


def generate_include_ini(
    source_dirs: list[str],
    mod_dirs: list[str],
    output_path: str = "include.ini",
) -> str:
    """
    Generate KGen include.ini with module search paths.

    KGen's strace-based module discovery is unreliable — it often fails to
    capture -I/-J flags from cmake/make child processes. This function
    generates an include.ini that explicitly lists all directories KGen
    should search for .mod files and Fortran source files.

    The format is:
        [include]
        /path/to/ftnmods=
        /path/to/src=

    Args:
        source_dirs: Directories containing Fortran source files
        mod_dirs: Directories containing .mod files (e.g., build/ftnmods)
        output_path: Where to write include.ini

    Returns:
        Path to the generated file
    """
    lines = ["[include]"]

    # Add .mod directories first (higher priority)
    for d in mod_dirs:
        d = str(Path(d).resolve())
        if os.path.isdir(d):
            lines.append(f"{d}=")

    # Add source directories
    for d in source_dirs:
        d = str(Path(d).resolve())
        if os.path.isdir(d):
            lines.append(f"{d}=")

    Path(output_path).write_text("\n".join(lines) + "\n")
    return output_path


def discover_source_dirs(source_dir: str) -> list[str]:
    """
    Find all directories containing Fortran source files under source_dir.

    Returns absolute paths.
    """
    dirs = set()
    source_path = Path(source_dir).resolve()
    if not source_path.exists():
        return []
    for pattern in ["**/*.f90", "**/*.F90", "**/*.f", "**/*.F"]:
        for fpath in source_path.glob(pattern):
            dirs.add(str(fpath.parent))
    return sorted(dirs)


def discover_mod_dirs(build_dir: str) -> list[str]:
    """
    Find all directories containing .mod files under build_dir.

    Returns absolute paths.
    """
    dirs = set()
    build_path = Path(build_dir).resolve()
    if build_path.exists():
        for fpath in build_path.rglob("*.mod"):
            dirs.add(str(fpath.parent))
    return sorted(dirs)


def clean_kgen_artifacts(working_dir: str = ".") -> list[str]:
    """
    Remove stale KGen artifacts that cause problems on re-extraction.

    KGen reuses strace.log if it exists, which means it won't re-trace
    the build. Since the include paths may have been wrong in the stale
    strace, we must remove it so KGen generates a fresh one.

    Returns:
        List of files removed
    """
    removed = []
    for artifact in ["kgen.log", "model.ini"]:
        path = os.path.join(working_dir, artifact)
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)

    # Create a dummy strace.log so KGen skips its compflag build phase.
    # KGen's compflag phase wraps the full build command with strace to
    # discover compiler flags — a clean + full rebuild (~100s) that we
    # don't need because VIT provides include.ini independently.
    # If strace.log exists, KGen skips the build entirely (line 62 of
    # compflag/main.py: "Reusing KGen strace logfile").
    strace_path = os.path.join(working_dir, "strace.log")
    if not os.path.exists(strace_path):
        Path(strace_path).write_text("# dummy — VIT provides include.ini\n")
        removed.append(f"Created dummy {strace_path}")

    # Remove elapsedtime/ directory — KGen stores ETime profiling data here
    # and reuses it across extractions, which can embed the wrong function
    # name in state capture instrumented code.
    etime_dir = os.path.join(working_dir, "elapsedtime")
    if os.path.isdir(etime_dir):
        shutil.rmtree(etime_dir)
        removed.append(etime_dir)

    # Remove model/ directory — KGen stores ElapsedTime raw profiling data
    # here (model/__data__/<id>/0.0) and reuses it if present. The data is
    # function-specific but not labeled by function name, so stale data from
    # a previous extraction causes ElapsedTime to use the wrong function's
    # timing profile for invocation selection.
    model_dir = os.path.join(working_dir, "model")
    if os.path.isdir(model_dir):
        shutil.rmtree(model_dir)
        removed.append(model_dir)

    # Note: .kgen_org files (KGen's instrumented source backups) are
    # cleaned in run_kgen_extract() which knows the source_dir.

    # Don't remove include.ini — we generate it ourselves and want KGen to reuse it
    # Don't remove kernel/ or state/ — user may want to keep previous data

    return removed


def check_editable_installs(project_dir: str, package_name: str = "") -> list[str]:
    """
    Check for editable pip installs that might load the wrong library.

    When a project is installed with `pip install -e .`, Python's import
    system follows .egg-link files to the original source directory. If the
    user has multiple checkouts (e.g., /project/original and /project/testing),
    the editable install may point to the wrong one — meaning the simulation
    loads an uninstrumented library instead of the KGen-instrumented one.

    This checks for common indicators and returns warnings.

    Args:
        project_dir: The project directory we WANT to be active
        package_name: Optional package name to check (e.g., "rosco")

    Returns:
        List of warning messages (empty if no issues detected)
    """
    warnings_list = []
    project_dir = str(Path(project_dir).resolve())

    # Check if there's a pip editable install pointing elsewhere
    try:
        result = subprocess.run(
            ["pip", "show", "-f", package_name] if package_name else ["pip", "list", "--editable"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout

        if package_name:
            # Look for "Editable project location:" or "Location:"
            for line in output.split("\n"):
                if "location:" in line.lower():
                    location = line.split(":", 1)[1].strip()
                    location_resolved = str(Path(location).resolve())
                    if location_resolved != project_dir:
                        warnings_list.append(
                            f"WARNING: Editable install for '{package_name}' points to:\n"
                            f"  {location_resolved}\n"
                            f"but VIT is working in:\n"
                            f"  {project_dir}\n"
                            f"The simulation may load the WRONG (uninstrumented) library.\n"
                            f"Fix: cd {project_dir} && pip install -e ."
                        )
        else:
            # Check if any editable installs point outside project_dir
            for line in output.split("\n"):
                if project_dir not in line and "editable" in line.lower():
                    warnings_list.append(
                        f"WARNING: Found editable install not in project dir: {line.strip()}"
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # pip not available, skip check

    return warnings_list


def cmake_reconfigure_command(build_dir: str) -> str:
    """Shell command that re-runs CMake's configure step for `build_dir`.

    Inside a configured build tree, `cmake .` regenerates it from the source
    directory recorded in its cache, wherever that is. The command used to be
    `cmake ..`, which assumed the build tree sat directly under the top-level
    CMakeLists.txt. With any other layout it configured the wrong directory,
    the failure was discarded, kgen_utils.f90 never entered the build, and the
    capture came back empty. `cmake ..` remains the fallback for a tree that
    has not been configured yet, the one case where it is the right guess.
    """
    return (f'cd "{build_dir}" && '
            f'if [ -f CMakeCache.txt ]; then cmake .; else cmake ..; fi')


def generate_state_capture_build_script(
    build_cmd: str,
    install_cmd: str = "",
    cmake_file: str = "",
    source_dir: str = "",
    build_dir: str = "",
    kernel_dir: str = "kernel",
    assumed_size_arrays: dict = None,
    dll_persistence: dict = None,
    state_dir: str = "state",
    add_source_cmd: str = "",
) -> str:
    """
    Generate a shell script that wraps the build command for state capture.

    KGen leaves an instrumented source tree that will not build, or will build
    and then silently capture nothing. This wrapper is what makes it work. It
    has grown well past its original job -- nine steps, not the one the module
    docstring used to claim -- so they are enumerated here with provenance,
    because a comment that undersells what code does is how bugs hide in it
    (see dev note 202608071058 for the FC_0 case).

    Steps, and whose problem each one is:

      1.  Copy kgen_utils.f90 from kernel/ into the source tree.
      2.  sed it into CMakeLists.txt, then reconfigure (step 3).
          [KGen gap, correctly ours] KGen instruments code with calls to
          kgen_utils functions (kgen_init_vars, kgen_check_save, ...) but never
          arranges for kgen_utils.f90 to be compiled. On Linux a shared library
          links with unresolved symbols, so the build SUCCEEDS and capture then
          produces nothing at runtime. Fixing this upstream would require KGen
          to understand arbitrary build systems -- the same objection that got
          --auto-allocate rejected (dev note 202605011207) -- so a build-command
          wrapper is the right shape, not debt.

      2b. sed assumed-size array actuals into explicit sections at kw_* call
          sites.
          [KGen code-generator bug -- STILL A WORKAROUND, fix scoped not made]
          KGen emits write subroutines with assumed-shape DIMENSION(:) dummies
          while passing assumed-size (*) actuals, which is invalid Fortran. We
          rewrite the actual to an explicit section, var(1:N), where N comes
          from VIT's evidence-based extent inference (exit code 10) -- KGen
          cannot infer N itself.

          This is the one patcher here that does meet CLAUDE.md's "fix it in
          the fork" bar, because KGen could emit explicit-shape dummies that
          take the extent as an argument. Not done yet: the kw_* generator
          builds declarations through the AST builder rather than a template,
          KGen has no unit test that would catch a regression in it, and
          verifying a change means a full extraction cycle against clean
          source. Do it the next time extraction is exercised, not as a
          drive-by. See dev note 202608071141.

      2c. Reset project SAVE guards between scenarios.
          [Ours, config-driven] Python ctypes may not truly dlclose() a Fortran
          shared library, so SAVE variables persist across scenario boundaries.
          Which guards to reset is per-project, configured in vit.yaml under
          kgen.dll_persistence. KGen's own SAVE arrays do NOT need this -- each
          extraction phase is a separate subprocess.

      2d. Touch Fortran sources so cmake recompiles them.

      2e. Transplant kw_* subroutines across cmake target boundaries.
          [KGen limitation, correctly ours] KGen follows USE chains and
          instruments files outside the build target. Those files are never
          compiled, so their kw_* subroutines surface as unresolved symbols.
          Fixing upstream would require KGen to model build-system target
          boundaries; same objection as step 2.

      3.  Reconfigure cmake if CMakeLists.txt changed. Required: cmake caches
          its source list, so patching without reconfiguring means
          `cmake --build .` silently uses the old list and never compiles
          kgen_utils.f90.

      4.  Run the real build, then the install step if any.
      5.  Strip the tagged persistence lines, restoring the source tree.

    What the step-2 patch assumes: the build file lists sources one per line,
    and has a `src/` directory beside it. It inserts `src/kgen_utils.f90` after
    the first line that is a single Fortran source path, and copies the file
    into that `src/`. A build file with no such line stops the build with an
    error naming `kgen.add_source_cmd`, the override. Without a `src/`
    directory the inserted entry names a file that is not there, and the build
    fails.

    The script is written to a temp file and returned as a command.

    Args:
        build_cmd: Original build command
        install_cmd: Optional post-build install command
        cmake_file: Path to CMakeLists.txt (for cmake projects)
        source_dir: Directory containing Fortran source files
        kernel_dir: Where KGen puts kernel/ (for finding kgen_utils.f90)

    Returns:
        Path to the generated script
    """
    # Find where kgen_utils.f90 will be
    kgen_utils_src = os.path.join(os.path.abspath(kernel_dir), "kgen_utils.f90")

    # Determine target location for kgen_utils.f90
    # Prefer the cmake_file's src/ subdirectory (matches the sed pattern that adds
    # "src/kgen_utils.f90" to the CMakeLists.txt source list). Fall back to source_dir.
    if cmake_file:
        cmake_dir = os.path.dirname(os.path.abspath(cmake_file))
        src_subdir = os.path.join(cmake_dir, "src")
        if os.path.isdir(src_subdir):
            kgen_utils_dest = os.path.join(src_subdir, "kgen_utils.f90")
        else:
            kgen_utils_dest = os.path.join(cmake_dir, "kgen_utils.f90")
    elif source_dir:
        kgen_utils_dest = os.path.join(os.path.abspath(source_dir), "kgen_utils.f90")
    else:
        kgen_utils_dest = os.path.join(os.getcwd(), "kgen_utils.f90")

    cmake_file_abs = os.path.abspath(cmake_file) if cmake_file else ""

    script_lines = [
        "#!/bin/bash",
        "set -e",
        "",
        "# VIT: State capture build wrapper",
        "# Prepares the KGen-instrumented tree so the build links and capture",
        "# actually produces state. See build_state_capture_wrapper's docstring",
        "# for all nine steps and which are KGen's problem vs ours.",
        "",
        f'KGEN_UTILS_SRC="{kgen_utils_src}"',
        f'KGEN_UTILS_DEST="{kgen_utils_dest}"',
        f'CMAKE_FILE="{cmake_file_abs}"',
        "",
        "# Diagnostics are opt-in: export VIT_WRAPPER_LOG=/path/to/log to enable.",
        "# Worth doing when capture reports no state data -- several failures here",
        "# are silent by construction (a shared library links with unresolved",
        "# symbols, so the build succeeds and only the capture comes back empty).",
        'VIT_LOG="${VIT_WRAPPER_LOG:-/dev/null}"',
        'echo "VIT_WRAPPER: invoked at $(date), cwd=$(pwd)" >> "$VIT_LOG"',
        'echo "VIT_WRAPPER: KGEN_UTILS_SRC=$KGEN_UTILS_SRC exists=$([ -f $KGEN_UTILS_SRC ] && echo YES || echo NO)" >> "$VIT_LOG"',
        "",
        "# Step 1: Copy kgen_utils.f90 into source tree (if it exists)",
        'if [ -f "$KGEN_UTILS_SRC" ]; then',
        '    cp "$KGEN_UTILS_SRC" "$KGEN_UTILS_DEST"',
        "    # (kgen_utils.f90 copied as-is, no modifications needed)",
        '    echo "VIT: Copied kgen_utils.f90 to source tree"',
        '    echo "VIT_WRAPPER: copied kgen_utils.f90 to $KGEN_UTILS_DEST" >> "$VIT_LOG"',
        "",
    ]

    # Step 2: Patch CMakeLists.txt if cmake project
    if cmake_file_abs:
        build_dir_abs = os.path.abspath(build_dir) if build_dir else ""
        script_lines.extend([
            '    # Patch CMakeLists.txt to include kgen_utils.f90 (only once)',
            '    if [ -f "$CMAKE_FILE" ]; then',
            '        # Backup only if no backup exists (wrapper may run multiple times)',
            '        if [ ! -f "${CMAKE_FILE}.vit_backup" ]; then',
            '            cp "$CMAKE_FILE" "${CMAKE_FILE}.vit_backup"',
            '        fi',
            '        # Add kgen_utils.f90 only if not already present',
            '        if ! grep -q "kgen_utils.f90" "$CMAKE_FILE"; then',
        ])
        if add_source_cmd:
            # An explicit override, for a build file the default cannot match.
            # A heuristic that can be replaced is a heuristic; one that cannot
            # is a requirement on every future codebase.
            script_lines.append(f'            ( {add_source_cmd} )')
        else:
            # Insert after the first Fortran source entry in the list.
            #
            # The old pattern was `^\\( *src\\/[A-Za-z_]*\\.f90\\)$`, which
            # assumed a `src/` prefix, a lowercase `.f90` extension, and file
            # names free of digits, hyphens and dots. It is widened here to any
            # path and any Fortran extension -- but widening a heuristic is not
            # the fix, because the next layout will break it too.
            script_lines.append(
                r"""            sed -i -E '0,/^([[:space:]]*"?[A-Za-z0-9_.\/${}-]+\.([fF]|[fF]9[05]|[fF]0[38])"?)$/s//\1\n    src\/kgen_utils.f90/' "$CMAKE_FILE" """)
        script_lines.extend([
            # THE FIX THAT MATTERS. The success message below used to print
            # unconditionally, so a sed that matched nothing announced that it
            # had patched the file. The build then succeeded, the shared library
            # linked with kgen_init_vars/kgen_check_save unresolved (a Linux .so
            # permits that), the simulation ran, and the only symptom was an
            # empty capture reported as "No state data captured" -- with nothing
            # pointing back here. See dev note 202608071141.
            '            if ! grep -q "kgen_utils.f90" "$CMAKE_FILE"; then',
            '                echo "VIT ERROR: could not add kgen_utils.f90 to $CMAKE_FILE." >&2',
            '                echo "  The source list did not match the expected shape." >&2',
            '                echo "  Set kgen.add_source_cmd in vit.yaml to a command that adds" >&2',
            '                echo "  src/kgen_utils.f90 to this build file, e.g.:" >&2',
            r'''                echo "    add_source_cmd: sed -i '/^set(SOURCES/a\\    src/kgen_utils.f90' \\"\$CMAKE_FILE\\"" >&2''',
            '                exit 1',
            '            fi',
            '            echo "VIT: Patched CMakeLists.txt to include kgen_utils.f90"',
        ])
        # Reconfigure cmake so it picks up the new source file.
        # cmake caches its source list internally — patching CMakeLists.txt
        # without reconfiguring means `cmake --build .` silently uses the
        # old source list and never compiles kgen_utils.f90.
        # This is critical for incremental build commands (cmake --build .)
        # as opposed to full clean builds (rm -rf * && cmake .. && cmake --build .).
        if build_dir_abs:
            script_lines.extend([
                f'            echo "VIT: Reconfiguring cmake to pick up kgen_utils.f90"',
                f'            {cmake_reconfigure_command(build_dir_abs)} > /dev/null 2>&1',
            ])
        script_lines.extend([
            '        fi',
            '    else',
            # `cmake_file` was configured, so its absence is a misconfiguration
            # and not a reason to carry on. Skipping quietly here produced the
            # same empty capture as a failed sed, one step earlier.
            '        echo "VIT ERROR: cmake_file is configured but $CMAKE_FILE does not exist." >&2',
            '        exit 1',
            "    fi",
        ])

    script_lines.extend([
        "else",
        '    echo "VIT_WRAPPER: kgen_utils.f90 not found at $KGEN_UTILS_SRC — skipping injection" >> "$VIT_LOG"',
        "fi",
        "",
    ])

    # Step 2b: Patch assumed-size arrays in instrumented source.
    # KGen generates write subroutines with DIMENSION(:) (assumed-shape)
    # parameters, but assumed-size (*) arrays can't be passed to assumed-shape.
    # Fix: replace bare array names with explicit sections at kw_* call sites.
    if assumed_size_arrays and source_dir:
        script_lines.append("# Step 2b: Patch assumed-size arrays in instrumented source")
        source_dir_abs = os.path.abspath(source_dir)
        for varname, size in assumed_size_arrays.items():
            varname_lower = varname.lower()
            # Replace (varname, kgen_unit with (varname(1:N), kgen_unit in kw_* calls
            # Case-insensitive because KGen lowercases variable names
            sed_pattern = f"s/({varname_lower}, kgen_unit/({varname_lower}(1:{size}), kgen_unit/gI"
            script_lines.extend([
                f'for f90 in "{source_dir_abs}"/*.f90 "{source_dir_abs}"/*.F90; do',
                f'    [ -f "$f90" ] || continue',
                f"    sed -i '{sed_pattern}' \"$f90\"",
                f'done',
                f'echo "VIT: Patched assumed-size array {varname} with size {size}"',
            ])
        script_lines.append("")

    # Step 2c: DLL SAVE variable persistence workaround.
    # When Python's ctypes loads a Fortran shared library, dlclose() may not
    # truly unload it — SAVE variables persist across scenario boundaries
    # within a single simulation run. Project-specific SAVE guards (e.g.,
    # AlreadyInitialized) need resetting so each scenario re-initializes.
    # Resets are configured in vit.yaml under kgen.dll_persistence.
    #
    # Note: KGen's own SAVE arrays (kgen_isstop, kgen_invoke, etc.) do NOT
    # need resetting. Each extraction phase runs as a separate subprocess
    # with fresh SAVE variables. Within a single run, KGen's invocation
    # counters naturally exceed the capture range (0:0:1-20) after the
    # first scenario, which is the desired behavior.
    if dll_persistence and source_dir:
        resets = dll_persistence.get("resets", [])

        if resets:
            source_dir_abs = os.path.abspath(source_dir)
            # Tag all inserted lines with !VIT_DLL_RESET so they can be
            # cleanly removed after the build. Without this, sed patches
            # accumulate in files KGen doesn't restore (e.g., DISCON.F90
            # when extracting from Controllers.f90).
            tag = "!VIT_DLL_RESET"
            script_lines.extend([
                "# Step 2c: Reset SAVE variables (DLL persistence workaround)",
                f'for f90 in "{source_dir_abs}"/*.f90 "{source_dir_abs}"/*.F90; do',
                '    [ -f "$f90" ] || continue',
            ])

            # Project-specific resets (configured in vit.yaml).
            for reset in resets:
                anchor = reset.get("anchor", "")
                code = reset.get("code", "")
                if anchor and code:
                    script_lines.append(
                        f"    sed -i '/{anchor}/i\\    {code} {tag}' \"$f90\""
                    )

            script_lines.extend([
                'done',
                'echo "VIT: Patched DLL SAVE variable reset in instrumented source"',
                "",
            ])

    # Step 2d: Touch all Fortran source files in the cmake source directory
    # to force cmake to recompile them. KGen's state Makefile copies instrumented
    # source files over the originals, but on Docker-mounted filesystems the
    # timestamps may not change, causing cmake to skip recompilation.
    if cmake_file_abs:
        cmake_src_dir = os.path.join(os.path.dirname(cmake_file_abs), "src")
        script_lines.extend([
            f'# Step 2d: Touch source files to force cmake recompilation',
            f'# (Docker filesystem timestamps may not update on cp)',
            f'for f90 in "{cmake_src_dir}"/*.f90 "{cmake_src_dir}"/*.F90; do',
            f'    [ -f "$f90" ] && touch "$f90"',
            f'done',
            "",
        ])

    # Step 2e: Fix cross-module KGen instrumentation.
    # KGen instruments files across cmake target boundaries (following USE chains).
    # Files outside the build target aren't compiled, so their kw_* subroutines
    # cause unresolved symbol errors. Transplant kw_* subroutines from cross-module
    # files into the in-target module file that defines the same Fortran MODULE.
    if cmake_file_abs and source_dir:
        state_dir_abs = os.path.abspath(state_dir)
        source_dir_abs = os.path.abspath(source_dir)
        script_lines.extend([
            "# Step 2e: Fix cross-module KGen instrumentation",
            f'python3 -c "',
            f'import sys; sys.path.insert(0, \\"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}\\")',
            f'from vit.extract import fix_cross_module_instrumentation',
            f'fixes = fix_cross_module_instrumentation(',
            f'    \\"{state_dir_abs}\\",',
            f'    \\"{cmake_file_abs}\\",',
            f'    \\"{source_dir_abs}\\"',
            f')',
            f'for f in fixes: print(\\"VIT: \\" + f)',
            f'" 2>&1 | tee -a "$VIT_LOG"',
            "",
        ])

    # Step 3: Reconfigure cmake if CMakeLists was modified (kgen_utils.f90 added).
    # cmake caches its source list internally — patching CMakeLists.txt without
    # reconfiguring means `cmake --build .` silently uses the old source list
    # and never compiles kgen_utils.f90. This is essential for incremental build
    # commands (cmake --build .) as opposed to full clean builds.
    if cmake_file_abs and build_dir:
        build_dir_abs = os.path.abspath(build_dir)
        script_lines.extend([
            "# Step 3: Reconfigure cmake if CMakeLists was modified",
            f'if [ -f "{cmake_file_abs}.vit_backup" ]; then',
            f'    echo "VIT_WRAPPER: reconfiguring cmake" >> "$VIT_LOG"',
            f'    {cmake_reconfigure_command(build_dir_abs)} >> "$VIT_LOG" 2>&1',
            f'    echo "VIT_WRAPPER: cmake reconfigure exit code=$?" >> "$VIT_LOG"',
            f'fi',
            "",
        ])

    script_lines.extend([
        "# Step 4: Run the actual build",
        "# Temporarily disable set -e so the cleanup step always runs",
        "set +e",
        f"{build_cmd}",
        "BUILD_RC=$?",
        "set -e",
        "",
    ])

    # Step 4: Install
    if install_cmd:
        script_lines.extend([
            "# Step 4: Install (inside set +e to ensure cleanup runs)",
            f'if [ $BUILD_RC -eq 0 ]; then',
            f"    {install_cmd} || true",
            "fi",
            "",
        ])

    # Step 5: Remove tagged DLL persistence lines from source.
    # The compiled library already has the patches baked in. Removing them
    # from source prevents accumulation across sequential extractions.
    if dll_persistence and source_dir:
        resets_cfg = dll_persistence.get("resets", [])
        if resets_cfg:
            source_dir_abs = os.path.abspath(source_dir)
            script_lines.extend([
                "# Step 5: Remove VIT_DLL_RESET tagged lines from source",
                f'for f90 in "{source_dir_abs}"/*.f90 "{source_dir_abs}"/*.F90; do',
                '    [ -f "$f90" ] || continue',
                "    sed -i '/!VIT_DLL_RESET/d' \"$f90\"",
                'done',
                "",
            ])

    script_lines.extend([
        "exit $BUILD_RC",
    ])

    # Write script
    script_path = os.path.join(os.getcwd(), ".vit_build_wrapper.sh")
    Path(script_path).write_text("\n".join(script_lines) + "\n")
    os.chmod(script_path, 0o755)

    return script_path


def cleanup_kgen_utils_injection(
    cmake_file: str = "",
    source_dir: str = "",
    build_dir: str = "",
) -> list[str]:
    """
    Remove kgen_utils.f90 injection after state capture is complete.

    Restores CMakeLists.txt from backup, removes the copied kgen_utils.f90,
    and reconfigures CMake to clear stale dependency cache entries.

    Without the CMake reconfiguration, DependInfo.cmake retains
    kgen_utils.f90 as a source file even after CMakeLists.txt is restored.
    This causes all subsequent cmake --build invocations to fail with
    "No rule to make target kgen_utils.f90". KGen's ElapsedTime phase
    is particularly affected because it runs the build before the
    Extractor generates kernel/kgen_utils.f90.

    Returns:
        List of files cleaned up
    """
    cleaned = []

    # Restore CMakeLists.txt
    if cmake_file:
        backup = cmake_file + ".vit_backup"
        if os.path.exists(backup):
            shutil.move(backup, cmake_file)
            cleaned.append(f"Restored {cmake_file}")

    # Remove injected kgen_utils.f90 (check cmake src/ subdir first, then source_dir)
    removed_kgen_utils = False
    if cmake_file:
        cmake_dir = os.path.dirname(os.path.abspath(cmake_file))
        for candidate in [os.path.join(cmake_dir, "src", "kgen_utils.f90"),
                          os.path.join(cmake_dir, "kgen_utils.f90")]:
            if os.path.exists(candidate):
                os.remove(candidate)
                cleaned.append(f"Removed {candidate}")
                removed_kgen_utils = True
                break
    if not removed_kgen_utils and source_dir:
        injected = os.path.join(source_dir, "kgen_utils.f90")
        if os.path.exists(injected):
            os.remove(injected)
            cleaned.append(f"Removed {injected}")

    # Reconfigure CMake to clear stale DependInfo.cmake references.
    if build_dir and os.path.isdir(build_dir):
        subprocess.run(
            cmake_reconfigure_command(build_dir),
            shell=True, capture_output=True,
        )
        cleaned.append(f"Reconfigured CMake in {build_dir}")

    # Remove wrapper script
    wrapper = os.path.join(os.getcwd(), ".vit_build_wrapper.sh")
    if os.path.exists(wrapper):
        os.remove(wrapper)
        cleaned.append(f"Removed {wrapper}")

    return cleaned


def generate_kgen_command(
    source_file: str,
    build_cmd: str,
    run_cmd: str,
    clean_cmd: str = "",
    kgen_path: str = "kgen",
    output_dir: Optional[str] = None,
    invocation: str = "",
    preprocessor_defines: Optional[list[str]] = None,
) -> str:
    """
    Generate the kgen command line.

    Args:
        source_file: Path to the Fortran file with KGen pragma
        build_cmd: Command to build the project (may include install step)
        run_cmd: Command to run the simulation
        clean_cmd: Command to clean build artifacts
        kgen_path: Path to KGen executable
        output_dir: Where to put kernel/ and state/ directories
        invocation: KGen invocation spec: "MPI_rank:OMP_thread:start-end"
                    e.g., "0:0:1-20" captures calls 1-20 on rank 0 thread 0
        preprocessor_defines: List of preprocessor macros to pass to KGen
                    (e.g., ["OPENFAST_DOUBLE_PRECISION"]). KGen uses these to
                    resolve #ifdef conditionals in Fortran source during parsing.

    Returns:
        Shell command string
    """
    parts = [kgen_path, source_file]

    parts.append(f"--cmd-build '{build_cmd}'")
    parts.append(f"--cmd-run '{run_cmd}'")

    if clean_cmd:
        parts.append(f"--cmd-clean '{clean_cmd}'")

    if output_dir:
        parts.append(f"--outdir {output_dir}")

    if invocation:
        parts.append(f"--invocation {invocation}")

    if preprocessor_defines:
        for define in preprocessor_defines:
            parts.append(f"-D {define}")

    return " ".join(parts)


def detect_assumed_size_from_source(source_file: str, line_num: int) -> list[str]:
    """Detect assumed-size (*) array parameters in the subroutine containing line_num.

    Reads the source file, finds the enclosing SUBROUTINE/FUNCTION header above
    line_num, and returns the names of any parameters declared with (*).

    Args:
        source_file: Path to the Fortran source file containing the call site
        line_num: 1-indexed line number within the file

    Returns:
        List of parameter names that are assumed-size (*) arrays.
    """
    content = Path(source_file).read_text()
    lines = content.split("\n")

    # Search backwards from line_num to find enclosing SUBROUTINE/FUNCTION
    enclosing_start = None
    for i in range(min(line_num - 1, len(lines) - 1), -1, -1):
        stripped = lines[i].strip().upper()
        if stripped.startswith("!"):
            continue
        if re.match(r'\s*(SUBROUTINE|FUNCTION)\s+\w+\s*\(', stripped):
            enclosing_start = i
            break

    if enclosing_start is None:
        return []

    # Find the matching END SUBROUTINE/FUNCTION
    enclosing_end = None
    for i in range(line_num, len(lines)):
        stripped = lines[i].strip().upper()
        if re.match(r'\s*END\s+(SUBROUTINE|FUNCTION)\b', stripped):
            enclosing_end = i
            break

    if enclosing_end is None:
        enclosing_end = len(lines) - 1

    # Extract the subroutine body and scan for (*) declarations
    assumed_size_names = []
    body = "\n".join(lines[enclosing_start:enclosing_end + 1])

    # Match patterns like: REAL(ReKi), INTENT(INOUT) :: avrSWAP(*)
    # Also: DIMENSION(*) in attribute list
    for line in lines[enclosing_start:enclosing_end + 1]:
        stripped = line.strip()
        if stripped.upper().startswith("!"):
            continue
        # Inline dimension: varname(*)
        for m in re.finditer(r'(\w+)\s*\(\s*\*\s*\)', stripped):
            varname = m.group(1).upper()
            # Skip if it's a type keyword (e.g., CHARACTER(*))
            if varname in ("REAL", "INTEGER", "LOGICAL", "CHARACTER", "COMPLEX",
                           "DIMENSION", "TYPE", "KIND"):
                continue
            assumed_size_names.append(m.group(1))
        # DIMENSION(*) attribute — find the variable name after ::
        if re.search(r'DIMENSION\s*\(\s*\*\s*\)', stripped, re.IGNORECASE):
            after_colons = stripped.split("::")[-1] if "::" in stripped else ""
            for m in re.finditer(r'(\w+)', after_colons):
                assumed_size_names.append(m.group(1))

    return list(dict.fromkeys(assumed_size_names))  # deduplicate, preserve order


def infer_assumed_size_arrays(
    source_dir: str,
    assumed_size_arrays: dict[str, int],
) -> list[Assumption]:
    """Scan source for assumed-size array index accesses and validate/infer sizes.

    For each assumed-size array in the dict, scans all Fortran source files for:
    1. Literal index accesses: arrayname(N) where N is a numeric constant
    2. Variable index accesses: arrayname(expr) where expr involves a variable
    3. Bounds checks: IF (var < N) or IF (var >= N) patterns that constrain index variables

    If configured_size > 0 and >= inferred_min: passes silently (adequate).
    If configured_size > 0 and < inferred_min: returns Assumption (undersized).
    If configured_size == 0: returns Assumption with inferred size (auto-detect).

    Returns a list of Assumptions for any arrays needing user confirmation.
    Returns empty list if all sizes are adequate.
    """
    assumptions = []
    src = Path(source_dir)
    f90_files = sorted(src.glob("*.f90")) + sorted(src.glob("*.F90"))

    for varname, configured_size in assumed_size_arrays.items():
        varname_lower = varname.lower()
        max_literal = 0
        literal_loc = ""
        variable_accesses = []
        bounds_checks = []

        for f90 in f90_files:
            content = f90.read_text()
            lines = content.split("\n")
            fname = f90.name

            for i, line in enumerate(lines, 1):
                line_stripped = line.strip()
                # Skip comments
                if line_stripped.startswith("!"):
                    continue
                line_upper = line_stripped.upper()

                # 1. Literal index: arrayname(digits)
                for m in re.finditer(
                    rf'\b{varname_lower}\s*\(\s*(\d+)\s*\)',
                    line_stripped, re.IGNORECASE
                ):
                    idx = int(m.group(1))
                    if idx > max_literal:
                        max_literal = idx
                        literal_loc = f"{fname}:{i}"

                # 2. Variable index: arrayname(expr) where expr contains %
                #    (derived type field access, e.g., CntrPar%CC_GroupIndex(I))
                for m in re.finditer(
                    rf'\b{varname_lower}\s*\(\s*([^)]*%[^)]+)\)',
                    line_stripped, re.IGNORECASE
                ):
                    expr = m.group(1).strip()
                    access = f"{varname}({expr}) at {fname}:{i}"
                    if access not in variable_accesses:
                        variable_accesses.append(access)

                # 3. Bounds checks: IF (field < N) or IF (field >= N) patterns
                #    Look for comparisons with numeric constants near GroupIndex-like names
                for m in re.finditer(
                    r'(\w+%\w*[Ii]ndex\w*)\s*\(\s*\w+\s*\)\s*(<|>=?)\s*(\d+)',
                    line_stripped
                ):
                    field = m.group(1)
                    op = m.group(2)
                    val = int(m.group(3))
                    check = f"{field} {op} {val} ({fname}:{i})"
                    if check not in bounds_checks:
                        bounds_checks.append(check)

        # Compute inferred minimum from bounds checks
        inferred_min = max_literal
        for check in bounds_checks:
            # Extract the numeric value from each check
            m = re.search(r'(\d+)', check.split("(")[0].split()[-1]
                          if "(" in check else check.split()[-1])
            if not m:
                # Try extracting from the full check string
                nums = re.findall(r'\d+', check)
                if nums:
                    val = max(int(n) for n in nums)
                    if val > inferred_min:
                        inferred_min = val
            else:
                val = int(m.group(1))
                if val > inferred_min:
                    inferred_min = val

        # Account for +1 offsets in variable accesses (e.g., avrSWAP(idx+1))
        for access in variable_accesses:
            if "+1" in access or "+ 1" in access:
                inferred_min = max(inferred_min, inferred_min + 1)
                break

        # Add margin (10% or at least 100)
        if inferred_min > 0:
            recommended = inferred_min + max(int(inferred_min * 0.1), 100)
        else:
            recommended = configured_size  # No evidence found, keep configured

        # Determine if confirmation or error is needed
        needs_confirm = False
        needs_error = False
        if configured_size == 0 and inferred_min == 0:
            # Auto-detect mode with no evidence: hard error
            needs_error = True
        elif configured_size == 0:
            # Auto-detect mode with evidence: confirm with inferred size
            needs_confirm = True
        elif configured_size < inferred_min:
            # Configured size is undersized
            needs_confirm = True

        if needs_error:
            assumptions.append(Assumption(
                id=f"assumed_size_{varname}_no_evidence",
                summary=(f"Detected assumed-size array {varname}(*) but found no index "
                         f"evidence in source. Cannot infer size automatically."),
                assumed_value="",
                inferred_from="no evidence found",
                context=(f"VIT detected {varname} as an assumed-size (*) array but could "
                         f"not find any literal index accesses or bounds checks in the "
                         f"source directory. Provide the size explicitly with "
                         f"--assumed-size {varname}=<SIZE> or configure "
                         f"kgen.assumed_size_arrays in vit.yaml."),
                cli_override=f"--assumed-size {varname}=<SIZE>",
            ))

        if needs_confirm:
            # Build evidence context
            context_lines = []
            if max_literal > 0:
                context_lines.append(f"Max literal index: {max_literal} ({literal_loc})")
            if variable_accesses:
                context_lines.append("Variable index accesses:")
                for va in variable_accesses:
                    context_lines.append(f"  - {va}")
            if bounds_checks:
                context_lines.append("Index bounds checks found in source:")
                for bc in bounds_checks:
                    context_lines.append(f"  - {bc}")
            context_lines.append(f"Inferred minimum index: {inferred_min}")

            if configured_size == 0:
                summary = (f"Detected assumed-size array {varname}(*). "
                           f"Inferred size {recommended} from source evidence.")
                context_lines.append(
                    f"VIT detected {varname} as an assumed-size (*) array and scanned "
                    f"source for index accesses. The recommended size is {recommended} "
                    f"(inferred minimum {inferred_min} + safety margin)."
                )
            else:
                summary = (f"assumed_size_arrays.{varname} ({configured_size}) "
                           f"is less than inferred minimum ({inferred_min})")
                context_lines.append(f"Configured size: {configured_size}")
                context_lines.append(
                    f"WARNING: Configured size ({configured_size}) is less than the "
                    f"inferred minimum ({inferred_min}). Undersizing causes silent heap "
                    f"corruption — Fortran cannot bounds-check assumed-size arrays."
                )

            assumptions.append(Assumption(
                id=f"assumed_size_{varname}",
                summary=summary,
                assumed_value=str(recommended),
                inferred_from="; ".join(bounds_checks[:3]) if bounds_checks
                              else f"max literal index {max_literal} at {literal_loc}",
                context="\n".join(context_lines),
                cli_override=f"--assumed-size {varname}={recommended}",
            ))

    return assumptions


def run_kgen_extract(
    source_file: str,
    build_cmd: str,
    run_cmd: str,
    clean_cmd: str = "",
    install_cmd: str = "",
    kgen_path: str = "kgen",
    source_dir: str = "",
    build_dir: str = "",
    cmake_file: str = "",
    output_dir: Optional[str] = None,
    package_name: str = "",
    invocation: str = "",
    assumed_size_arrays: Optional[dict] = None,
    preprocessor_defines: Optional[list[str]] = None,
    dll_persistence: Optional[dict] = None,
    add_source_cmd: str = "",
    env: Optional[dict] = None,
    function_name: str = "",
    dry_run: bool = False,
) -> dict:
    """
    Run KGen extraction with all necessary setup.

    This handles:
    1. Checking for editable pip install path conflicts
    2. Cleaning stale artifacts (strace.log, kgen.log)
    3. Pre-building so .mod files exist for include.ini generation
    4. Generating include.ini with proper module/source paths
    5. Generating a build wrapper script that injects kgen_utils.f90
    6. Running KGen
    7. Cleaning up injected files (restoring source tree)
    8. Locating the generated kernel/ and state/ directories

    Returns:
        Dict with keys: 'success', 'kernel_dir', 'state_dir', 'cmd', 'output',
                        'include_ini', 'cleaned', 'state_cases', 'warnings'
    """
    result = {
        "success": False,
        "kernel_dir": None,
        "state_dir": None,
        "cmd": "",
        "output": "",
        "include_ini": None,
        "cleaned": [],
        "state_cases": 0,
        "warnings": [],
    }

    # Step 1: Check for editable install conflicts
    if package_name or source_dir:
        project_dir = source_dir or os.path.dirname(os.path.abspath(source_file))
        install_warnings = check_editable_installs(project_dir, package_name)
        result["warnings"].extend(install_warnings)
        for w in install_warnings:
            warnings.warn(w, UserWarning, stacklevel=2)

    # Step 2: Clean stale artifacts
    result["cleaned"] = clean_kgen_artifacts()

    # Clean stale .kgen_org files (KGen's instrumented source backups).
    # These may contain pragmas from a previous extraction that would
    # contaminate this extraction — KGen reads all source files and
    # processes all pragmas it finds, including in .kgen_org files
    # that get restored to the source directory during state capture.
    if source_dir:
        result["cleaned"].extend(remove_stale_kgen_org(source_dir))

    # Step 3: Run clean command to force full rebuild for strace
    if clean_cmd:
        subprocess.run(clean_cmd, shell=True, capture_output=True)

    # Step 4: Pre-build so .mod files exist for include.ini generation
    # KGen needs .mod files to resolve USE statements. They may have been
    # removed by the clean step above.
    if build_cmd:
        subprocess.run(build_cmd, shell=True, capture_output=True)

    # Step 5: Generate include.ini with proper paths
    if source_dir or build_dir:
        src_dirs = discover_source_dirs(source_dir) if source_dir else []
        mod_dirs = discover_mod_dirs(build_dir) if build_dir else []
        if src_dirs or mod_dirs:
            ini_path = generate_include_ini(src_dirs, mod_dirs)
            result["include_ini"] = ini_path

    # Step 6: Generate build wrapper script that injects kgen_utils.f90
    # into the project build (KGen bug workaround)
    wrapper_script = generate_state_capture_build_script(
        build_cmd=build_cmd,
        install_cmd=install_cmd,
        cmake_file=cmake_file,
        source_dir=source_dir,
        build_dir=build_dir,
        kernel_dir=output_dir or "kernel",
        assumed_size_arrays=assumed_size_arrays,
        dll_persistence=dll_persistence,
        state_dir=os.path.join(output_dir or ".", "state"),
        add_source_cmd=add_source_cmd,
    )
    effective_build_cmd = f"bash {wrapper_script}"

    cmd = generate_kgen_command(
        source_file=source_file,
        build_cmd=effective_build_cmd,
        run_cmd=run_cmd,
        clean_cmd=clean_cmd,
        kgen_path=kgen_path,
        output_dir=output_dir,
        invocation=invocation,
        preprocessor_defines=preprocessor_defines,
    )
    result["cmd"] = cmd

    if dry_run:
        result["output"] = f"[DRY RUN] Would execute:\n  {cmd}"
        result["output"] += f"\n\nBuild wrapper script:\n{Path(wrapper_script).read_text()}"
        return result

    # Step 7: Run KGen
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
            timeout=1200,  # 20 minutes — extraction can be slow
        )
        result["output"] = proc.stdout + proc.stderr

        # Step 8: Locate output directories
        cwd = output_dir or "."
        for candidate_base in [cwd, str(Path(source_file).parent)]:
            kernel_dir = os.path.join(candidate_base, "kernel")
            state_dir = os.path.join(candidate_base, "state")

            if os.path.isdir(kernel_dir) and result["kernel_dir"] is None:
                result["kernel_dir"] = kernel_dir
            if os.path.isdir(state_dir) and result["state_dir"] is None:
                result["state_dir"] = state_dir

        # Count state data files (in kernel dir — that's where KGen puts them)
        if result["kernel_dir"]:
            result["state_cases"] = count_kernel_cases(
                result["kernel_dir"], function_name)
            result["success"] = True

            # Deduplicate kgen_statefile.lst. KGen's ElapsedTime phase
            # appends profiling-selected invocation triples to the CLI
            # triples instead of replacing them, causing duplicate entries.
            # See dev note 202603271457.
            lst = Path(result["kernel_dir"]) / "kgen_statefile.lst"
            if lst.exists():
                lst.write_text('\n'.join(dict.fromkeys(lst.read_text().splitlines())) + '\n')

            # Create .kgen backups of clean kernel files.
            # These are the ONLY safe point to create backups — source is
            # guaranteed clean (fresh from KGen, no VIT patches applied yet).
            # vit verify will restore from these backups, never create new ones.
            _create_kgen_backups(Path(result["kernel_dir"]))

            if result["state_cases"] == 0:
                result["output"] += (
                    "\n\nWARNING: Kernel generated but no state data captured.\n"
                    "Possible causes:\n"
                    "  1. The simulation didn't run (check run_cmd)\n"
                    "  2. The instrumented library wasn't installed where the simulation\n"
                    "     loads it from. Set install_cmd in vit.yaml to copy the built\n"
                    "     library to the install location.\n"
                    "  3. The call site wasn't reached during the simulation run.\n"
                    "  4. kgen_utils.f90 wasn't compiled into the shared library.\n"
                    "     Check that the build wrapper script ran correctly.\n"
                    "  5. An editable pip install (pip install -e .) may be pointing\n"
                    "     to a different checkout, causing the simulation to load an\n"
                    "     uninstrumented library. Run: pip show <package> and check\n"
                    "     'Editable project location' matches your working directory.\n"
                )

    except subprocess.TimeoutExpired:
        result["output"] = "ERROR: KGen extraction timed out after 1200 seconds"
    except Exception as e:
        result["output"] = f"ERROR: {e}"
    finally:
        # Step 9: Clean up kgen_utils.f90 injection (restore source tree)
        cleanup_result = cleanup_kgen_utils_injection(
            cmake_file=cmake_file,
            source_dir=source_dir,
            build_dir=build_dir,
        )
        if cleanup_result:
            result["cleaned"].extend(cleanup_result)

    return result


def count_kernel_cases(kernel_dir: str, function_name: Optional[str] = None) -> int:
    """
    Count the KGen state data files for `function_name` in a directory.

    KGen state files are named <function>.<rank>.<thread>.<invocation>, e.g.
    saturate.0.0.1.

    **Pass function_name.** Without it this counts any function's state files,
    and a stale file left by a previous extraction is indistinguishable from a
    real capture. That turns the one loud failure mode extraction has -- "no
    state data captured" -- into a silent wrong answer: `vit extract` reports
    success with a case count for a call site that never executed, and the
    kernel then replays another function's state.

    Found in the ROSCO replication extracting CurDate, whose only call site is
    behind `IF (CntrPar%Echo > 0)` and is dead in all 27 scenarios. Extraction
    reported "Cases: 1"; the file was AddToList.0.0.1 from the previous
    function. See DECISIONS.md, CurDate (#26).

    The name is matched case-insensitively: KGen lowercases in some paths, and a
    case-sensitive match would under-count and report a false zero, which is the
    same defect pointing the other way.
    """
    if not os.path.isdir(kernel_dir):
        return 0

    if function_name:
        pattern = re.compile(
            r'^' + re.escape(function_name) + r'\.\d+\.\d+\.\d+$',
            re.IGNORECASE)
    else:
        pattern = re.compile(r'.+\.\d+\.\d+\.\d+$')

    return sum(1 for item in os.listdir(kernel_dir) if pattern.match(item))