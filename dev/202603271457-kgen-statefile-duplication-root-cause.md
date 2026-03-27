# KGen kgen_statefile.lst Duplication: Root Cause Analysis
**Date:** 2026-03-27
**Status:** completed
**Scope:** KGen extraction internals, kernel verification count accuracy

## Origin

This investigation began during a Q&A discourse validating the verification pipeline (dev note `202603271332`). After confirming that the verification is substantive (broken C++ = 0/40, correct = 40/40), the user asked a deceptively simple question: "If it is 20 for the Fortran kernel, and 20 for the C++ kernel, then why was it 0/40 when we just broke the C++ translation?"

This question exposed that the "40/40" count had never been properly understood. The initial assumption — 20 Fortran runs + 20 C++ runs = 40 — was wrong. What followed was a multi-layered investigation that ultimately traced the 40 to a bug in KGen's ElapsedTime phase.

## Investigation: Where Does "40" Come From?

### Layer 1: Is the 40 from two runs?

VIT verification runs the kernel twice: once with original Fortran (reference), once with C++ (under test). Each kernel.exe execution replays captured invocations. If each run produced 20, the total would be 40.

**Finding:** Wrong. The Fortran reference run's pass/fail counts are **discarded** (`_run_fortran_reference` at `kernel_verify.py:350-351` returns only `result.field_data`). The "40" comes entirely from the C++ run's output, parsed by `_parse_verify_output`.

### Layer 2: Does the kernel replay each case multiple times?

Examining the kernel driver (`kernel/saturate/kernel_driver.f90`), KGen calls the function **three times** per case:

1. **`kgen_evalstage`** (line 66-78): Correctness check
2. **`kgen_warmupstage`** (line 80-92): CPU cache warmup
3. **`kgen_mainstage`** (line 94-108): Timed measurement run

KGen is a **kernel timing tool** — the three stages are standard benchmarking methodology. Only `kgen_mainstage` emits "Verification PASSED/FAILED with" lines (confirmed at `Controllers.f90:114-151`). The eval and warmup stage blocks are empty after the function call (lines 100-103, 165-168).

So each case should produce 1 verification line per kernel.exe execution, for 20 lines total. But running `./kernel.exe | grep -c "Verification PASSED with"` returned **40**.

### Layer 3: Is each case replayed twice?

```bash
./kernel.exe 2>&1 | grep "Verification PASSED with" | sort | uniq -c
```

Every case appeared exactly **twice**. Each case emits 2 "Verification PASSED with" lines in a single kernel.exe run.

### Layer 4: The kernel driver iterates `kgen_statefile.lst`

The kernel driver (line 53) reads state file paths from `kgen_statefile.lst` in a `DO WHILE` loop. Each entry gets the full 3-stage treatment (eval → warmup → main). If a case appears twice in the list, it gets replayed twice.

```bash
cat kernel/saturate/kgen_statefile.lst | sort | uniq -c | head -5
```

Every entry appeared **twice**. The file contained 40 lines: cases 1-20, then cases 1-20 again.

### Layer 5: Proof by deletion

Recreated the list from the actual state directories:
```bash
ls -1 saturate.*.*.* > kgen_statefile.lst   # produces 20 entries
```

Re-ran verification: **20/20 passed** (with 4,620 field entries instead of 9,240 — exactly half).

This confirmed: the 40/40 is an artifact of duplicate entries in `kgen_statefile.lst`, not an intentional design.

## Root Cause: KGen ElapsedTime Appends Instead of Replaces

### KGen's Two-Phase Extraction

KGen extraction runs in two sequential phases within a single invocation (`kgen.py:61-76`):

1. **ElapsedTime phase**: Instruments the source with timing probes, builds, runs the simulation. Profiles ~40,000 invocations of the target function across all scenarios. Selects `ndata` (default: 20) representative invocations via bin-stratified sampling. Writes results to `model.ini`.

2. **Extractor phase**: Generates the kernel and instrumented source for state capture. Builds, runs the simulation. Captures input/output state at the selected invocations.

### How Invocation Triples Accumulate

**Step 1 — CLI parsing** (`kgconfig.py:821-842`): The `--invocation 0:0:1-20` argument is parsed into one triple: `[('0','0'), ('0','0'), ('1','20')]`. This represents a **range** — MPI rank 0, OpenMP thread 0, invocations 1 through 20. Appended to `Config.invocation['triples']`.

**Step 2 — ElapsedTime profiling** (`elapsedtime/main.py:428-473`): After profiling, ElapsedTime distributes ~40,000 observed invocations into `nbins=5` time-based bins and selects ~20 representative invocations. For each selected invocation, it creates an individual triple like `[('0','0'), ('0','0'), ('5','5')]` (a single invocation, not a range). These are **appended** to `Config.invocation['triples']`:

```python
# elapsedtime/main.py:471-473 — THE BUG
for ranknum, threadnum, invokenum in triples:
    Config.invocation['triples'].append( ( (str(ranknum), str(ranknum)), (str(threadnum), str(threadnum)), \
        (str(invokenum), str(invokenum)) ) )
```

After this, `Config.invocation['triples']` contains **21 entries**: the original CLI range triple (covering invocations 1-20) plus 20 individual triples from profiling.

### How Code Generation Creates Duplicates

**Step 3 — Instrumented source generation** (`gen_state_callsite_file.py:1001-1005`): The state capture code generator loops over `getinfo('invocations')` (which returns `Config.invocation['triples']`) and generates one `CALL kgen_write_list(...)` per triple:

