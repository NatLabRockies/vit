# FMA Hypothesis Confirmed — Bit-Identical with Matched `-ffp-contract=off`
**Date:** 2026-04-24
**Status:** completed
**Scope:** Investigating IN_TOL results from Calculate_Cn verification

## Summary

The IN_TOL results from the previous verification attempt were caused by an FMA mismatch: the state capture simulation was built without `-ffp-contract=off` (gfortran default: `-ffp-contract=fast`, FMA enabled), but the kernel verification used `-ffp-contract=off`. With matched flags in both builds, all 20 cases are **IDENTICAL** — zero differences.

## Investigation

### What we observed

Previous verification showed all 20 cases as IN_TOL with RMS differences ~1e-17 to 1e-18. These sub-ULP differences persisted even after removing the unnecessary `REAL(Cd0, C_DOUBLE)` conversion in the wrapper.

### The hypothesis

The project build flags (from `flags.make`) were:
```
Fortran_FLAGS = -fPIC -ffree-line-length-none -cpp -fstack-reuse=none -fdefault-real-8 -fdefault-double-8 -O3 -DNDEBUG -O3 -J../../ftnmods
```

No `-ffp-contract` flag — gfortran defaults to `-ffp-contract=fast`. The kernel verification used `-ffp-contract=off` (from `fortran.flags` in vit.yaml). The cos/sin computations in Calculate_Cn could be FMA-contracted differently between the two builds.

### The fix

Rebuilt OpenFAST with `-ffp-contract=off`:
```bash
cd /workspace/openfast/build
cmake .. -DCMAKE_Fortran_FLAGS='-ffp-contract=off'
cmake --build . --target aerodyn_driver -j$(nproc)
```

Then re-extracted and re-verified. Result: all 20 cases IDENTICAL.

### The data

Before (mismatched FMA):
```csv
Calculate_Cn.0.0.1,cn,array,IN_TOL,,,n_diff=200 rms=1.31e-18
Calculate_Cn.0.0.2,cn,array,IN_TOL,,,n_diff=200 rms=1.69e-17
```

After (matched `-ffp-contract=off`):
```csv
Calculate_Cn.0.0.1,cn,array,IDENTICAL,,,size=200
Calculate_Cn.0.0.2,cn,array,IDENTICAL,,,size=200
```

20/20 IDENTICAL, 0 IN_TOL.

## Remaining Issue: False Negative from Fortran Reference Comparison

VIT still reports "VERIFICATION FAILED: 20/20 passed" despite all entries being IDENTICAL. The cause is in VIT's two-run comparison infrastructure:

1. VIT runs the kernel with **original Fortran** (Fortran reference) to establish a baseline field log
2. VIT runs the kernel with **C++ translation** to produce the test field log
3. VIT compares the two field logs — they should match exactly

The Fortran reference run fails to produce field log entries because VIT's `[VIT_FIELD]`/`[VIT_ARRAY]` logging patches are in AirfoilInfo.f90 — which is the **target file**. The Fortran reference run restores the target file to its clean KGen state (stripping the logging patches). Result: Fortran produces no field log entries, C++ produces 20 entries, comparison says "present in C++ but not Fortran."

In ROSCO, this wasn't a problem because field logging was patched into the Types file (ROSCO_Types.f90), which is NOT the target file and is preserved during the Fortran reference run. For AeroDyn, the logging patches happen to land in the target file itself.

**This is a VIT infrastructure issue, not a translation correctness issue.** The `--no-field-log` flag works around it by skipping the two-run comparison. The underlying fix requires VIT to preserve field logging patches in the target file during the Fortran reference run (restoring only the function body, not the entire file).

## Parallel to ROSCO

This is the exact same issue ROSCO encountered: `-ffp-contract=off` must be set on both the project build (for state capture) and the kernel build (for verification). ROSCO solved it by adding `-ffp-contract=off` to the project's CMakeLists.txt. For AeroDyn, we passed it via cmake configure flag: `cmake .. -DCMAKE_Fortran_FLAGS='-ffp-contract=off'`.

The long-term fix for AeroDyn: add `-ffp-contract=off` to the OpenFAST CMakeLists.txt (or a VIT-specific cmake preset) for the duration of the translation effort. This is a verification-phase flag, not permanent — it gets removed when the pure C++ build is finalized.

## Lessons Reinforced

1. **FMA mismatch between state capture and kernel verification produces sub-ULP differences** — the same root cause as ROSCO's experience, now confirmed on AeroDyn.
2. **The vit.yaml `preprocessor_defines` field can get lost** if the config is reformatted. The `--no-field-log` verification updated vit.yaml with translation status but dropped the preprocessor_defines section. This caused the re-extraction to use the wrong precision (R4Ki instead of R8Ki).
3. **`-ffp-contract=off` must be set BEFORE extraction**, not just for verification. The state data reflects the flags used during capture.

## Related

- Dev note `202604241122` — first verification (IN_TOL results, hypothesis formed)
- Dev note `202604241051` — VIT adaptation issues catalog
- CLAUDE.md: "If a translation shows IN_TOL instead of IDENTICAL with -ffp-contract=off, it indicates a real translation bug"
