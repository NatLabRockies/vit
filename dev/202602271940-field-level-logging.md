# Field-Level Logging for Verification Output
**Date:** 2026-02-27
**Status:** completed
**Scope:** kernel_verify.py, cli.py, tests

## Background

KGen's generated verification kernels compare computed values against captured
reference state and report a per-case verdict: PASSED or FAILED. That binary
output is sufficient for go/no-go decisions but provides zero visibility into
what is actually being compared. When a case fails, you cannot tell which field
diverged, by how much, or whether the mismatch is a genuine bug vs. a
floating-point tolerance issue. Even when all cases pass, there is no way to
confirm that the right data is flowing through the C++ translation — you are
trusting that KGen wired everything correctly without being able to inspect the
values.

This is especially problematic during early development when the interface
generator, type mappings, and kernel patching are all in flux. A "40/40 passed"
result is only meaningful if you can verify that the fields being compared are
the ones you expect, with the values you expect.

## Implementation

### Injecting WRITE Statements into KGen's Verification Code

VIT already patches KGen-generated Fortran source to wire in the C++ function.
Field-level logging extends this patching: after the existing verification
patches are applied, a second pass scans the generated code for `diff` and
`rmsdiff` variable declarations, which KGen emits for every field comparison.
Each such declaration indicates a scalar or array field being verified.

For each detected field, VIT injects tagged WRITE statements:

- **Scalars** get `[VIT_FIELD]` tags: field name, computed value, reference
  value, and PASS/FAIL status.
- **Arrays** get `[VIT_ARRAY]` tags: field name, element count, and aggregated
  status. Individual element values are not logged (would be thousands of lines
  for large arrays), but the array-level pass/fail and diff magnitude are
  captured.
- **CHARACTER fields** are handled separately since they use string comparison
  rather than numeric diff.

The tag format is machine-parseable:
```
[VIT_FIELD] case=3 name=saturate_result type=scalar status=PASS computed=25.000000 reference=25.000000 diff=0.000000E+00
[VIT_ARRAY] case=3 name=xData type=array status=PASS elements=10
```

### Parsing and Structured Output

The `_parse_field_data()` function in kernel_verify.py extracts tagged lines
from kernel.exe stdout and builds a list of dictionaries, one per field per
case. These are stored as `field_data` on the `VerifyResult` dataclass (which
previously only held case-level pass/fail counts).

After verification completes, the field data is written to
`verify_fields.csv` in the kernel directory. The CSV columns are: case, field,
type, status, computed, reference, diff. For ROSCO's saturate function (a
simple min/max clamp with 20 invocations x 2 directions = 40 cases), this
produces approximately 9,200 entries — every scalar and array comparison across
all cases.

### CLI Integration

- The `vit verify` and `vit run` commands now emit a summary line showing how
  many fields were logged.
- A `--no-field-log` flag suppresses field-level logging entirely. This is
  useful for CI or repeated runs where the CSV is not needed and the extra
  WRITE statements add measurable I/O overhead to kernel execution.

### Idempotency

The patching checks for existing `[VIT_FIELD]` tags before inserting. If
field-level logging was already injected (e.g., re-running verify on an already
patched kernel), the injection is skipped. This prevents duplicate WRITE
statements that would corrupt the CSV output.

## Decisions

- **Per-field granularity, not per-element**: For arrays, only aggregate status
  is logged. Logging every element would balloon output for functions with large
  array arguments (interp1d has variable-length xData/yData). The aggregate is
  sufficient for debugging — if an array fails, you know which array, and can
  then inspect the binary state files directly.

- **CSV over JSON**: CSV was chosen for the output format because it opens
  directly in spreadsheets and is trivially greppable. The data is flat (no
  nesting), so CSV's limitations don't apply.

- **Tagged output over separate files**: Rather than writing a separate log
  file from the Fortran kernel, tagged lines are interleaved with KGen's
  normal stdout and parsed out. This avoids needing to pass file paths into
  the generated Fortran and keeps the patching simple (just WRITE statements).

## Verification

- 148 unit tests passing after this change.
- Verification regression: saturate 40/40, wrap_180 40/40.
- CSV output manually inspected to confirm correct field names, values, and
  status for all saturate cases.

## Files Changed

- `vit/vit/kernel_verify.py`: Field detection, WRITE injection, output parsing,
  CSV generation.
- `vit/vit/cli.py`: `--no-field-log` flag, field count summary in output.
- `vit/tests/test_kernel_verify.py`: Tests for field detection, WRITE injection,
  tag parsing, and CSV output. Tests use synthetic Fortran snippets rather than
  full ROSCO kernel code.

## Commit

59ca876 — 3 files changed, 516 insertions(+), 9 deletions(-)
