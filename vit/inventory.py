"""
Structural inventory of a Fortran codebase, using fparser2.

Produces nine JSON data files, a markdown summary and a work order, as input to planning a
translation (docs/CREATING_PROJECT_PLANS.md, Step 1: Structural Inventory).

Usage:
    vit inventory --src path/to/src --output inventory/
        [--deps path/to/dependency/src] [--search path/to/whole/repo]
        [--generated-marker TEXT] [--entry-point NAME] [--entry-points-file FILE]

fparser is an optional dependency (`pip install 'vit[inventory]'`). This module
imports it at the top, so the CLI imports this module only when the command
runs.

Nothing here is specific to one codebase. What a codebase must tell it:
    --generated-marker   text that marks a machine-generated source file, so its
                         procedures are counted apart from hand-written ones
    --entry-point(s)     procedures called from outside the source tree, by name,
                         which no reading of the source alone can establish

Output files:
    function_inventory.json   — every procedure (name, module, file, args, visibility,
                                its dependency depth, and the units it does I/O on)
    call_graph.json           — caller→callee edges with internal/external tags
    type_inventory.json       — derived TYPE definitions with fields
    module_dependencies.json  — USE graph, topologically sorted
    uncalled_procedures.json  — procedures with no call site in the scanned source,
                                each with the likely reason (not all are dead)
    framework_boundary.json   — procedures that form the external interface
    loc_metrics.json          — lines of code per file/module/procedure
    ordering.json             — where leaf-first ordering goes wrong: procedures doing
                                I/O on a unit they do not open, with the procedures
                                that own it, and candidates for an early non-leaf
    work_order.json, .md      — every in-scope procedure in the order to translate it,
                                with its reason and, given --coverage, its verification
                                route (vit/work_order.py)
    inventory_summary.md      — human-readable digest
"""

import glob
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

from fparser.two.parser import ParserFactory
from fparser.two.utils import walk
from fparser.two import Fortran2003 as f2003
from fparser.common.readfortran import FortranFileReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches a line whose final non-whitespace character is a backslash. Sources
# built with `-cpp` get C-preprocessor line splicing, so a trailing backslash is
# a continuation. fparser does not preprocess and rejects it as a syntax error,
# silently dropping the whole file.
_CPP_CONTINUATION = re.compile(r"\\(\s*)$")


def _despliced(path):
    """Read `path`, rewriting cpp line-splices as Fortran `&` continuations.

    Returns (text, n_rewritten). Rewriting rather than joining is deliberate:
    joining shifts every subsequent line number, and line numbers are what the
    extraction step needs. `&` is the free-form Fortran equivalent and keeps the
    line count identical.

    A trailing backslash inside a string literal would be data, not a splice, so
    lines with an odd number of quote characters before the backslash are left
    alone.
    """
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()

    n = 0
    for i, line in enumerate(lines):
        body = line.rstrip("\n")
        if not _CPP_CONTINUATION.search(body):
            continue
        prefix = body[: body.rindex("\\")]
        if prefix.count("'") % 2 or prefix.count('"') % 2:
            continue  # backslash sits inside an unterminated string literal
        lines[i] = _CPP_CONTINUATION.sub(r"&\1", body) + "\n"
        n += 1
    return "".join(lines), n


# The C preprocessor's predefined macros. fparser does not preprocess, so a
# file using one, such as `Year = __DATE__(8:11)`, fails to parse as a whole.
# Each becomes an identifier of the same length, which parses and keeps every
# column where it was; the inventory never needs the value.
_CPP_MACROS = re.compile(r"\b__(DATE|TIME|FILE|LINE)__\b")


def _cpp_macros_as_names(text):
    """Replace predefined cpp macros with identifiers. Returns (text, count)."""
    return _CPP_MACROS.subn(lambda m: "vit_" + m.group(1).lower(), text)


def parse_file(parser, path):
    """Parse a single Fortran file, return the AST or None on failure."""
    tmp_path = None
    try:
        text, n_spliced = _despliced(path)
        text, n_macros = _cpp_macros_as_names(text)
        if n_spliced:
            print(
                f"  NOTE: {os.path.basename(path)}: rewrote {n_spliced} cpp "
                f"line-splice(s) as Fortran continuations before parsing",
                file=sys.stderr,
            )
        if n_macros:
            print(f"  NOTE: {os.path.basename(path)}: read {n_macros} predefined cpp "
                  f"macro(s) as names before parsing", file=sys.stderr)
        if n_spliced or n_macros:
            fd, tmp_path = tempfile.mkstemp(suffix=os.path.basename(path))
            with os.fdopen(fd, "w") as f:
                f.write(text)
            read_path = tmp_path
        else:
            read_path = path
        reader = FortranFileReader(read_path, ignore_comments=False)
        return parser(reader)
    except Exception as e:
        print(f"  WARN: failed to parse {os.path.basename(path)}: {e}", file=sys.stderr)
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def is_generated(path, markers):
    """Whether a file is machine-generated: any of `markers` in its first lines.

    No markers, no generated files. Code generators mark their output in
    different ways, so the marker is the caller's to supply.
    """
    if not markers:
        return False
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                if any(m in line for m in markers):
                    return True
                # Only check first 50 lines for efficiency
                if f.tell() > 5000:
                    break
    except Exception:
        pass
    return False


