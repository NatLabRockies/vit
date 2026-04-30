# Fortran Intrinsics Shared Header
**Date:** 2026-04-29
**Status:** completed
**Scope:** OpenFAST translations, VIT infrastructure

## Problem

Fortran array intrinsics (MINLOC, MAXLOC, CSHIFT) were copy-pasted as inline loops in ~14 locations across 5 AirfoilInfo translation files. Each instance was 6-12 lines of boilerplate loop code. Future translations would re-invent the same patterns, and any fix to the pattern would need to be applied in every copy.

## Solution

Created `vit_fortran_intrinsics.h` — a header-only file with template implementations:

```cpp
template<typename ValueFn, typename MaskFn>
inline int fortran_minloc(int n, ValueFn value, MaskFn mask);

template<typename ValueFn, typename MaskFn>
inline int fortran_maxloc(int n, ValueFn value, MaskFn mask);

inline void fortran_cshift(const double* src, double* dst, int n, int shift);
```

Templates with callable parameters handle the varying array expressions and mask conditions at each call site. Lambdas inline at -O2 with no runtime cost.

### Example replacement

Before (10 lines):
```cpp
double min_val = 1.0e30;
int min_idx = 1;
for (int i = 0; i < p->n_Alpha; i++) {
    if (p->Alpha[i] >= p->UA_BL.alphaLower) {
        if (p->Alpha[i] < min_val) {
            min_val = p->Alpha[i];
            min_idx = i + 1;
        }
    }
}
*iLower = min_idx;
```

After (3 lines):
```cpp
*iLower = fortran_minloc(p->n_Alpha,
    [&](int i) { return p->Alpha[i]; },
    [&](int i) { return p->Alpha[i] >= p->UA_BL.alphaLower; });
```

## Files Modified

### New
- `openfast/modules/aerodyn/src/vit_fortran_intrinsics.h`

### Updated (both translations/ and modules/aerodyn/src/ copies)
- `compute_iloweriupper.cpp` — 1 MINLOC + 1 MAXLOC
- `computeuaseparationfunction_zero.cpp` — 1 MINLOC + 1 MAXLOC (compound: MINLOC then MAXLOC with dependent mask)
- `computeua360_updateseparationf.cpp` — 1 MINLOC + 2 MAXLOC + removed static cshift + 3 fortran_cshift calls
- `computeua360_attachedflow.cpp` — 1 MAXLOC (on abs(roots) with abs threshold mask)
- `calculateuacoeffs.cpp` — 2 MINLOC + 2 MAXLOC

### Infrastructure
- `openfast/vit.yaml` — added to shared_files
- `openfast/TRANSLATION_TIPS.md` — references shared header instead of inline examples

## Also in this session

`SetErrStat` was extracted from inline lambdas to a shared function in `vit_nwtc.h`, matching Fortran's `NWTC_Base::SetErrStat`. Both ComputeTau1 and DBEMT_RK4 updated to use it. `std::string` overload provided for direct error reporting with constructed messages.

## Verification

17/17 baselines PASS. Pure refactor — no behavioral change.
