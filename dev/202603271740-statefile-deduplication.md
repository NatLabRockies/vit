# Deduplicate kgen_statefile.lst After Extraction
**Date:** 2026-03-27
**Status:** completed
**Scope:** VIT extraction post-processing

## What Changed

Added a deduplication step to `run_kgen_extract()` in `extract.py`. After KGen extraction completes and state cases are counted, VIT now removes duplicate entries from `kgen_statefile.lst`:

```python
lst = Path(result["kernel_dir"]) / "kgen_statefile.lst"
if lst.exists():
    lst.write_text('\n'.join(dict.fromkeys(lst.read_text().splitlines())) + '\n')
```

This preserves entry order and removes duplicates. A list with 40 entries (cases 1-20 listed twice) becomes 20 entries.

## Why

KGen's ElapsedTime phase appends profiling-selected invocation triples to the CLI-specified triples instead of replacing them (see dev note `202603271457`). When both sets cover the same invocations — which they do for ROSCO functions because the timing distribution is too skewed for bin stratification to produce different selections — the generated `kgen_statefile.lst` contains every case twice.

The kernel driver iterates this list and replays each entry, so duplicates cause every case to be verified twice. The result is harmless (identical replays produce identical results) but wasteful: ~2x verification time, inflated field logs (9,240 entries instead of 4,620 for saturate), and a misleading "40/40 passed" count when only 20 cases are being tested.

With deduplication, verification correctly reports "20/20 passed" and runs in half the time.

## Tests Added

4 tests in `tests/test_extract.py` class `TestStatefileDeduplication`:
- `test_removes_duplicates` — exact duplicate block (the common case)
- `test_preserves_order_with_partial_overlap` — ElapsedTime selects slightly different invocations
- `test_no_duplicates_unchanged` — clean list passes through unchanged
- `test_empty_lines_removed` — empty lines are stripped during dedup

## Related Changes (Same Session)

- **ElapsedTime build fix** (commit `54e7337`): `cleanup_kgen_utils_injection()` now reconfigures CMake after cleanup to clear stale `DependInfo.cmake` references. Also cleans `model/` directory between extractions so ElapsedTime profiles the correct function.
- **Root cause analysis** (dev note `202603271457`): Full investigation of why the list has duplicates — traced to `elapsedtime/main.py:471` appending instead of replacing.
