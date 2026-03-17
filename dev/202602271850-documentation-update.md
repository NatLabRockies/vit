# Documentation Overhaul: README and ROSCO Guide
**Date:** 2026-02-27
**Status:** completed
**Scope:** documentation

## Background

By this point in development, the code had evolved significantly faster than the documentation. The README and guide_ROSCO.md contained several factual inaccuracies, missing sections, and outdated instructions that reflected earlier iterations of the pipeline. The unit test suite (see `202602271826-unit-test-suite.md`) had just been completed, providing confidence that the code was stable enough to document accurately.

This was a single focused documentation pass to bring both files up to date with the actual state of the codebase and pipeline behavior.

## What Changed

### Commit 757e9b7 (2026-02-27)

2 files changed, 195 insertions, 66 deletions.

### README.md Corrections and Additions

**Verification count fix (20/20 to 40/40)**: The most user-visible error. KGen tests both the input state and output state for each captured invocation. With 20 runtime captures, there are 40 verification checks (20 input-side + 20 output-side). The README had been saying "20/20 passed" since the early days when the count was misunderstood. The correct expected output is "40/40 passed".

**CLI Reference section**: Added comprehensive documentation for all 9 CLI commands (`vit extract`, `vit verify`, `vit integrate`, `vit parse`, `vit interface`, `vit run`, `vit config`, `vit status`, `vit clean`). Each command includes its flags, required arguments, and a brief description of what it does. This section did not exist before — users had to read the source or run `--help` to discover available commands.

**Expanded vit.yaml example**: The configuration file example was a minimal stub showing only the `project` and `kgen` sections. Expanded to show all sections including `build_system`, `fortran` (with kind aliases), `verification` (with tolerance), and `translations` (with status tracking). Added inline comments explaining each field.

**Troubleshooting section**: New section covering common failure modes:
- KGen Python 2.7 errors (wrong Python version on PATH)
- Clock skew warnings in Docker (harmless, caused by filesystem timestamp differences)
- Missing turbine model pickle (need to run 01_turbine_model.py first)
- Verification failures from stale kernels (need to re-extract after source changes)

**NaN-safe verification in Key Design Decisions**: Added explanation of why NaN handling is necessary and how it works, so users understand why some cases show as "skipped" and what "identical NaN" means in the output.

**Split Python requirements**: Previously stated "Python 3.10+" as the only requirement. Clarified that VIT itself requires Python 3.10+, but KGen (which VIT wraps) requires Python 2.7. Both must be available in the environment. Added note about the Docker container having both pre-installed.

**Docker recommendation**: Added explicit recommendation to use the provided Docker container rather than attempting native installation, given the dual Python version requirement and KGen's dependencies.

### guide_ROSCO.md Corrections and Additions

**Case count fixes**: Same 20/20 to 40/40 correction throughout the ROSCO-specific guide.

**Baseline capture command fix**: The command for capturing baseline simulation output had an incorrect redirect that swallowed stderr. Fixed to `> baseline_output.txt 2>&1` to capture both stdout and stderr, which is necessary because ROSCO's Python simulation prints some status information to stderr.

**Removed conda wrappers**: Early versions of the guide used `conda activate` commands that were no longer relevant in the Docker-based workflow. All conda references removed, replaced with direct `python3` invocations.

**Fixed parse output format**: The `vit parse` command's expected output format had changed when field-level reporting was added. Updated the example output to match the current format, including NaN field annotations.

**Added vit interface and vit run sections**: These commands were added to the CLI after the guide was originally written. `vit interface` generates the Fortran-to-C++ binding code for inspection without running verification. `vit run` executes the full pipeline (extract + verify) in one command. Both documented with examples and expected output.

**NaN-Safe Verification section**: New section explaining NaN handling in the context of ROSCO specifically. ROSCO passes derived types (like `ErrorVariables`) through its function interfaces, and these types contain fields that are sometimes uninitialized. The section explains why users will see NaN-related messages and why they are not cause for concern.

**Expanded kernel patches documentation**: The guide previously mentioned that VIT patches KGen's generated kernel but didn't explain what the patches do. Added documentation of each patch: NaN comparison fix, USE statement reordering, Makefile C++ rules, and verboseLevel setting.

**Removed obsolete manual workarounds**: Earlier versions of the guide included manual steps that VIT now automates (like editing KGen's Makefile by hand or manually copying state files). These were removed to avoid confusing users who might follow the manual steps and conflict with VIT's automation.

## Factual Inaccuracies Caught

The documentation pass identified and corrected these specific inaccuracies:

1. Verification count: 20/20 was wrong, should be 40/40
2. Baseline capture command: missing stderr redirect
3. Python version: guide implied only Python 3 was needed
4. Parse output format: showed old single-line format instead of current table format
5. Missing CLI commands: `vit interface` and `vit run` were undocumented
6. Stale manual workarounds: several steps that VIT now automates were still listed as manual

## Files Modified

- `README.md`
- `guide_ROSCO.md`

## Lessons Learned

Documentation debt accumulates quickly when features are being added in rapid succession. The gap between the documented behavior and actual behavior was large enough that a new user following the guide would have encountered multiple confusing failures. Scheduling a documentation pass immediately after stabilizing the test suite was the right time — the code was known-correct, making it safe to document definitively.
