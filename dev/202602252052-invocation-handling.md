# Invocation Count Configuration and KGen Format Fix
**Date:** 2026-02-25
**Status:** completed
**Scope:** config, extract, cli

## Background

KGen captures runtime state by instrumenting a Fortran function and recording its inputs and outputs each time it is called during a real simulation run. The number of invocations captured directly affects both the quality of verification (more cases = more coverage of code paths and value ranges) and the time extraction takes (each captured invocation adds I/O overhead to the simulation).

Before this change, the invocation range was hardcoded. There was no way for the user to control how many runtime calls KGen recorded, nor which MPI rank or OpenMP thread to capture from. For ROSCO this was not yet a problem (single-rank, single-thread), but the design needed to be flexible for multi-process Fortran codes.

## What Changed

### Commit cf393ce (2026-02-25): Add invocation count flag to CLI

Added an `invocation` field to `VitConfig` with a default value of `"0:0:1:20"`, meaning:

- MPI rank 0
- OpenMP thread 0
- Invocations 1 through 20

This was wired through three layers:

1. **config.py** — `VitConfig` dataclass gained the `invocation` field; `load_config()` and `save_config()` read/write it from `vit.yaml` under the `kgen` section.
2. **extract.py** — `generate_kgen_command()` now reads the invocation string from config and passes it to KGen's `--invocation` flag instead of using a hardcoded value.
3. **cli.py** — The `extract` subcommand gained an `--invocation` flag that overrides the config default for a single run.

The default of 20 invocations was chosen empirically: for ROSCO's `saturate` function (called once per controller timestep), 20 captures provides enough diversity in input values to exercise the min/max clamp logic without making extraction noticeably slower. Functions with more complex branching might benefit from higher counts.

3 files changed, 13 insertions.

### Commit 3e04602 (2026-02-26): Fix invocation format to match KGen

The initial implementation used a colon-only format: `0:0:1:20`. This was wrong. KGen's `--invocation` flag expects the invocation range portion to use a dash separator: `0:0:1-20`. The colon-only format caused KGen to misinterpret the range, silently capturing only a single invocation instead of 20.

The fix changed the default in `VitConfig` from `"0:0:1:20"` to `"0:0:1-20"` and updated the format documentation. The separator meaning is:

- Colons separate the three dimensions: MPI rank, OpenMP thread, invocation range
- Dash separates the start and end of the invocation range

This was a subtle bug because KGen did not error on the malformed input — it simply parsed `0:0:1:20` as four colon-separated fields and silently used only part of it.

3 files changed, 10 insertions.

## Design Decisions

- **Config-file default with CLI override**: The invocation string lives in `vit.yaml` so it is persistent across runs, but can be overridden per-extraction via the CLI flag. This follows the same pattern used for other KGen-related settings.
- **String passthrough, not structured fields**: The invocation is stored as a single string rather than separate `mpi_rank`, `omp_thread`, `start`, `end` fields. This keeps the config simple and avoids VIT needing to understand KGen's full invocation syntax, which could change across KGen versions.
- **20 as the default**: Balances coverage against extraction time. For ROSCO, each extraction takes roughly 2 minutes with 20 invocations. Going to 100 would add meaningful time without proportional verification benefit for simple functions like `saturate`.

## Files Modified

- `vit/config.py`
- `vit/extract.py`
- `vit/cli.py`
