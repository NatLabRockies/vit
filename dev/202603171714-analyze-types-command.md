# Decision: Build `vit analyze-types` as Part of Phase 4.5

**Date:** 2026-03-17
**Status:** completed
**Scope:** Phase 4.5 planning, VIT tool design

## Background

After completing the deep type system investigation (see [202603171647-type-system-deep-investigation.md](202603171647-type-system-deep-investigation.md)), we had comprehensive findings for all 12 ROSCO types — field-by-field compatibility analysis, fix prescriptions, usage patterns, and C struct strategies. But this was all one-off manual analysis performed by the agentic tool (Claude), which requires evaluation to ensure confidence in results.

The question was raised: can this analysis be made deterministic and placed into the VIT codebase as a tool?

## Analysis: What's Mechanizable

Breaking down what the manual investigation did:

1. **Parse every field of every derived type** — types, attributes, dimensions. *Already partially implemented* in `derived_type_parser.py`. Gap: only gives binary BIND(C) yes/no, not field-by-field blockers.

2. **Classify each field by C compatibility** — ALLOCATABLE? CHARACTER? COMPLEX? LOGICAL? Nested type? *Fully deterministic.* Known patterns with known fixes.

3. **Determine usage patterns** — read-only? write? passthrough? which functions access which fields? *Mechanizable via source scanning* — regex for `TypeName%FieldName`, classify by position relative to assignment operators.

4. **Prescribe fixes for incompatible fields** — *Deterministic for known patterns:*
   - `ALLOCATABLE → fixed-size with max bound`
   - `CHARACTER(:), ALLOCATABLE → CHARACTER(N)`
   - `COMPLEX → split real/imag REAL pair`
   - `LOGICAL → INTEGER(C_INT)`

5. **Generate C struct equivalent** — *Fully deterministic* given parsed type and fix decisions.

## Decision

Build a `vit analyze-types` command as part of Phase 4.5. The manual ROSCO analysis becomes the spec and golden test cases — the tool's output for ROSCO must match our findings.

The command takes a source directory and produces:
- Per-type BIND(C) compatibility report with specific blockers
- Fix prescriptions for each incompatible field
- Usage analysis (which functions access which fields, read vs. write)
- C++ struct header (`.h` file)
- Fortran BIND(C) type modifications

## Why Build During Phase 4.5, Not Before

- Phase 4.5 is when we actually need the artifacts — building earlier would be in a vacuum
- ROSCO serves as the concrete test case to drive the implementation
- The manual analysis provides exact expected outputs to test against
- Avoids building speculative features that may not match real-world needs

## Why This Matters Beyond ROSCO

This is a key step toward Phase 11 (generalization). The manual analysis we did required deep understanding of ROSCO specifically. A deterministic tool would apply the same analysis to any Fortran codebase — different type names, different field patterns, but the same classification logic. The tool makes VIT's type system support scale without requiring per-codebase manual investigation.

## Relationship to Existing VIT Code

- `derived_type_parser.py` — extend from binary BIND(C) check to field-by-field report
- `type_map.py` — extend to handle fix prescriptions (COMPLEX → split, LOGICAL → C_INT, etc.)
- `fortran_parser.py` — reuse for scanning source files for field access patterns
- New module likely needed for C struct generation and Fortran type patching
