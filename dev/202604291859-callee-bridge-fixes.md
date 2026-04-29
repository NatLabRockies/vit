# Callee Bridge Fixes — Three Root Causes
**Date:** 2026-04-29
**Status:** completed
**Scope:** VIT callee bridge generation, kernel verification

## Problem

CalcContStateDeriv calls ComputeTau2 (already translated to C++). In the kernel, the C++ CalcContStateDeriv needs a Fortran bridge to call the original ComputeTau2. The bridge failed to generate correctly due to three independent bugs, each masking the next.

Initially worked around by inlining ComputeTau2's computation. This dev note documents the proper fix.

## Bug 1: C++ Callee Detection on Integrated Source

**File:** `vit/vit/kernel_verify.py`

`_generate_callee_bridges` detects callees by scanning the FORTRAN source (`detect_callees(target_source, ...)`). On integrated source, CalcContStateDeriv's Fortran body is a wrapper that calls `dbemt_calccontstatederiv_c` — it has no `CALL ComputeTau2`. Callee detection finds nothing, no bridge is generated, and the C++ gets a linker error on `computetau2_c`.

**Fix:** After Fortran callee detection, also scan the C++ source for `_c(` patterns. For each match, look up the base name in `known_funcs` and add it to `callee_names`.

```python
for m in re.finditer(r'\b(\w+)_c\s*\(', raw_cpp):
    base_name = m.group(1)
    for known in known_funcs:
        if known.lower() == base_name:
            callee_names.append(known)
```

This ensures bridges are generated for callees referenced in C++ even when the Fortran source has been integrated.

## Bug 2: CONTAINS Whitespace in _make_callees_public_in_kernel

**File:** `vit/vit/kernel_verify.py`

After generating bridges, VIT makes callees PUBLIC in the kernel's Fortran modules (KGen's reduced modules default to PRIVATE). The code searched for `\nCONTAINS` using `content_upper.find()`, but the kernel file had `   contains` (indented lowercase). The exact string match failed, so ComputeTau2 was never made PUBLIC.

**Fix:** Replace the exact string search with a regex that handles whitespace:
```python
contains_match = re.search(r'^\s*CONTAINS\s*$', content, re.MULTILINE | re.IGNORECASE)
```

## Bug 3: Missing USE for Inherited Types

**File:** `vit/vit/interface_gen.py`

The bridge declared `TYPE(DBEMT_ELEMENTINPUTTYPE), POINTER :: u_f` but never USEd the module that defines it. `_get_use_statements_for_derived_types(sig)` checks `sig.use_statements` — the function's OWN USE statements. Module procedures like ComputeTau2 inherit `USE DBEMT_Types` from the parent DBEMT module and don't have their own USE for the type.

**Fix:** After collecting USE statements from the function, check if any C-compatible struct args have types not covered. For uncovered types, look up `source_module` in the derived_types registry:
```python
if type_info and type_info.source_module:
    use_stmts.append(f"USE {type_info.source_module}, ONLY: {arg.derived_type_name}")
```

## The View Stash Pattern Already Worked

The bridge's handling of view-type parameters (like `DBEMT_ParameterType`) via the kernel stash was already correctly implemented. Lines 1116-1127 in `kernel_verify.py` USE the stash module, and lines 1311-1315 pass `vit_original_dbemt_parametertype` to the Fortran callee. This pattern was proven during AirfoilInfo but hadn't been exercised with DBEMT view types until now.

## Verification

The generated bridge now correctly:
- `USE DBEMT_Types, ONLY: DBEMT_ELEMENTINPUTTYPE` (type for C_F_POINTER)
- `USE vit_dbemt_parametertype_view, ONLY: vit_original_dbemt_parametertype` (stash)
- Passes `vit_original_dbemt_parametertype` to ComputeTau2 (not the view struct)
- Handles OPTIONAL k_tau_out with IF/ELSE branching

CalcContStateDeriv: 20/20 KGen IDENTICAL, 17/17 baselines PASS — with proper ComputeTau2 callee bridge, no inlining.

## Impact

These three fixes unblock Phase B time integrators. DBEMT_RK4, DBEMT_AB4, and DBEMT_ABM4 all call CalcContStateDeriv (which takes view-type parameters). Without these fixes, every such callee bridge would fail. The fixes also benefit any future module where C++ functions call translated callees that take view-type or inherited-type arguments.
