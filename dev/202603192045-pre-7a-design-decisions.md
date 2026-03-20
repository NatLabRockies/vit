# Pre-Phase 7A Design Decisions: ReadSetParameters Sequencing + C++→C++ Call Path
**Date:** 2026-03-19
**Status:** completed
**Scope:** Architecture — two design questions resolved before Phase 7A begins

## Background

The post-Phase 4C strategic assessment raised two architectural questions that needed resolution before translating Controller functions:

1. Should ReadSetParameters translation come earlier than Phase 10? It populates ControlParameters — the type we're about to design a pointer+size view struct for in Phase 4D.
2. When both the caller and callee are translated C++, how do they call each other? The current integration puts a Fortran wrapper around every translated function, but C++ can't call Fortran wrappers.

These were design questions first, implementation questions second. The design conversation happened in plan mode; the implementation followed immediately.

## Question 1: ReadSetParameters Sequencing

### Investigation

Explored `ReadControlParameterFileSub` (~560 lines in ReadSetParameters.f90) to understand how ControlParameters gets populated:

- **Pattern:** Scalar size fields are parsed first (`F_NumNotchFilts`, `VS_n`, `PC_GS_n`), then `ParseAry()` allocates and fills the corresponding ALLOCATABLE arrays using those sizes
- **2D arrays** (`OL_CableControl`, `OL_StructControl`) use explicit ALLOCATE with dimensions from different sources — one from a scalar, one from `SIZE()` of another array
- **All CHARACTER fields** in ControlParameters are already fixed-length (`CHARACTER(1024)`) — no Phase 4A-style fix needed
- **ReadAvrSWAP** does NOT touch ControlParameters (INTENT(IN)) — only reads into LocalVar
- **ReadCpFile** populates PerformanceData (Cp/Ct/Cq tables)

### Decision: Read deeply during 4D, don't translate yet

The exploration revealed that understanding ReadControlParameterFileSub is essential for Phase 4D — the size-field-to-ALLOCATABLE-field pairings determine the view struct's parameter names. But we don't need to *translate* ReadSetParameters to get that understanding. We just need to read it.

**No phase reordering.** Added a note to Phase 4D in PROJECT_PLAN.md: map every ALLOCATABLE field to its size scalar before designing the view struct. The actual ReadSetParameters translation stays in Phase 10.

ReadAvrSWAP (~115 lines, pure array unpacking, no allocation, INTENT(IN) on CntrPar) could be an early Phase 10 pathfinder, but there's no urgency.

## Question 2: C++→C++ Call Path

### The Problem

Every translated function gets a Fortran wrapper:
```
Fortran caller → Fortran wrapper (ISO_C_BINDING) → extern "C" stub → C++ implementation
```

This works when the caller is Fortran. But when the caller is also translated C++ (e.g., PitchControl calling PIController after both are translated), the C++ implementation has no way to call the callee. It can't call the Fortran wrapper (it's C++). It can't call `picontroller_c()` because there's no header declaring it.

The current translations are all **leaf functions** — they don't call other translated functions. `ratelimit.cpp` even inlines the saturate logic rather than calling `saturate_c()`. This was pragmatic during early phases but won't scale to Controllers, where PitchControl calls PIController, ratelimit, LPFilter, and saturate.

### Considered Approaches

1. **Keep wrappers everywhere, strip in Phase 11.** The approach from the earlier strategic assessment. Problem: C++ functions *literally cannot call* through Fortran wrappers. This isn't a performance question — it's a "this doesn't compile" question.

2. **Round-trip through Fortran somehow.** C++ calls a C stub that calls Fortran that calls the wrapper. Absurd complexity for zero benefit.

3. **Shared C header with `_c` declarations.** Each integrated function already has an `extern "C"` entry point (`saturate_c`, `picontroller_c`, etc.). Generate a shared header that declares all of them. C++ translations `#include` this header and call `_c` directly.

### Decision: Shared C header (option 3)

This is the simplest correct solution:
- **Fortran callers** → use the wrapper (unchanged, no code changes to existing integrations)
- **C++ callers** → `#include "vit_translated.h"` and call `function_c()` directly
- **No Fortran round-trip** — C++ talks to C++ through the C ABI
- **Phase 11** strips the Fortran wrappers; C++ still calls `_c` directly (or renames to drop the suffix)

### Implementation

Added to `integrate.py`:

**`_generate_c_declaration(sig)`** — generates a single-line C function declaration from a parsed Fortran signature:
```c
double saturate_c(double inputValue, double minValue, double maxValue);
```

**`append_header` patch action** — `generate_integration_plan()` now includes a patch that creates or updates `vit_translated.h`. The `_apply_header_patch()` function:
- Creates the file with include guard and `extern "C"` block on first integration
- Appends new declarations before `#endif` on subsequent integrations
- Skips if the declaration is already present (idempotent)

The header structure:
```c
#ifndef VIT_TRANSLATED_H
#define VIT_TRANSLATED_H

#ifdef __cplusplus
extern "C" {
#endif

double saturate_c(double inputValue, double minValue, double maxValue);
double wrap_180_c(double x);
// ... one line per integrated function

#ifdef __cplusplus
}
#endif

#endif // VIT_TRANSLATED_H
```

The `#ifdef __cplusplus` guard means the header works from both C and C++ — important because the `_c` functions use C linkage.

### What Happens in Phase 7A

PIController, PIDController, PIIController, ResController are self-contained — they don't call other translated functions internally. So the shared header isn't strictly needed for Phase 7A's own translations. But:

1. Integrating them will populate `vit_translated.h` with their `_c` declarations
2. When Phase 7B translates PitchControl, the scaffold will `#include "vit_translated.h"` and the C++ can call `picontroller_c()` directly
3. The infrastructure is proven before it's needed

### What Remains for `translate.py`

The plan includes updating `vit translate` to detect already-translated callees from the `vit.yaml` translations registry and include their `_c` declarations in the scaffold. This wasn't implemented yet — it's a Phase 7A task (the scaffold needs to know about the header). The header generation in `integrate.py` is the prerequisite; the scaffold integration follows.

## Tests Added

5 new tests in `test_integrate.py::TestSharedHeader`:
- `test_c_declaration_for_function` — correct `extern "C"` declaration format
- `test_plan_includes_header_patch` — plan contains an `append_header` patch
- `test_apply_creates_header_file` — first integration creates `vit_translated.h` with include guard
- `test_apply_appends_to_existing_header` — second integration adds to existing file
- `test_apply_is_idempotent` — same integration twice doesn't duplicate

451 total tests pass.

## Documentation Updates

- **PROJECT_PLAN.md Phase 4D:** Note to read ReadControlParameterFileSub before view struct design
- **PROJECT_PLAN.md Phase 7A:** Shared C header generation item added
- **PROJECT_PLAN.md Phase 7B:** Notes that C++ controllers call `_c` directly via header
- **CLAUDE.md Known Limitations:** New "C++ → C++ call path" section documenting the strategy

## Related Notes
- [202603192020-post-4c-strategic-assessment.md](202603192020-post-4c-strategic-assessment.md) — Where these questions originated
- [202603191827-phase4c-localvariables-c-compatible.md](202603191827-phase4c-localvariables-c-compatible.md) — Phase 4C that unblocked this work
