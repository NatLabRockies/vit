# Shared NWTC Utility Header + ComputeUA360_AttachedFlow Integration
**Date:** 2026-04-25
**Status:** completed
**Scope:** AeroDyn Phase 1, NWTC utility infrastructure, VIT kernel verification

## Summary

Created a shared C++ header (`vit_nwtc.h`) with translations of three NWTC_Library utility functions and five constants, refactored 6 existing translations to use it, enhanced VIT to copy shared headers to kernel directories, and integrated the 10th AirfoilInfo function (ComputeUA360_AttachedFlow) — the first function to use NWTC utility callees.

## Why Shared NWTC Utilities

ComputeUA360_AttachedFlow calls three NWTC_Library functions: `EqualRealNos` (epsilon comparison), `fZeros` (zero-crossing finder), and `InterpExtrapStp` (linear interpolation with stepping search). The initial plan was to inline these in the translation, but the user correctly identified that:

1. **Forward-looking:** When AeroDyn is fully C++, all NWTC functions need C++ implementations. Inlining creates duplicates that can drift.
2. **Existing debt:** EqualRealNos was already inlined in 4 translations. Constants like TwoPi and D2R were duplicated in 6 files.
3. **Single source of truth:** NWTC_Library functions are shared infrastructure across OpenFAST. They should be translated once and shared.

The decision was to create proper C++ translations in a shared header, not Fortran bridges. This keeps everything in C++ and provides transitive verification — every function that uses the shared implementations gets them verified against the Fortran originals via KGen kernel comparison.

## vit_nwtc.h

**File:** `modules/aerodyn/src/vit_nwtc.h`

Header-only, `static inline` implementations:

### Constants
- `Pi`, `TwoPi`, `PiBy2`, `D2R`, `R2D` — all as `static constexpr double`
- Source: NWTC_Library precision/system modules

### EqualRealNos (NWTC_Num.f90:1647, EqualRealNos8)
- Epsilon-based relative tolerance comparison
- Already verified in 4 translations — same code, just centralized
- ~8 lines

### fZeros (NWTC_Num.f90:7205, fZero_R8)
- **New translation.** Linear-scan zero-crossing detector with optional periodic wraparound
- Takes 0-based C arrays, `has_period` bool + `period` double for the OPTIONAL parameter
- Algorithm: scan for sign changes, linear interpolation to estimate root location
- ~20 lines of C++
- Called in AttachedFlow as: `fZeros(p->Alpha, cn_cl, N, roots.data(), N, nZeros, true, TwoPi)`

### InterpExtrapStp (NWTC_Num.f90:3268)
- **New translation.** Boundary-aware linear interpolation with stepping index search
- `Ind` parameter is 1-based (matches Fortran convention) — important indexing detail
- Array parameters are 0-based C arrays; internal access converts via `XAry[Ind-1]` pattern
- Contains internal lambda for `GetLinearVal()`
- ~25 lines of C++
- Called in AttachedFlow on 3-element arrays (x_, f_) — stepping search is trivial at this size

### Index Convention Decision

InterpExtrapStp's `Ind` parameter is kept 1-based to match Fortran semantics. The caller initializes `Indx = 1` and the function updates it for efficient sequential lookups. Internal array access adjusts: Fortran `XAry(Ind)` becomes C `XAry[Ind-1]`. This was chosen over converting to 0-based because:
1. The caller code mirrors the Fortran exactly (`Indx = 1`)
2. The function is self-contained — the 1-based convention doesn't leak
3. Converting would require careful audit of every comparison and boundary check

## Refactoring Existing Translations

6 files modified to replace inlined code with `#include "vit_nwtc.h"`:

| File | Removed |
|------|---------|
| `calculate_c_alpha.cpp` | Inline EqualRealNos + `<cmath>`, `<algorithm>`, `<limits>` |
| `computeua360_updatecnseparated.cpp` | Inline EqualRealNos + `<limits>` |
| `computeua360_updateseparationf.cpp` | Inline EqualRealNos + `const TwoPi` + `<limits>`, `<algorithm>` |
| `computeuaseparationfunction_oncl.cpp` | Inline EqualRealNos + `<limits>`, `<algorithm>` |
| `computeua360_cnoffset.cpp` | `const R2D`, `const PiBy2`, `<cmath>` |
| `computeuaseparationfunction_zero.cpp` | `const TwoPi`, `<cmath>`, `<limits>` |

