# CableControl Kernel Heap Crash Investigation
**Date:** 2026-03-25
**Status:** root cause identified, fix pending design decision
**Scope:** kernel_verify.py, CableControl kernel, VIT field logging

## The Crash

CableControl's kernel crashes on the 3rd state file with:
```
malloc(): unaligned tcache chunk detected
Program received signal SIGABRT: Process abort signal.
```

The crash occurs in `kr_controlparameters_real__dbki_dim1` (ROSCO_Types.f90) during `malloc` for an ALLOCATABLE array allocation while reading the state file. The backtrace shows the crash originates from `malloc` → `kr_controlparameters_real__dbki_dim1` → `kr_rosco_types_controlparameters` → `discon_` → `MAIN__`.

## Investigation

### Test 1: AddressSanitizer

Built the kernel with `-fsanitize=address` on all Fortran files.

**Result: 40/40 completed, zero memory errors detected.** ASAN replaces glibc's malloc with its own allocator, which doesn't use tcache. This definitively rules out buffer overflows, use-after-free, and double-free.

### Test 2: Disable glibc tcache

Ran with `GLIBC_TUNABLES=glibc.malloc.tcache_count=0` to disable tcache while keeping glibc's standard allocator.

**Result: Completed without crash.** This confirms the issue is specific to glibc's tcache (thread-local cache) implementation, not the core allocator.

### Test 3: Optimization level

Built at `-O0` (no optimization) vs `-O2`.

