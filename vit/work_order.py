"""
The translation work order: every in-scope procedure, in the order to translate
it, derived from the structural inventory and, when given, line coverage.

Nothing here is judgement. Given the same source, entry points, coverage and
overrides, the order is the same. What a person decides comes in as input: the
scope (`vit inventory --src/--deps`), the entry points, and an overrides file
for the few calls only a person can make, such as rewriting a subsystem as
idiomatic C++ instead of mirroring it.

How the order is built:

  Scope     Generated procedures (change the generator instead), procedures
            nothing calls, procedures called only from commented-out lines or
            from test code, and `out_of_scope` overrides are left out, each with
            its reason. An internal procedure is translated with its host.

  Steps     Procedures that must be translated together form one step:
            - a procedure doing I/O on a unit another procedure owns, with the
              owners: C++ cannot read a Fortran unit, so it cannot go first, and
              an owner holding a C++ stream cannot pass it to Fortran, so they
              cannot go first either. A stored unit's opener joins the
              procedures that use it the same way.
            - procedures that call each other in a cycle.

  Order     Callees before callers, by layer: a step's layer is one more than
            its deepest callee step's. The first non-leaf probe and its callees
            go first. Within a layer, steps a test case reaches come before
            steps none does, then smaller before larger, then by name.
            `deferred` overrides go last, and so must their callers.

  Verify    From coverage, per procedure: a kernel, with the test case that
            calls it most as the case to extract with; or "not reached", which
            needs another method (docs/VERIFICATION_METHODS.md); or "not built".
"""

import heapq
import json
import os
from collections import defaultdict

OVERRIDE_STATUSES = {
    "out_of_scope": "left out by override",
    "deferred": "placed last by override",
}

# Uncalled procedures that are left out, by inventory reason.
_LEFT_OUT = {
    "genuinely_dead": "nothing calls it; confirm with `vit reachability`",
    "commented_out": "called only from commented-out lines",
    "test_function": "test code",
}


def load_overrides(path, procedures):
    """{procedure key: {status, reason}} from a JSON file, or {} without one.

    The file maps a procedure name, or `module:name` when the name is not
    unique, to {"status": "out_of_scope" | "deferred", "reason": "..."}. A
    reason is required, and a name that matches no procedure, or more than
    one, is an error: an override that silently applies to nothing, or to the
    wrong procedure, would change the order without saying so.
    """
    if not path:
        return {}
    with open(path) as f:
        raw = json.load(f)
    by_name = defaultdict(list)
    for p in procedures:
        by_name[p["name"].lower()].append(p)
        by_name[f"{(p['module'] or '').lower()}:{p['name'].lower()}"].append(p)
    out = {}
    for name, entry in raw.items():
        if isinstance(entry, str):
            entry = {"status": entry}
        status = entry.get("status")
        if status not in OVERRIDE_STATUSES:
            raise ValueError(f"{path}: override for '{name}' has status {status!r}; "
                             f"expected one of {sorted(OVERRIDE_STATUSES)}")
        if not entry.get("reason"):
            raise ValueError(f"{path}: override for '{name}' has no reason")
        matches = by_name.get(name.lower(), [])
        if not matches:
            raise ValueError(f"{path}: override for '{name}' matches no procedure")
        if len(matches) > 1:
            modules = sorted({m["module"] or "-" for m in matches})
            raise ValueError(f"{path}: '{name}' names {len(matches)} procedures "
                             f"(modules {', '.join(modules)}); write module:name")
        out[_key(matches[0])] = {"status": status, "reason": entry["reason"]}
    return out


def _key(p):
    """Procedures are told apart by file and start line: internal procedures
    in one file often share a name, such as one Cleanup per host."""
    return (p["name"].lower(), p["file"], p.get("start_line"))


def _coverage_index(coverage_procedures):
    """{(file basename, name, host): coverage item}, ambiguous keys dropped."""
    index, seen = {}, defaultdict(int)
    for item in coverage_procedures or ():
        k = (os.path.basename(item["file"]).lower(), item["name"].lower(),
             (item["host"] or "").lower())
        seen[k] += 1
        index[k] = item
    return {k: v for k, v in index.items() if seen[k] == 1}