```python
for (mpi_s, mpi_e), (openmp_s, openmp_e), (invoke_s, invoke_e) in getinfo('invocations'):
    attrs = {'designator': 'kgen_write_list', 'items': [...]}
    part_append_gensnode(iflst, EXEC_PART, statements.Call, attrs=attrs)
```

This generates 21 `CALL kgen_write_list(...)` statements in the Fortran source. Each call writes entries to the file:
- The range triple `(1,20)` → `kgen_write_list` loops from 1 to 20, writing 20 entries
- Each individual triple `(N,N)` → writes 1 entry

Total: 20 + 20 = 40 entries. The file is opened once with `STATUS="REPLACE"`, so all 40 entries go into the same file.

### `kgen_write_list` Subroutine

Generated by `gen_state_callsite_file.py:310-359`, this subroutine has three nested loops:
```fortran
DO mpi_idx = mpi_s, mpi_e
    DO openmp_idx = omp_s, omp_e
        DO invoke_idx = invoke_s, invoke_e
            WRITE(myunit, '(A)') "saturate." // mpi_idx // "." // openmp_idx // "." // invoke_idx
        END DO
    END DO
END DO
```

For the range triple `(mpi=0-0, omp=0-0, invoke=1-20)`: 1 * 1 * 20 = 20 entries.
For each individual triple `(mpi=0-0, omp=0-0, invoke=N-N)`: 1 * 1 * 1 = 1 entry.

### Why the Entries Are Identical

The range triple covers invocations 1-20. The 20 individual triples selected by ElapsedTime are also from the 1-20 range (since we specified `0:0:1-20` as the capture window). So both sets of writes produce the same 20 entries, just in different order. The result: cases 1-20 listed twice, exactly matching what we observed.

## Impact Assessment

### Is the 40/40 Harmful?

**No.** Each duplicate replay produces identical results:
- Same state inputs → same function call → same outputs → same verification status
- The field-level cross-comparison (`_compare_field_logs`) compares by `(case, field)` key. Duplicate cases overwrite in the lookup dict — the last value wins, and it's identical to the first.
- The only waste is computation time (~2x kernel execution) and inflated field log size (9,240 entries instead of 4,620).

### Is the 40/40 Misleading?

**Yes.** It led us to believe there were 40 "cases" being tested, when in reality there are 20 captured invocations replayed twice. This created confusion about what the denominator represented and whether the Fortran/C++ runs were being counted together.

## The Full Verification Architecture (Corrected)

With the duplication understood, here is the accurate picture:

1. **Extraction**: KGen instruments the source and runs the simulation. 20 invocations are captured as state files (`saturate.0.0.1` through `saturate.0.0.20`). `kgen_statefile.lst` is written with 40 entries (20 duplicated due to the append bug).

2. **Verification Run 1 (Fortran reference)**: VIT restores the kernel's function body to original Fortran from `.kgen` backups. Builds and runs `kernel.exe`. The kernel iterates `kgen_statefile.lst`, replaying 40 entries (20 cases × 2). VIT captures field data from the output. Pass/fail counts are **discarded**.

3. **Verification Run 2 (C++ under test)**: VIT patches the kernel with the C++ wrapper. Builds and runs `kernel.exe`. Same 40 entries replayed. VIT captures field data and pass/fail counts.

4. **Cross-comparison**: VIT compares Fortran field data against C++ field data field-by-field. The duplicate case entries in the field data don't affect the comparison — `_compare_field_logs` builds a `(case, field)` lookup dict, and duplicates simply overwrite with the same values.

5. **Final result**: "40/40 passed" = all 40 KGen per-case lines from the C++ run said PASSED, and the cross-comparison confirmed C++ matched Fortran. Would be "20/20 passed" without the duplication.

## Key Files

| File | Role |
|------|------|
| `KGen/kgen/kgconfig.py:821-842` | CLI `--invocation` parsing, initial triple creation |
| `KGen/kgen/elapsedtime/main.py:471-473` | **The bug**: appends profiling triples instead of replacing |
| `KGen/kgen/kggenfile.py:326` | `getinfo('invocations')` returns the combined triples list |
| `KGen/kgen/extractor/plugins/gencore/gen_state_callsite_file.py:1001-1005` | Code gen loop: one `CALL kgen_write_list` per triple |
| `KGen/kgen/extractor/plugins/gencore/gen_state_callsite_file.py:310-345` | `kgen_write_list` subroutine: nested mpi/omp/invoke loops |
| `ROSCO/kernel/saturate/kernel_driver.f90:53-118` | Kernel driver: iterates `kgen_statefile.lst` in `DO WHILE` loop |
| `ROSCO/kernel/saturate/Controllers.f90:114-151` | Per-case verification print (only in `kgen_mainstage`) |
| `vit/vit/kernel_verify.py:1682-1700` | VIT's `_parse_verify_output`: counts all "Verification PASSED with" lines |

## Fix Options (Not Yet Implemented)

Three approaches were considered:

1. **Fix KGen**: Clear `Config.invocation['triples']` before appending in `elapsedtime/main.py:471`. Root cause fix but changes KGen behavior.

2. **Fix VIT (deduplicate)**: After extraction, deduplicate `kgen_statefile.lst` in `extract.py`. Safe but treats the symptom.

3. **Fix VIT (regenerate)**: After extraction, regenerate `kgen_statefile.lst` from the actual state directories on disk. Most robust — makes VIT independent of KGen's list-generation logic.

Decision on which fix to implement is deferred to the user.