All 9 existing functions re-verified against golden kernels after refactoring: **9/9 IDENTICAL.** The shared header is proven bit-identical to the previously inlined code.

## VIT Enhancement: Shared Header Copy

**File:** `vit/vit/kernel_verify.py`, Step 1a

The existing `rosco_constants.h` copy mechanism (search near impl file) was generalized to a loop over shared header names: `["rosco_constants.h", "vit_nwtc.h"]`. For each:
1. Search parent directories of the impl file (ROSCO-style: headers near translations)
2. If not found, search `source_dir` recursively (AeroDyn-style: headers in src/)

This ensures the kernel has all shared headers available for `#include` during C++ compilation. ~10 lines of Python replacing the original 7.

## ComputeUA360_AttachedFlow

**10th AirfoilInfo function.** Computes fully-attached aerodynamic coefficients for 360-degree UA models.

### What It Does
1. Calls `Compute_iLoweriUpper` to find the linear region bounds
2. Sets alphaLower/alphaUpper and c_alphaLower/c_alphaUpper in UA_BL
3. Computes Cn slopes at upper and lower bounds, extrapolates to find alpha0
4. Calls `fZeros` to find zero-crossings in cn_cl with periodic wraparound
5. Selects the reverse-flow zero (|alpha| >= 45 deg, maximum absolute value)
6. Computes break points where separated and attached flow regimes intersect
7. Fills `Coefs(:,col_fa)` via piecewise `InterpExtrapStp` interpolation in 3 regions

### Signature
```fortran
SUBROUTINE ComputeUA360_AttachedFlow(p, ColUAf, cn_cl, iLower, iUpper)
   TYPE (AFI_Table_Type),    intent(inout) :: p
   integer(IntKi),           intent(in   ) :: ColUAf
   REAL(ReKi),               intent(in   ) :: cn_cl(:)
   INTEGER(IntKi),           intent(  out) :: iLower
   INTEGER(IntKi),           intent(  out) :: iUpper
```

### Callees
| Callee | Source | How Used |
|--------|--------|----------|
| `Compute_iLoweriUpper` | Already integrated | Callee bridge (`compute_iloweriupper_c()`) |
| `EqualRealNos` | NWTC_Num.f90 | From `vit_nwtc.h` |
| `fZeros` | NWTC_Num.f90 | From `vit_nwtc.h` |
| `InterpExtrapStp` | NWTC_Num.f90 | From `vit_nwtc.h` |

### Fortran Constructs Translated
- `MAXLOC(abs(...), DIM=1, MASK=...)` → manual loop with max-tracking, returns 0 if no match
- Array constructors `(/ a, b, c /)` → `double x_[3] = {a, b, c};`
- `COEFS(Row, col)` → standard column-major macro
- `size(x_)` → literal `3`

### Verification
- **Extraction:** `ad_BAR_CombinedCases` test case, line 1087 (87 invocations), 20 cases captured
- **Kernel verification:** 20/20 IDENTICAL
- **Integration:** `--reverse-copy` (modifies 9 scalar fields in p%UA_BL)
- **Regression:** 16/16 baselines pass
- **integrate_all.sh:** 10/10 clean→integrated cycle

### Scalar Fields Modified (requiring --reverse-copy)
- alphaLower, alphaUpper, c_alphaLower, c_alphaUpper
- alpha0ReverseFlow
- alphaBreakUpper, CnBreakUpper, alphaBreakLower, CnBreakLower

## COEFS Macro Decision

The `COEFS(row1, col1)` macro is still duplicated in 5 files. We explicitly decided NOT to centralize it because:
- It references the local variable name `p` (`p->Coefs`, `p->n_Coefs_rows`)
- A parameterized macro would be ugly
- Each file defines it locally and `#undef`s it
- This is acceptable duplication (pattern, not logic)

## Commits

| Repo | What |
|------|------|
| VIT | kernel_verify.py shared header copy enhancement |
| OpenFAST | vit_nwtc.h, 6 refactored translations, ComputeUA360_AttachedFlow translation + integration, integrate_all.sh updated |

## State

10 AirfoilInfo functions integrated. Two remaining:
- **CalculateUACoeffs** — the orchestrator, calls most of the above + fZeros again (lines 1222, 1237 with array slicing)
- **AFI_ValidateInitInput** — simulation-level verification (no KGen)

Then DBEMT module.
