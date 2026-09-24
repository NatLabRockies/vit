# CLAUDE.md Version Control and Expansion
**Date:** 2026-02-27
**Status:** completed
**Scope:** CLAUDE.md, project documentation

## Background

Throughout the initial development sprint (Feb 25-27), the CLAUDE.md file
lived only in the parent `vit_translation/` directory and was not tracked in
any git repository. This was a problem for two reasons:

1. **No version history**: CLAUDE.md evolved significantly across the sprint
   as new features were added, workflows were refined, and lessons were learned.
   Without version control, earlier states were lost and there was no way to
   understand how the development instructions changed over time.

2. **Fragile location**: Claude Code discovers CLAUDE.md by walking up from
   the working directory. Starting Claude Code from `vit_translation/vit/`
   (the VIT repo root) would not find CLAUDE.md at `vit_translation/CLAUDE.md`
   unless configured to look in parent directories. Moving the file into the
   VIT repo ensures it is always discoverable.

## What Changed

### File Relocation

The canonical CLAUDE.md was moved into the VIT repository at
`vit_translation/vit/CLAUDE.md`, making it a tracked file in the VIT git repo.
A symlink was created at `vit_translation/CLAUDE.md` pointing to
`vit/CLAUDE.md`, so that Claude Code still finds the file when started from
the parent workspace directory.

### Content Expansion

The CLAUDE.md content was substantially expanded from its earlier form. The
major additions in this commit:

**Clean Build Test procedure**: A complete step-by-step process for running
the definitive end-to-end test — starting from a completely clean ROSCO state,
building a pure-Fortran baseline, extracting kernels for all three functions,
verifying translations, integrating them, rebuilding, and confirming
byte-identical output. This procedure takes about 10 minutes due to three
KGen extractions and serves as the ultimate regression test.

**Corrected wrap_180 call site**: Early in development, the wrap_180 call
site was incorrectly documented as Controllers.f90:400. That call site is
inside a `Y_ControlMode == 1` branch, which is disabled in ROSCO's default
simulation configuration. KGen would instrument it but capture zero
invocations. The correct call site is Filters.f90:430, which is always
active. This correction was discovered during the multi-function work
(Note 12) and is now properly documented.

**Extraction parameters table**: A reference table listing the exact extraction
command for each golden fixture function:

| Function   | Call site file    | Line |
|------------|-------------------|------|
| saturate   | Controllers.f90   | 102  |
| wrap_180   | Filters.f90       | 430  |
| interp1d   | Controllers.f90   | 62   |

This eliminates guesswork when regenerating golden fixtures and ensures
consistent extraction parameters across developers.

**Known Limitations section**: Documenting the architectural limitations
identified during derived type and assumed-shape array support (Note 13):
per-array size parameters, unconnected derived type registry, no
multi-dimensional assumed-shape handling, and string-based USE statement
matching.

**Translations table**: Canonical list of all C++ translation files, their
corresponding Fortran functions, and their status (verified, integrated, etc.).

## Rationale

This commit marks the end of the initial development sprint. Over three days
(Feb 25-27), VIT went from project inception to a working pipeline capable of
extracting, verifying, and integrating three Fortran functions with C++
translations, including non-trivial type signatures (assumed-shape arrays,
derived types). Capturing the accumulated project knowledge in a
version-controlled CLAUDE.md ensures that future development sessions —
potentially weeks or months later — start with full context about how the
system works, what its limitations are, and how to test it.

The expanded CLAUDE.md effectively serves as both developer documentation and
an AI context file: it tells a human developer everything they need to know
to work on VIT, and it tells Claude Code everything it needs to know to
assist with development tasks correctly.

## Files Changed

- `vit/CLAUDE.md`: New file, 409 lines. Complete project documentation
  including architecture, workflow, testing procedures, configuration,
  known limitations, and development guidelines.

## Commit

3938f6e — 1 file changed, 409 insertions(+)
