#!/usr/bin/env python3
"""
fortran_inventory.py — Structural inventory of a Fortran codebase using fparser2.

Produces 7 JSON data files + 1 markdown summary for use as input to PROJECT_PLAN
documents. Follows the methodology in CREATING_PROJECT_PLANS.md (Step 1: Structural
Inventory).

Usage:
    python3 fortran_inventory.py \
        --src /workspace/openfast/modules/aerodyn/src \
        --deps /workspace/openfast/modules/nwtc-library/src \
        --output /workspace/vit/analysis/aerodyn

Output files:
    function_inventory.json   — every procedure (name, module, file, args, visibility, ...)
    call_graph.json           — caller→callee edges with internal/external tags
    type_inventory.json       — derived TYPE definitions with fields
    module_dependencies.json  — USE graph, topologically sorted
    uncalled_procedures.json  — procedures with zero call sites within scanned scope
    framework_boundary.json   — public API procedures (external-facing)
    loc_metrics.json          — lines of code per file/module/procedure
    inventory_summary.md      — human-readable digest
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict

from fparser.two.parser import ParserFactory
from fparser.two.utils import walk
from fparser.two import Fortran2003 as f2003
from fparser.common.readfortran import FortranFileReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_file(parser, path):
    """Parse a single Fortran file, return the AST or None on failure."""
    try:
        reader = FortranFileReader(path, ignore_comments=False)
        return parser(reader)
    except Exception as e:
        print(f"  WARN: failed to parse {os.path.basename(path)}: {e}", file=sys.stderr)
        return None


def is_registry_generated(path):
    """Check if a file is Registry-generated (contains the marker comment)."""
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                if "STARTOFREGISTRYGENERATEDFILE" in line:
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

def collect_modules_and_procedures(parser, src_files, file_registry_map):
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
        is_gen = file_registry_map.get(path, False)

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
                "is_registry_generated": is_gen,
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

    return {
        "name": name,
        "module": module_name,
        "file": filename,
        "kind": kind,
        "args": args,
        "arg_count": len(args),
        "line_count": lines,
        "visibility": visibility,
        "has_save": has_save,
        "is_registry_generated": is_gen,
        "is_pure": is_pure,
        "is_elemental": is_elemental,
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
            "is_registry_generated": mod_info["is_registry_generated"],
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

def find_uncalled_procedures(procedures, call_edges):
    """
    Find procedures with zero call sites WITHIN the scanned source tree.

    CAVEAT: This does NOT mean the procedure is truly dead code. It may be
    called from:
    - OpenFAST glue code (outside AeroDyn)
    - Other OpenFAST modules (InflowWind, SeaState, etc.)
    - C/C++ code via C-binding entry points
    - Main programs (drivers)
    - Type-bound procedure dispatch

    This list is useful for identifying *candidates* for exclusion from
    translation scope, but each entry needs manual verification.
    """
    # All callees (from both CALL statements and function references)
    called = set()
    for e in call_edges:
        called.add(e["callee"].lower())

    uncalled = []
    for p in procedures:
        if p["is_registry_generated"]:
            continue  # Skip registry boilerplate
        if p.get("is_internal", False):
            continue  # Skip CONTAINS-block internal procedures
        if p["name"].lower() not in called:
            # Classify the likely reason it appears uncalled
            reason = "unknown"
            mod = p["module"] or ""
            if "c_binding" in mod.lower():
                reason = "c_binding_entry_point"
            elif "driver" in mod.lower() or "dvr" in mod.lower():
                reason = "driver_entry_point"
            elif "test" in mod.lower() or "TEST" in p["name"]:
                reason = "test_function"
            elif p["visibility"] == "public" and p["name"].startswith(("AD_", "BEMT_", "DBEMT_", "UA_", "FVW_", "AA_", "AFI_", "ADI_")):
                reason = "likely_framework_interface"

            uncalled.append({
                "name": p["name"],
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

def find_framework_boundary(modules, procedures):
    """
    Identify procedures that form the external API:
    - PUBLIC procedures in top-level modules (AeroDyn, BEMT, etc.)
    - Procedures in Driver modules
    - C-binding entry points
    """
    boundary = []

    for p in procedures:
        if p["is_registry_generated"]:
            continue

        reason = None

        # PUBLIC procedures (not in a private-default module, or explicitly public)
        if p["visibility"] == "public" and p["module"]:
            mod_info = modules.get(p["module"].lower(), {})
            # Top-level modules (AeroDyn, BEMT, FVW, UnsteadyAero, etc.)
            # are the ones that external code calls
            if mod_info.get("private_default", False):
                # Explicitly public in a private-default module = deliberate API
                if p["name"] in mod_info.get("public_names", []):
                    reason = "explicit_public"

        # C-binding modules
        if p["module"] and "c_binding" in p["module"].lower():
            reason = "c_binding"

        # Driver modules
        if p["module"] and "driver" in p["module"].lower():
            reason = "driver"

        # Standard OpenFAST module interface names
        standard_prefixes = ["AD_Init", "AD_End", "AD_UpdateStates", "AD_CalcOutput",
                             "AD_CalcConstrStateResidual", "AD_CalcContStateDeriv",
                             "AD_UpdateDiscState", "AD_JacobianPInput",
                             "AD_JacobianPContState", "AD_JacobianPDiscState",
                             "AD_JacobianPConstrState", "AD_GetOP",
                             "BEMT_Init", "BEMT_End", "BEMT_UpdateStates",
                             "BEMT_CalcOutput", "BEMT_ReInit",
                             "UA_Init", "UA_End", "UA_UpdateStates", "UA_CalcOutput",
                             "FVW_Init", "FVW_End", "FVW_UpdateStates", "FVW_CalcOutput",
                             "DBEMT_Init", "DBEMT_End", "DBEMT_UpdateStates", "DBEMT_CalcOutput",
                             "AA_Init", "AA_End", "AA_UpdateStates", "AA_CalcOutput",
                             "AFI_Init", "AFI_End",
                             ]
        if p["name"] in standard_prefixes:
            reason = "standard_interface"

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

def compute_loc_metrics(src_files, procedures, modules, file_registry_map):
    """Compute lines of code metrics."""
    per_file = []
    total_lines = 0
    total_generated = 0
    total_handwritten = 0

    for path in src_files:
        fname = os.path.basename(path)
        lines = count_file_lines(path)
        is_gen = file_registry_map.get(path, False)
        total_lines += lines
        if is_gen:
            total_generated += lines
        else:
            total_handwritten += lines
        per_file.append({
            "file": fname,
            "lines": lines,
            "is_registry_generated": is_gen,
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
                  "line_count": p["line_count"], "is_registry_generated": p["is_registry_generated"]}
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
                  uncalled, boundary):
    """Write inventory_summary.md."""
    lines = []
    lines.append("# AeroDyn Structural Inventory Summary")
    lines.append("")
    lines.append(f"Generated by `fortran_inventory.py`")
    lines.append("")

    # Overview
    hw_procs = [p for p in procedures if not p["is_registry_generated"]]
    gen_procs = [p for p in procedures if p["is_registry_generated"]]
    internal_procs = [p for p in procedures if p.get("is_internal", False)]
    top_level_hw = [p for p in hw_procs if not p.get("is_internal", False)]
    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total files | {loc['file_count']} |")
    lines.append(f"| Total lines | {loc['total_lines']:,} |")
    lines.append(f"| Hand-written lines | {loc['total_handwritten']:,} |")
    lines.append(f"| Registry-generated lines | {loc['total_generated']:,} |")
    lines.append(f"| Modules | {loc['module_count']} |")
    lines.append(f"| Total procedures | {loc['procedure_count']} |")
    lines.append(f"| Hand-written procedures (top-level) | {len(top_level_hw)} |")
    lines.append(f"| Hand-written procedures (CONTAINS internal) | {len([p for p in internal_procs if not p['is_registry_generated']])} |")
    lines.append(f"| Registry-generated procedures | {len(gen_procs)} |")
    lines.append(f"| Subroutines | {sum(1 for p in procedures if p['kind'] == 'subroutine')} |")
    lines.append(f"| Functions | {sum(1 for p in procedures if p['kind'] == 'function')} |")
    lines.append(f"| Procedures with SAVE state | {sum(1 for p in procedures if p['has_save'])} |")
    lines.append(f"| Derived types | {len(type_defs)} |")
    lines.append(f"| Call graph edges | {len(call_edges)} |")
    lines.append(f"| Uncalled within AeroDyn | {len(uncalled)} |")
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
        gen = "yes" if p["is_registry_generated"] else "no"
        lines.append(f"| {p['name']} | {p['module'] or '-'} | {p['line_count']} | {gen} |")
    lines.append("")

    # Files by size
    lines.append("## Files by Size")
    lines.append("")
    lines.append("| File | Lines | Generated? |")
    lines.append("|------|-------|-----------|")
    for f in loc["per_file"]:
        gen = "yes" if f["is_registry_generated"] else "no"
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
        gen = "yes" if m["is_registry_generated"] else "no"
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
    lines.append(f"## Uncalled Within AeroDyn: {len(uncalled)} procedures")
    lines.append("")
    lines.append("*These have zero call sites in the scanned AeroDyn source. Many are called from*")
    lines.append("*outside AeroDyn (OpenFAST glue code, C bindings, drivers, tests). Not dead code.*")
    lines.append("")
    if uncalled:
        lines.append("| Name | Module | Lines | Likely Reason |")
        lines.append("|------|--------|-------|--------------|")
        for d in sorted(uncalled, key=lambda x: -x["line_count"]):
            lines.append(f"| {d['name']} | {d['module'] or '-'} | {d['line_count']} | {d.get('likely_reason', '?')} |")
    else:
        lines.append("(none found)")
    lines.append("")

    # Types summary
    src_types = [t for t in type_defs if not t["is_dependency"]]
    dep_types = [t for t in type_defs if t["is_dependency"]]
    lines.append(f"## Derived Types: {len(src_types)} in AeroDyn, {len(dep_types)} in dependencies")
    lines.append("")
    lines.append("### AeroDyn Types (top 20 by field count)")
    lines.append("")
    lines.append("| Type | Module | Fields | Has Allocatable | Has Pointer |")
    lines.append("|------|--------|--------|----------------|-------------|")
    for t in sorted(src_types, key=lambda x: -x["field_count"])[:20]:
        has_alloc = any(f.get("is_allocatable") for f in t["fields"])
        has_ptr = any(f.get("is_pointer") for f in t["fields"])
        lines.append(f"| {t['name']} | {t['module'] or '-'} | {t['field_count']} | {'yes' if has_alloc else 'no'} | {'yes' if has_ptr else 'no'} |")
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
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fortran structural inventory via fparser2")
    parser.add_argument("--src", required=True, help="Primary source directory (full inventory)")
    parser.add_argument("--deps", default=None, help="Dependency source directory (module/type only)")
    parser.add_argument("--output", required=True, help="Output directory for JSON/MD files")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Collect source files
    src_files = sorted(glob.glob(os.path.join(args.src, "*.f90")) +
                       glob.glob(os.path.join(args.src, "*.F90")))
    print(f"Source files: {len(src_files)} in {args.src}")

    dep_files = []
    if args.deps:
        dep_files = sorted(glob.glob(os.path.join(args.deps, "*.f90")) +
                           glob.glob(os.path.join(args.deps, "*.F90")))
        print(f"Dependency files: {len(dep_files)} in {args.deps}")

    # Check which files are registry-generated
    file_registry_map = {}
    for f in src_files + dep_files:
        file_registry_map[f] = is_registry_generated(f)

    gen_count = sum(1 for v in file_registry_map.values() if v)
    print(f"Registry-generated files: {gen_count}")

    # Create parser
    fparser = ParserFactory().create(std="f2003")

    # Pass 1: Parse primary source
    print("\nPass 1: Parsing source files and collecting modules/procedures...")
    modules, procedures, all_trees = collect_modules_and_procedures(
        fparser, src_files, file_registry_map)
    print(f"  Modules: {len(modules)}, Procedures: {len(procedures)}")

    # Parse dependency files for types only
    dep_trees = {}
    dep_modules = {}
    if dep_files:
        print("  Parsing dependency files...")
        dep_modules_raw, dep_procs, dep_trees = collect_modules_and_procedures(
            fparser, dep_files, file_registry_map)
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
    uncalled = find_uncalled_procedures(procedures, call_edges)
    print(f"  Uncalled within scope: {len(uncalled)}")

    # Pass 6: Framework boundary
    print("\nPass 6: Identifying framework boundary...")
    boundary = find_framework_boundary(modules, procedures)
    print(f"  Boundary procedures: {len(boundary)}")

    # Pass 7: LOC metrics
    print("\nPass 7: Computing LOC metrics...")
    loc = compute_loc_metrics(src_files, procedures, modules, file_registry_map)

    # Write JSON outputs
    def write_json(name, data):
        path = os.path.join(args.output, name)
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

    # Write markdown summary
    write_summary(args.output, loc, procedures, call_edges, type_defs, mod_deps,
                  uncalled, boundary)

    print(f"\nDone. {len(procedures)} procedures inventoried across {loc['file_count']} files.")


if __name__ == "__main__":
    main()
