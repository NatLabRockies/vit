# ComputeTau1 Translation — Three VIT Bugs Found and Fixed
**Date:** 2026-04-28
**Status:** completed
**Scope:** DBEMT Phase A, VIT infrastructure bugs, code audit

## Summary

ComputeTau1 is the second DBEMT function integrated (after ComputeTau2). 66 lines, computes the primary DBEMT time constant. First DBEMT function with error handling (ErrStat/ErrMsg) and mutable misc state (FirstWarn_tau1). 20/20 KGen IDENTICAL, 17/17 baselines PASS.

The translation itself was straightforward. The session's real value was discovering and fixing three VIT infrastructure bugs that the new type patterns exposed, plus a fourth latent bug found during a follow-up code audit.

## Prerequisites Added

- **ErrID_Severe (value 3)** added to `vit_nwtc.h` — was missing between ErrID_Warn(2) and ErrID_Fatal(4)
- **`num2lstr` inline function** added to `vit_nwtc.h` — converts double to left-adjusted string for error messages, matches Fortran `Num2LStr` behavior
- **DBEMT_InputType view struct** — new view with 3 scalars + ALLOCATABLE `element(:,:)` pointer. View populator at `vit_dbemt_inputtype_view.f90`
- **DBEMT_MiscVarType C struct** — single `int32_t FirstWarn_tau1` field, BIND(C)-compatible. VIT auto-generated the struct and wrapper signature

## VIT Bug 1: Catastrophic Regex Backtracking in `scan_allocatable_sizes`

**Symptom:** `vit translate ComputeTau1` hung at 99% CPU for 7+ minutes. Four Python processes accumulated before the user noticed.

**Root cause:** The ALLOCATE pattern regex in `analyze_types.py` used a nested quantifier `((?:[^()]*|\([^()]*\))*)` to match one level of nested parentheses. When applied to Fortran lines containing `%element` (an extremely common field name) followed by complex expressions that partially match but ultimately fail, the regex engine enters catastrophic backtracking — exponential time on certain inputs.

The specific trigger: `ALLOCATE(u%element(p%numNodes, p%numBlades), stat=ErrStat2)`. The `, stat=ErrStat2` after the dimension parens causes the outer group to try all possible splitting points between the dimension and stat clauses.

**Fix:** Replaced the regex with a two-step approach:
1. Quick string pre-filter: skip lines without both `ALLOCATE` and `%fieldname`
2. Paren-counting extraction: walk the string character by character, counting `(` and `)` to extract the dimension text. No regex, no backtracking.

**File:** `vit/vit/analyze_types.py` — new `_extract_allocate_dims()` function.

## VIT Bug 2: TYPE() ALLOCATABLE Rank Mismatch in View Populator

**Symptom:** Kernel build failed with `'n_element' is not a member of the 'dbemt_inputtype_view_t' structure`.

**Root cause:** Two files had the same bug independently:
- `derived_type_parser.py` (C header generation): TYPE() ALLOCATABLE fields always got a single `n_{name}` size field regardless of rank. Primitive ALLOCATABLE fields correctly got `n_{name}_rows` + `n_{name}_cols` for rank 2.
- `view_populator.py` (Fortran populator generation): same issue — the `is_type_alloc` branch used `n_{name}` for all ranks, while the primitive branch was rank-aware.

`DBEMT_InputType.element(:,:)` is a rank-2 TYPE() ALLOCATABLE — the first such field encountered. All previous ALLOCATABLE TYPE() fields were rank-1 (e.g., `AFI_ParameterType.Table(:)`).

**Fix:** Made the TYPE() ALLOCATABLE path rank-aware in both files, matching the primitive ALLOCATABLE handling: `n_{name}` for rank 1, `n_{name}_rows`/`n_{name}_cols` for rank 2, `n_{name}_dim1`/`dim2`/`dim3` for rank >= 3.

**Files:** `vit/vit/derived_type_parser.py`, `vit/vit/view_populator.py`

## VIT Bug 3: KGen CHARACTER(*) Code Generation

**Symptom:** Kernel build failed with two errors:
1. `Syntax error in CALL statement` — subroutine name `kv_dbemt_updatestates_character_*_` contains `*`, which is illegal in Fortran identifiers
2. `Entity with assumed character length must be a dummy argument` — `CHARACTER(LEN=*) :: kgenref_errmsg` is a local variable, not a dummy arg

**Root cause:** KGen generates verify subroutines named after the argument types. For `CHARACTER(*)` arguments, it embeds the literal `*` in the identifier. It also generates local variables with assumed-length `CHARACTER(LEN=*)`, which is only legal for dummy arguments and PARAMETERs. A second local, `CHARACTER(LEN=*) :: diff`, had the same issue.

ComputeTau1 is the first extracted function where `errMsg` (CHARACTER(*)) is a direct output argument of the instrumented function. Previous functions either didn't have CHARACTER outputs or had them handled internally.

