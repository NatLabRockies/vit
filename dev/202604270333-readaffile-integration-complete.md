# ReadAFfile Integration Complete — Three Root Causes Identified and Fixed
**Date:** 2026-04-27
**Status:** completed
**Scope:** C++ translation of ReadAFfile (16th AirfoilInfo function)

## Summary

ReadAFfile is the core airfoil file parser in OpenFAST's AeroDyn module — 459 lines of Fortran that reads airfoil data files (header fields, coordinate data, multi-table Reynolds-number-dependent coefficient data, and 36 Unsteady Aerodynamics parameters per table) and populates `AFI_ParameterType`. It is PRIVATE to the AirfoilInfo module, called by `AFI_Init` once per airfoil file during initialization.

The C++ translation uses an idiomatic keyword-based `AirfoilFileParser` class with a two-pass architecture (Pass 1: parse file + return sizes; Pass 2: fill Fortran-allocated arrays from cache). The Fortran wrapper handles allocation between passes.

Three bugs were discovered during integration, each requiring investigation of NWTC Library internals. All three were fixed, and the translation passes **17/17 baseline regression tests** (data-identical).

## Architecture

### C++ Side (`translations/AirfoilInfo/readaffile.cpp`)

**Two entry points:**

- `readaffile_pass1_c(InitInp, NumCoefsIn, p_view, numalf_out, ncoefstab_out, errStat, errMsg)` — Opens and parses the airfoil file using `std::ifstream`. Strips `!` comments, resolves `@"filename"` includes recursively. Parses header (InterpOrd, RelThickness, NonDimArea, NumCoords, coordinates, BL_file, NumTabs) and per-table data (Re, UserProp, InclUAdata, 36 UA parameters, NumAlf, coefficient rows). Caches all parsed data in a static `ParsedFile g_cache`. Returns per-table sizes via output arrays.

- `readaffile_fill_c(p_view, InitInp, NumCoefsIn, errStat, errMsg)` — Copies cached Alpha, Coefs, UA_BL data into the Fortran-allocated view struct arrays. Calls `calculateuacoeffs_c` (already translated) for non-constant tables with UA data. Validates periodic continuity (-Pi to Pi). Clears cache.

### Fortran Wrapper (in `AirfoilInfo.f90`)

Replaces the entire body of the original `SUBROUTINE ReadAFfile`:

1. Populate a local `BIND(C)` mirror of `AFI_InitInputType` (character-by-character copy of FileName, integer field copies)
2. Call `readaffile_pass1_c` → get NumTabs, per-table NumAlf/NumCoefsTab
3. Copy scalars from view to Fortran `p` (InterpOrd, RelThickness, NonDimArea, NumCoords, NumTabs, ColUAf, BL_file)
4. `ALLOCATE(p%Table(p%NumTabs))`, `ALLOCATE(p%X_Coord)`, `ALLOCATE(p%Y_Coord)` if needed
5. Per-table: `ALLOCATE(p%Table(i)%Alpha(numalf_out(i)))`, `ALLOCATE(p%Table(i)%Coefs(numalf_out(i), ncoefstab_out(i)))`
6. `CALL vit_populate_afi_parametertype(p, p_view)` — re-populate view with pointers to allocated arrays
7. Call `readaffile_fill_c` → fill arrays from cache
8. Copy scalars back: `p%ColUAf` from view, per-table scalars via `C_F_POINTER` + `vit_copy_scalars_to_afi_table_type`

### Key Design Decisions

- **No ProcessComFile, no FileInfo, no NWTC ParseVar.** The C++ reads the file directly with `std::ifstream` + `std::getline`. Comment stripping and `@include` resolution happen during file loading (`readAndDecomment`).
- **Static cache** between passes: `ParsedFile g_cache` persists parsed data between Pass 1 and Pass 2. Safe because init is single-threaded.
- **BIND(C) mirror of AFI_InitInputType**: Created locally in the wrapper as a nested `TYPE, BIND(C)` because the original Fortran type is not `BIND(C)`. Character array populated character-by-character from `CHARACTER(1024)`.
- **C_F_POINTER for table view access**: After Pass 2, the wrapper uses `C_F_POINTER(p_view%Table, table_views, [p%NumTabs])` to access the module-level table view array and copy per-table scalars back.

