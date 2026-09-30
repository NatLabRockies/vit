# Translate unwrap: Array-Returning FUNCTION, Parser Fix, Inline Revert

**Date:** 2026-04-02
**Status:** completed
**Scope:** Translate the `unwrap` function, fix VIT's Fortran parser for nested DIMENSION expressions, revert inline unwrap implementations in two callers

## Background

During Stage C readiness analysis, a complete ROSCO function inventory revealed that `unwrap` (Functions.f90:518) had been deferred during Phase 10A with the rationale: "Requires array-valued return where result size depends on input (DIMENSION(SIZE(x))), which VIT doesn't support yet." Two C++ translations had inline reimplementations of unwrap instead of calling a shared `unwrap_c()`:

- `variablespeedcontrol.cpp:150-154` — inline 2-element unwrap in OL_Mode=2 azimuth tracking
- `readcontrolparameterfilesub.cpp:64-75` — static `unwrap()` function, called at line 989 for OL_Azimuth

This duplicated logic, broke the pattern where every Fortran function gets a proper standalone translation, and meant there was no single verified implementation of unwrap.

## The Deferral Was Wrong

Investigation revealed that **VIT already handles array-returning FUNCTIONs**. The `identity` function (`FUNCTION identity(n) RESULT(A)` with `DIMENSION(n, n)`) has been translated, KGen-verified, and integrated since Phase 2. VIT's existing code correctly:

1. **Parser** (`fortran_parser.py`): Sets `result_is_array = True`, extracts `result_dims`
2. **Interface generator** (`interface_gen.py`): Converts array-returning FUNCTION to `void` C function with output pointer parameter, generates `SUBROUTINE` BIND(C) interface, generates `FUNCTION` wrapper that `CALL`s the C subroutine
3. **C++ scaffold** (`translate.py`): Generates `void` function with `double* identity_result` output parameter

For `unwrap` with `DIMENSION(SIZE(x))`, the result size is `n_x` — already provided as the assumed-shape size parameter for input `x`. No additional size parameter needed.

The PROJECT_PLAN claim was a justification for deferral, not a technical limitation.

## Parser Bug: Nested Parentheses in DIMENSION

While VIT's pipeline could handle array-returning FUNCTIONs in principle, the parser had a bug that prevented `DIMENSION(SIZE(x))` from being extracted correctly.

### The Bug

`fortran_parser.py:355` used a simple regex to extract DIMENSION content:

```python
dim_match = re.search(r'DIMENSION\s*\(([^)]*)\)', attrs)
```

The character class `[^)]*` matches "any character that's not `)`". For `DIMENSION(SIZE(x))`, this captures `SIZE(x` — stopping at the first `)` (which closes `SIZE(`) instead of the second `)` (which closes `DIMENSION(`).

**Result:** `result_dims = "SIZE(x"` instead of `"SIZE(x)"`. The generated Fortran wrapper declared `DIMENSION(SIZE(X)` (missing closing paren), causing a compile error.

### The Fix

Replaced the simple regex with balanced-parenthesis counting:

```python
dim_start = re.search(r'DIMENSION\s*\(', attrs)
if dim_start:
    has_dimension = True
    open_pos = dim_start.end() - 1
    depth = 0
    for k in range(open_pos, len(attrs)):
        if attrs[k] == '(':
            depth += 1
        elif attrs[k] == ')':
            depth -= 1
            if depth == 0:
                dim_spec = attr_part_original[open_pos + 1:k]
                break
```

### Case Preservation Fix

A second issue: the parser extracted `rest` from `line_upper` (line 327), making all downstream attribute values uppercase. `DIMENSION(SIZE(x))` became `DIMENSION(SIZE(X))`. While Fortran is case-insensitive (so `SIZE(X)` compiles fine), we fixed it properly by maintaining `rest_original` from the original-case `line` alongside the uppercase `rest` used for matching:

```python
rest = line_upper[m.end():].strip()
rest_original = line.strip()[m.end():].strip()
```

And extracting dimension content from `attr_part_original` instead of the uppercased `attr_part`.

**After fix:** `result_dims = "SIZE(x)"` — correct case, correct parentheses.

### Impact Assessment

The bug only affects DIMENSION attributes with nested parentheses. Existing translations all use simple dimensions: `:` (assumed-shape), `3` or `3,3` (fixed-size), `n, n` (variable but no nesting). The only affected function in ROSCO is `unwrap`. No existing tests broke.

All 594 unit tests pass after the fix.

## The Translation

### unwrap.cpp (translations/Functions/unwrap.cpp)

The Fortran `unwrap` function:
1. Copies input array `x` to output array `y`
2. For each element i (2 to SIZE(x)), adjusts all elements from i onward by ±2*PI until the consecutive difference `y(i) - y(i-1)` is within [-PI, PI]
3. If `ErrVar%aviFAIL < 0`, prepends `"unwrap:"` to the error message

