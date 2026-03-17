# Plan Review: Surfacing Assumptions and Six Key Decisions

**Date:** 2026-03-17
**Status:** completed
**Scope:** project planning, design decisions

## Background

After completing the type system investigation and adding Phase 4.5 to the plan, the user asked for a reflection on the character of their feedback throughout the session, then asked me to review PROJECT_PLAN.md and surface any remaining assumptions or details that should be scrutinized before execution.

## Reflection on Feedback Patterns

Three consistent patterns emerged from the user's corrections throughout the session:

1. **No workarounds, no deferrals.** The opaque pointer + accessor API for ControlParameters was rejected in favor of pushing until we found that every type can become a C struct. The "ErrorVariables stays passthrough until Phase 8" framing was corrected — passthrough is the complete solution, not a deferral. Workarounds become permanent; solve problems properly the first time.

2. **If a human has to do it, the tool should do it.** The `analyze-types` insight. Manual analysis is a missed opportunity for a deterministic tool. This is the difference between a one-off project and a reusable capability.

3. **Completeness over incrementalism.** "Nothing is out of scope" was the first correction. The goal is 100% of the problem solved, even if it requires more upfront investigation.

These patterns directly informed the review process: look for workarounds, deferred decisions, manual processes, and scope limitations hiding in the plan.

## Six Issues Surfaced

### 1. Phase 4 Bridge Workaround for AeroDynTorque

**Issue:** Phase 4 used field extraction for AeroDynTorque as a "bridge" to Phase 4.5, where the real struct solution would be built.

**Decision:** Move AeroDynTorque out of Phase 4 to after Phase 4.5. Phase 4 delivers ratelimit only (13/14). AeroDynTorque becomes the first function verified after the type system is complete (14/14). No bridge workarounds.

**Rationale:** Consistent with the "no workarounds" principle. If Phase 4.5 solves the problem properly, AeroDynTorque should use the proper solution.

### 2. Phase 9 I/O Modules — "Outside VIT Pipeline"

**Issue:** The plan said I/O modules (ReadSetParameters, ROSCO_Helpers, ROSCO_IO — ~4,700 lines) are "better translated directly" because I/O logic doesn't have meaningful runtime state to capture. This is the same "out of scope" logic the user corrected earlier.

**Decision:** VIT will develop a capability for verifying I/O translations. The specific design will be explored in a separate session. The question isn't whether to verify I/O translations, but how.

**Rationale:** VIT's core value is proving translations are correct. Exempting I/O code from verification contradicts that value. The verification approach may differ from algorithmic code (struct snapshot comparison vs. field-by-field replay), but verification must exist.

### 3. KGen Risk for New Fortran Patterns

**Issue:** Every new Fortran pattern has surfaced KGen workarounds, but the plan was silent on KGen risk for Phases 2-4 (subroutines, 2D arrays, stateful functions).

**Decision:** Favor workarounds now. Stabilize with rigorous regression tests. Selectively replace workarounds with KGen patches later, case-by-case. The user has already forked KGen and is open to patching it, but wants the system working and proven first before touching KGen internals.

**Rationale:** KGen is battle-tested community software. Workarounds are preferable to upstream modifications until the workaround is proven brittle. Once the full pipeline is running with regression tests, individual workarounds can be evaluated for replacement.

### 4. Who Writes the C++ Translations

**Issue:** The plan said "humans or LLMs write the C++ translations" without specifying the workflow. For 65+ functions, this is significant labor.

**Decision:**
- Claude writes the C++ translations.
- Translations are organized by module: `translations/Functions/saturate.cpp`, `translations/Filters/LPFilter.cpp`, etc.
- Each translation carries metadata: source Fortran file, function signature, hash of original Fortran source.
- VIT will have a `vit translate` command that scaffolds the C++ file with the correct extern "C" signature, includes, and parameter conventions. Claude fills in the algorithm body.
- Designing this command is part of PROJECT_PLAN.md.

**Rationale:** The translation workflow is a core part of the project. Leaving it unspecified means ad-hoc processes. The `vit translate` command ensures translations start from a correct scaffold, reducing errors in boilerplate.

### 5. Fixed-Size Array Max Bounds for ControlParameters

**Issue:** The plan proposed replacing ALLOCATABLE arrays with fixed-size arrays using compile-time max bounds. The user flagged this as a red flag.

**Decision:** Do NOT replace ALLOCATABLE with fixed-size arrays. Instead:
- **During transition:** C++ gets a pointer+size view struct. A thin Fortran BIND(C) routine populates it once at init by copying scalars and taking `C_LOC` of each ALLOCATABLE array. C++ reads through pointers into Fortran-owned memory.
- **Final state:** C++ owns the data with `std::vector<double>` or similar. The config parser (ReadSetParameters, by then translated to C++) allocates naturally.
- **VIT tracks ownership state** of each type and flags when all Fortran consumers are translated, meaning ownership can transfer from Fortran to C++.

**Rationale:** Fixed-size arrays are a workaround that modifies ROSCO's source code, picks arbitrary max bounds, and wastes memory. Pointer+size views give C++ direct read access to Fortran-owned memory with no ROSCO modifications and no arbitrary limits. The ownership tracking makes the transition explicit rather than ad-hoc.

### 6. Column-Major Array Layout

**Issue:** Fortran stores multi-dimensional arrays column-major; C stores row-major. WE has `DIMENSION(3,3)` matrices. The plan noted this but didn't resolve it.

**Decision:**
- The final C++ code uses natural row-major indexing, as if the code was never Fortran.
- During transition, VIT generates transposition code in the Fortran wrapper at the BIND(C) boundary. C++ always sees row-major data.
- When Fortran is removed, the transposition disappears and data is natively row-major.
- VIT detects multi-dimensional arrays in BIND(C) interfaces automatically and generates the transposition.

**Rationale:** Option B (write C++ with Fortran indexing, refactor later) means rewriting C++ code. Option A (transpose at boundary) means writing C++ correctly from day one. The transposition cost is negligible — the largest matrices in ROSCO are 3×3 and 26×36.

## Impact on Plan

These six decisions require updates to PROJECT_PLAN.md:
- Phase 4: Remove AeroDynTorque, move to after Phase 4.5
- Phase 4.5: Replace fixed-size array strategy with pointer+size views; add ownership tracking; add auto-transposition for multi-dimensional arrays
- Phase 9: Add I/O verification capability (design TBD)
- New: `vit translate` command with module-organized translations and metadata
- New: Ownership tracking system for type transition
- Throughout: KGen risk acknowledged as case-by-case workaround-then-patch strategy

These updates will be applied after the I/O verification design session.