## Bug 1: Heap Corruption from Uninitialized FileInfo Cleanup

### Symptom

Previous integration attempt crashed with `free(): invalid pointer` during NWTC cleanup in `ad_init`. The crash occurred deep in glibc's `cfree`, not in our code.

### Root Cause

The previous wrapper preserved the original `CONTAINS Cleanup()` subroutine from ReadAFfile. This subroutine calls `NWTC_Library_DestroyFileInfoType(FileInfo, ErrStat2, ErrMsg2)`. Since the C++ parser skips `ProcessComFile` entirely, the local `FileInfo` variable (of type `FileInfoType`) was never allocated — it was uninitialized stack memory. Calling the destroy routine on uninitialized memory corrupted the heap, which manifested later during AeroDyn's cleanup.

### Fix

Replaced the **entire** subroutine body of ReadAFfile — no `Cleanup()` subroutine, no `FileInfo` variable, no `ProcessComFile` call. The wrapper only declares what it needs: the view struct, the BIND(C) mirror, output arrays, and interface blocks.

### Lesson

When replacing a Fortran function body with a C++ wrapper, remove ALL local variables and contained subroutines from the original. Even if they look harmless, contained subroutines may reference local variables that are no longer initialized.

## Bug 2: ParseVar Keyword-Checking and Non-Consuming Failure

### Symptom

After fixing the heap corruption, 10/17 tests crashed with `"ReadAFfile C++: failed to parse NumAlf for table 1"`. The parser consumed too many lines during UA parameter parsing and couldn't find NumAlf at the expected position.

### Root Cause

**The Fortran `ParseVar` does keyword checking and does NOT consume the line when the keyword doesn't match.**

This was the critical discovery. NWTC Library's `ParseR8Var` (the real-valued `ParseVar` implementation) works as follows:

1. Read two words from the current line: `CALL GetWords(FileInfo%Lines(LineNum), Words, 2)`
2. Call `ChkParseData(Words, ExpVarName, ...)` — this checks whether EITHER word matches the expected variable name (case-insensitive). If neither matches, it returns `ErrID_Fatal`.
3. **If ChkParseData fails: RETURN immediately. `LineNum` is NOT incremented** (line 3206 in NWTC_IO.f90).
4. Only on success: parse the value, increment `LineNum` (line 3225).

This means when an expected UA parameter is MISSING from the file (e.g., `alphaUpper` — not present in BAR airfoil files), the Fortran:
- Reads the current line (which has a DIFFERENT parameter, e.g., `eta_e`)
- Sees that "ETA_E" does not match "ALPHAUPPER"
- Sets `CalcDefaults%alphaUpper = .TRUE.` (the caller captures the error status)
- Does NOT advance the line counter
- The next `ParseVar('alphaLower')` reads the SAME line, also fails, also doesn't advance
- Then `ParseVar('eta_e')` reads the line, finds a match, parses the value, advances

The original C++ `parseDouble()` ALWAYS called `nextLine()` which ALWAYS advanced the position. With 36 expected UA parameters and only 10 present in some files, the C++ consumed 26 extra lines (the NumAlf line + 25 coefficient data rows), then couldn't find NumAlf.

### Fix

Added `parseVarDouble(keyword, val)` and `parseVarDoubleWDefault(keyword, val, defVal)` methods to `AirfoilFileParser` that match Fortran's `ParseVar`/`ParseVarWDefault` behavior:

```cpp
bool parseVarDouble(const std::string& expectedKeyword, double& val) {
    // Extract two words from current line
    // Check if either word matches expectedKeyword (case-insensitive)
    // If neither matches: return false, DON'T advance position
    // If match found: parse the other word as the value, advance, return true
}
```

All 36 UA parameter reads now use these keyword-checked methods. When a parameter is missing, the line is not consumed and CalcDefaults is set to 1.

### Lesson

NWTC Library's `ParseVar` is NOT a simple positional line reader — it's a keyword-checking parser that fails gracefully without consuming. Any C++ replacement of Fortran file parsing that uses `ParseVar` must replicate this keyword-check-before-consume behavior. This is not documented in OpenFAST but is critical for backward compatibility with older airfoil files that have fewer UA parameters.

## Bug 3: Cpmin Output Column Mapping When Cm Is Absent

### Symptom

