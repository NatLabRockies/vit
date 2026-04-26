# Eliminate Dual `vit_types.h` — Single Canonical Location
**Date:** 2026-04-26
**Status:** completed
**Scope:** vit_types.h architecture consolidation (Issue 5 from test-validate audit)

## Background

VIT maintained two copies of `vit_types.h` with the same filename but different content, different locations, and different update paths:

1. **`translations/vit_types.h`** — written by `vit translate` (cli.py `cmd_translate()`) and `vit test-validate` (cli.py `cmd_test_validate()`). Accumulated ALL types including not-yet-integrated ones. Updated via `generate_consolidated_types_header()` with `existing_content` merge, preserving types across translate calls.

2. **`modules/aerodyn/src/vit_types.h`** — written by `vit integrate` (integrate.py `generate_integration_plan()`). Only contained already-integrated types. Also used `existing_content` merge, growing monotonically as functions are integrated.

These two files had drifted apart in three ways:
- **Types present in only one file:** translations/ had `TFinInputFileType` (from a scaffold that was never integrated); production didn't.
- **Same type, different representation:** `AFI_UA_BL_Default_Type` used `int8_t` in translations/ but `int32_t` in production — the LOGICAL fields were mapped differently depending on when the file was generated. This is a **struct layout mismatch** that would cause silent memory corruption if code compiled against one version linked with code compiled against the other.
- **Syntax bug:** translations/ had `TFinInputFileType` with broken C array syntax (`double TFinRefP_n[1:3]` — Fortran range notation, not valid C). Production didn't have this type so wasn't affected.

## Investigation

A thorough investigation of every code path that references `vit_types.h` revealed that the translations/ copy was **not consumed by any VIT tool at runtime**:

| Tool | How it handles vit_types.h | Needs translations/ copy? |
|------|---------------------------|---------------------------|
| `vit verify` | Generates fresh in kernel directory from type registry (kernel_verify.py:199) | No |
| `vit integrate` | Generates into production source directory from type registry (integrate.py:130) | No |
| `vit test-validate` | Had include-path hack to prefer translations/ copy (test_validate.py:234-240) | **Only because of the hack** |
| `vit translate` | Wrote translations/ copy, but only as a side effect — scaffold embeds `#include "vit_types.h"` which is resolved at compile time | No |

The test-validate include-path hack existed specifically to work around the dual-file problem: the comment said "translations/vit_types.h has ALL types (including newly translated ones), while modules/aerodyn/src/vit_types.h only has already-integrated types. Put translations/ FIRST so the complete vit_types.h is found." This was the only consumer, and it could generate the header locally instead.

## Decision

During discussion, we identified that the translations/ copy served no purpose:
- `vit verify` generates its own fresh copy in each kernel directory
- `vit integrate` generates its own in the production directory
- `vit test-validate` can generate its own in the test directory (same pattern as kernel_verify)
- `vit translate` just needs the scaffold to say `#include "vit_types.h"` — it doesn't need the file to exist at scaffold time

The solution: **delete `translations/vit_types.h` entirely**, generate types on-demand where needed (kernel dir, test dir), and keep the production copy at `modules/aerodyn/src/vit_types.h` as the only persistent file (written only by `vit integrate`).

We considered an intermediate approach (rename the translations/ copy to `vit_types_draft.h` or `vit_types_staging.h` to make it obviously temporary, then have integrate clean it up). But once we traced the actual consumers, we realized the file wasn't needed at all — no staging step necessary.

## Changes

### 1. `vit/vit/cli.py` — `cmd_translate()` (was lines 491-502)

Removed the 12-line block that generated `translations/vit_types.h`. Replaced with:
```python
struct_header_names = ["vit_types.h"] if derived_types else []
```
This preserves the `#include "vit_types.h"` directive in scaffolds (resolved at compile time) without writing any file to disk.

### 2. `vit/vit/cli.py` — `cmd_test_validate()` (was lines 1200-1211)

Changed to generate `vit_types.h` into the test output directory (`out_dir`, e.g., `translations/AirfoilInfo/`) instead of `translations/`. Fresh from registry with no `existing_content` merge — same pattern as kernel_verify.py:199:
```python
consolidated = generate_consolidated_types_header(derived_types, config.kind_aliases)
types_path = out_dir / "vit_types.h"
types_path.write_text(consolidated)
```

### 3. `vit/vit/test_validate.py` — `generate_test_makefile()` (was lines 233-240)

Removed the 8-line include-path ordering hack that added `translations/` to CXX_FLAGS. Replaced with:
```python
include_dirs.add(".")
```
The `-I.` flag finds the locally generated `vit_types.h` in the test directory. The generated Makefile's CXX_FLAGS no longer includes `-I/workspace/openfast/translations`.

### 4. Deleted `openfast/translations/vit_types.h`

The 278-line file removed from disk. It had accumulated 12 type definitions (including the broken `TFinInputFileType`) across multiple `vit translate` calls.

## What Did NOT Change

- **`integrate.py`** (lines 124-141) — continues writing to `modules/aerodyn/src/vit_types.h` with `existing_content` merge. This is correct: the production file grows monotonically as functions are integrated.
- **`kernel_verify.py`** (lines 198-202) — already generated fresh per-kernel. No change needed.
- **`derived_type_parser.py`** — the `[1:3]` array syntax bug is a separate issue in the DIMENSION spec parsing. Only affected the now-deleted translations/ copy (which included `TFinInputFileType`). Filed as a follow-up in the audit dev note (Issue 5 sub-item).
- **All `.cpp` translation files** — still `#include "vit_types.h"`, resolved at compile time by whichever directory the compiler runs in.

## Verification

- **Unit tests:** 590 passed, 8 failed (all pre-existing, unrelated to vit_types.h)
- **`vit test-validate AFI_ValidateInitInput ...`:** generates `translations/AirfoilInfo/vit_types.h` (correct local location), `translations/vit_types.h` does not exist
- **Test build + run:** 7/7 pass (happy_path + 6 error branches, C++ == Fortran)
- **Generated Makefile CXX_FLAGS:** `-I. -I/workspace/openfast/modules/aerodyn/src` (no translations/ path)
- **Kernel verification and baseline regression:** unaffected (these tools never used the translations/ copy)

## Architecture After Fix

```
vit_types.h lifecycle:
  vit translate   → emits #include "vit_types.h" in scaffold (no file written)
  vit verify      → generates vit_types.h fresh in kernel/<FuncName>/
  vit test-validate → generates vit_types.h fresh in translations/<Module>/
  vit integrate   → generates/updates vit_types.h in modules/aerodyn/src/ (production)
```

One canonical persistent file: `modules/aerodyn/src/vit_types.h`. All other instances are ephemeral, generated on-demand from the type registry into the working directory. No name collision, no include-path ordering tricks, no drift between copies.

## Related

- **Audit dev note:** `202604261142-test-validate-audit-findings.md` (Issue 5)
- **End-to-end dev note:** `202604261131-test-validate-end-to-end.md` (documents the dual vit_types.h workaround during implementation)
- **Remaining audit issues:** 7 of 8 still open (Issues 1-4, 6-8). Issue 1 (hardcoded library paths) is the next priority.
