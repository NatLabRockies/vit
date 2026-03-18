# Assumption Confirmation Protocol
**Date:** 2026-03-18
**Status:** completed
**Scope:** General interaction protocol for VIT → Claude → user assumption confirmation, plus silent type fallback fixes

## Background

During Phase 4A review, we identified that `fix_character_allocatable` hardcoded CHARACTER(1024) as the default buffer size. This is correct for ROSCO but is an assumption that could silently produce wrong results on other codebases. The user identified this as a general design problem: VIT's standard workflow is Claude Code running VIT commands, but there was no mechanism for VIT to signal "I inferred a value and need human confirmation before proceeding."

A codebase audit also found two silent type fallbacks (`return "double"` in translate.py and `REAL(8)` in interface_gen.py) that would produce code that compiles but behaves incorrectly if triggered.

## The Principle

We established a three-tier classification for defaults, documented in `confirm.py` module docstring and CLAUDE.md:

1. **Has evidence, inferred a value** → confirmation protocol (exit code 10). VIT found source evidence and derived a value, but the inference could be wrong. Human must validate. Example: inferred CHARACTER buffer size from ALLOCATE call.

2. **No evidence, arbitrary fallback** → hard error (exit code 1). VIT doesn't have enough information to proceed. Guessing would silently produce wrong code. Example: unknown function return type.

3. **Technical/performance default** → safe to hardcode. Wrong value = slower or noisier, not semantically incorrect. Examples: `max_iterations=5`, `tolerance=1e-14`.

**The test:** if a wrong default would produce code that compiles but behaves incorrectly, it needs either EXIT_CONFIRM (if inference exists) or a hard error (if no evidence).

This principle applies to all future VIT development. When adding a new command or fix type, any domain-specific value that affects generated code semantics must be classified under this framework.

## Part 1: The Confirmation Protocol

### Mechanism

- **Exit code 10** — distinct from 0 (success) and 1 (error). Means "analysis succeeded, but a decision needs human confirmation before proceeding."
- **`[VIT:CONFIRM]...[/VIT:CONFIRM]` blocks** — structured output on stdout containing:
  - `id`: machine-readable assumption identifier
  - `summary`: one-line human-readable description
  - `assumed_value`: the inferred or default value
  - `inferred_from`: evidence source (file:line, expression)
  - `context`: multi-line explanation
  - `cli_override`: exact CLI flag to confirm the value

### CLAUDE.md Instructions

Claude is instructed to:
1. Parse `[VIT:CONFIRM]` blocks from VIT's output when exit code is 10
2. Present the assumption to the user clearly
3. Wait for confirmation — **never auto-confirm**
4. Re-run VIT with the confirmed value as an explicit CLI argument

The "never auto-confirm" rule is critical. The entire point of exit code 10 is that VIT identified a decision requiring human judgment. Even if the inferred value looks correct, Claude must present it and wait.

### Implementation

New module `vit/vit/confirm.py`:
- `Assumption` dataclass with all block fields
- `EXIT_CONFIRM = 10`
- `print_assumption()` — formats and prints the block
- `parse_assumptions()` — extracts Assumption objects from output (for tests)

### First Use Case: CHARACTER Length Inference

New function `infer_character_length()` in `analyze_types.py`:
1. Searches all .f90/.F90 for `ALLOCATE(...%field_name(...))` patterns, including commented-out lines
2. Extracts the size expression
3. For literal integers: returns directly (e.g., `ALLOCATE(Var%Field(256))` → 256)
4. For `variable - N` patterns: searches for PARAMETER declarations to find the variable's value
5. If expression found but can't be evaluated: returns `(None, evidence)` — the evidence is still surfaced in the confirmation block so the user can evaluate it
6. If no ALLOCATE found: returns None

CLI change: `--length` default changed from 1024 to None. When omitted:
- VIT runs inference
- Builds an Assumption with inferred value (or 1024 fallback) and evidence
- Prints confirmation block, returns exit code 10
- When `--length` is explicitly provided: proceeds without confirmation

### ROSCO Example

Running `vit analyze-types --fix character --type ErrorVariables --field ErrMsg --apply` without `--length`:

