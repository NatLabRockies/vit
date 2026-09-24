# Fix Duplicate Nested Type Definitions in vit_types.h
**Date:** 2026-04-26
**Status:** completed
**Scope:** VIT type header generation — deduplicate inline nested type blocks

## Problem

`vit_types.h` contained `afi_ua_bl_type_t` defined twice — 45 lines of dead code. The C preprocessor include guards prevented a compile error, but the duplication was generated every time the header was regenerated.

**How it happened:** Two VIT code generators independently emitted the same type:

1. `generate_c_struct_header()` for `AFI_UA_BL_Type` — emitted a standalone block with guard `AFI_UA_BL_TYPE_T_H` (lines 9-62)
2. `generate_view_struct_header()` for `AFI_Table_Type` — emitted an inline copy of `AFI_UA_BL_Type` inside the view struct block (lines 75-123), because `AFI_Table_Type` has a `UA_BL` field of type `AFI_UA_BL_Type`

`generate_consolidated_types_header()` assembled both blocks into one file. It checked top-level guards to avoid duplicating whole types (`AFI_UA_BL_TYPE_T_H` vs `AFI_TABLE_TYPE_VIEW_T_H` — different guards, both passed), but didn't detect the inline nested copy inside the view struct block.

## Why Regex Dedup Failed Initially

The first attempt used `re.sub` with a backreference pattern:
```python
re.sub(r'#ifndef (\w+_T_H)\n#define \1\n.*?#endif // \1', ...)
```

This failed because the nested `AFI_UA_BL_TYPE_T_H` block was inside the `AFI_TABLE_TYPE_VIEW_T_H` block. The regex's non-greedy `.*?` matched the outer block from `#ifndef B_T_H` to `#endif // B_T_H`, consuming the nested block as part of the match. The inner block was never seen as a separate match.

## Solution

Line-by-line parsing instead of regex. New `_dedup_guarded_blocks()` function:

```python
def _dedup_guarded_blocks(content: str) -> str:
    seen_guards = set()
    skip_until_endif = None

    for line in lines:
        if skip_until_endif:
            if line.strip() == f"#endif // {skip_until_endif}":
                skip_until_endif = None
            continue

        m = re.match(r'^#ifndef (\w+_T_H)\s*$', line)
        if m:
            guard = m.group(1)
            if guard in seen_guards:
                skip_until_endif = guard
                continue
            seen_guards.add(guard)

        result.append(line)
```

When a `#ifndef GUARD` is seen for a guard already encountered, all lines through `#endif // GUARD` are skipped. This correctly handles nesting because the inner block's guard is different from the outer block's guard — the skip only targets the specific duplicate guard.

Applied to all return paths in `generate_consolidated_types_header()`:
- "Create from scratch" path (new files)
- "Append to existing" path (incremental integration)

## Result

Before: `afi_ua_bl_type_t` appeared 3 times in `vit_types.h` (standalone typedef + inline typedef + member usage)
After: appears 2 times (standalone typedef + member usage)

The inline copy within the `AFI_TABLE_TYPE_VIEW_T_H` block is removed. The view struct references the standalone definition by name.

## Verification

- Unit tests: 590 passed, 8 pre-existing failures
- FindBoundingTables kernel: 21/21 IDENTICAL
- `grep -c "afi_ua_bl_type_t" vit_types.h` = 2 (typedef + member)

## Also Fixed

Updated 3 unit tests in `test_view_types.py` that expected `C_LOC(src%field(1))` — changed to `C_LOC(src%field(LBOUND(src%field,1)))` to match the earlier LBOUND fix.

## Commit

VIT `1d11c7f`