def count_file_lines(path):
    """Count lines in a file."""
    try:
        with open(path, "r", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def procedure_line_count(node):
    """Estimate line count of a procedure from its AST string representation."""
    return str(node).count("\n") + 1


def get_names(node, cls=f2003.Name):
    """Extract all Name nodes from an AST subtree."""
    return [str(n) for n in walk(node, cls)]


def first_name(node):
    """Get the first Name in a node, or '?' if none."""
    names = walk(node, f2003.Name)
    return str(names[0]) if names else "?"


# ---------------------------------------------------------------------------
# Pass 1: Parse all files, collect modules and procedures
# ---------------------------------------------------------------------------

def collect_modules_and_procedures(parser, src_files, file_generated_map):
    """
    Parse all source files and collect:
    - modules: {name_lower: {name, file, uses, public_names, private_default, ...}}
    - procedures: [{name, module, file, kind, args, visibility, ...}]
    """
    modules = {}
    procedures = []
    all_trees = {}  # path -> AST

    for path in src_files:
        fname = os.path.basename(path)
        tree = parse_file(parser, path)
        if tree is None:
            continue
        all_trees[path] = tree
        is_gen = file_generated_map.get(path, False)

        # Find modules in this file
        for mod_node in walk(tree, f2003.Module):
            mod_stmts = walk(mod_node, f2003.Module_Stmt)
            if not mod_stmts:
                continue
            mod_name = first_name(mod_stmts[0])

            # USE statements at module level
            uses = set()
            for u in walk(mod_node, f2003.Use_Stmt):
                names = walk(u, f2003.Name)
                if names:
                    uses.add(str(names[0]))

            # PUBLIC/PRIVATE access defaults and specific names
            private_default = False
            public_names = set()
            private_names = set()
            for acc in walk(mod_node, f2003.Access_Stmt):
                acc_str = str(acc).strip().upper()
                # "PRIVATE" alone sets the default
                if acc_str == "PRIVATE":
                    private_default = True
                elif acc_str == "PUBLIC":
                    private_default = False
                elif acc_str.startswith("PUBLIC"):
                    # PUBLIC :: name1, name2, ...
                    for n in get_names(acc):
                        if n.upper() not in ("PUBLIC", "PRIVATE"):
                            public_names.add(n)
                elif acc_str.startswith("PRIVATE"):
                    for n in get_names(acc):
                        if n.upper() not in ("PUBLIC", "PRIVATE"):
                            private_names.add(n)

            modules[mod_name.lower()] = {
                "name": mod_name,
                "file": fname,
                "uses": sorted(uses),
                "public_names": sorted(public_names),
                "private_names": sorted(private_names),
                "private_default": private_default,
                "is_generated": is_gen,
            }

            # Collect procedures defined in this module.
            # Build a set of all procedure nodes so we can detect CONTAINS-block nesting.
            all_subs = walk(mod_node, f2003.Subroutine_Subprogram)
            all_fns = walk(mod_node, f2003.Function_Subprogram)

            # Identify which procedures are nested inside other procedures (CONTAINS block)
            nested_nodes = set()
            for parent in all_subs + all_fns:
                for child in walk(parent, f2003.Subroutine_Subprogram) + walk(parent, f2003.Function_Subprogram):
                    if child is not parent:
                        nested_nodes.add(id(child))

            for sub in all_subs:
                is_internal = id(sub) in nested_nodes
                proc = _extract_procedure(sub, "subroutine", mod_name, fname, is_gen,
                                          private_default, public_names, private_names,
                                          is_internal=is_internal)
                if proc:
                    procedures.append(proc)

            for fn in all_fns:
                is_internal = id(fn) in nested_nodes
                proc = _extract_procedure(fn, "function", mod_name, fname, is_gen,
                                          private_default, public_names, private_names,
                                          is_internal=is_internal)
                if proc:
                    procedures.append(proc)

        # Also collect module-less procedures (e.g., PROGRAM blocks, standalone subs)
        parent_mods = walk(tree, f2003.Module)
        all_mod_subs = set()
        all_mod_fns = set()
        for m in parent_mods:
            for s in walk(m, f2003.Subroutine_Subprogram):
                all_mod_subs.add(id(s))
            for f in walk(m, f2003.Function_Subprogram):
                all_mod_fns.add(id(f))

        standalone_subs = [s for s in walk(tree, f2003.Subroutine_Subprogram) if id(s) not in all_mod_subs]
        standalone_fns = [f for f in walk(tree, f2003.Function_Subprogram) if id(f) not in all_mod_fns]

        # Detect nesting among standalone procedures
        standalone_nested = set()
        for parent in standalone_subs + standalone_fns:
            for child in walk(parent, f2003.Subroutine_Subprogram) + walk(parent, f2003.Function_Subprogram):
                if child is not parent and id(child) not in all_mod_subs and id(child) not in all_mod_fns:
                    standalone_nested.add(id(child))

        for sub in standalone_subs:
            is_internal = id(sub) in standalone_nested
            proc = _extract_procedure(sub, "subroutine", None, fname, is_gen,
                                      False, set(), set(), is_internal=is_internal)
            if proc:
                procedures.append(proc)

        for fn in standalone_fns:
            is_internal = id(fn) in standalone_nested
            proc = _extract_procedure(fn, "function", None, fname, is_gen,
                                      False, set(), set(), is_internal=is_internal)
            if proc:
                procedures.append(proc)

    return modules, procedures, all_trees


def _extract_procedure(node, kind, module_name, filename, is_gen,
                       private_default, public_names, private_names,
                       is_internal=False):
    """Extract metadata from a subroutine or function AST node."""
    if kind == "subroutine":
        stmts = walk(node, f2003.Subroutine_Stmt)
    else:
        stmts = walk(node, f2003.Function_Stmt)

    if not stmts:
        return None

    stmt = stmts[0]
    # Use items[1] for the procedure name — first_name() picks up kind specs
    # (e.g., IntKi from "INTEGER(KIND=IntKi) FUNCTION OutParamFlags()")
    if hasattr(stmt, "items") and len(stmt.items) > 1 and stmt.items[1] is not None:
        name = str(stmt.items[1])
    else:
        name = first_name(stmt)

    # Arguments
    dummy_lists = walk(stmt, f2003.Dummy_Arg_List)
    args = []
    if dummy_lists:
        args = get_names(dummy_lists[0])

    # SAVE detection
    saves = walk(node, f2003.Save_Stmt)
    # Also check for SAVE attribute on declarations
    has_save = len(saves) > 0
    if not has_save:
        # Check for variables with SAVE attribute in their declaration
        for decl in walk(node, f2003.Type_Declaration_Stmt):
            decl_str = str(decl).upper()
            if ", SAVE" in decl_str or "SAVE," in decl_str or "SAVE ::" in decl_str:
                has_save = True
                break

    # BIND(C): callable from C, so a missing Fortran caller proves nothing
    is_bind_c = "BIND(C" in str(stmt).upper().replace(" ", "")

    # PURE / ELEMENTAL
    prefix_str = str(stmt).upper().split(kind.upper())[0] if kind.upper() in str(stmt).upper() else ""
    is_pure = "PURE" in prefix_str
    is_elemental = "ELEMENTAL" in prefix_str

    # Visibility
    if name in public_names:
        visibility = "public"
    elif name in private_names:
        visibility = "private"
    elif private_default:
        visibility = "private"
    else:
        visibility = "public"

    # Line count
    lines = procedure_line_count(node)
    span = getattr(getattr(stmt, "item", None), "span", None)

    return {
        "name": name,
        "module": module_name,
        "file": filename,
        "start_line": span[0] if span else None,
        "kind": kind,
        "args": args,
        "arg_count": len(args),
        "line_count": lines,
        "visibility": visibility,
        "has_save": has_save,
        "is_generated": is_gen,
        "is_pure": is_pure,
        "is_elemental": is_elemental,
        "is_bind_c": is_bind_c,
        "is_internal": is_internal,
    }


# ---------------------------------------------------------------------------
# Pass 2: Build call graph
# ---------------------------------------------------------------------------

def _get_procedure_name(proc_node):
    """Extract the procedure name from a subroutine or function node."""
    sub_stmts = walk(proc_node, f2003.Subroutine_Stmt)
    fn_stmts = walk(proc_node, f2003.Function_Stmt)
    stmt = sub_stmts[0] if sub_stmts else (fn_stmts[0] if fn_stmts else None)
    if stmt is None:
        return None
    if hasattr(stmt, "items") and len(stmt.items) > 1 and stmt.items[1] is not None:
        return str(stmt.items[1])
    return first_name(stmt)


def build_call_graph(all_trees, procedures, modules):
    """
    Build caller->callee edges from CALL statements AND function references
    (Part_Ref nodes) within each procedure.
    """
    # Build lookup: procedure name (lower) -> defined module
    proc_lookup = {}
    for p in procedures:
        proc_lookup[p["name"].lower()] = p

    # Build lookup: module name (lower) -> set of procedure names (lower)
    mod_procs = defaultdict(set)
    for p in procedures:
        if p["module"]:
            mod_procs[p["module"].lower()].add(p["name"].lower())

    edges = []

    for path, tree in all_trees.items():
        fname = os.path.basename(path)

        # Find all procedures in this tree
        for proc_node in (walk(tree, f2003.Subroutine_Subprogram) +
                          walk(tree, f2003.Function_Subprogram)):
            caller = _get_procedure_name(proc_node)
            if caller is None:
                continue

            # Track CALL statements (subroutine calls)
            for call in walk(proc_node, f2003.Call_Stmt):
                callee_names = walk(call, f2003.Name)
                if callee_names:
                    callee = str(callee_names[0])
                    is_internal = callee.lower() in proc_lookup
                    edges.append({
                        "caller": caller,
                        "callee": callee,
                        "file": fname,
                        "is_internal": is_internal,
                        "kind": "call",
                    })

            # Track function references in expressions (x = func(y), if(func(y)))
            # fparser represents these as Part_Ref or Structure_Constructor depending
            # on context. Check both, and only record if the name matches a known procedure.
            for ref_node in (walk(proc_node, f2003.Part_Ref) +
                             walk(proc_node, f2003.Structure_Constructor)):
                ref_names = walk(ref_node, f2003.Name)
                if ref_names:
                    ref_name = str(ref_names[0])
                    if ref_name.lower() in proc_lookup:
                        edges.append({
                            "caller": caller,
                            "callee": ref_name,
                            "file": fname,
                            "is_internal": True,
                            "kind": "function_ref",
                        })

    # Deduplicate edges (same caller->callee may appear multiple times)
    seen = set()
    unique_edges = []
    for e in edges:
        key = (e["caller"], e["callee"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(e)

    return unique_edges


# ---------------------------------------------------------------------------
# Pass 3: Type inventory
# ---------------------------------------------------------------------------

def collect_types(all_trees, dep_trees=None):
    """Collect derived TYPE definitions from ASTs."""
    type_defs = []

    trees_to_scan = list(all_trees.items())
    if dep_trees:
        trees_to_scan.extend(dep_trees.items())

    for path, tree in trees_to_scan:
        fname = os.path.basename(path)
        is_dep = dep_trees and path in dep_trees

        # Find module context
        for mod_node in walk(tree, f2003.Module):
            mod_name = first_name(walk(mod_node, f2003.Module_Stmt)[0]) if walk(mod_node, f2003.Module_Stmt) else None

            for type_def in walk(mod_node, f2003.Derived_Type_Def):
                type_stmts = walk(type_def, f2003.Derived_Type_Stmt)
                if not type_stmts:
                    continue

                # Get type name
                type_names = walk(type_stmts[0], f2003.Type_Name)
                if not type_names:
                    # Try regular Name
                    type_names = walk(type_stmts[0], f2003.Name)
                type_name = str(type_names[0]) if type_names else "?"

                # Extract fields
                fields = []
                for comp in walk(type_def, f2003.Component_Decl):
                    field_name = first_name(comp)
                    fields.append({"name": field_name})

                # Extract field types from Data_Component_Def_Stmt
                field_details = []
                for comp_def in walk(type_def, f2003.Data_Component_Def_Stmt):
                    comp_str = str(comp_def).strip()
                    # Extract type
                    type_spec = walk(comp_def, f2003.Intrinsic_Type_Spec) or walk(comp_def, f2003.Declaration_Type_Spec)
                    field_type = str(type_spec[0]).strip() if type_spec else "?"

                    is_alloc = "ALLOCATABLE" in comp_str.upper()
                    is_ptr = "POINTER" in comp_str.upper()

                    # Get component names from this statement
                    for cd in walk(comp_def, f2003.Component_Decl):
                        fn = first_name(cd)
                        # Check for dimension
                        dim = None
                        array_spec = walk(cd, f2003.Component_Array_Spec) or walk(cd, f2003.Explicit_Shape_Spec_List) or walk(cd, f2003.Deferred_Shape_Spec_List)
                        if array_spec:
                            dim = str(array_spec[0]).strip()

                        field_details.append({
                            "name": fn,
                            "type": field_type,
                            "is_allocatable": is_alloc,
                            "is_pointer": is_ptr,
                            "dimension": dim,
                        })

                # Type-bound procedures (CONTAINS section)
                tbp_names = []
                for tbp in walk(type_def, f2003.Specific_Binding):
                    tbp_names.extend(get_names(tbp))

                type_defs.append({
                    "name": type_name,
                    "module": mod_name,
                    "file": fname,
                    "field_count": len(field_details),
                    "fields": field_details,
                    "type_bound_procedures": tbp_names,
                    "is_dependency": is_dep,
                })

    return type_defs


# ---------------------------------------------------------------------------
# Pass 4: Module dependencies (topological sort)
# ---------------------------------------------------------------------------

def build_module_dependencies(modules, procedures):
    """Build module dependency graph with topological sort."""
    # Group procedures by module
    mod_procedures = defaultdict(list)
    mod_types_map = defaultdict(list)
    for p in procedures:
        if p["module"]:
            mod_procedures[p["module"].lower()].append(p["name"])

    entries = []
    for mod_key, mod_info in modules.items():
        entries.append({
            "name": mod_info["name"],
            "file": mod_info["file"],
            "uses": mod_info["uses"],
            "is_generated": mod_info["is_generated"],
            "provides_procedures": sorted(mod_procedures.get(mod_key, [])),
        })

    # Topological sort by USE dependencies
    # Build adjacency list
    name_to_idx = {e["name"].lower(): i for i, e in enumerate(entries)}
    in_degree = [0] * len(entries)
    adj = defaultdict(list)

    for i, e in enumerate(entries):
        for dep in e["uses"]:
            dep_idx = name_to_idx.get(dep.lower())
            if dep_idx is not None:
                adj[dep_idx].append(i)
                in_degree[i] += 1

    # Kahn's algorithm
    from collections import deque
    queue = deque([i for i, d in enumerate(in_degree) if d == 0])
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Add any remaining (cycles) at the end
    remaining = [i for i in range(len(entries)) if i not in set(order)]
    order.extend(remaining)

    sorted_entries = [entries[i] for i in order]

    # Tag topological level
    for i, idx in enumerate(order):
        sorted_entries[i]["topo_order"] = i

    return sorted_entries


# ---------------------------------------------------------------------------
# Pass 5: Dead code detection
# ---------------------------------------------------------------------------

def _name_found_in_tree(name, exclude_dir, search_dir):
    """
    Search the broader source tree (--search) for references to a procedure name,
    excluding files in the primary source directory (--src) which we already scanned.
    Returns True if the name is referenced outside the primary source.
    """
    import subprocess
    name_lower = name.lower()
    # Use grep for speed — searching potentially thousands of files
    try:
        result = subprocess.run(
            ["grep", "-ril", "--include=*.f90", "--include=*.F90",
             name_lower, search_dir],
            capture_output=True, text=True, timeout=30)
        for fpath in result.stdout.strip().split("\n"):
            if not fpath:
                continue
            # Skip files in the primary source directory
            if os.path.abspath(fpath).startswith(os.path.abspath(exclude_dir)):
                continue
            # Verify word-boundary match in this file
            try:
                with open(fpath, "r", errors="replace") as f:
                    import re as _re
                    pattern = _re.compile(r'\b' + _re.escape(name_lower) + r'\b', _re.IGNORECASE)
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith("!"):
                            continue
                        # Strip strings
                        clean = _re.sub(r"'[^']*'", "", stripped)
                        clean = _re.sub(r'"[^"]*"', "", clean)
                        if pattern.search(clean):
                            # Exclude definition lines
                            clean_lower = clean.lower()
                            if any(kw in clean_lower for kw in
                                   ["subroutine " + name_lower, "function " + name_lower]):
                                continue
                            return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def is_test_procedure(name, module):
    """Test code by its name: a module with "test" in its name, or a procedure
    with Test as a word of its name (TEST01_x, DBEMT_TEST01, Test_Foo,
    RunUnitTests), not merely as letters (LATEST_VALUE)."""
    words = {w.upper() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", name or "")}
    return "test" in (module or "").lower() or bool(words & {"TEST", "TESTS"})


def find_uncalled_procedures(procedures, call_edges, src_dir, search_dir=None,
                             entry_points=frozenset()):
    """
    Find procedures with zero call sites WITHIN the scanned source tree,
    then classify each by WHY it appears uncalled.

    Categories:
    - declared_entry_point: named with --entry-point, called from outside
    - c_entry_point: declared BIND(C), so callable from C/C++
    - driver_entry_point: in a driver module, called from PROGRAM blocks
    - test_function: in a test module or has TEST in name
    - framework_interface: confirmed called from outside --src via --search
    - commented_out: calls exist in source but are commented out (!call)
    - callback: procedure name appears as an argument in a CALL statement
    - program_block: called from a PROGRAM block (not scanned as module)
    - genuinely_dead: defined but never referenced anywhere in source tree
    """
    # All callees (from both CALL statements and function references)
    called = set()
    for e in call_edges:
        called.add(e["callee"].lower())

    # Pre-classify by module/name heuristics first
    uncalled_candidates = []
    for p in procedures:
        if p["is_generated"]:
            continue
        if p.get("is_internal", False):
            continue
        if p["name"].lower() not in called:
            uncalled_candidates.append(p)

    if not uncalled_candidates:
        return []

    # For remaining unknowns, scan source files for deeper classification.
    # Build a text index: for each source file, read its lines.
    # Check for:
    #   1. Commented-out calls: lines matching "!.*call.*<name>" (case-insensitive)
    #   2. Callback usage: name appears as an argument inside a CALL statement
    #   3. PROGRAM-block calls: name called from a PROGRAM ... END PROGRAM block
    import re

    file_lines_cache = {}
    def get_file_lines(filename):
        if filename not in file_lines_cache:
            full_path = os.path.join(src_dir, filename)
            try:
                with open(full_path, "r", errors="replace") as f:
                    file_lines_cache[filename] = f.readlines()
            except Exception:
                file_lines_cache[filename] = []
        return file_lines_cache[filename]

    # Read ALL source files in directory for text search
    all_src_files = sorted(glob.glob(os.path.join(src_dir, "*.f90")) +
                           glob.glob(os.path.join(src_dir, "*.F90")))
    all_lines_by_file = {}
    all_text_by_file = {}
    for fpath in all_src_files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, "r", errors="replace") as f:
                lines = f.readlines()
                all_lines_by_file[fname] = lines
                all_text_by_file[fname] = "".join(lines)
        except Exception:
            pass

    uncalled = []
    for p in uncalled_candidates:
        name = p["name"]
        mod = p["module"] or ""

        # Deep source text analysis for ALL uncalled procedures.
        # Scan every source file for references to this procedure name.
        # Track three separate signals:
        #   - commented_out: name appears in commented-out call/reference
        #   - callback_usage: name appears as non-string argument in a CALL
        #   - program_block_call: name called from a PROGRAM block
        #   - any_live_reference: name appears in live (non-comment, non-string,
        #     non-definition, non-access-stmt) code
        name_lower = name.lower()
        commented_out = False
        callback_usage = False
        program_block_call = False
        any_live_reference = False

        # Pre-compile word-boundary regex for this name to avoid substring
        # false positives (e.g., "print_tree" matching "print_tree_rec")
        name_re = re.compile(r'\b' + re.escape(name_lower) + r'\b')

        for fname, lines in all_lines_by_file.items():
            in_program_block = False
            for line in lines:
                stripped = line.strip().lower()

                # Track PROGRAM blocks
                if stripped.startswith("program ") and not stripped.startswith("program("):
                    in_program_block = True
                if stripped.startswith("end program"):
                    in_program_block = False

                # Quick substring check before expensive regex
                if name_lower not in stripped:
                    continue

                # Word-boundary check — reject substring matches
                if not name_re.search(stripped):
                    continue

                # Skip the definition line itself (SUB/FUNCTION declaration or END)
                # Use regex to handle variable whitespace
                stripped_nocomment = stripped.lstrip("!").strip()
                if re.search(r'\b(subroutine|function)\b', stripped_nocomment):
                    if name_re.search(stripped_nocomment):
                        # This line has both "subroutine/function" and our name —
                        # likely a definition, END statement, or forward declaration
                        if any(stripped_nocomment.startswith(kw) for kw in
                               ("subroutine", "function", "pure", "elemental",
                                "real", "integer", "logical", "end ")):
                            continue

                # Check for commented-out calls: !...call name or !...name(
                if stripped.startswith("!"):
                    rest = stripped.lstrip("!")
                    if ("call " + name_lower in rest or
                        name_lower + "(" in rest):
                        commented_out = True
                    continue  # Don't check further for comment lines

                # From here: non-comment lines only
                # Strip string literals for all remaining checks
                line_no_strings = re.sub(r"'[^']*'", "", stripped)
                line_no_strings = re.sub(r'"[^"]*"', "", line_no_strings)

                if not name_re.search(line_no_strings):
                    continue  # Name only appeared inside string literals

                # Check for callback: name appears as non-callee argument in CALL
                if "call " in line_no_strings:
                    call_match = re.search(r"call\s+\w+\s*\((.+)\)", line_no_strings)
                    if call_match:
                        args_text = call_match.group(1)
                        if name_re.search(args_text):
                            callback_usage = True
                            any_live_reference = True

                # Check for PROGRAM block call
                if in_program_block:
                    if ("call " + name_lower in line_no_strings or
                        name_lower + "(" in line_no_strings):
                        program_block_call = True
                        any_live_reference = True

                # Check for any live reference (excluding PUBLIC/PRIVATE/definition)
                if not any(kw in line_no_strings for kw in ["public ", "private ",
                                                             "subroutine " + name_lower,
                                                             "function " + name_lower]):
                    any_live_reference = True

        # Classification priority:
        # 1. Source evidence from within --src (strongest signal)
        # 2. Broader search via --search (confirms framework interface)
        # 3. Heuristic by module/name (when no evidence available)
        # 4. genuinely_dead (no evidence at all)
        if name_lower in entry_points:
            reason = "declared_entry_point"
        elif p.get("is_bind_c"):
            reason = "c_entry_point"
        elif callback_usage:
            reason = "callback"
        elif program_block_call:
            reason = "program_block"
        elif commented_out and not any_live_reference:
            reason = "commented_out"
        elif any_live_reference:
            if "driver" in mod.lower():
                reason = "driver_entry_point"
            elif is_test_procedure(name, mod):
                reason = "test_function"
            else:
                reason = "unknown"
        else:
            # No live reference in --src. Check --search tree if provided.
            if search_dir and _name_found_in_tree(name, src_dir, search_dir):
                reason = "framework_interface"
            else:
                reason = "genuinely_dead"

        uncalled.append({
            "name": name,
            "module": p["module"],
            "file": p["file"],
            "line_count": p["line_count"],
            "visibility": p["visibility"],
            "likely_reason": reason,
        })

    return uncalled


# ---------------------------------------------------------------------------
# Pass 6: Framework boundary
# ---------------------------------------------------------------------------

def find_framework_boundary(modules, procedures, entry_points=frozenset()):
    """
    Identify procedures that form the external API:
    - PUBLIC procedures named explicitly in a PRIVATE-by-default module
    - BIND(C) procedures, callable from C/C++
    - Procedures in driver modules
    - Procedures named with --entry-point
    """
    boundary = []

    for p in procedures:
        if p["is_generated"]:
            continue

        reason = None

        # Explicitly PUBLIC in a PRIVATE-by-default module: a deliberate API
        if p["visibility"] == "public" and p["module"]:
            mod_info = modules.get(p["module"].lower(), {})
            if mod_info.get("private_default", False):
                if p["name"] in mod_info.get("public_names", []):
                    reason = "explicit_public"

        if p.get("is_bind_c"):
            reason = "c_entry_point"

        # Driver modules
        if p["module"] and "driver" in p["module"].lower():
            reason = "driver"

        if p["name"].lower() in entry_points:
            reason = "declared_entry_point"

        if reason:
            boundary.append({
                "name": p["name"],
                "module": p["module"],
                "file": p["file"],
                "reason": reason,
                "arg_count": p["arg_count"],
            })

    return boundary


# ---------------------------------------------------------------------------
# Pass 7: LOC metrics
# ---------------------------------------------------------------------------

def compute_loc_metrics(src_files, procedures, modules, file_generated_map):
    """Compute lines of code metrics."""
    per_file = []
    total_lines = 0
    total_generated = 0
    total_handwritten = 0

    for path in src_files:
        fname = os.path.basename(path)
        lines = count_file_lines(path)
        is_gen = file_generated_map.get(path, False)
        total_lines += lines
        if is_gen:
            total_generated += lines
        else:
            total_handwritten += lines
        per_file.append({
            "file": fname,
            "lines": lines,
            "is_generated": is_gen,
        })

    per_file.sort(key=lambda x: -x["lines"])

    # Per module
    mod_lines = defaultdict(int)
    for p in procedures:
        if p["module"]:
            mod_lines[p["module"]] += p["line_count"]

    per_module = [{"module": k, "procedure_lines": v} for k, v in sorted(mod_lines.items(), key=lambda x: -x[1])]

    # Top 20 largest procedures
    sorted_procs = sorted(procedures, key=lambda p: -p["line_count"])
    top_procs = [{"name": p["name"], "module": p["module"], "file": p["file"],
                  "line_count": p["line_count"], "is_generated": p["is_generated"]}
                 for p in sorted_procs[:20]]

    return {
        "total_lines": total_lines,
        "total_generated": total_generated,
        "total_handwritten": total_handwritten,
        "file_count": len(src_files),
        "module_count": len(modules),
        "procedure_count": len(procedures),
        "per_file": per_file,
        "per_module": per_module,
        "top_20_procedures": top_procs,
    }


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def write_summary(output_dir, loc, procedures, call_edges, type_defs, mod_deps,
                  uncalled, boundary, ordering=None, unparsed=()):
    """Write inventory_summary.md."""
    lines = []
    lines.append("# Structural Inventory Summary")
    lines.append("")
    lines.append(f"Generated by `vit inventory`")
    lines.append("")

    # Overview
    hw_procs = [p for p in procedures if not p["is_generated"]]
    gen_procs = [p for p in procedures if p["is_generated"]]
    internal_procs = [p for p in procedures if p.get("is_internal", False)]
    top_level_hw = [p for p in hw_procs if not p.get("is_internal", False)]
    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total files | {loc['file_count']} |")
    lines.append(f"| Total lines | {loc['total_lines']:,} |")
    lines.append(f"| Hand-written lines | {loc['total_handwritten']:,} |")
    lines.append(f"| Generated lines | {loc['total_generated']:,} |")
    lines.append(f"| Modules | {loc['module_count']} |")
    lines.append(f"| Total procedures | {loc['procedure_count']} |")
    lines.append(f"| Hand-written procedures (top-level) | {len(top_level_hw)} |")
    lines.append(f"| Hand-written procedures (CONTAINS internal) | {len([p for p in internal_procs if not p['is_generated']])} |")
    lines.append(f"| Generated procedures | {len(gen_procs)} |")
    lines.append(f"| Subroutines | {sum(1 for p in procedures if p['kind'] == 'subroutine')} |")
    lines.append(f"| Functions | {sum(1 for p in procedures if p['kind'] == 'function')} |")
    lines.append(f"| Procedures with SAVE state | {sum(1 for p in procedures if p['has_save'])} |")
    lines.append(f"| Derived types | {len(type_defs)} |")
    lines.append(f"| Call graph edges | {len(call_edges)} |")
    lines.append(f"| Uncalled within the scanned source | {len(uncalled)} |")
    lines.append(f"| Framework boundary procedures | {len(boundary)} |")
    lines.append("")

    # Leaf functions (no outgoing call edges among internal callees, excluding CONTAINS helpers)
    callers_with_internal = set()
    for e in call_edges:
        if e["is_internal"]:
            callers_with_internal.add(e["caller"].lower())
    leaves = [p for p in top_level_hw if p["name"].lower() not in callers_with_internal]
    leaves.sort(key=lambda p: p["arg_count"])
    lines.append(f"## Leaf Functions (no internal callees): {len(leaves)}")
    lines.append("")
    lines.append("| Name | Module | Args | Lines |")
    lines.append("|------|--------|------|-------|")
    for p in leaves[:30]:
        lines.append(f"| {p['name']} | {p['module'] or '-'} | {p['arg_count']} | {p['line_count']} |")
    if len(leaves) > 30:
        lines.append(f"| ... | ... | ... | ... |")
        lines.append(f"| ({len(leaves) - 30} more) | | | |")
    lines.append("")

    # Top 20 largest procedures
    lines.append("## Top 20 Largest Procedures")
    lines.append("")
    lines.append("| Name | Module | Lines | Generated? |")
    lines.append("|------|--------|-------|-----------|")
    for p in loc["top_20_procedures"]:
        gen = "yes" if p["is_generated"] else "no"
        lines.append(f"| {p['name']} | {p['module'] or '-'} | {p['line_count']} | {gen} |")
    lines.append("")

    # Files by size
    lines.append("## Files by Size")
    lines.append("")
    lines.append("| File | Lines | Generated? |")
    lines.append("|------|-------|-----------|")
    for f in loc["per_file"]:
        gen = "yes" if f["is_generated"] else "no"
        lines.append(f"| {f['file']} | {f['lines']:,} | {gen} |")
    lines.append("")

    # Module dependencies
    lines.append("## Module Dependencies (topological order)")
    lines.append("")
    lines.append("| Order | Module | File | USEs | Procedures | Generated? |")
    lines.append("|-------|--------|------|------|------------|-----------|")
    for m in mod_deps:
        uses = ", ".join(m["uses"][:5])
        if len(m["uses"]) > 5:
            uses += f" (+{len(m['uses'])-5})"
        gen = "yes" if m["is_generated"] else "no"
        lines.append(f"| {m['topo_order']} | {m['name']} | {m['file']} | {uses} | {len(m['provides_procedures'])} | {gen} |")
    lines.append("")

    # External dependencies
    ext_deps = set()
    for m in mod_deps:
        for u in m["uses"]:
            if u.lower() not in {md["name"].lower() for md in mod_deps}:
                ext_deps.add(u)
    lines.append(f"## External Module Dependencies: {len(ext_deps)}")
    lines.append("")
    for d in sorted(ext_deps):
        lines.append(f"- {d}")
    lines.append("")

    # Framework boundary
    lines.append(f"## Framework Boundary: {len(boundary)} procedures")
    lines.append("")
    lines.append("| Name | Module | Reason | Args |")
    lines.append("|------|--------|--------|------|")
    for b in sorted(boundary, key=lambda x: x["name"]):
        lines.append(f"| {b['name']} | {b['module'] or '-'} | {b['reason']} | {b['arg_count']} |")
    lines.append("")

    # Dead code
    lines.append(f"## Uncalled Within the Scanned Source: {len(uncalled)} procedures")
    lines.append("")

    # Group by reason
    from collections import Counter as _Counter
    reason_counts = _Counter(d.get("likely_reason", "?") for d in uncalled)
    reason_order = ["declared_entry_point", "c_entry_point", "driver_entry_point", "test_function",
                    "framework_interface", "commented_out", "callback",
                    "program_block", "genuinely_dead", "unknown"]
    reason_labels = {
        "declared_entry_point": "Declared entry points (--entry-point)",
        "c_entry_point": "C entry points (BIND(C), callable from C/C++)",
        "driver_entry_point": "Driver entry points (called from PROGRAM blocks)",
        "test_function": "Test functions",
        "framework_interface": "Framework interface (referenced outside --src, in --search)",
        "commented_out": "Commented-out calls (dormant debug code)",
        "callback": "Callbacks (passed as function arguments)",
        "program_block": "Called from PROGRAM block (not scanned as module)",
        "genuinely_dead": "Genuinely dead code (no reference anywhere in source)",
        "unknown": "Unclassified",
    }

    lines.append("| Category | Count | Translation Impact |")
    lines.append("|----------|-------|--------------------|")
    for r in reason_order:
        c = reason_counts.get(r, 0)
        if c > 0:
            label = reason_labels.get(r, r)
            if r == "genuinely_dead":
                impact = "Exclude from scope"
            elif r in ("commented_out", "callback", "program_block"):
                impact = "Translate if parent is in scope"
            else:
                impact = "Translate (called externally)"
            lines.append(f"| {label} | {c} | {impact} |")
    lines.append("")

    # Show each category
    for r in reason_order:
        items = [d for d in uncalled if d.get("likely_reason") == r]
        if not items:
            continue
        label = reason_labels.get(r, r)
        lines.append(f"### {label} ({len(items)})")
        lines.append("")
        lines.append("| Name | Module | Lines |")
        lines.append("|------|--------|-------|")
        for d in sorted(items, key=lambda x: -x["line_count"]):
            lines.append(f"| {d['name']} | {d['module'] or '-'} | {d['line_count']} |")
        lines.append("")
    else:
        lines.append("(none found)")
    lines.append("")

    # Types summary
    src_types = [t for t in type_defs if not t["is_dependency"]]
    dep_types = [t for t in type_defs if t["is_dependency"]]
    lines.append(f"## Derived Types: {len(src_types)} in the source, {len(dep_types)} in dependencies")
    lines.append("")
    lines.append("### Source Types (top 20 by field count)")
    lines.append("")
    lines.append("| Type | Module | Fields | Has Allocatable | Has Pointer |")
    lines.append("|------|--------|--------|----------------|-------------|")
    for t in sorted(src_types, key=lambda x: -x["field_count"])[:20]:
        has_alloc = any(f.get("is_allocatable") for f in t["fields"])
        has_ptr = any(f.get("is_pointer") for f in t["fields"])
        lines.append(f"| {t['name']} | {t['module'] or '-'} | {t['field_count']} | {'yes' if has_alloc else 'no'} | {'yes' if has_ptr else 'no'} |")
    lines.append("")

    if ordering is not None:
        lines.extend(_ordering_summary(ordering))

    if unparsed:
        lines.append(f"## Files that could not be parsed: {len(unparsed)}")
        lines.append("")
        lines.append("These are missing from every count above.")
        lines.append("")
        for name in unparsed:
            lines.append(f"- {name}")
        lines.append("")

    # Sanity check answer
    if leaves:
        smallest = leaves[0]
        lines.append("## Sanity Check")
        lines.append("")
        lines.append(f"**Leaf function with smallest argument list:** `{smallest['name']}` "
                      f"in module `{smallest['module']}` ({smallest['arg_count']} args, "
                      f"{smallest['line_count']} lines)")
        lines.append("")

    path = os.path.join(output_dir, "inventory_summary.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {path}")


# ---------------------------------------------------------------------------
# Pass 8: Ordering exceptions
# ---------------------------------------------------------------------------
#
# Translating leaves first is right when a procedure computes on its
# arguments: a leaf needs nothing its caller lacks. It is backwards when the
# procedure works on a resource someone else owns. A routine whose body is
# `READ(Un, ...)` reads from a Fortran unit number, which means nothing on the
# C++ side; it can be translated only with the procedure that opens the unit,
# or after it, when the file is a C++ stream and the routine shrinks to a line
# of the standard library. The call graph does not show this, so this pass
# finds it from the I/O statements themselves.
#
# Separately, a campaign of leaves never exercises calls between translated
# procedures, so this pass also proposes small first non-leaf procedures.

_IO_STMTS = (f2003.Read_Stmt, f2003.Write_Stmt, f2003.Open_Stmt, f2003.Close_Stmt,
             f2003.Inquire_Stmt, f2003.Backspace_Stmt, f2003.Rewind_Stmt,
             f2003.Endfile_Stmt, f2003.Flush_Stmt, f2003.Wait_Stmt)
_SUBPROGRAMS = (f2003.Subroutine_Subprogram, f2003.Function_Subprogram)
# gfortran's preconnected units: standard error, input and output.
_PRECONNECTED = {"0", "5", "6"}


def _own_nodes(proc_node, types):
    """Nodes of `types` in this procedure, not in procedures it CONTAINS."""
    found = []

    def visit(node, top):
        if not top and isinstance(node, _SUBPROGRAMS):
            return
        if isinstance(node, types):
            found.append(node)
        for child in getattr(node, "children", None) or ():
            if child is not None and not isinstance(child, str):
                visit(child, False)

    visit(proc_node, True)
    return found


def _split_top_level(text):
    """Split on commas outside parentheses and quotes."""
    items, depth, quote, start = [], 0, None, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(text[start:i])
            start = i + 1
    items.append(text[start:])
    return [i.strip() for i in items if i.strip()]


def io_statement_unit(stmt):
    """(operation, unit text) for an I/O statement, or None if it names no unit.

    The unit is the UNIT= or NEWUNIT= item, or the first positional one.
    `READ fmt, list` reads standard input, reported as `*`.
    """
    text = str(stmt).strip()
    m = re.match(r"(?i)(READ|WRITE|OPEN|CLOSE|INQUIRE|BACKSPACE|REWIND|ENDFILE|FLUSH|WAIT)\s*",
                 text)
    if not m:
        return None
    op, rest = m.group(1).upper(), text[m.end():]
    if not rest.startswith("("):
        if op == "READ":
            return op, "*"
        unit = rest.split(",")[0].strip()
        return (op, unit) if unit else None
    depth = 0
    for i, ch in enumerate(rest):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            inner = rest[1:i]
            break
    else:
        return None
    positional = None
    for item in _split_top_level(inner):
        k = re.match(r"([A-Za-z]+)\s*=(?!=)\s*(.*)$", item, re.S)
        if k and k.group(1).upper() in ("UNIT", "NEWUNIT"):
            return op, k.group(2).strip()
        if not k and positional is None:
            positional = item
    return (op, positional) if positional else None


def _declared_types(nodes):
    """{name lower: type text} for the Type_Declaration_Stmts in `nodes`."""
    out = {}
    for decl in nodes:
        type_text = str(decl.items[0]).upper() if decl.items else ""
        for entity in walk(decl, f2003.Entity_Decl):
            name = first_name(entity)
            if name:
                out[name.lower()] = type_text
    return out


def _integer_parameters(trees):
    """{name lower: value} for integer named constants with a literal value."""
    out = {}
    for tree in trees:
        for decl in walk(tree, f2003.Type_Declaration_Stmt):
            text = str(decl).upper()
            if not text.startswith("INTEGER") or "PARAMETER" not in text:
                continue
            for entity in walk(decl, f2003.Entity_Decl):
                m = re.match(r"\s*(\w+)\s*=\s*(\d+)(?:_\w+)?\s*$", str(entity))
                if m:
                    out[m.group(1).lower()] = m.group(2)
    return out


def _module_spec_types(tree):
    """{module lower: {name lower: type}} for variables declared at module level."""
    out = {}
    for mod in walk(tree, f2003.Module):
        stmts = walk(mod, f2003.Module_Stmt)
        if not stmts:
            continue
        spec = [c for c in mod.children if isinstance(c, f2003.Specification_Part)]
        decls = walk(spec, f2003.Type_Declaration_Stmt) if spec else []
        out[first_name(stmts[0]).lower()] = _declared_types(decls)
    return out


class _Scope:
    """The names one procedure can see: its dummies, its locals, and, for an
    internal procedure, its host's."""

    def __init__(self, name, file, args, local_types, host=None, module=None, start_line=None):
        self.name, self.file, self.module = name, file, module
        self.start_line = start_line
        self.key = (name.lower(), file, start_line)
        self.args = [a.lower() for a in args]
        self.local_types = local_types
        self.host = host

    def kind_of(self, base):
        if base in self.args:
            return "argument"
        if base in self.local_types:
            return "local"
        if self.host and (base in self.host.args or base in self.host.local_types):
            return "host"
        return "module"

    def type_of(self, base):
        t = self.local_types.get(base)
        if t is None and self.host:
            t = self.host.local_types.get(base)
        return t


def _type_name(type_text):
    m = re.search(r"TYPE\s*\(\s*(\w+)\s*\)", type_text or "", re.I)
    return m.group(1).lower() if m else None


def _parent_type(u, scope, global_types, type_fields):
    """The derived type holding the last component of designator `u`, such as
    `dvr%out%unOutFile(i)`, or None if the declarations do not say."""
    parts = [re.sub(r"\(.*", "", part).lower() for part in u.split("%")]
    declared = scope.type_of(parts[0])
    if declared is None:
        candidates = {_type_name(t) for t in global_types.get(parts[0], ())} - {None}
        declared = f"TYPE({candidates.pop()})" if len(candidates) == 1 else None
    current = _type_name(declared)
    for field in parts[1:-1]:
        if current is None:
            return None
        current = _type_name(type_fields.get(current, {}).get(field))
    return current


def _classify_unit(op, unit, scope, constants, field_types, global_types, type_fields=None):
    """How a procedure gets the unit it names, or None if that is not unit I/O.

    Returns a dict with `unit` and `source`: "argument" (a dummy, or a
    component of one), "local", "host" (a variable of the procedure an
    internal one is CONTAINed in), "module" (a module variable, or a
    component of one) or "literal" (a fixed unit number, or an integer named
    constant). A derived-type component also gets `component`. Standard input
    and output are None, and so, for READ and WRITE, is an internal file: a
    CHARACTER variable, which is string formatting, not a unit.
    """
    u = re.sub(r"\s+", "", unit)
    if u == "*":
        return None
    lit = re.fullmatch(r"(\d+)(?:_\w+)?", u)
    if lit:
        return None if lit.group(1) in _PRECONNECTED else {"unit": lit.group(1), "source": "literal",
                                                             "value": lit.group(1)}
    base_m = re.match(r"[A-Za-z]\w*", u)
    if not base_m:
        return {"unit": u, "source": "unknown"}
    base = base_m.group(0).lower()
    component = u.rsplit("%", 1)[1].split("(")[0].lower() if "%" in u else None
    kind = scope.kind_of(base)
    if component is None and kind == "module" and base in constants:
        value = constants[base]
        return None if value in _PRECONNECTED else {"unit": u, "source": "literal", "value": value}
    if op in ("READ", "WRITE"):
        if component:
            types = field_types.get(component, set())
            if types and all(t.startswith("CHARACTER") for t in types):
                return None
        else:
            declared = scope.type_of(base)
            if declared is None and kind == "module":
                types = global_types.get(base, set())
                declared = "CHARACTER" if types and all(t.startswith("CHARACTER") for t in types) else ""
            if (declared or "").startswith("CHARACTER"):
                return None
    entry = {"unit": u, "source": kind}
    if component:
        entry["component"] = component
        holder = _parent_type(u, scope, global_types, type_fields or {})
        if holder:
            entry["component_of"] = holder
    return entry


def _identity(entry):
    """What a unit is, independent of the procedure naming it: a component
    name, a module variable or a unit number. None for a dummy or a local."""
    if "component" in entry:
        return ("component", entry.get("component_of"), entry["component"])
    if entry["source"] == "literal":
        return ("literal", entry["value"])
    if entry["source"] == "module":
        return ("module", re.match(r"[A-Za-z]\w*", entry["unit"]).group(0).lower())
    return None


def _calls(node):
    """(callee lower, positional actual texts, {keyword: actual text}) for each
    CALL in this procedure, not in procedures it CONTAINS."""
    out = []
    for call in _own_nodes(node, f2003.Call_Stmt):
        names = walk(call, f2003.Name)
        if not names:
            continue
        positional, keywords = [], {}
        arg_list = call.items[1] if len(call.items) > 1 else None
        for item in (getattr(arg_list, "items", None) or ()):
            if isinstance(item, f2003.Actual_Arg_Spec):
                keywords[str(item.items[0]).lower()] = str(item.items[1])
            else:
                positional.append(str(item))
        out.append((str(names[0]).lower(), positional, keywords))
    return out


def _scopes(tree, fname):
    """(node, _Scope) for every procedure in `tree`, hosts before their
    internal procedures."""
    out = []

    def visit(node, host, module=None):
        if isinstance(node, f2003.Module):
            stmts = walk(node, f2003.Module_Stmt)
            module = first_name(stmts[0]) if stmts else module
        for child in getattr(node, "children", None) or ():
            if child is None or isinstance(child, str):
                continue
            if isinstance(child, f2003.Module):
                visit(child, host, module)
            elif isinstance(child, _SUBPROGRAMS):
                name = _get_procedure_name(child)
                stmts = walk(child, (f2003.Subroutine_Stmt, f2003.Function_Stmt))
                dummies = walk(stmts[0], f2003.Dummy_Arg_List) if stmts else []
                args = get_names(dummies[0]) if dummies else []
                local_types = _declared_types(_own_nodes(child, f2003.Type_Declaration_Stmt))
                span = getattr(getattr(stmts[0], "item", None), "span", None) if stmts else None
                scope = _Scope(name or "?", fname, args, local_types, host, module,
                               span[0] if span else None)
                out.append((child, scope))
                visit(child, scope, module)
            else:
                visit(child, host, module)

    visit(tree, None)
    return out


def find_unit_io(all_trees, dep_trees, procedures, type_defs):
    """Set `unit_io` on every procedure, and gather what the ordering needs.

    Reads the source and the dependencies: a dependency can be the procedure
    that opens a unit, as a library's open-a-file routine does for its
    callers. Returns a dict of facts for find_ordering_exceptions.
    """
    trees = list(all_trees.items()) + list((dep_trees or {}).items())
    constants = _integer_parameters([t for _, t in trees])
    field_types = defaultdict(set)
    type_fields = {}
    for t in type_defs:
        fields = type_fields.setdefault(t["name"].lower(), {})
        for fld in t.get("fields", []):
            field_types[fld["name"].lower()].add(str(fld.get("type", "")).upper())
            fields[fld["name"].lower()] = str(fld.get("type", "")).upper()
    global_types = defaultdict(set)
    for _, tree in trees:
        for decls in _module_spec_types(tree).values():
            for name, typ in decls.items():
                global_types[name].add(typ)

    by_key = {(p["name"].lower(), p["file"], p.get("start_line")): p for p in procedures}
    for p in procedures:
        p["unit_io"] = []
        p["host"] = None

    scopes, opens, opens_arg = {}, defaultdict(set), defaultdict(set)
    statements = {}
    for path, tree in trees:
        fname = os.path.basename(path)
        for node, scope in _scopes(tree, fname):
            key = scope.key
            scopes[key] = scope
            units = {}
            for stmt in _own_nodes(node, _IO_STMTS):
                found = io_statement_unit(stmt)
                if not found:
                    continue
                op, unit = found
                entry = _classify_unit(op, unit, scope, constants, field_types, global_types,
                                       type_fields)
                if entry is None:
                    continue
                if op == "OPEN":
                    opens[key].add(entry["unit"].lower())
                    if entry["source"] == "argument" and "component" not in entry:
                        opens_arg[scope.name.lower()].add(entry["unit"].lower())
                units.setdefault(entry["unit"].lower(), {**entry, "ops": set()})["ops"].add(op)
            statements[key] = (node, units)
            if key in by_key and path in all_trees:
                by_key[key]["unit_io"] = [{**e, "ops": sorted(e["ops"])} for e in units.values()]
                by_key[key]["host"] = scope.host.name if scope.host else None

    # Who opens each unit identity: by an OPEN of it, or by passing it to a
    # procedure that OPENs the dummy it arrives in.
    openers = defaultdict(set)
    calls = []
    args_by_name = {}
    for key, scope in scopes.items():
        args_by_name.setdefault(key[0], scope.args)
    for key, (node, units) in statements.items():
        scope = scopes[key]
        for u in units.values():
            ident = _identity(u)
            if ident and u["unit"].lower() in opens.get(key, ()):
                openers[ident].add(scope)
        for callee, positional, keywords in _calls(node):
            calls.append((scope, callee, positional, keywords))
            if callee not in opens_arg:
                continue
            callee_args = args_by_name.get(callee, [])
            for dummy in opens_arg[callee]:
                actual = _actual(callee_args, dummy, positional, keywords)
                entry = _classify_unit("OPEN", actual, scope, constants, field_types,
                                       global_types, type_fields) if actual else None
                if entry and _identity(entry):
                    openers[_identity(entry)].add(scope)
                if entry and entry["source"] == "local":
                    opens[key].add(entry["unit"].lower())

    return {"scopes": scopes, "opens": opens, "openers": openers, "calls": calls,
            "classify": lambda actual, scope: _classify_unit(
                "OPEN", actual, scope, constants, field_types, global_types, type_fields)}


def _actual(callee_args, dummy, positional, keywords):
    """The actual argument a call passes for `dummy`, or None."""
    if dummy in keywords:
        return keywords[dummy]
    if dummy in callee_args:
        i = callee_args.index(dummy)
        if i < len(positional):
            return positional[i]
    return None


def program_calls(all_trees, procedures):
    """(program name, callee name lower) for each call a main program makes.

    The call graph records calls from procedures; a driver's PROGRAM block is
    a caller too, and without it a procedure called from a program and from a
    test looks called only from the test.
    """
    names = {p["name"].lower() for p in procedures}
    out = set()
    for tree in all_trees.values():
        for prog in walk(tree, f2003.Main_Program):
            stmts = walk(prog, f2003.Program_Stmt)
            prog_name = first_name(stmts[0]) if stmts else "main"
            for call in walk(prog, f2003.Call_Stmt):
                callee = walk(call, f2003.Name)
                if callee:
                    out.add((prog_name, str(callee[0]).lower()))
            for ref in walk(prog, (f2003.Part_Ref, f2003.Structure_Constructor)):
                ref_names = walk(ref, f2003.Name)
                if ref_names and str(ref_names[0]).lower() in names:
                    out.add((prog_name, str(ref_names[0]).lower()))
    return sorted(out)


def generic_interfaces(all_trees):
    """{generic name lower: specific procedure names lower} from named
    INTERFACE blocks, so a call through the generic name reaches its specifics.
    """
    out = defaultdict(set)
    for tree in all_trees.values():
        for block in walk(tree, f2003.Interface_Block):
            stmts = walk(block, f2003.Interface_Stmt)
            names = walk(stmts[0], f2003.Name) if stmts else []
            if not names:
                continue  # an abstract or unnamed interface
            for proc_stmt in walk(block, f2003.Procedure_Stmt):
                out[str(names[0]).lower()].update(n.lower() for n in get_names(proc_stmt))
    return out


def _depths(procedures, internal_callees):
    """Dependency depth: 0 for a leaf, else 1 + the deepest callee. A call that
    closes a cycle is ignored."""
    depth, visiting = {}, set()

    def visit(name):
        if name in depth:
            return depth[name]
        visiting.add(name)
        d = 0
        for callee in internal_callees.get(name, ()):
            if callee not in visiting:
                d = max(d, visit(callee) + 1)
        visiting.discard(name)
        depth[name] = d
        return d

    for p in procedures:
        visit(p["name"].lower())
    return depth


def _resolver(scopes, module_uses):
    """A function mapping (calling scope, procedure name) to the scope keys the
    call can mean: the same file's, else the same module's, else a USEd
    module's, else every procedure of that name."""
    by_name = defaultdict(list)
    for key, scope in scopes.items():
        by_name[key[0]].append(key)

    def resolve(caller, name):
        keys = by_name.get(name, [])
        if len(keys) <= 1:
            return keys
        mine = (caller.module or "").lower()
        for test in (lambda k: k[1] == caller.file,
                     lambda k: mine and (scopes[k].module or "").lower() == mine,
                     lambda k: (scopes[k].module or "").lower() in module_uses.get(mine, ())):
            narrowed = [k for k in keys if test(k)]
            if narrowed:
                return narrowed
        return keys

    return resolve


def find_ordering_exceptions(procedures, call_edges, facts, generics=None, module_uses=None,
                             max_probes=5):
    """The procedures leaf-first ordering gets wrong, and early probe candidates.

    A procedure is an exception when it does I/O on a unit it neither holds as
    a local nor opens itself. Its owners are found by following the unit up the
    call chain, actual argument by actual argument, to the caller that holds it
    as a local; a unit stored in a derived type, a module variable or a fixed
    number is owned by whoever opens it. Sets `depth` on every procedure; a
    call through a generic interface counts as a call to each specific.
    Returns {"unit_io_not_owned": [...], "probe_candidates": [...]}.
    """
    generics = generics or {}
    names = {p["name"].lower() for p in procedures}
    callees, callers = defaultdict(set), defaultdict(set)
    for e in call_edges:
        a = e["caller"].lower()
        for b in generics.get(e["callee"].lower(), {e["callee"].lower()}):
            if a != b and a in names and b in names:
                callees[a].add(b)
                callers[b].add(a)
    depth = _depths(procedures, callees)
    for p in procedures:
        p["depth"] = depth.get(p["name"].lower(), 0)

    scopes = facts["scopes"]
    resolve = _resolver(scopes, {m.lower(): {u.lower() for u in uses}
                                 for m, uses in (module_uses or {}).items()})
    calls_to = defaultdict(list)
    for scope, callee, positional, keywords in facts["calls"]:
        for specific in generics.get(callee, {callee}):
            for key in resolve(scope, specific):
                calls_to[key].append((scope, positional, keywords))
    openers, opens, classify = facts["openers"], facts["opens"], facts["classify"]

    def owners_by_identity(ident, owners, notes, unit):
        found = set(openers.get(ident, set()))
        if ident[0] == "component" and ident[1] is None:
            found = {n for k, ns in openers.items()
                     if k[0] == "component" and k[2] == ident[2] for n in ns}
            if found:
                notes.append(f"{unit}: the type holding %{ident[2]} is not declared in the "
                             f"scanned source, so its owners are matched by component name")
        for n in found:
            owners.setdefault(n, None)
        if not found:
            notes.append({
                "component": f"{unit} is stored in a derived type"
                             + (f" ({ident[1]})" if ident[1] else "")
                             + ", and nothing in the scanned source opens it",
                "module": f"{unit} is a module variable, and nothing in the scanned source "
                          f"opens it",
                "literal": f"unit {ident[1]} is opened nowhere in the scanned source; the "
                           f"Fortran runtime opens it implicitly",
            }[ident[0]])

    def owners_by_argument(key, args, dummy, owners, notes):
        frontier, seen, d = [(key, tuple(args), dummy)], set(), 0
        while frontier and d < 50:
            d += 1
            nxt = []
            for callee, callee_args, dm in frontier:
                for scope, positional, keywords in calls_to.get(callee, ()):
                    actual = _actual(list(callee_args), dm, positional, keywords)
                    entry = classify(actual, scope) if actual else None
                    if entry is None:
                        continue
                    if entry["source"] == "local":
                        owners[scope] = min(d, owners.get(scope) or d)
                    elif entry["source"] == "host":
                        owners[scope.host] = min(d, owners.get(scope.host) or d)
                    elif entry["source"] == "argument" and "component" not in entry:
                        base = re.match(r"[A-Za-z]\w*", entry["unit"]).group(0).lower()
                        step = (scope.key, tuple(scope.args), base)
                        if step not in seen:
                            seen.add(step)
                            nxt.append(step)
                    elif _identity(entry):
                        owners_by_identity(_identity(entry), owners, notes, actual.strip())
            frontier = nxt
        if not owners and not notes:
            notes.append(f"no caller in the scanned source passes it a unit it holds")

    exceptions = []
    for p in procedures:
        key = (p["name"].lower(), p["file"], p.get("start_line"))
        held = opens.get(key, set())
        not_owned = [u for u in p["unit_io"]
                     if u["source"] not in ("local", "host") and u["unit"].lower() not in held
                     and not (_identity(u) and any(
                         o.key == key for o in openers.get(_identity(u), ())))]
        if not not_owned:
            continue
        owners, notes = {}, []
        for u in not_owned:
            if u["source"] == "argument" and "component" not in u:
                owners_by_argument(key, [a.lower() for a in p["args"]],
                                   u["unit"].lower(), owners, notes)
            elif _identity(u):
                owners_by_identity(_identity(u), owners, notes, u["unit"])
            else:
                notes.append(f"{u['unit']}: cannot tell where the unit comes from")
        exceptions.append({
            "name": p["name"], "module": p["module"], "file": p["file"],
            "start_line": p.get("start_line"),
            "depth": p["depth"], "units": not_owned,
            "owners": [{"name": o.name, "module": o.module, "file": o.file,
                        "start_line": o.start_line,
                        "host": o.host.name if o.host else None, "distance": d}
                       for o, d in sorted(owners.items(),
                                          key=lambda kv: (kv[1] or 0, kv[0].name.lower()))],
            "notes": list(dict.fromkeys(notes)),
        })

    flagged = {e["name"].lower() for e in exceptions}
    by_name = {p["name"].lower(): p for p in procedures}
    candidates = []
    for p in procedures:
        name = p["name"].lower()
        cs = callees.get(name, set())
        if (p["depth"] != 1 or p["is_generated"] or p.get("is_internal")
                or name in flagged or not callers.get(name)
                or any(c in flagged for c in cs)):
            continue
        candidates.append({"name": p["name"], "module": p["module"], "file": p["file"],
                           "start_line": p.get("start_line"),
                           "line_count": p["line_count"], "arg_count": p["arg_count"],
                           "callees": sorted(by_name[c]["name"] for c in cs)})
    candidates.sort(key=lambda c: (c["line_count"], c["arg_count"], c["name"].lower()))
    return {"unit_io_not_owned": exceptions, "probe_candidates": candidates[:max_probes]}


def _ordering_summary(ordering):
    """The summary's section on where leaf-first ordering goes wrong."""
    lines = ["## Ordering Exceptions", ""]
    exc = ordering["unit_io_not_owned"]
    lines.append(f"### Unit I/O on a unit the procedure does not open: {len(exc)}")
    lines.append("")
    lines.append("C++ cannot read or write a Fortran unit number. Translate each of these with the "
                 "procedure that owns the unit, or after it, whatever their depth in the call graph.")
    lines.append("")
    if exc:
        lines.append("| Procedure | Module | Depth | Unit (source) | Owner | Note |")
        lines.append("|-----------|--------|-------|---------------|-------|------|")
        for e in sorted(exc, key=lambda e: (e["depth"], e["name"].lower())):
            units = ", ".join(f"`{u['unit']}` ({u['source']})" for u in e["units"])
            owners = ", ".join(
                o["name"] + (f" in {o['host']}" if o["host"] else "")
                + (f" ({o['module']})" if o["module"] else "")
                + (f", {o['distance']} up" if o["distance"] else "")
                for o in e["owners"]) or "-"
            lines.append(f"| {e['name']} | {e['module'] or '-'} | {e['depth']} | {units} | "
                         f"{owners} | {'; '.join(e['notes']) or '-'} |")
    else:
        lines.append("(none found)")
    lines.append("")
    probes = ordering["probe_candidates"]
    lines.append(f"### First non-leaf probe candidates: {len(probes)}")
    lines.append("")
    lines.append("A run of leaves never exercises calls between translated procedures. Translate one "
                 "of these early, with its callees, to exercise that path before the leaf phase is long.")
    lines.append("")
    if probes:
        lines.append("| Procedure | Module | Lines | Args | Callees |")
        lines.append("|-----------|--------|-------|------|---------|")
        for c in probes:
            lines.append(f"| {c['name']} | {c['module'] or '-'} | {c['line_count']} | "
                         f"{c['arg_count']} | {', '.join(c['callees'])} |")
    else:
        lines.append("(none found)")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def read_entry_points_file(path):
    """Entry-point names from a file, one per line; `#` starts a comment."""
    names = []
    with open(path) as f:
        for line in f:
            name = line.split("#", 1)[0].strip()
            if name:
                names.append(name)
    return names


def run(src, output, deps=None, search=None, generated_markers=(), entry_points=(),
        coverage=None, overrides=None):
    """Inventory the Fortran files in `src` and write the results to `output`.

    `vit inventory` calls this. See the module docstring for the outputs.
    `coverage` (a file from `vit coverage run`) and `overrides` (a JSON file of
    procedure dispositions) feed the work order; see vit/work_order.py.
    """
    entry_points = {n.strip().lower() for n in entry_points if n.strip()}
    generated_markers = list(generated_markers)

    os.makedirs(output, exist_ok=True)

    # Collect source files
    src_files = sorted(glob.glob(os.path.join(src, "*.f90")) +
                       glob.glob(os.path.join(src, "*.F90")))
    print(f"Source files: {len(src_files)} in {src}")

    dep_files = []
    if deps:
        dep_files = sorted(glob.glob(os.path.join(deps, "*.f90")) +
                           glob.glob(os.path.join(deps, "*.F90")))
        print(f"Dependency files: {len(dep_files)} in {deps}")

    # Check which files are machine-generated
    file_generated_map = {}
    for f in src_files + dep_files:
        file_generated_map[f] = is_generated(f, generated_markers)

    if generated_markers:
        gen_count = sum(1 for v in file_generated_map.values() if v)
        print(f"Generated files: {gen_count}")

    # Create parser
    # The Fortran 2008 parser accepts everything the 2003 one does, and syntax
    # such as OPEN(NEWUNIT=...) that would otherwise drop a whole file.
    fparser = ParserFactory().create(std="f2008")

    # Pass 1: Parse primary source
    print("\nPass 1: Parsing source files and collecting modules/procedures...")
    modules, procedures, all_trees = collect_modules_and_procedures(
        fparser, src_files, file_generated_map)
    print(f"  Modules: {len(modules)}, Procedures: {len(procedures)}")

    # Parse dependency files for types only
    dep_trees = {}
    dep_modules = {}
    if dep_files:
        print("  Parsing dependency files...")
        dep_modules_raw, dep_procs, dep_trees = collect_modules_and_procedures(
            fparser, dep_files, file_generated_map)
        dep_modules = dep_modules_raw

    # Pass 2: Call graph
    print("\nPass 2: Building call graph...")
    call_edges = build_call_graph(all_trees, procedures, modules)
    internal_edges = [e for e in call_edges if e["is_internal"]]
    external_edges = [e for e in call_edges if not e["is_internal"]]
    print(f"  Edges: {len(call_edges)} ({len(internal_edges)} internal, {len(external_edges)} external)")

    # Pass 3: Type inventory
    print("\nPass 3: Collecting type definitions...")
    type_defs = collect_types(all_trees, dep_trees)
    print(f"  Types: {len(type_defs)}")

    # Pass 4: Module dependencies
    print("\nPass 4: Building module dependency graph...")
    mod_deps = build_module_dependencies(modules, procedures)
    print(f"  Modules in topo order: {len(mod_deps)}")

    # Pass 5: Dead code
    print("\nPass 5: Finding uncalled procedures...")
    uncalled = find_uncalled_procedures(procedures, call_edges, src,
                                        search_dir=search,
                                        entry_points=entry_points)
    print(f"  Uncalled within scope: {len(uncalled)}")

    # Pass 6: Framework boundary
    print("\nPass 6: Identifying framework boundary...")
    boundary = find_framework_boundary(modules, procedures, entry_points)
    print(f"  Boundary procedures: {len(boundary)}")

    # Pass 7: LOC metrics
    print("\nPass 7: Computing LOC metrics...")
    loc = compute_loc_metrics(src_files, procedures, modules, file_generated_map)

    # Pass 8: Ordering exceptions
    print("\nPass 8: Finding ordering exceptions...")
    facts = find_unit_io(all_trees, dep_trees, procedures, type_defs)
    ordering = find_ordering_exceptions(
        procedures, call_edges, facts,
        generics=generic_interfaces({**all_trees, **dep_trees}),
        module_uses={name: m["uses"] for name, m in {**dep_modules, **modules}.items()})
    print(f"  Unit I/O on a unit the procedure does not open: "
          f"{len(ordering['unit_io_not_owned'])}")
    print(f"  First non-leaf probe candidates: {len(ordering['probe_candidates'])}")

    unparsed = sorted(os.path.basename(f) for f in src_files + dep_files
                      if f not in all_trees and f not in dep_trees)
    if unparsed:
        print(f"\n  WARNING: {len(unparsed)} file(s) could not be parsed and are missing "
              f"from every count: {', '.join(unparsed)}")

    # Write JSON outputs
    def write_json(name, data):
        path = os.path.join(output, name)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Wrote {path}")

    print("\nWriting output files...")
    write_json("function_inventory.json", procedures)
    write_json("call_graph.json", call_edges)
    write_json("type_inventory.json", type_defs)
    write_json("module_dependencies.json", mod_deps)
    write_json("uncalled_procedures.json", uncalled)
    write_json("framework_boundary.json", boundary)
    write_json("loc_metrics.json", loc)

    write_json("ordering.json", ordering)

    from . import work_order as wo
    coverage_procs = None
    if coverage:
        from . import coverage as cov
        data = cov.load(coverage)
        coverage_procs = cov.procedure_coverage(data, cov.coverage_root(data, src))
    order = wo.build_work_order(
        procedures, call_edges, ordering, uncalled,
        program_calls=program_calls(all_trees, procedures),
        generics=generic_interfaces({**all_trees, **dep_trees}),
        module_uses={name: m["uses"] for name, m in {**dep_modules, **modules}.items()},
        coverage_procedures=coverage_procs,
        overrides=wo.load_overrides(overrides, procedures))
    write_json("work_order.json", order)
    inputs = {"source": src, "dependencies": deps or "none",
              "entry points": len(entry_points),
              "coverage": os.path.basename(coverage) if coverage else "none",
              "overrides": os.path.basename(overrides) if overrides else "none"}
    if unparsed:
        inputs["not parsed"] = ", ".join(unparsed)
    order_md = os.path.join(output, "work_order.md")
    with open(order_md, "w") as f:
        f.write(wo.work_order_markdown(order, inputs))
    print(f"  Wrote {order_md}")
    print(f"\nWork order: {sum(len(s['procedures']) for s in order['steps'])} procedures in "
          f"{len(order['steps'])} steps; {len(order['left_out'])} left out"
          + (f"; {len(order['needs_decision'])} need"
             + ("s" if len(order["needs_decision"]) == 1 else "") + " a decision"
             if order["needs_decision"] else ""))

    # Write markdown summary
    write_summary(output, loc, procedures, call_edges, type_defs, mod_deps,
                  uncalled, boundary, ordering=ordering, unparsed=unparsed)

    print(f"\nDone. {len(procedures)} procedures inventoried across {loc['file_count']} files.")
    return 0