```
[VIT:CONFIRM]
id: character_length
summary: CHARACTER buffer size for ErrorVariables.ErrMsg
assumed_value: 1024
inferred_from: ALLOCATE(%ErrMsg(size_avcMSG-1)) in ReadSetParameters.f90:173 (expression not evaluable; using default 1024)
context: |
  The field ErrorVariables.ErrMsg is CHARACTER(:), ALLOCATABLE.
  VIT will convert it to CHARACTER(1024).
  Confirm this length is correct for your project.
cli_override: --length 1024
[/VIT:CONFIRM]
EXIT_CODE=10
```

VIT found the commented-out ALLOCATE, extracted `size_avcMSG-1`, couldn't evaluate `size_avcMSG` (runtime variable, not a PARAMETER constant), but surfaced the evidence so the user can see the expression and confirm 1024 is correct.

## Part 2: Silent Type Fallback Fixes

### translate.py: `_get_c_return_type()`

Previously returned `"double"` when `sig.result_type` was None. Now raises `ValueError` with message: "Cannot determine return type for function 'X'. Ensure the Fortran parser captures the result type."

This is a "no evidence, arbitrary fallback" case — there's no source evidence to infer from, so guessing `double` would silently produce wrong code for INTEGER or LOGICAL return types.

### interface_gen.py: wrapper result type

Previously fell back to `REAL(8)` when no `result_type` and no args. Now raises `ValueError`: "Cannot determine result type for wrapper of 'X'. Function has no result_type and no arguments to infer from."

Same classification — no evidence, must error rather than guess.

### Impact

Neither fallback was triggered by any existing test or ROSCO function (all parsed functions have result types). The change is defensive — it prevents silent wrong answers for future functions or other codebases where the parser might not capture the result type.

## Design Decisions

**Why tagged text blocks instead of JSON?** VIT's existing output is human-readable text with Unicode markers. JSON would require a parsing library and look alien in terminal output. The `[VIT:CONFIRM]` delimiters are trivially parseable by both Claude (LLM) and regex.

**Why exit code 10?** Well above the 0/1/2 range used by conventions (success/general error/misuse), below the 126-255 range reserved by POSIX for signal/shell use. Memorable and distinct.

**Why not interactive (stdin)?** VIT's architecture is non-interactive — all via CLI args and stdout/exit codes. The caller is Claude Code running `docker exec`, not a human at a terminal. stdin would add complexity without benefit.

**Why surface evidence even when evaluation fails?** The original implementation returned None when it found an ALLOCATE but couldn't evaluate the expression. This lost valuable information — the user could see the expression `size_avcMSG-1` and know that 1024 is correct even if VIT couldn't prove it programmatically. The fix returns `(None, evidence)` so the confirmation block always shows what VIT found.

## Test Coverage

- `test_confirm.py` — 11 tests: Assumption formatting, parse roundtrip, multiple blocks, blocks mixed with normal output, empty/no-block cases
- `test_analyze_types.py` — 6 new tests: literal ALLOCATE inference, commented-out ALLOCATE with literal, runtime variable returns (None, evidence), PARAMETER expression evaluation, no ALLOCATE returns None, complex expression returns (None, evidence)
- Total: 352 tests, zero regressions
- Golden fixture regression: 40/40 (saturate confirmed)

## Files

- `vit/vit/confirm.py` — new: Assumption, EXIT_CONFIRM, print/parse
- `vit/vit/analyze_types.py` — new: `infer_character_length()`, `_evaluate_size_expression()`, `_find_parameter_value()`
- `vit/vit/cli.py` — `--length` default None, confirmation logic in cmd_analyze_types
- `vit/vit/translate.py` — `_get_c_return_type()` raises on unknown type
- `vit/vit/interface_gen.py` — wrapper result type raises on unknown type
- `vit/CLAUDE.md` — protocol documentation
- `vit/tests/test_confirm.py` — new: 11 tests
- `vit/tests/test_analyze_types.py` — extended: 6 new inference tests

## Future Applications

The confirmation protocol is designed to be general. Future uses:
- Array size inference when fixing ALLOCATABLE arrays (Phase 4D)
- LOGICAL replacement value choices (Phase 4C)
- Any VIT command that infers domain-specific values from source evidence

The `[VIT:CONFIRM]` format supports multiple blocks per invocation, though v1 only uses one. The `id` field enables programmatic handling when needed.
