# VIT Project Inception: Initial Architecture and Module Structure
**Date:** 2026-02-25
**Status:** completed
**Scope:** full project — initial commit of all core modules

## Summary

The entire VIT (Verified Incremental Translator) codebase was laid down in a single initial commit (8411e91), followed immediately by a .gitignore addition (a4bc44c). This was not an incremental build-up — the complete architecture, all core modules, tests, and an example configuration shipped together as one cohesive unit. 14 files, 3,279 lines of code.

## Background and Motivation

VIT exists to solve a specific problem in scientific computing: how do you replace Fortran functions with C++ translations in a large simulation codebase and *prove* the replacement is correct? The answer is runtime state capture and bit-identical replay.

The key insight is that VIT does not translate Fortran to C++. It verifies translations written by humans or LLMs. The workflow is:

1. **Extract** — Instrument a Fortran function call site, run the real simulation, capture all inputs and outputs at runtime using KGen (an NCAR tool for Fortran kernel extraction).
2. **Verify** — Wire a user-provided C++ translation into a standalone kernel, feed it the captured inputs, compare outputs against the captured Fortran outputs.
3. **Integrate** — Generate patches to replace the Fortran function with the verified C++ in the real codebase (ISO_C_BINDING wrappers, CMake changes, etc.).

ROSCO, a wind turbine controller written in Fortran, serves as the test case. The proof-of-concept target function is `saturate` — a simple min/max clamp.

## Module Structure at Inception

### `vit/cli.py` (519 lines)
The command-line interface, built on argparse with three subcommands: `extract`, `verify`, and `integrate`. This is the orchestration layer — it reads configuration, calls into the other modules, and manages the overall workflow. At 519 lines it was already the largest module, reflecting the complexity of coordinating KGen invocations, file management, and user-facing output.

### `vit/config.py` (242 lines)
Configuration management via `vit.yaml`. Handles loading the YAML config file, resolving paths (relative to project root), and providing structured access to settings like KGen path, build commands, Fortran kind aliases, and verification tolerances. The config file design was driven by ROSCO's specific needs — kind aliases like `DbKi -> REAL(8)` are ROSCO-specific but the mechanism is general.

### `vit/extract.py` (223 lines)
Wraps KGen to perform runtime state capture. Responsibilities: insert KGen pragmas at call sites in the Fortran source, invoke KGen with the right flags, manage the build/run/capture cycle, and clean up. This module has to deal with KGen's specific requirements — it needs Python 2.7, expects pragmas in a particular format, and modifies source files during instrumentation.

### `vit/fortran_parser.py` (360 lines)
Parses Fortran function/subroutine signatures to extract argument names, types, intents, and return values. This is not a full Fortran parser — it's a targeted extractor that handles the subset of Fortran syntax found in ROSCO. It reads the source file, finds the function definition, and walks through the declarations to build a structured representation of the interface. Handles Fortran-specific complications like continuation lines, INTENT attributes, and kind-parameterized types.

### `vit/integrate.py` (389 lines)
Generates integration patches to replace a Fortran function with its C++ translation in the real codebase. This involves multiple coordinated changes: adding an ISO_C_BINDING wrapper function in Fortran that calls the C++ via `BIND(C)`, modifying CMakeLists.txt to compile the C++ file and link with `stdc++`, and replacing the original Fortran call with a call to the C wrapper. The complexity comes from generating syntactically correct Fortran and CMake patches that work with the existing codebase structure.

### `vit/interface_gen.py` (337 lines)
Generates the C interface layer between Fortran and C++. Given a parsed Fortran function signature, it produces: (1) a C++ function signature with the right types and pointer semantics, (2) a Fortran ISO_C_BINDING interface block, and (3) a Fortran wrapper function that bridges the calling conventions. This is where Fortran's pass-by-reference meets C's pass-by-value/pointer semantics.

### `vit/kernel_verify.py` (480 lines)
The verification engine. Takes a KGen-generated kernel (which replays captured Fortran state), patches it to also call the C++ translation, and compares outputs. This involves: modifying the kernel's Fortran driver to call the C++ function via ISO_C_BINDING, patching the Makefile to compile C++ alongside Fortran, building the combined kernel, running it, and parsing the output to determine pass/fail for each captured invocation.

### `vit/type_map.py` (155 lines)
Maps Fortran types to C/C++ types. Handles standard Fortran types (INTEGER, REAL, LOGICAL, CHARACTER) with their kind parameters, plus ROSCO-specific kind aliases (DbKi, SiKi, IntKi, ReKi). The mapping must be exact — bit-identical verification demands that type sizes match precisely.

### `tests/test_core.py` (311 lines)
Unit tests covering the core modules, particularly `fortran_parser.py` and `type_map.py`. Tests use synthetic Fortran snippets rather than requiring the full ROSCO codebase, making them runnable in isolation.

### `examples/rosco/saturate.cpp`
The reference C++ translation of ROSCO's `saturate` function — a simple min/max clamp. This serves as both a test case and an example of what a C++ translation looks like when consumed by VIT.

### `examples/rosco/vit.yaml`
Example configuration file for running VIT against ROSCO.

### `setup.py` and `vit/__init__.py`
Standard Python packaging. VIT installs as an editable package (`pip install -e`) and provides the `vit` command-line entry point.

## Architectural Decisions

**KGen as the state capture mechanism:** Rather than building custom Fortran instrumentation, VIT wraps KGen, which already solves the hard problem of capturing all inputs and outputs of a Fortran function during a real simulation run. The trade-off is that KGen requires Python 2.7 and has specific pragma format requirements, which VIT must accommodate.

**YAML configuration:** All project-specific settings (paths, build commands, kind aliases) live in `vit.yaml` rather than being hardcoded. This makes VIT theoretically applicable to any Fortran project, not just ROSCO.

**Fortran as the glue language:** The ISO_C_BINDING wrapper that bridges Fortran and C++ is written in Fortran, not C. This means the existing Fortran build system only needs minimal modifications (add one C++ source file and link `stdc++`) rather than requiring a separate C compilation step.

**Bit-identical verification:** The verification tolerance is configurable but defaults to `1.0e-14`, which is effectively bit-identical for double precision. This is a deliberate choice — "close enough" is not acceptable for scientific computing where results must be reproducible.

## What Shipped vs. What Worked

It's important to note that while the entire architecture was committed in this initial drop, it did not work end-to-end against ROSCO yet. The subsequent commits on the same day (covered in later dev notes) document the fixes needed to make extraction, verification, and integration actually succeed. The initial commit established the *design*; making it *run* required several more rounds of debugging against real KGen behavior.
