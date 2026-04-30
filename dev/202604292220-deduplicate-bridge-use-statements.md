# Deduplicate USE Statements in Kernel Callee Bridges
**Date:** 2026-04-29
**Status:** completed
**Scope:** VIT interface_gen.py

## Problem

Auto-generated kernel callee bridges emitted duplicate USE statements when multiple arguments shared the same derived type. For example, `DBEMT_ElementInputType_ExtrapInterp` takes both `u(:)` and `u_out` as `TYPE(DBEMT_ElementInputType)`, producing:

```fortran
USE DBEMT_Types, ONLY: DBEMT_ElementInputType
USE DBEMT_Types, ONLY: DBEMT_ElementInputType
```

The `use_stmts` list was built by appending a USE for each struct arg independently. The fallback path (registry `source_module` lookup for inherited types) also appended per-arg without checking for existing entries.

## Fix

**File:** `vit/vit/interface_gen.py`, `generate_kernel_callee_bridge()` (~line 1199)

Deduplicate `use_stmts` at the point of emission using `dict.fromkeys()`, which preserves insertion order:

```python
for use_stmt in dict.fromkeys(use_stmts):
```

One-line fix. Harmless in Fortran (redundant USE is legal), but cleaner generated code.
