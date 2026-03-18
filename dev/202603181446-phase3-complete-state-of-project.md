# State of the Project After Phase 3
**Date:** 2026-03-18
**Status:** completed
**Scope:** Project-wide reflection

## What We've Built

### The VIT Tool

VIT is a complete pipeline for verified Fortran→C++ translation. Every step is automated and deterministic:

1. **Parse** — extract function signatures, argument types, intents, array dims, OPTIONAL, USE statements from Fortran source
2. **Extract** — instrument Fortran via KGen, run simulation, capture runtime state (inputs/outputs) for every call to the target function
3. **Translate** — generate C++ scaffold with correct signature, metadata header, struct typedefs, and LLM prompt with translation tips
4. **Verify** — wire C++ into KGen's standalone kernel, compile, run, compare against captured Fortran state (40 cases = 20 invocations × input+output)
5. **Integrate** — generate and apply patches: C++ source file with extern "C" wrapper, Fortran wrapper with BIND(C) interface, CMakeLists.txt updates

The interface generation handles: scalar by-value and by-reference, fixed-size arrays, assumed-shape arrays with synthetic size params, array-valued function results (FUNCTION→void SUBROUTINE), derived type struct pointers (TYPE(C_PTR)/C_LOC/TARGET), OPTIONAL flag+value decomposition, LOGICAL conversion, kind alias resolution.

KGen kernel patching handles: USE kgen_utils_mod ONLY removal, USE ordering, verboseLevel override, NaN==NaN comparison patching (IEEE 754 guard for uninitialized struct fields), per-field value logging with CSV output.

### Translated Functions

| # | Function | Module | Key Features | Phase |
|---|----------|--------|-------------|-------|
| 1 | saturate | Functions | Scalar IN, REAL return | 1 |
| 2 | wrap_180 | Functions | Scalar IN, REAL return | 1 |
| 3 | wrap_360 | Functions | Scalar IN, REAL return | 1 |
| 4 | ColemanTransform | Functions | Subroutine, fixed array(3) IN, scalar OUT | 2 |
| 5 | ColemanTransformInverse | Functions | Subroutine, fixed array(3) OUT | 2 |
| 6 | identity | Functions | Array-valued result DIMENSION(n,n), integer IN | 2 |
| 7 | ratelimit | Functions | Derived type (rlParams), OPTIONAL, LOGICAL, scalar INOUT in FUNCTION | 3 |

All 7 verified 40/40. Full clean build test passes: extract all from clean source → verify all → integrate all → rebuild → byte-identical simulation diff.

### What These 7 Functions Have in Common

They are all **self-contained, low-argument-count functions** in Functions.f90 (plus identity in ControllerBlocks.f90). The most complex signature is ratelimit with 8 args including one simple derived type. None of them take the large, pervasive types that dominate the rest of the codebase: LocalVariables, ControlParameters, ErrorVariables, FilterParameters.

They represent roughly 5% of the codebase's function count but validated the entire pipeline end-to-end — every VIT capability was built to translate one of these 7 functions.

## What Remains

### The Type System Gate (~48 functions blocked)

Every remaining function takes at least one complex derived type argument. The distribution:

- **ErrorVariables only** (~5 functions): interp1d, sigma, unwrap, interp2d, plus AeroDynTorque (which also needs ControlParameters + PerformanceData + LocalVariables). ErrorVariables needs one field fix: `CHARACTER(:), ALLOCATABLE :: ErrMsg` → `CHARACTER(1024)`.

- **FilterParameters** (~7 functions): LPFilter, SecLPFilter, SecLPFilter_Vel, HPFilter, NotchFilter, NotchFilterSlopes, PreFilterMeasuredSignals. FilterParameters is already C-compatible (24 arrays of double[1024]) but needs C struct generation and nested type recursion support (it's nested inside LocalVariables).

- **LocalVariables** (~22 functions): All Controllers and ControllerBlocks. LocalVariables has 168 fields including 5 nested structs, 2 LOGICALs, 1 COMPLEX, 1 CHARACTER ALLOCATABLE. Needs 4 automated field fixes + BIND(C).

- **ControlParameters/PerformanceData** (~14 functions that access ALLOCATABLE array fields): Need the pointer+size view pattern — a new architectural concept where C++ accesses Fortran-allocated memory through a parallel "view struct."

### Phase 4A-4D: The Type System Solution

Phase 4 has been decomposed into 4 sub-phases that interleave with translation work:

```
4A (ErrorVariables)  ──→ Phase 5 first batch (interp1d, sigma, unwrap, interp2d)
4B (Simple structs)  ──→ Phase 6 Filters (7 functions)
4C (LocalVariables)  ──→ Phase 7 Controllers + Phase 8 ControllerBlocks
4D (View structs)    ──→ AeroDynTorque + full ControlParameters access
```

### The Automation Imperative

All Fortran source modifications are automated by VIT (`vit analyze-types --fix`). This is a firm design decision, not a nice-to-have. The LOGICAL→INTEGER fix alone touches 65 references across 5 files. Manual editing is:
- Error-prone (miss one `.NOT. restart` → silent logic bug)
- Not reproducible (Phase 12 targets other codebases)
- Not verifiable (no way to confirm completeness without tooling)

The fix patterns are general:
- LOGICAL → INTEGER(C_INT) with boolean idiom rewriting
- CHARACTER(:), ALLOCATABLE → CHARACTER(N) with ALLOCATE removal
- COMPLEX → split re/im REAL fields with arithmetic rewriting
- Add BIND(C) with default initializer removal

### Key Risk: Source Rewriting Correctness

The `analyze-types --fix` automation is the riskiest new VIT capability. It rewrites Fortran source — both type definitions and all downstream `%fieldname` references. Getting the LOGICAL boolean idiom rewrites wrong could silently break simulation correctness. The COMPLEX→split rewrite is particularly tricky because AWC_complexangle uses real complex arithmetic (EXP, multiplication with imaginary unit).

Mitigation: unit tests with real ROSCO patterns (not just synthetic snippets), plus the simulation diff as the ultimate validation gate.

### Beyond Phase 4: Volume Translation

Once all types are C-passable, the remaining work is volume translation — each function follows the established pipeline. The VIT tooling shouldn't need major new capabilities. Phases 7-8 translate ~30 functions across Controllers and ControllerBlocks. Phase 9 transfers data ownership from Fortran to C++. Phase 10 handles I/O with new verification modes (file comparison, structured diff). Phase 11 removes Fortran and produces a pure C++ libdiscon.so.

## Workflow Lessons Learned

1. **Extraction requires clean source.** KGen captures current file state. Pre-integrated source → kernel has unresolved `_c` function calls. Reset to upstream (`git checkout e8010f0`) before extracting.

2. **Golden kernel fixtures persist.** Once extracted from clean source, reused indefinitely for fast regression. Only re-extract when extraction logic or KGen changes.

3. **Integration is cumulative and idempotent.** `vit integrate --apply` reads current file state, skips what's already present. Run all integrations sequentially from clean source.

4. **The simulation diff is the definitive test.** Unit tests catch component-level bugs. Kernel verification catches translation errors. But `diff baseline_output.txt integrated_output.txt` is the only test that proves the entire system works end-to-end.

5. **Build the tool, then use it.** Phase 3 built derived type auto-discovery and immediately used it for ratelimit. Phase 4 builds `analyze-types` incrementally and uses each capability as it's added. Don't front-load tooling — build just enough for the next function.
