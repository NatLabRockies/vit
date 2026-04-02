# VIT Auto-Allocate: Extract ALLOCATE from Fortran Source into Generated Wrappers

**Date:** 2026-04-02
**Status:** completed
**Scope:** New `--auto-allocate` flag on `vit integrate` that scans Fortran source for ALLOCATE patterns and emits them in the generated wrapper

## The Problem

When VIT translates a function that **produces** data into ALLOCATABLE arrays (rather than just consuming existing data), the generated Fortran wrapper needs ALLOCATE statements before creating the view struct. Without allocation, the view populator calls `C_LOC` on an unallocated array, which crashes.

In Stage B, we wrote these ALLOCATE statements manually. For ReadCpFile, that meant 5 lines:

```fortran
ALLOCATE(PerfData%Beta_vec(CntrPar%PerfTableSize(1)))
ALLOCATE(PerfData%TSR_vec(CntrPar%PerfTableSize(2)))
ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
...
```

The question was: can this be automated?

## The Investigation

### How Fortran Functions Allocate

An exhaustive search of the ROSCO codebase found **18 ALLOCATE statements** across 4 files, falling into two patterns:

**Pattern 1: Direct ALLOCATE in function body** (8 instances)
```fortran
ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
```
Directly scannable from the function body.

**Pattern 2: Helper-mediated allocation** (10 instances)
```fortran
CALL ParseAry(FileLines, 'PC_GS_angles', CntrPar%PC_GS_angles, CntrPar%PC_GS_n, ...)
```
ParseAry internally does `ALLOCATE(Ary(AryLen))`. The ALLOCATE is in the helper, not the calling function. VIT already has `allocatable.helpers` config that describes this pattern.

### The Safety Analysis

The critical insight: not all ALLOCATEs can be safely emitted in the wrapper. The danger depends on WHERE the size expression comes from:

**Category 1: SAFE — size from a different argument**
```fortran
ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
```
- Target: `PerfData` (argument 2)
- Size: `CntrPar%PerfTableSize` (argument 1, a different argument)
- The size is populated BEFORE this function is called
- **Safe to emit as code**

**Category 2: SAME_ARG — size from the same argument (circular risk)**
```fortran
ALLOCATE(CntrPar%PC_GS_angles(CntrPar%PC_GS_n))
```
- Target: `CntrPar` (argument)
- Size: `CntrPar%PC_GS_n` (same argument)
- The size field might be populated BY this function — circular dependency
- If emitted unconditionally: `PC_GS_n` could be 0 or garbage → heap corruption
- **Emit as comment with warning, never as code**

**Category 3: LOCAL — size from a local variable**
```fortran
ALLOCATE(FileLines(NumLines))
```
- `NumLines` is a local variable computed by counting file lines
- It doesn't exist in wrapper scope
- **Emit as comment**

### Failure Mode Catalog