def _verification(p, index, has_coverage, line_basis=False):
    if not has_coverage:
        return {"route": "unknown", "note": "no coverage given"}
    item = index.get((p["file"].lower(), p["name"].lower(), (p.get("host") or "").lower()))
    if item is None:
        if line_basis:
            # Older coverage files dropped every file in which no line ran.
            return {"route": "not reached", "note": "no coverage recorded: not reached by "
                    "any test case, or not built"}
        return {"route": "not built", "note": "not in the coverage build"}
    if item["status"] == "reached":
        case, n = max(item["calls"].items(), key=lambda kv: (kv[1], kv[0]))
        unit = "calls" if item["basis"] == "calls" else "executions"
        return {"route": "kernel", "case": case, "count": n,
                "note": f"extract with {case} ({n} {unit})"}
    if item["status"] == "not built":
        return {"route": "not built", "note": "not in the coverage build"}
    return {"route": "not reached",
            "note": "no test case reaches it: test-validate, a harness, or a new test case"}


class _Union:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _sccs(nodes, edges):
    """Strongly connected components (Tarjan), as lists of nodes."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]

    def visit(v):
        work = [(v, iter(sorted(edges.get(v, ()))))]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(edges.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[node] = min(low[node], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                out.append(comp)

    for v in sorted(nodes):
        if v not in index:
            visit(v)
    return out


def _test_code(procedures, call_edges, generics, program_calls=()):
    """{key: reason} for test code: procedures matching the test-name rule, and
    procedures whose every caller, main programs included, is test code."""
    from .inventory import is_test_procedure
    names = defaultdict(list)
    for p in procedures:
        names[p["name"].lower()].append(p)
    callers = defaultdict(set)
    for e in call_edges:
        for callee in generics.get(e["callee"].lower(), {e["callee"].lower()}):
            for q in names.get(callee, ()):
                callers[_key(q)].add(e["caller"].lower())
    test_names = set()
    for prog, callee in program_calls:
        for c in generics.get(callee, {callee}):
            for q in names.get(c, ()):
                callers[_key(q)].add("program:" + prog.lower())
        if is_test_procedure(prog, None):
            test_names.add("program:" + prog.lower())
    tests = {_key(p): "test code: a test module, or Test as a word of its name"
             for p in procedures if is_test_procedure(p["name"], p["module"])}
    test_names |= {k[0] for k in tests}
    changed = True
    while changed:
        changed = False
        for p in procedures:
            k = _key(p)
            cs = callers.get(k, set()) - {p["name"].lower()}
            if k not in tests and cs and cs <= test_names:
                tests[k] = "test code: called only from test code"
                test_names.add(k[0])
                changed = True
    return tests


def build_work_order(procedures, call_edges, ordering, uncalled, generics=None,
                     module_uses=None, coverage_procedures=None, overrides=None,
                     program_calls=()):
    """The work order. See the module docstring for the rules."""
    generics = generics or {}
    overrides = overrides or {}
    module_uses = {m.lower(): {u.lower() for u in uses}
                   for m, uses in (module_uses or {}).items()}
    has_coverage = coverage_procedures is not None
    line_basis = has_coverage and not any(c["basis"] == "calls" for c in coverage_procedures)
    index = _coverage_index(coverage_procedures)
    by_key = {_key(p): p for p in procedures}

    # --- scope -------------------------------------------------------------
    left_out = {}
    uncalled_reason = {(u["name"].lower(), (u.get("module") or "").lower()): u["likely_reason"]
                       for u in uncalled}
    tests = _test_code(procedures, call_edges, generics, program_calls)
    for p in procedures:
        k = _key(p)
        reason = uncalled_reason.get((p["name"].lower(), (p["module"] or "").lower()))
        if p.get("is_generated"):
            left_out[k] = "generated: change the generator instead"
        elif k in tests:
            left_out[k] = tests[k]
        elif reason in _LEFT_OUT:
            left_out[k] = _LEFT_OUT[reason]
        elif overrides.get(k, {}).get("status") == "out_of_scope":
            left_out[k] = f"out of scope by override: {overrides[k]['reason']}"

    def owner_of(p):
        """The key a procedure is translated under: its outermost host's."""
        k = _key(p)
        seen = set()
        while by_key[k].get("is_internal") and by_key[k].get("host") and k not in seen:
            seen.add(k)
            host = next((q for q in procedures
                         if q["name"] == by_key[k]["host"] and q["file"] == by_key[k]["file"]
                         and not q.get("is_internal")), None)
            if host is None:
                break
            k = _key(host)
        return k

    unit_of = {_key(p): owner_of(p) for p in procedures}
    units = sorted({u for u in unit_of.values() if u not in left_out})

    # --- calls between units, resolved by module ---------------------------
    by_name = defaultdict(list)
    for p in procedures:
        by_name[p["name"].lower()].append(p)

    def resolve(caller, name):
        # An internal procedure is visible only inside its host.
        cands = [q for q in by_name.get(name, [])
                 if not q.get("is_internal")
                 or (q.get("host") == caller["name"] and q["file"] == caller["file"])]
        if len(cands) <= 1:
            return cands
        mine = (caller["module"] or "").lower()
        for test in (lambda q: q["file"] == caller["file"],
                     lambda q: mine and (q["module"] or "").lower() == mine,
                     lambda q: (q["module"] or "").lower() in module_uses.get(mine, ())):
            narrowed = [q for q in cands if test(q)]
            if narrowed:
                return narrowed
        return cands

    callers_by_file = defaultdict(list)
    for p in procedures:
        callers_by_file[(p["name"], p["file"])].append(p)
    deps = defaultdict(set)  # unit -> units it calls
    for e in call_edges:
        for caller in callers_by_file.get((e["caller"], e["file"]), ()):
            if caller.get("is_internal"):
                continue  # the host's own edges already include its internal procedures' calls
            cu = unit_of[_key(caller)]
            if cu in left_out:
                continue
            for name in generics.get(e["callee"].lower(), {e["callee"].lower()}):
                for callee in resolve(caller, name):
                    tu = unit_of[_key(callee)]
                    if tu != cu and tu not in left_out:
                        deps[cu].add(tu)

    # --- steps: I/O owners with their users, then cycles -------------------
    union = _Union(units)
    needs_decision = []
    for e in ordering.get("unit_io_not_owned", ()):
        member = unit_of.get((e["name"].lower(), e["file"], e.get("start_line")))
        if member is None or member in left_out:
            continue
        owners = [unit_of[k] for o in e["owners"]
                  for k in [(o["name"].lower(), o["file"], o.get("start_line"))] if k in unit_of]
        owners = [o for o in owners if o not in left_out]
        if not owners:
            needs_decision.append({"name": e["name"], "module": e["module"],
                                   "reason": "; ".join(e["notes"]) or
                                   "its unit's owner is outside the translation scope"})
            continue
        for o in owners:
            union.join(member, o)

    for _ in range(len(units) + 1):
        groups = defaultdict(list)
        for u in units:
            groups[union.find(u)].append(u)
        step_deps = {g: {union.find(t) for m in ms for t in deps[m]} - {g}
                     for g, ms in groups.items()}
        merged = False
        for comp in _sccs(set(groups), step_deps):
            for other in comp[1:]:
                union.join(comp[0], other)
                merged = True
        if not merged:
            break

    groups = defaultdict(list)
    for u in units:
        groups[union.find(u)].append(u)
    step_deps = {g: {union.find(t) for m in ms for t in deps[m]} - {g}
                 for g, ms in groups.items()}

    layer = {}

    def layer_of(g):
        if g not in layer:
            layer[g] = 1 + max((layer_of(t) for t in step_deps[g]), default=-1)
        return layer[g]

    for g in groups:
        layer_of(g)

    # --- the probe: the first non-leaf to translate -------------------------
    def reached(k):
        if not has_coverage:
            return True
        return _verification(by_key[k], index, True, line_basis)["route"] == "kernel"

    probe = None
    candidates = []
    for c in ordering.get("probe_candidates", ()):
        k = (c["name"].lower(), c["file"], c.get("start_line"))
        if k in by_key and unit_of[k] == k and k not in left_out and layer[union.find(k)] == 1:
            g = union.find(k)
            ok = reached(k) and all(reached(m) for t in step_deps[g] for m in groups[t])
            candidates.append((not ok, c["line_count"], c["name"].lower(), g))
    if candidates:
        probe = min(candidates)[3]
    priority = {probe} | set(step_deps.get(probe, ())) if probe else set()

    # --- order ----------------------------------------------------------------
    deferred = {union.find(unit_of[k]) for k, o in overrides.items()
                if o["status"] == "deferred" and k in unit_of and unit_of[k] not in left_out}

    def size(g):
        return sum(by_key[m]["line_count"] for m in groups[g])

    def any_reached(g):
        return any(reached(m) for m in groups[g])

    def key(g):
        first = min(groups[g], key=lambda m: by_key[m]["name"].lower())
        return (g in deferred, g not in priority, layer[g], not any_reached(g), size(g),
                by_key[first]["name"].lower(), first[1])

    waiting = {g: set(step_deps[g]) for g in groups}
    dependents = defaultdict(set)
    for g, ts in step_deps.items():
        for t in ts:
            dependents[t].add(g)
    ready = [(key(g), g) for g, ts in waiting.items() if not ts]
    heapq.heapify(ready)
    order = []
    while ready:
        _, g = heapq.heappop(ready)
        order.append(g)
        for d in sorted(dependents[g]):
            waiting[d].discard(g)
            if not waiting[d]:
                heapq.heappush(ready, (key(d), d))
    assert len(order) == len(groups), "the step graph is acyclic by construction"

    # --- describe ---------------------------------------------------------------
    flagged = {(e["name"].lower(), e["file"], e.get("start_line")): e
               for e in ordering.get("unit_io_not_owned", ())}
    steps = []
    for position, g in enumerate(order, 1):
        members = sorted(groups[g], key=lambda m: (by_key[m]["name"].lower(), m[1]))
        why = []
        if g == probe:
            why.append("first non-leaf probe: exercises calls between translated procedures")
        elif g in priority:
            why.append("callee of the first non-leaf probe")
        if g in deferred:
            why.extend(f"deferred by override: {overrides[k]['reason']}"
                       for k in overrides if unit_of.get(k) in groups[g]
                       and overrides[k]["status"] == "deferred")
        io = [m for m in members if m in flagged]
        undecided = {(d["name"].lower(), (d["module"] or "").lower()) for d in needs_decision}
        if io and len(members) > 1:
            why.append("translated together: " + ("one of them does" if len(io) == 1 else
                                                   f"{len(io)} of them do")
                       + " I/O on a unit another member owns")
        elif len(members) > 1:
            why.append("translated together: they call each other")
        if any((by_key[m]["name"].lower(), (by_key[m]["module"] or "").lower()) in undecided
               for m in members):
            why.append("needs a decision: I/O on a unit no procedure in scope owns")
        why.append("a leaf" if layer[g] == 0 else
                   f"layer {layer[g]}: after the steps it calls")
        def contained(m):
            return sorted(q["name"] for q in procedures
                          if q.get("is_internal") and unit_of[_key(q)] == m and _key(q) != m)

        steps.append({
            "position": position,
            "layer": layer[g],
            "procedures": [{
                "name": by_key[m]["name"], "module": by_key[m]["module"], "file": m[1],
                "lines": by_key[m]["line_count"], "has_save": by_key[m].get("has_save", False),
                "bind_c": by_key[m].get("is_bind_c", False),
                "verification": _verification(by_key[m], index, has_coverage, line_basis),
                "contains": contained(m),
            } for m in members],
            "after": sorted(by_key[min(groups[t])]["name"] for t in step_deps[g]),
            "why": why,
        })

    return {
        "steps": steps,
        "left_out": [{"name": by_key[k]["name"], "module": by_key[k]["module"], "reason": r}
                     for k, r in sorted(left_out.items())
                     if not by_key[k].get("is_internal")],
        "needs_decision": needs_decision,
        "coverage": has_coverage,
    }