**Fix:** Added two kernel repair rules to `_patch_kernel_fortran()` in `kernel_verify.py`:
1. Replace `character_*_` in identifiers with `character_chr_`
2. Replace `CHARACTER(LEN=*)` and `CHARACTER(*)` in non-INTENT local declarations with `CHARACTER(LEN=4096)` (a safe buffer size for KGen's string comparisons)

**File:** `vit/vit/kernel_verify.py`

### Related fix: C++ callee auto-detection expansion

During the CHARACTER(*) fix, VIT was also generating unnecessary callee bridges for `SetErrStat` and `num2lstr`. These are inlined in the C++ translation (SetErrStat as a lambda, num2lstr from vit_nwtc.h), but VIT's C++ function scanner didn't recognize them because:
- `auto SetErrStat = [&]` — `auto` wasn't in the return-type list
- `inline std::string num2lstr(double)` — `std::string` wasn't in the return-type list

**Fix:** Expanded the C++ function scanner to cover `std::` types and `const` qualifiers for function definitions, plus a separate pattern for lambda assignments (`auto name = [`).

## Code Audit Findings (4 additional fixes)

After ComputeTau1 was integrated and passing, a full audit of all modified VIT code was conducted. Four additional issues were found and fixed:

### Audit Fix 1: rank >= 3 ELSE branch missing

`view_populator.py`: when a TYPE() ALLOCATABLE field with rank >= 3 is not allocated, the ELSE branch didn't zero out `dim1/dim2/dim3` size fields. The IF (allocated) branch was correct. Only the ELSE was incomplete.

### Audit Fix 2: CHARACTER(*) without LEN= keyword

The kernel repair only matched `CHARACTER(LEN=*)`. Fortran also allows `CHARACTER(*)` without `LEN=`. Broadened the regex to match both forms.

### Audit Fix 3: C++ scan false positives on variable assignments

The expanded regex `(\w+)\s*(?:\(|=)` matched variable assignments like `int x = 5` as function definitions. Split into two separate patterns: function definitions (`type name(`) and lambda assignments (`auto name = [`). Variables with `=` no longer false-match.

### Audit Fix 4: Fortran keyword/intrinsic callee detection

`detect_callees` was picking up Fortran keywords (`CHARACTER`, `INTEGER`, `REAL`, `TYPE`) and intrinsics (`LEN`, `TRIM`) as callees because they exist as user-defined function names in other modules within the source tree. Added a comprehensive skip list of ~50 Fortran keywords and intrinsics to filter these out.

## Remaining Known Issues (low priority)

- **Continuation-line handling:** `_extract_allocate_dims` and `_scan_field_size` process one line at a time. Multi-line ALLOCATE statements are not handled. Rare in practice — most ALLOCATEs are single-line.
- **Rank >= 4 arrays:** Silently map to the rank-3 handling pattern (dim1/dim2/dim3 only). No known rank-4 ALLOCATABLE TYPE() fields exist in AeroDyn.
- **Kernel re-verification fragility:** Running `vit verify` multiple times on the same kernel directory can corrupt state file reading (segfault during array sum check). The first verification pass works correctly. Pre-existing issue, not introduced by these changes.

## Files Changed

| File | Changes |
|------|---------|
| `vit/vit/analyze_types.py` | New `_extract_allocate_dims()`, replaced regex with paren counting |
| `vit/vit/derived_type_parser.py` | TYPE() ALLOCATABLE rank-aware size fields |
| `vit/vit/view_populator.py` | TYPE() ALLOCATABLE rank-aware populator + ELSE branch fix |
| `vit/vit/kernel_verify.py` | CHARACTER(*) repair, C++ scan expansion, Fortran intrinsic skip list |
| `openfast/modules/aerodyn/src/vit_nwtc.h` | ErrID_Severe, num2lstr |
| `openfast/modules/aerodyn/src/vit_types.h` | dbemt_inputtype_view_t, dbemt_miscvartype_t |
| `openfast/modules/aerodyn/src/vit_dbemt_inputtype_view.f90` | New view populator |
| `openfast/translations/DBEMT/computetau1.cpp` | New translation |

## Process Note

All three VIT bugs were discovered organically during the ComputeTau1 workflow — not by running VIT's test suite, but by actually using VIT on a new function with new type patterns. Each bug was a latent assumption that happened to hold for all previous AirfoilInfo functions:

1. **Regex backtracking:** AirfoilInfo fields (Coefs, Table, etc.) are uncommon enough that the regex didn't backtrack. `element` is ubiquitous in OpenFAST.
2. **TYPE() rank mismatch:** All previous ALLOCATABLE TYPE() fields were rank-1 (`Table(:)`). `element(:,:)` is the first rank-2.
3. **CHARACTER(*) in kernel:** Previous functions had CHARACTER outputs handled internally. ComputeTau1 exposes `errMsg` as a direct output argument.

The code audit then found that the initial fixes, while they resolved the immediate failures, had their own gaps (missing ELSE branch, narrow regex, false-positive scan). This is a pattern: the first fix addresses the symptom, the audit finds the edge cases.