C++ signature (from VIT's `build_c_params`):
```cpp
void unwrap(double* x, int n_x, errorvariables_t* ErrVar, double* unwrap_result)
```

The `n_x` parameter (from assumed-shape handling of input `x`) provides the output size — no separate result size parameter needed.

### KGen Verification

- **Call site:** Controllers.f90:339 — `LocalVar%AzBuffer = UNWRAP(LocalVar%AzBuffer, ErrVar)` inside VariableSpeedControl's OL_Mode=2 branch
- **Scenario:** 10 (OL_Mode=2, azimuth tracking)
- **Result:** 25/25 passed, 5,875 field entries verified

### Integration

Standard `vit integrate unwrap --apply` generated:
- Fortran wrapper FUNCTION (replaces original in Functions.f90)
- BIND(C) SUBROUTINE interface (array-returning FUNCTION uses SUBROUTINE interface)
- C++ extern "C" wrapper (`unwrap_c`)
- CMakeLists.txt update
- vit_translated.h declaration

## Reverting Inline Implementations

### variablespeedcontrol.cpp

**Before (inline 2-element unwrap):**
```cpp
while (LocalVar->AzBuffer[1] - LocalVar->AzBuffer[0] <= -PI)
    LocalVar->AzBuffer[1] += 2.0 * PI;
while (LocalVar->AzBuffer[1] - LocalVar->AzBuffer[0] >= PI)
    LocalVar->AzBuffer[1] -= 2.0 * PI;
```

**After (shared call):**
```cpp
double az_unwrapped[2];
unwrap_c(LocalVar->AzBuffer, 2, ErrVar, az_unwrapped);
LocalVar->AzBuffer[0] = az_unwrapped[0];
LocalVar->AzBuffer[1] = az_unwrapped[1];
```

A temporary `az_unwrapped[2]` buffer is needed because the Fortran semantics are `LocalVar%AzBuffer = UNWRAP(LocalVar%AzBuffer, ErrVar)` — the result is assigned back to the same array via an implicit temporary. In C++, passing the same pointer as both input and output would work (since the algorithm copies x→result first), but using a temporary is clearer and avoids aliasing concerns.

Added `unwrap_c` declaration to the existing `extern "C"` block.

### readcontrolparameterfilesub.cpp

**Before:** Static `unwrap()` function (lines 64-75) + call `unwrap(CntrPar->OL_Azimuth, nRows)` at line 989.

**After:** Removed static function entirely. Call `unwrap_c(CntrPar->OL_Azimuth, nRows, ErrVar, CntrPar->OL_Azimuth)` at line 977. Here, aliased input/output is safe because:
1. The algorithm copies x→result first (self-copy when aliased = no-op)
2. Then only modifies result elements from index i onward
3. No element is read-after-write in a way that would produce different results

Added `unwrap_c` forward declaration inside the `extern "C"` block.

## Bug Fix: Missing END SUBROUTINE in Wrapper Template

During integration testing, a build error revealed that `readcontrolparameterfilesub_wrapper.f90` was missing its `END SUBROUTINE ReadControlParameterFileSub` statement. The `integrate_all.sh` regex replacement captures the original `END SUBROUTINE` line in its pattern, but the wrapper template didn't include a replacement. This caused the ReadCpFile subroutine to be parsed as part of ReadControlParameterFileSub, producing a Fortran compile error.

**Fix:** Added `END SUBROUTINE ReadControlParameterFileSub` to the end of the wrapper template. This was a pre-existing bug that would have affected any clean integration run — it wasn't introduced by the unwrap changes.

## File Restoration Issue

During testing, `git checkout HEAD -- translations/ReadSetParameters/readcpfile.cpp` was needed because the file had been overwritten by a VIT scaffold (from `vit translate` run earlier in the session). This highlighted that `vit translate` overwrites existing translation files — running it for one function can accidentally clobber a nearby committed translation if the output path overlaps. In this case, `vit translate unwrap` created `translations/Functions/unwrap.cpp` (correct), but a prior `vit translate ReadCpFile` run had overwritten the committed readcpfile.cpp with a scaffold.

**Lesson:** Be cautious with `vit translate` — it creates/overwrites the output file unconditionally. If a translation already exists, the scaffold will destroy it. Always check `git diff` before committing.

## Verification

1. **Direct:** `vit verify unwrap` — 25/25 passed against KGen kernel
2. **Caller:** VariableSpeedControl verified via KGen (indirectly, through integration)
3. **End-to-end:** `vit sim-compare` — 10 scenarios, 692,000 values IDENTICAL
4. **Unit tests:** 594/594 passed
5. **prepare_commit.sh:** No changes needed — `unwrap.cpp` is now tracked, `git ls-files` finds it

## Updated Counts

| Metric | Before | After |
|--------|--------|-------|
| Total translated | 44 | 45 |
| KGen-verified | 41 | 42 |
| Simulation-verified | 3 | 3 |
| integrate_all.sh total | 44 | 45 |
| verify_all.sh total | 41 | 42 |
| Unit tests | 594 | 594 |

## Commits

**ROSCO** `1e0be84`: Translate unwrap, revert inline implementations in callers
**VIT** `53ee402`: Fix DIMENSION parser for nested parens, add pre-Stage C dev notes

## Files Changed

### ROSCO repo
| File | Change |
|------|--------|
| `translations/Functions/unwrap.cpp` | New — C++ translation |
| `translations/Controllers/variablespeedcontrol.cpp` | Reverted inline → unwrap_c() |
| `translations/ReadSetParameters/readcontrolparameterfilesub.cpp` | Removed static unwrap, uses unwrap_c() |
| `translations/ReadSetParameters/readcontrolparameterfilesub_wrapper.f90` | Fixed missing END SUBROUTINE |
| `rosco/controller/src/unwrap.cpp` | New — VIT-generated extern "C" wrapper |
| `rosco/controller/CMakeLists.txt` | Added unwrap.cpp to SOURCES |
| `kernel/unwrap/` | New — golden KGen kernel (25 cases, Scenario 10) |
| `scripts/integrate_all.sh` | Added unwrap, updated count to 45 |
| `scripts/verify_all.sh` | Added unwrap, updated count to 42 |
| `vit.yaml` | Added unwrap translation entry |

### VIT repo
| File | Change |
|------|--------|
| `vit/fortran_parser.py` | Balanced-paren DIMENSION extraction + case preservation |
| `dev/202604021308-stage-c-readiness.md` | Pre-Stage C readiness assessment |
| `dev/202604021327-complete-function-inventory.md` | Complete ROSCO function inventory |
| `dev/202604021820-unwrap-translation.md` | This dev note |