def work_order_markdown(order, inputs):
    """The work order as a readable table."""
    steps = order["steps"]
    n_procs = sum(len(s["procedures"]) for s in steps)
    lines = ["# Work Order", "",
             f"{n_procs} procedure{'s' if n_procs != 1 else ''} in {len(steps)} "
             f"step{'s' if len(steps) != 1 else ''}, in the order to translate them. "
             "Callees come before callers; procedures in one step are translated together.", ""]
    lines += ["Inputs: " + "; ".join(f"{k}: {v}" for k, v in inputs.items()), ""]
    if not order["coverage"]:
        lines += ["No coverage was given (`--coverage`), so there is no verification route "
                  "or extraction case, and reached procedures cannot be put first.", ""]
    if order["needs_decision"]:
        lines += [f"## Needs a decision: {len(order['needs_decision'])}", "",
                  "Each does I/O on a unit no procedure in scope owns. The order places it "
                  "by its calls alone.", "",
                  "| Procedure | Module | Why |", "|---|---|---|"]
        lines += [f"| {d['name']} | {d['module'] or '-'} | {d['reason']} |"
                  for d in order["needs_decision"]]
        lines.append("")
    lines += ["## Steps", "", "| # | Procedure | Module | Lines | Verify | Why here |",
              "|---|---|---|---|---|---|"]
    for s in steps:
        for i, p in enumerate(s["procedures"]):
            flags = [f for f, on in (("SAVE", p["has_save"]), ("BIND(C)", p["bind_c"])) if on]
            name = p["name"] + (f" [{', '.join(flags)}]" if flags else "")
            if p["contains"]:
                name += f" (with {', '.join(p['contains'])})"
            lines.append(f"| {s['position'] if i == 0 else ''} | {name} | {p['module'] or '-'} | "
                         f"{p['lines']} | {p['verification']['note']} | "
                         f"{'; '.join(s['why']) if i == 0 else ''} |")
    lines += ["", f"## Left out: {len(order['left_out'])}", ""]
    if order["left_out"]:
        lines += ["| Procedure | Module | Why |", "|---|---|---|"]
        lines += [f"| {o['name']} | {o['module'] or '-'} | {o['reason']} |"
                  for o in order["left_out"]]
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"
