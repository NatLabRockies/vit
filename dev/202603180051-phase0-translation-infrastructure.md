# Phase 0: Translation Infrastructure
**Date:** 2026-03-18
**Status:** completed
**Scope:** `vit translate` command, module-organized translation storage, metadata headers

## What Was Done

Phase 0 builds the translation workflow tooling described in PROJECT_PLAN.md Section 4a. The goal: every translation from Phase 1 onward follows the correct convention from day one — module-organized storage, metadata headers, and a `vit translate` command that scaffolds the C++ file and generates a translation prompt.

### New Files

- **`vit/vit/translate.py`** — Two main functions:
  - `generate_translation_scaffold()` — produces a C++ file with metadata header comment block and the correct function signature (from `build_c_params()`), with a placeholder body. The scaffold is an **implementation-only** file, matching the pattern of existing hand-written translations. The `extern "C"` wrapper is added later at integration time by `_generate_standalone_cpp()` in `integrate.py`.
  - `generate_translation_prompt()` — produces a complete prompt for LLM-assisted translation, embedding the original Fortran source verbatim, the generated scaffold, call site info, and the contents of `TRANSLATION_TIPS.md` if it exists.
  - Helper: `_extract_function_text()` — pulls a function's raw text from a Fortran source file for inclusion in the prompt.
  - Helper: `_generate_metadata_header()` — generates the comment block with function name, source file, module, Fortran signature summary, MD5 hash of the Fortran source, VIT version, verification status, and UTC timestamp.

- **`vit/tests/test_translate.py`** — 24 unit tests covering scaffold generation (basic scalars, arrays with size params, passthrough exclusion), metadata content, module detection, prompt generation (with/without tips), and helper functions.

- **`ROSCO/translations/Functions/`** — Module-organized translation directory. All 4 existing translations moved here from `vit_translation/translations/` (flat layout).

- **`ROSCO/TRANSLATION_TIPS.md`** — Empty living document. Tips are added when a translation fails verification and the fix reveals a generalizable lesson. `vit translate` includes its contents in the prompt automatically.

### Modified Files

- **`vit/vit/cli.py`** — Added `translate` subcommand (the 10th command). Subparser: `vit translate <function_name> -f <fortran_file> [-o <output_dir>] [--module <module_name>]`. Handler parses the Fortran signature, determines module name (from signature or filename), generates scaffold, writes it to `translations/<Module>/<function>.cpp`, and prints the translation prompt to stdout.

- **`CLAUDE.md`** (and `vit/CLAUDE.md`, which is identical) — Updated the C++ Translations section to reference `ROSCO/translations/Functions/` instead of `vit_translation/translations/`. Updated Clean Build Test Step 2 to copy from the new location. Added a TRANSLATION_TIPS.md subsection.

### Retrofitted Metadata

All 4 existing translations received metadata headers by running a script that parsed each function's Fortran signature from `Functions.f90`, generated the header via `_generate_metadata_header()`, and prepended it to the existing file. Status set to `verified` (not `unverified`) since these translations have already passed verification.

Files retrofitted:
- `translations/Functions/saturate.cpp`
- `translations/Functions/wrap_180.cpp`
- `translations/Functions/wrap_360.cpp`
- `translations/Functions/interp1d.cpp`

## Design Decisions

### Implementation-only scaffold (not extern "C" wrapper)

The plan's Section 4a says the scaffold generates "the correct `extern "C"` wrapper." The Phase 0 implementation plan overrides this: the scaffold produces an implementation-only file matching the existing translation pattern. The `extern "C"` wrapper is generated at integration time by `_generate_standalone_cpp()`. This means `vit translate` produces files in the same format as today's hand-written translations — no workflow change for existing translations.

### Metadata header as comments (not structured format)

Metadata is stored as `// Key: value` comment lines at the top of the C++ file. This was chosen over alternatives (YAML front matter, sidecar files, embedded pragmas) because: (a) it's visible to humans reading the file, (b) it doesn't interfere with compilation, (c) it's trivial to parse, and (d) it matches the existing pattern of comment headers in the translations.

### Translation prompt printed to stdout

The prompt is printed to stdout rather than written to a file. This makes it easy to pipe into other tools or copy-paste into an LLM conversation. The scaffold (the part that matters for the codebase) is written to disk.

### TRANSLATION_TIPS.md starts empty

The tips file is intentionally empty. Tips are added only when a translation fails verification and we discover something generalizable — not assumed upfront. This avoids prescriptive rules that may not apply and ensures every tip is grounded in a real failure.

## Passthrough Discussion

During implementation, a question arose about whether the scaffold should exclude passthrough (non-C-compatible derived type) args. Reading the full PROJECT_PLAN.md clarified the trajectory:

- **Current state**: `TYPE(ErrorVariables)` is passthrough because the derived type registry isn't wired in. All `TYPE(...)` args default to passthrough. `build_c_params()` correctly excludes them.
- **Phase 4**: The derived type registry gets wired into the pipeline. `rlParams` becomes a C struct. `ratelimit`'s scaffold would include it.
- **Phase 4.5**: ALL types become C structs. `LocalVariables`, `ControlParameters`, `FilterParameters` etc. all become BIND(C)-compatible. The scaffold for Phase 5+ functions would include them as struct pointer parameters.
- **ErrorVariables specifically**: Stays passthrough for functions that don't touch errors. For functions that need error reporting, gets `int* aviFAIL` + `char* ErrMsg` as separate C args.

The key insight: `build_c_params()` checks `is_c_compatible`, not `is_derived`. When the registry is wired in and types become C-compatible, they automatically appear in the scaffold. No changes to translate.py needed — the behavior evolves as the type system evolves.

## Verification

1. **Unit tests**: 231/231 passed (24 new + 207 existing)
2. **Verification regression**: saturate 40/40, wrap_180 40/40 from new `translations/Functions/` paths
3. **interp1d kernel**: Golden fixture not present (missing `kernel/interp1d/`). Would need re-extraction to test. Not a regression — the kernel was never captured in this workspace state.
4. **CLI smoke test**: `vit translate saturate -f rosco/controller/src/Functions.f90` produces correct scaffold and prompt

## What's Next

Phase 1: Low-Hanging Fruit — integrate `wrap_360` (already verified), then `vit translate sigma` and `vit translate CPfunction` to scaffold, translate, extract, verify, and integrate two new functions. No VIT changes needed.
