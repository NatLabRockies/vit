# ReadAFfile Translation — In Progress
**Date:** 2026-04-27
**Status:** in-progress
**Scope:** C++ translation of ReadAFfile (16th AirfoilInfo function)

## What's Done

### C++ Translation Written and Compiles
`openfast/translations/AirfoilInfo/readaffile.cpp` — two-pass architecture:

**Pass 1 (`ReadAFfile_pass1`):** Parses the entire airfoil file, caches all data in static storage, returns per-table sizes (NumAlf, NumCoefsTab) via output arrays. Sets scalar fields on the view struct (NumTabs, NumCoords, InterpOrd, etc.).

**Pass 2 (`ReadAFfile_fill`):** Copies cached data into Fortran-allocated view struct arrays (Alpha, Coefs, UA_BL). Calls `calculateuacoeffs_c` (already in C++) for non-constant tables. Validates periodic continuity. Clears cache.

### Key Design Decisions
1. **Pure C++ file parsing** — reimplements ProcessComFile + ParseVar in C++ using `std::ifstream` + `std::getline` + `std::istringstream`. No Fortran NWTC utilities called.
2. **Comment stripping** — handles `!` comments and `@"filename"` include syntax.
3. **ParseVar replacement** — `parseDouble()`, `parseInt()`, `parseBool()`, `parseString()` extract first whitespace-delimited token from each line. Handle "Default" keyword.
4. **CalcDefaults tracking** — per-UA-parameter flags stored in `afi_ua_bl_default_type_t` struct (same as Fortran). Passed to `calculateuacoeffs_c`.
5. **Column remapping** — input column indices (InCol_Alfa, InCol_Cl, etc.) → output columns (ColCl=1, ColCd=2, etc.) with column-major Coefs storage.
6. **Static cache** — `ParsedFile g_cache` persists between Pass 1 and Pass 2 (same-thread init code, safe).

### Compilation Issue Found and Fixed
`vit test-validate` for CheckValuesAreUniqueMonotonicIncreasing left a minimal `vit_types.h` in `translations/AirfoilInfo/` that defined `VIT_TYPES_H` guard and only contained `AFI_InitInputType`. This shadowed the real `vit_types.h` from `modules/aerodyn/src/`. Removing the local file fixed compilation.

**Lesson:** `vit test-validate` generates a local `vit_types.h` with only the types needed for that test. It shadows the production header if any later compilation uses the same `-I` search path. Need to either (a) generate with a different name, (b) delete after test, or (c) have the test Makefile use explicit include paths.

## What Remains

1. **Fortran wrapper** — manual two-pass wrapper in AirfoilInfo.f90:
   - Call Pass 1 → get NumTabs, per-table NumAlf/NumCoefsTab
   - Copy scalars back from view to Fortran type
   - Allocate: p%Table(NumTabs), per-table Alpha and Coefs arrays, optionally X_Coord/Y_Coord
   - Re-populate view struct
   - Call Pass 2 → fill arrays

2. **Integration** — add to CMakeLists.txt, update vit_translated.h with extern "C" declarations for both passes

3. **Verification** — simulation-level (17/17 baselines, generated from clean Fortran)

4. **Edge case testing** — the @"include" syntax for coordinate files, "Default" keyword for UA parameters, backward-compatible "Ctrl" vs "UserProp" field names

## Parsing Format Notes

The Fortran source uses NWTC's `ParseVar` which reads `"value  VarName  ! comment"` — value first, then name. The C++ parser mirrors this: extract first whitespace-delimited token as the value, ignore everything after. This works for all test case airfoil files.

Special cases:
- `@"NACA6_0240_coords.txt"` on the NumCoords line → indicates coordinate data is in a separate included file (handled by the comment-stripping include mechanism)
- `"default"` or `"DEFAULT"` as value → use the provided default (for InterpOrd, UA parameters)
- `False`/`True` for InclUAdata → boolean parsing
- Re values in millions → multiply by 1e6
- Angle parameters in degrees → multiply by D2R
- Backward-compatible `Ctrl` field → try `UserProp` first, fall back to `Ctrl`

## Files

| File | Status |
|------|--------|
| `openfast/translations/AirfoilInfo/readaffile.cpp` | Written, compiles |
| Fortran wrapper | NOT YET WRITTEN |
| CMakeLists.txt | NOT YET UPDATED |
| vit_translated.h | NOT YET UPDATED |
| vit.yaml | NOT YET UPDATED |
