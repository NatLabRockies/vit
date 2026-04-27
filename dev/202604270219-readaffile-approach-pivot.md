# ReadAFfile Approach Pivot — Idiomatic C++ File Parser
**Date:** 2026-04-27
**Status:** in-progress
**Scope:** ReadAFfile translation strategy revision

## What Happened

Attempted a line-by-line C++ translation of ReadAFfile that replicated NWTC Library's `ProcessComFile` + `ParseVar` semantics. Hit a wall: the Fortran parsing has fragile line-consumption behavior that's tightly coupled to NWTC internals.

### Specific Issue: ParseVarWDefault for Optional Lines

`RelThickness` is an optional field. Some airfoil files (BAR, MHK) don't have it. The Fortran handles this via `ParseVarWDefault`, which:
1. Reads the next line from `FileInfo` (always increments `CurLine`)
2. Tries to parse the first token as a double
3. On failure: uses default, sets `ErrStat >= AbortErrLev`
4. ReadAFfile catches the error and silently continues

When `RelThickness` is missing, `ParseVarWDefault` consumes the `NonDimArea` line instead (reads 1.0 as RelThickness). Then `ParseVar('NonDimArea')` reads the `NumCoords` line. This "shifting" works because the affected fields (`RelThickness`, `NonDimArea`) are both unused in practice.

A C++ parser that tries to replicate this exact behavior is brittle and unmaintainable.

### @"filename" Include Semantics

NWTC's `ProcessComFile` replaces `@"filename"` lines with the included file's contents. The included file starts with `NumCoords` on its first non-comment line, so the count appears at the right position in the flattened line stream.

This was correctly handled by my C++ `readAndDecomment` function (after fixing a quote extraction bug). Not the blocking issue.

## Revised Approach: Idiomatic C++ Parser

Instead of replicating Fortran's parsing quirks, write a clean C++ parser that:

1. **Reads the file format natively** — understands the airfoil file structure (header fields in known order, per-table blocks, coefficient rows)
2. **Uses keyword matching** — parse each line by matching the variable name (e.g., look for "InterpOrd", "NonDimArea", "NumTabs") rather than consuming lines in strict order
3. **Handles optional fields gracefully** — if `RelThickness` isn't found, use default (-1). If `NonDimArea` is found, use it. No line-shifting.
4. **Handles @includes** — resolve includes during file loading (already implemented)
5. **Produces bit-identical structures** — same Alpha, Coefs, UA_BL values as Fortran

This is the same approach ROSCO used: `DisconParser` in `ReadControlParameterFileSub` reads the DISCON.IN format idiomatically in C++. It doesn't replicate NWTC's parsing — it reads the same file format and produces the same result.

### Key Insight

The Fortran's parsing is position-based (read line N as variable X). The C++ parser should be **keyword-based** (find the line containing "NumTabs" and parse its value). This naturally handles optional fields, different ordering, and format variations across airfoil files.

The actual data format is consistent across all airfoil files:
- `value  VarName  ! optional comment`
- For each table: `value  Re`, `value  UserProp/Ctrl`, `True/False  InclUAdata`, then UA params, then `N  NumAlf`, then N rows of coefficient data

A keyword-based parser with fallback-to-positional for the coefficient data rows is robust and maintainable.

## Status

- C++ translation file exists at `translations/AirfoilInfo/readaffile.cpp` — has correct two-pass structure, static cache, extern "C" wrappers, but parser needs rewrite
- Fortran wrapper was reverted — original ReadAFfile is back in AirfoilInfo.f90
- 17/17 baselines passing (reverted to working state)
- Next: rewrite the parser section with keyword-based approach

## ROSCO Precedent

From ROSCO CLAUDE.md on Stage B functions:
> "ReadControlParameterFileSub: DISCON.IN parser, two-pass protocol"

The C++ translation used a `DisconParser` class with `parseDbl()`, `parseInt()`, `parseStr()` methods that read from `std::vector<std::string>`. No NWTC Library calls. Bit-identical output verified via 27-scenario simulation comparison.

Same approach for ReadAFfile: write a clean `AirfoilFileParser` that reads the airfoil format, produces the same structures, verified via 17/17 baselines.
