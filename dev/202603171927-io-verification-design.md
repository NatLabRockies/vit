# I/O Verification Capability Design

**Date:** 2026-03-17
**Status:** completed
**Scope:** Phase 9 design, VIT verification architecture

## Background

The plan review identified that Phase 9 I/O modules were marked "outside VIT pipeline" — contradicting VIT's core value of proving translations correct. Investigation of every I/O function in ROSCO determined that they fall into four distinct categories requiring different verification strategies.

## The Four Categories

### Category 1: Pure Transformers
Functions with no file I/O — pure input→output transformations.
- `ReadAvrSWAP`, `ParseDbAry`, `FindLine`, `GetWords`, `Conv2UC`, etc.
- **Verification**: Standard VIT pipeline (extract → verify → integrate). KGen works directly.

### Category 2: File Readers
Functions that read files and populate structs. Deterministic: same file → same struct.
- `ReadControlParameterFileSub`, `ReadCpFile`, `ReadRestartFile`
- **Verification**: VIT pipeline + file pre-staging. `vit extract` captures file dependencies and copies input files into the kernel directory. Verification compares output structs field-by-field.
- **VIT enhancement**: `vit extract` learns to record and capture file dependencies.

### Category 3: File Writers
Functions whose output is a file on disk, not a Fortran argument. KGen cannot capture file system side effects.
- `WriteRestartFile`
- **Verification**: `vit verify --io`. Capture input struct state, run both Fortran and C++ with those inputs, byte-compare output files.
- **VIT enhancement**: New `--io` mode in verify command.

### Category 4: Diagnostic/Logging
Functions with side effects only, stateful (SAVE variables), non-deterministic elements (timestamps). Correctness means "equivalent information," not "identical bytes."
- `Debug`
- **Verification**: `vit verify --diagnostic`. Run full simulation with both versions, compare output files using a configurable structured diff (which columns to compare, which to ignore, numeric tolerance, format-agnostic whitespace).
- **VIT enhancement**: New `--diagnostic` mode with comparison spec.

## Why Category 4 Matters Beyond ROSCO

Diagnostic/logging functions will appear in every Fortran codebase VIT targets. They share characteristics: side effects only, stateful, non-deterministic elements, human-readable output. A principled, configurable approach (comparison specs in vit.yaml) makes this generalizable rather than ad-hoc.

## Key Insight: KGen's Boundary

KGen's state capture model assumes pure computational functions that transform input arguments into output arguments. The "state" it captures is the Fortran call signature — arguments before and after. For functions whose meaningful output is on the file system, KGen's model doesn't apply.

The four categories map directly to KGen's boundary:
- Category 1: Inside KGen's model (no file I/O)
- Category 2: At the boundary (file I/O on input side, but output is a struct KGen can capture)
- Category 3: Outside KGen's model (output is a file, not an argument)
- Category 4: Outside KGen's model (output is non-deterministic files)

## Configuration

```yaml
translations:
  ReadControlParameterFileSub:
    io_mode: file_reader
    file_dependencies:
      - rosco/controller/config/DISCON.in
  WriteRestartFile:
    io_mode: file_writer
    output_files:
      - "*.RO.chkp"
  Debug:
    io_mode: diagnostic
    comparison_spec:
      ignore_columns: [1]           # timestamp column
      numeric_tolerance: 1.0e-14
      ignore_whitespace: true
```

## Impact on Plan

- Phase 9 now has four VIT enhancement checkboxes (file pre-staging, --io mode, --diagnostic mode, vit.yaml config) that are built first, then used to verify each translation.
- Every I/O function has a clear, principled verification strategy.
- The design is generalizable to other Fortran codebases (Phase 11).