After fixing the keyword issue, 14/17 tests passed but the 3 MHK tests (ad_MHK_RM1_Fixed, ad_MHK_RM1_Fixed_IfW, ad_MHK_RM1_Floating) showed large differences (max_abs=5.27) in `SgCav` (sigma cavitation) channels.

### Root Cause

The C++ hardcoded output column indices:
```cpp
tab.Coefs[0 * NumAlf + row] = Cl;    // column 1
tab.Coefs[1 * NumAlf + row] = Cd;    // column 2
tab.Coefs[2 * NumAlf + row] = Cm;    // column 3
tab.Coefs[3 * NumAlf + row] = Cpmin; // column 4
```

But `AFI_Init` computes output column indices conditionally:
```fortran
p%ColCl = 1
p%ColCd = 2
IF (InCol_Cm > 0) THEN
    p%ColCm = 3
    IF (InCol_Cpmin > 0) p%ColCpmin = 4
ELSE IF (InCol_Cpmin > 0) THEN
    p%ColCpmin = 3   ! <-- Cpmin goes to column 3 when Cm is absent!
END IF
```

The MHK airfoil files have `InCol_Cm=0` (no Cm column) and `InCol_Cpmin=4`. So `p%ColCpmin=3`, meaning Cpmin is stored in column 3 of the output Coefs array. The hardcoded C++ stored Cpmin in column 4 (0-based index 3). Column 3 was left at zero, causing the cavitation check to compute wrong sigma values.

### Fix

Compute output column indices in the C++ using the same conditional logic as `AFI_Init`:
```cpp
int colCl = 1, colCd = 2, colCm = 0, colCpmin = 0;
if (InitInp->InCol_Cm > 0) {
    colCm = 3;
    if (InitInp->InCol_Cpmin > 0) colCpmin = 4;
} else if (InitInp->InCol_Cpmin > 0) {
    colCpmin = 3;
}
```

These indices are stored in the static cache and used in both Pass 1 (coefficient parsing) and Pass 2 (`calculateuacoeffs_c` call).

### Lesson

Output column indices in OpenFAST are not simply `1,2,3,4` — they depend on which optional columns are present. When `Cm` is absent, `Cpmin` shifts from column 4 to column 3. Any C++ replacement must replicate this conditional assignment.

## Verification

- **17/17 baseline regression tests pass** (data-identical)
- Tests cover 9 different airfoil file formats:
  - BAR: single-table, partial UA parameters (missing alphaUpper/alphaLower, many defaults)
  - BAR CombinedCases: multi-Re tables
  - MHK RM1: 7 Re tables, Cpmin column, no Cm column, `@"filename"` coordinate includes
  - MHK RM1 UA: 7 Re tables with full UA data (InclUAdata=True, Re-varying UA parameters)
  - OLAF variants: single-table, no UA data
  - Kite: multi-component OLAF
  - Timeseries shutdown: time-varying conditions

## Files Modified

| File | Action |
|------|--------|
| `openfast/translations/AirfoilInfo/readaffile.cpp` | NEW — C++ translation (~650 lines) |
| `openfast/modules/aerodyn/src/readaffile.cpp` | Copy of above for build |
| `openfast/modules/aerodyn/src/AirfoilInfo.f90` | ReadAFfile body replaced with two-pass wrapper |
| `openfast/modules/aerodyn/CMakeLists.txt` | Added `src/readaffile.cpp` to basicaerolib |
| `openfast/modules/aerodyn/src/vit_translated.h` | Added `readaffile_pass1_c`, `readaffile_fill_c` declarations |

## Relationship to Previous Approach Pivot

The initial approach (line-by-line Fortran replica) failed on `ParseVarWDefault` for optional fields like `RelThickness` — see dev note `202604270219-readaffile-approach-pivot.md`. The idiomatic C++ approach with keyword-based `AirfoilFileParser` was the right call, but it still needed to replicate the Fortran's keyword-check-before-consume semantics for UA parameters. The keyword checking is not at odds with the "idiomatic" approach — it's a necessary compatibility behavior for files with missing parameters.

The heap corruption from the previous session turned out to be the simpler of the three bugs (uninitialized `FileInfo` in `Cleanup()`). The real complexity was in understanding NWTC Library's `ParseVar` line-consumption behavior and the conditional column mapping.
