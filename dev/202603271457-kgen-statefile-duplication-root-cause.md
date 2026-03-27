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

1. **ElapsedTime phase**: Instruments the source with timing probes, builds, runs the simulation. Profiles all invocations of the target function (~80,000 for saturate across all scenarios). Selects `ndata` (default: 20) representative invocations via bin-stratified sampling. Writes results to `model.ini`.

2. **Extractor phase**: Generates the kernel and instrumented source for state capture. Builds, runs the simulation. Captures input/output state at the selected invocations.

### How Invocation Triples Accumulate

**Step 1 — CLI parsing** (`kgconfig.py:821-842`): The `--invocation 0:0:1-20` argument is parsed into one triple: `[('0','0'), ('0','0'), ('1','20')]`. This represents a **range** — MPI rank 0, OpenMP thread 0, invocations 1 through 20. Appended to `Config.invocation['triples']`.

**Step 2 — ElapsedTime profiling** (`elapsedtime/main.py:428-473`): After profiling, ElapsedTime distributes the observed invocations into `nbins=5` time-based bins and selects ~20 representative invocations. For each selected invocation, it creates an individual triple like `[('0','0'), ('0','0'), ('5','5')]` (a single invocation, not a range). These are **appended** to `Config.invocation['triples']`:

```python
# elapsedtime/main.py:471-473 — THE BUG
for ranknum, threadnum, invokenum in triples:
    Config.invocation['triples'].append( ( (str(ranknum), str(ranknum)), (str(threadnum), str(threadnum)), \
        (str(invokenum), str(invokenum)) ) )
```

After this, `Config.invocation['triples']` contains **21 entries**: the original CLI range triple (covering invocations 1-20) plus 20 individual triples from profiling.

### Confirmed by Instrumentation

Added a debug write to `gen_state_callsite_file.py:create_callsite_parts1()` to dump the triples list at code generation time. Fresh extraction of `saturate` produced:

```
create_callsite_parts1 called with 21 triples:
  triple: [('0', '0'), ('0', '0'), ['1', '20']]        ← CLI range
  triple: (('0', '0'), ('0', '0'), ('1', '1'))          ← ElapsedTime individual
  triple: (('0', '0'), ('0', '0'), ('2', '2'))
  triple: (('0', '0'), ('0', '0'), ('3', '3'))
  ...
  triple: (('0', '0'), ('0', '0'), ('20', '20'))
```

### Why ElapsedTime Selects Invocations 1-20 (Not High-Numbered Ones)

Despite having ~80,000 profiled invocations to choose from, ElapsedTime always selects invocations 1 through 20. This is because the selection algorithm at `elapsedtime/main.py:451` iterates each bin's invocations in **sorted ascending order**:

```python
for invokenum in sorted(etimebin.keys()):
    if len(bin_triples) >= datacollect[binnum]: break
    ...
```

With `ndata=20` distributed across 5 bins (~4 per bin), each bin picks its lowest-numbered invocations first. Since invocations 1-20 are the first controller calls in the simulation — spanning the timing range because the controller initializes differently on the first call vs steady state — they are distributed across the timing bins and consistently win the "lowest first" selection.

The result: ElapsedTime independently selects the exact same invocations (1-20) that the CLI already specified. This would be true even for complex functions like PitchControl or FlapControl — confirmed by checking all 28 golden fixtures, every one has cases 1-20 doubled with no high-numbered invocations.

This `sorted()` bias arguably defeats the purpose of ElapsedTime's bin-stratified sampling. The intent is to select invocations representative of different execution profiles (fast calls, slow calls), but by always picking the lowest-numbered from each bin, it misses the invocations with the most interesting timing characteristics deeper in the simulation.

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

### Note on `model/` Directory Persistence

VIT's pre-extraction cleanup (`extract.py:239-251`) deletes `model.ini`, `kgen.log`, `strace.log`, and the `elapsedtime/` directory. However, it does NOT delete the `model/` directory, which contains ElapsedTime's raw profiling data at `model/__data__/1/0.0`. KGen's ElapsedTime phase detects this existing data and reuses it ("Reusing elapsedtime raw data" in kgen.log), skipping the profiling simulation run but still reading the data, writing `model.ini`, and selecting invocation triples. This means the profiling data from the **first** extraction persists across all subsequent extractions, and the `sorted()` bias produces the same 1-20 selection every time.

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
| `KGen/kgen/elapsedtime/main.py:451` | `sorted()` bias: always picks lowest-numbered invocations from each bin |
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