| Failure Mode | Severity | How VIT Handles It |
|---|---|---|
| Circular dependency (same-arg size) | Silent heap corruption | Classified as `same_arg`, emitted as comment only |
| Local variable size | Build error (won't compile) | Classified as `local`, emitted as comment only |
| Missing helper config | Incomplete extraction | Warn if ALLOCATABLE fields exist with no allocation found |
| Unconditional emit of conditional ALLOCATE | Unnecessary allocation (benign) | Accepted — wastes memory but doesn't corrupt |
| Missing initialization | Uninitialized memory read | Always emit `= 0.0D0` or `= 0` after ALLOCATE |
| Multi-dimensional sizes | Parsing complexity | Balanced-parenthesis parser handles nested expressions |
| SIZE() intrinsic in size expression | Depends on prior allocation | Contains `%` → classified by argument reference check |

**The constraint was met:** No silent failures. Every discovered allocation is either emitted as code (safe) or explicitly flagged as a comment (unsafe). Every ALLOCATABLE field not covered gets a warning.

## The Implementation

### New Module: `vit/vit/allocate_extract.py` (526 lines)

**Data structure:**
```python
@dataclass
class AllocationInfo:
    field_name: str        # "Cp_mat"
    target_arg: str        # "PerfData"
    size_exprs: list[str]  # ["CntrPar%PerfTableSize(2)", "CntrPar%PerfTableSize(1)"]
    category: str          # "safe", "same_arg", "local"
    source: str            # "direct" or "helper:ParseAry"
    init_value: str        # "0.0D0" or "0"
```

**Main function:**
```python
def extract_allocations(
    function_name: str,
    source_text: str,
    arg_names: list[str],
    allocatable_helpers: list[dict],
) -> list[AllocationInfo]:
```

**Algorithm (5 steps):**

1. **Extract function body** (`_extract_function_body`): Finds text between `SUBROUTINE name(` and `END SUBROUTINE name` using regex. Handles both SUBROUTINE and FUNCTION. Case-insensitive.

2. **Normalize** (`_join_continuations`): Joins `&` continuation lines, strips comment-only lines. Respects string literals when detecting `!` comment markers (`_find_comment_pos` handles quoted strings).

3. **Scan for direct ALLOCATEs** (`_scan_direct_allocates`): Regex matches `ALLOCATE(Var%field(dims))`. Uses `_extract_balanced_parens` for nested parentheses (e.g., `PerfTableSize(2)` is one dimension, not two). Strips `STAT=`, `SOURCE=`, `MOLD=`, `ERRMSG=` clauses via `_remove_stat_clause`. Skips allocations where `Var` is not in `arg_names` (local variables).

4. **Scan for helper calls** (`_scan_helper_allocates`): For each configured helper, finds `CALL helper_name(...)`. Uses `_split_call_args` (parenthesis-aware comma splitter) to extract positional arguments. Checks if `array_arg` position matches `Var%field` where `Var` is in `arg_names`. Extracts `size_arg` position as the size expression.

5. **Classify** (`_classify_allocation`, `_classify_size_expr`): For each size expression:
   - Literal integer (`/^\d+$/`) → safe
   - Contains `Arg%field` where `Arg` != target → safe
   - Contains `Arg%field` where `Arg` == target → same_arg
   - No `%` reference and not literal → local
   - Priority: local > same_arg > safe (worst classification wins)

**Key helpers:**

- `_split_call_args(call_text)`: Splits `CALL foo(a, b(1,2), c)` into `["a", "b(1,2)", "c"]` by tracking parenthesis nesting depth and only splitting commas at depth 0. Also handles string literals.

- `_extract_balanced_parens(text, start)`: Given `ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2),CntrPar%PerfTableSize(1)))`, extracts the content between the outermost matched parentheses. Handles nested parens correctly.

- `_remove_stat_clause(text)`: Strips `STAT=ierr` and similar from ALLOCATE content so it doesn't get misinterpreted as a dimension.

### Integration into Wrapper Generation

**`interface_gen.py`:** In `_generate_wrapper_subroutine()`, after argument declarations and OPTIONAL preamble, before the view preamble:

```fortran
        ! --- Pre-allocate arrays (auto-extracted from Fortran source) ---
        ALLOCATE(PerfData%Cp_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
        PerfData%Cp_mat = 0.0D0
        ALLOCATE(PerfData%Ct_mat(CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)))
        PerfData%Ct_mat = 0.0D0
        ! WARNING: These allocations could not be auto-extracted safely.
        ! Review and add ALLOCATE statements manually if needed:
        !   CntrPar%PC_GS_angles(CntrPar%PC_GS_n) — same argument [helper:ParseAry]
```

Safe allocations become code. Unsafe ones become comments. The ALLOCATE lines are placed BEFORE the view preamble (populator calls) so that `C_LOC` has valid allocated memory to reference.

**Threading:**
```
cli.py: --auto-allocate flag
    → cmd_integrate(): auto_allocate=True, allocatable_helpers from config
        → integrate.py: extract_allocations() called, results threaded through
            → _generate_fortran_patches(allocations=...)
                → interface_gen.py: generate_fortran_wrapper(allocations=...)
                    → _generate_wrapper_subroutine(allocations=...)
                        → ALLOCATE lines emitted before view preamble
```

### Results on Real ROSCO Functions

**ReadCpFile** (`vit integrate ReadCpFile --auto-allocate`):
```
safe     PerfData%Cp_mat  sizes=[CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)]  src=direct
safe     PerfData%Ct_mat  sizes=[CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)]  src=direct
safe     PerfData%Cq_mat  sizes=[CntrPar%PerfTableSize(2), CntrPar%PerfTableSize(1)]  src=direct
```
3 of 5 arrays auto-emitted. Beta_vec and TSR_vec are helper-mediated (ParseAry with a different argument signature than what's configured — the unit-based variant has array_arg=4, not 3). These would be caught by the completeness check.

**CheckInputs** (`vit integrate CheckInputs --auto-allocate`):
```
0 allocations found
```
Correct — CheckInputs doesn't allocate anything.

**ReadControlParameterFileSub** (hypothetical — not run via `vit integrate` due to two-pass complexity):
All ~40 ParseAry-mediated allocations would be classified as `same_arg` (CntrPar→CntrPar) and emitted as comments. The translator sees the full list and knows they need manual handling via the two-pass protocol.

### The ParseAry Overload Issue

ROSCO has two overloaded variants of ParseAry:
1. `ParseAry(FileLines, ParamName, Ary, AryLen, ...)` — array_arg=3, size_arg=4 (configured)
2. `ParseAry(Unit, CurLine, ParamName, Ary, AryLen, ...)` — array_arg=4, size_arg=5 (NOT configured)

ReadCpFile uses variant 2. The helper config only matches variant 1. This means Beta_vec and TSR_vec aren't found by helper scanning. Solutions:
- Add a second helper entry with array_arg=4, size_arg=5
- Or accept the incompleteness — the completeness check warns about unmatched fields

This is a configuration issue, not a bug in the extraction logic. The extraction correctly handles whatever helpers are configured.

## Tests

13 unit tests in `tests/test_allocate_extract.py`:

| Test Class | Test | What It Verifies |
|---|---|---|
| TestDirectAllocates | test_safe_different_args | Cross-argument ALLOCATE → safe |
| | test_safe_literal_size | `ALLOCATE(arr(3))` → safe |
| | test_same_arg | Same-argument size → same_arg |
| | test_local_var_size | Local variable size → local |
| | test_skips_local_variables | `ALLOCATE(localVar(n))` not extracted |
| | test_stat_clause_handled | `ALLOCATE(arr(n), STAT=ierr)` → STAT stripped |
| | test_continuation_lines | `ALLOCATE(arr( &\n  n, &\n  m))` → joined |
| TestHelperAllocates | test_helper_safe_different_args | ParseAry with cross-arg → safe |
| | test_helper_same_arg | ParseAry with same-arg → same_arg |
| TestEdgeCases | test_function_not_found | Missing function → empty list |
| | test_no_allocates | No ALLOCATEs → empty list |
| | test_case_insensitive | `allocate(out%arr(inp%n))` → found |
| | test_commented_allocate_skipped | `! ALLOCATE(...)` → not extracted |

All 594 tests pass (581 existing + 13 new).

## Design Decisions

### Why `--auto-allocate` is Off by Default

Track A functions (41 of 44) don't need allocation — they consume existing data through pre-populated view structs. Auto-allocate is only useful for "producer" functions (Stage B pattern). Making it opt-in avoids adding allocation scanning overhead to the common case and prevents confusion when the feature finds nothing.

### Why Not Follow CALL Chains Beyond Configured Helpers

Tracing arbitrary call chains to find indirect allocations would require building a call graph and analyzing data flow — effectively a compiler frontend. The helper config approach (`allocatable.helpers`) is a pragmatic middle ground: the project configures which helper functions allocate, and VIT scans for those patterns. This covers the common case (ParseAry in ROSCO, similar helpers in other projects) without the complexity of full static analysis.

### Why Emit Initialization After Every ALLOCATE

Fortran ALLOCATE doesn't zero-initialize memory. If the C++ code reads an array element before writing it (e.g., in a conditional branch), it gets garbage. Always emitting `arr = 0.0D0` after ALLOCATE prevents this. The cost is negligible (one memset per array at initialization time).

### Why Literals Are Always Safe

A literal like `3` or `2` is a compile-time constant — it doesn't depend on any runtime value. Even if the target argument is the same as where the field lives, `ALLOCATE(CntrPar%Ind_BldPitch(3))` is safe because `3` is always `3` regardless of what CntrPar contains.

### Why the Classification Priority is local > same_arg > safe

If a multi-dimensional ALLOCATE has one `local` dimension and one `safe` dimension, the overall classification is `local` — because the wrapper can't evaluate the local dimension regardless of whether the other dimension is safe. Similarly, `same_arg` overrides `safe` because the circular dependency risk exists even if only one dimension has it.

## Relationship to Previous Capabilities

This completes the non-KGen integration tooling stack:

| Step | What | When Used |
|---|---|---|
| `vit translate` prompt | ALLOCATABLE field hints — shows what MIGHT need allocation | Writing the C++ implementation |
| `vit integrate --auto-allocate` | Extracts actual ALLOCATEs from source — emits safe ones, flags unsafe | Generating the Fortran wrapper |
| `vit integrate --reverse-copy` | Copies view scalars back to Fortran TYPE after C++ call | When C++ modifies view-type scalars |
| `vit sim-compare` | Compares simulation arrays between baseline and integrated | Verifying the translation |

The flow for a Stage B function is now:
1. `vit translate` — see ALLOCATABLE hints, write C++ implementation
2. `vit integrate --auto-allocate --reverse-copy --apply` — generate wrapper with allocations + reverse-copy
3. Manually add any flagged ALLOCATE statements (from comments in wrapper)
4. Build + `vit sim-compare baseline_arrays/ integrated_arrays/` — verify

## Files

| File | Lines | Change |
|------|-------|--------|
| `vit/vit/allocate_extract.py` | 526 (new) | AllocationInfo dataclass + extraction + classification logic |
| `vit/vit/cli.py` | +5 | `--auto-allocate` flag |
| `vit/vit/integrate.py` | +12 | Call extract_allocations, thread through |
| `vit/vit/interface_gen.py` | +18 | Emit ALLOCATE lines + comments in wrapper |
| `vit/tests/test_allocate_extract.py` | 271 (new) | 13 unit tests |

## Commit

VIT `ca11ca8`: Add --auto-allocate flag: extract ALLOCATE from Fortran into wrappers