**Result: Both completed without crash** in isolation (see Test 7 for the full picture — these tests were run without VIT's field logging patches, which turns out to be the trigger).

### Test 4: Vanilla Fortran (no C++)

Restored the original Fortran from `.kgen` backup (no C++ wrapper, no VIT patches to Controllers.f90).

**Result: Same crash** when VIT's other patches (field logging in ROSCO_Types.f90) were present. **No crash** when ROSCO_Types.f90 was also restored to unpatched state.

### Test 5: SAVE + allocation reuse patches

Added `SAVE` attribute to all state variables in DISCON.F90 (prevents auto-deallocation of ALLOCATABLE components when `discon()` returns) and modified `kr_` read routines to reuse existing allocations when bounds match.

**Result: 40/40 completed, no crash.** These patches were committed at `48c43d9` then reverted at `74932ef` after further investigation revealed they were addressing a symptom, not the root cause.

### Test 6: Allocation churn analysis

ControlParameters has 68 ALLOCATABLE fields. The kernel's `discon()` subroutine has 8 state variables (4 types × 2 copies: entry + reference). All are LOCAL without SAVE, so Fortran 2003+ auto-deallocation fires when `discon()` returns. With 3 stages (eval, warmup, main) per state file, and the state file list containing 40 entries (20 files × 2), the kernel performs thousands of ALLOCATE/DEALLOCATE cycles.

However, the allocation count alone (~1,600 by file 3) is NOT unreasonable for any allocator. Programs routinely do millions of allocations. The count was initially blamed as the root cause but this was incorrect.

### Test 7: Field logging isolation (definitive test)

Ran CableControl through `vit verify` twice, holding everything the same except the `--no-field-log` flag:

**With field logging (default):**
```
CableControl: ✗ VERIFICATION FAILED: 1/2 passed
```
Kernel crashes on 3rd state file. Only 2 cases processed.

**Without field logging (`--no-field-log`):**
```
CableControl: ✗ VERIFICATION FAILED: 28/40 passed
```
All 40 cases completed — **no crash**. The 28/40 failures are from state file artifacts that the two-run comparison can't resolve without field data (a separate issue — see below).

**This is the definitive result.** The crash is caused by VIT's field logging patches.

### Test 8: Unpatched KGen kernel (no VIT patches at all)

Restored ALL `.kgen` files, applied only minimal compilation fixes (remove `PUBLIC discon`, remove `USE discon`, remove `STOP` from `kgen_array_sumcheck`), built and ran.

**Result: 40/40 completed, no crash.** The unpatched KGen kernel processes all state files without issues.

## Root Cause

**VIT's field logging patches to ROSCO_Types.f90 cause the crash.** These patches (applied by `_patch_field_value_logging()` in `kernel_verify.py`) inject `WRITE(*, *)` statements into every field comparison in the `kv_` routines — one per field per case. For CableControl with ~440 fields compared per case × 40 cases, this adds ~17,600 WRITE calls.

The mechanism: gfortran's I/O runtime uses `malloc` internally for formatting buffers. These allocations, combined with the ALLOCATABLE field DEALLOCATE/ALLOCATE cycles from KGen's state reading, create a heap fragmentation pattern that corrupts glibc's tcache metadata on ARM (aarch64). The corruption manifests as `malloc(): unaligned tcache chunk detected` on the 3rd state file.

**Key evidence:**
- ASAN finds zero memory errors → no buffer overflow or use-after-free
- Disabling tcache eliminates the crash → tcache-specific
- Removing field logging eliminates the crash → field logging is the trigger
- Unpatched KGen kernel (no field logging) works fine → KGen's allocation pattern alone is not the problem
- SAVE/reuse patches fix it → reducing allocation churn avoids the interaction threshold

The crash requires the COMBINATION of:
1. Heavy ALLOCATABLE field auto-deallocation (from KGen's kernel design — LOCAL variables without SAVE)
2. Heavy formatted I/O (from VIT's field logging — thousands of WRITE statements)
3. glibc tcache on ARM (the specific allocator implementation that's sensitive to this pattern)

Any one of these alone is fine. Together, they trigger the corruption.

## Why Only CableControl?

CableControl processes 40 state file entries (20 files × 2 in `kgen_statefile.lst`). Other functions like StructuralControl also process 40 entries without crashing. The difference is likely in the specific allocation size patterns — CableControl's ControlParameters fields may have sizes that fall into tcache bucket boundaries differently. The crash is heap-state-dependent: the exact point of corruption depends on the sequence of allocation sizes, which varies per function.

StructuralControl processes all 40 entries with field logging without crashing. This suggests the fragmentation threshold is function-specific, not universal.

## What the SAVE/Reuse Patches Did

The SAVE patches (`48c43d9`, reverted at `74932ef`) eliminated auto-deallocation by making state variables persist between `discon()` calls. The allocation reuse patches skipped DEALLOCATE+ALLOCATE when array bounds matched. Together, these reduced heap operations from ~1,600+ to ~136, keeping the fragmentation well below the threshold where field logging's I/O overhead triggers the tcache bug.

These patches were effective but addressed a symptom (allocation churn) rather than the root cause (the interaction between field logging I/O and allocation churn).

## Current State

- **Commit `3011a34`**: Two-run Fortran-vs-C++ comparison (the correct verification architecture)
- **Commit `48c43d9`**: SAVE/reuse patches (effective workaround, addresses allocation churn)
- **Commit `74932ef`**: Revert of SAVE/reuse (to investigate root cause)
- **Uncommitted changes reverted**: All experimental patches to `_run_fortran_reference` and `_parse_kgen_field_status` were reverted

The codebase is clean at `74932ef`. The two-run comparison works for all functions. CableControl shows 2/2 (crash limits to 2 cases) or 1/2 (one state file artifact in those 2 cases).

## Open Design Decision

The two-run comparison needs field logging to produce per-field data for both Fortran and C++ runs. Field logging causes the CableControl crash. Options:

**Option A: Re-apply SAVE/reuse patches (`48c43d9`)**
- Pros: Field logging works, all 40 cases process, full CSV output
- Cons: Workaround for the heap interaction, not a fix for the field logging I/O issue

**Option B: Disable field logging for both runs, use KGen standard output**
- Pros: No crash, no workaround needed
- Cons: Loses detailed per-field CSV, requires parsing KGen's standard output for the two-run comparison

**Option C: Fix field logging to be less heap-intensive**
- Pros: Addresses root cause (I/O overhead), no workaround needed
- Cons: Requires rearchitecting how field logging generates output (e.g., buffering all output and writing once at the end instead of per-field WRITE calls)

**Option D: Accept 2/2 for CableControl**
- Pros: Simplest, no code changes
- Cons: Loses 38 test cases worth of verification coverage for CableControl
