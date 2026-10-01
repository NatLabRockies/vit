# CLAUDE.md

Guidance for working on VIT itself. To translate a codebase with VIT, start with `README.md`. The
AeroDyn campaign's operating manual is `docs/case-studies/aerodyn/AGENT_MANUAL.md`.

## What VIT is

VIT (Verified Incremental Translator) verifies and integrates Fortran→C++ translations one function
at a time. It does not translate. For each function it:
1. extracts runtime state at a call site with KGen
2. generates a C++ scaffold and a translation prompt
3. replays the captured state through the C++ in a KGen kernel and compares its outputs with the
   Fortran's, field by field (the target is bit-identical)
4. patches the verified C++ into the real build

VIT depends on the KGen fork at `https://github.com/kevinmenear/KGen`, which needs Python 2.7 and
strace. `docker/Dockerfile` provides both; see `docker/README.md`.

## Layout

| Path | What |
|---|---|
| `vit/` | The package. `cli.py` is the `vit` entry point. |
| `tests/` | The pytest suite. `tests/conformance/` holds the Fortran/C++ conformance matrix. |
| `scripts/` | Standalone helpers: the Fortran inventory and the telemetry join. |
| `examples/quickstart/` | A small runnable project; `docs/GETTING_STARTED.md` runs the whole cycle on it. |
| `docker/` | The image that runs KGen. |
| `docs/` | User documentation. `docs/case-studies/` holds everything specific to ROSCO and AeroDyn. |
| `dev/` | Dated development notes; see `dev/README.md`. |

## Setup and tests

```bash
conda env create -f environment.yml
conda activate vit
python -m pytest tests/
```

- The tests that compile generated Fortran, C and C++ need `gfortran`, `gcc` and `g++` on `PATH`.
- On macOS with Homebrew GCC, also `export SDKROOT=$(xcrun --show-sdk-path)`, or eight round-trip
  tests fail to link.
- The suite does not need KGen. `vit extract` and `vit verify` do, so run those in the container.

Every `vit` command writes a telemetry event to `.vit/cycle_log.jsonl` in the current directory
(`vit/telemetry.py`). A test that runs the CLI should set `VIT_TELEMETRY=0` or run in a temporary
directory.

## Checks, not prose

A translation defect with a known shape becomes a check in `vit/checks.py`, not advice in a document.
- Every check carries a known-bad example it must flag and a known-good one it must not.
  `tests/test_checks.py` runs every registered check against both.
- The guidance section of the `vit translate` prompt is generated from the registry, so it cannot
  drift from what is enforced.
- A defect with no precise detector goes in `PUSHED_TO_MUTATION` or `TOOL_GAPS`, with the reason.
- `vit check --list` shows the registry.

The same rule applies to shared helpers: `vit/header_api.py` reads the callable inventory out of the
target's shared headers at prompt time, so no one maintains a list.

## Principles

- **A passing result must be able to fail.** A verification step that can report success without
  comparing anything is worse than none. When adding one, break it on purpose once and confirm it
  says so (`vit/redtest.py` does this for kernels). A result should say what it compared.
- **Fix the infrastructure; don't work around it.** If a bridge, type mapping or Fortran pattern
  fails, fix VIT, so the next function doesn't hit the same wall.
- **A KGen failure is almost always a usage error.** Trace KGen's source before blaming it. Fix a
  genuine KGen bug in the fork, not with a workaround in VIT.
- **No codebase-specific logic.** A comment may cite the codebase where a rule was measured, but no
  code path depends on one; a project's specifics come from its `vit.yaml`.

## Documentation

Documents outside `docs/case-studies/` are generic: they hold no content specific to one codebase.
A codebase-specific example, plan, manual or data set belongs in that codebase's case study.

## Development notes

Notes live in `dev/` and are named `YYYYMMDDHHMM-descriptor.md`. They are dated records, cited by
ID, and are not rewritten. When one turns out to be wrong, say so on its status line and point to
where the correction is recorded.

When creating a dev note:
1. Run `date '+%Y%m%d%H%M'`. Do not guess the timestamp.
2. Use the standard header: `# Title` / `**Date:** YYYY-MM-DD` / `**Status:** completed | in-progress | abandoned` / `**Scope:** [area]`
3. Record the background, rationale, decisions, alternatives and next steps.
