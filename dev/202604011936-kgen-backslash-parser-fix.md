# KGen Parser Fix: Backslash Escape Bug in String Literals

**Date:** 2026-04-01
**Status:** completed
**Scope:** Fix KGen's Python 2.7 parser to handle backslash characters in Fortran string literals

## The Bug

KGen's parser (`splitline.py`, function `splitquote()`) treated backslash as an escape character, following Python conventions. When it encountered:

```fortran
I = INDEX( GivenFil, '\', BACK=.TRUE. )
```

It parsed `'\' ` as: open quote, backslash, escaped quote (still inside string) — and never found the closing quote. This corrupted the parser state for all subsequent lines, eventually triggering an exception in `base_classes.py`.

Fortran does not use backslash escaping. A backslash inside a string literal is just a character. The string `'\'` is a one-character string containing a backslash, terminated by the second quote.

## The Fix

Two locations in `splitquote()` had the backslash check:

1. **String start detection** (line ~153): `if char in quotechars and not nofslashes % 2` — prevented recognizing a quote as a string start if preceded by backslash
2. **String end detection** (line ~183): `if char==stopchar and not nofslashes % 2` — prevented recognizing a quote as a string terminator if preceded by backslash

Both simplified to remove the `nofslashes` check:
```python
# Before:
if char==stopchar and not nofslashes % 2:
# After:
if char==stopchar:
```

The `nofslashes` variable and its tracking logic were removed from both locations.

## Why This Wasn't Hit in Phases 1-9

The bug only triggers when KGen's parser reads a file containing backslash string literals. The 39 functions extracted in Phases 1-9 came from Controllers.f90, ControllerBlocks.f90, Functions.f90, Filters.f90, and DISCON.F90 — none of which contain backslash characters.

ReadAvrSWAP (Phase 10A) is in ReadSetParameters.f90, which has `USE ROSCO_Helpers`. KGen's type resolution follows USE chains, so it parsed ROSCO_Helpers.f90 — which contains backslashes in `GetPath` and `PathIsRelative` (Windows path separator handling). Four other files also have backslashes: SysGnuWin.f90, SysIVF.f90, SysLLVMWin.f90.

## Prior Workaround (Removed)

Before this fix, `extract_all.sh` had a manual sed patch that backed up ROSCO_Helpers.f90, replaced backslashes with forward slashes, ran the extraction, and restored the original. This workaround is now removed — the parser handles backslashes correctly.

## This Is the 4th KGen Fork Patch

| Commit | Fix |
|--------|-----|
| `31d653c` | PROCEDURE POINTER parsing (Fortran 2003+) |
| `b5fd702` | BIND(C) type declaration crash |
| `6144156` | Complex and deferred-length character types |
| `4676161` | Backslash escape bug in string literals (this fix) |

## Verification

- ReadAvrSWAP extraction: 21 cases (no sed patch needed)
- ReadAvrSWAP verification: 21/21 IDENTICAL
- KGen log: no ERROR entries for ROSCO_Helpers.f90 parsing

## Files Modified

| File | Repo | Change |
|------|------|--------|
| `kgen/parser/splitline.py` | KGen | Remove backslash escape logic from `splitquote()` |
| `scripts/extract_all.sh` | ROSCO | Remove ROSCO_Helpers.f90 sed patch/restore block |
| `CLAUDE.md` | VIT | Remove backslash patch note from extraction docs |
