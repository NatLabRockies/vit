# Session Reflection: Verification Pipeline Overhaul
**Date:** 2026-03-27
**Status:** completed
**Scope:** Session retrospective — verification pipeline investigation, extraction infrastructure rebuild

## What Actually Happened

This session started with a question about how verification works, and ended with rebuilding the entire extraction and testing infrastructure from the ground up.

The question about why "0/40" didn't make sense if only the C++ was broken was the thread that, when pulled, unraveled a chain of assumptions nobody had examined:

1. **"40/40" was never 40 cases.** It was 20 cases listed twice in `kgen_statefile.lst` due to KGen's ElapsedTime phase appending invocation triples instead of replacing them. We traced this through KGen's Python code generator, confirmed it with debug instrumentation, and understood exactly why ElapsedTime selected invocations 1-20 (sorted bias + skewed timing distribution for fast ROSCO functions).

2. **The CMake cache was silently broken after every extraction.** `cleanup_kgen_utils_injection()` restored CMakeLists.txt but left `DependInfo.cmake` with a stale reference to `kgen_utils.f90`. This created a break-fix-break cycle that VIT silently absorbed — until ElapsedTime tried to build before the Extractor could patch it. Nobody noticed because the failures were swallowed by `capture_output=True`.

3. **Eight functions were being verified with zero inputs.** ForeAftDamping, FloatingFeedback, YawRateControl, StructuralControl, CableControl, FlapControl, IPC, and ActiveWakeControl all passed "40/40" — but with all-zero inputs, that only proved the wrapper plumbing worked. The arithmetic was untested. And five more functions (wrap_360, ColemanTransform, ColemanTransformInverse, sigma, ResController) couldn't even be extracted with `--scenario 3` because their call sites are gated by mode flags not active in that scenario.

4. **ElapsedTime profiling data was cross-contaminating between extractions.** The `model/` directory persisted, so every function after the first used saturate's timing profile.

## What We Built

- **Per-function scenario mapping** — every function now extracts with the specific scenario that exercises its call site with non-zero inputs
- **Statefile deduplication** — the ElapsedTime append bug is neutralized
- **CMake cache cleanup** — extractions no longer leave the build in a broken state
- **Three automation scripts** — `extract_all.sh`, `verify_all.sh`, `integrate_all.sh` replace ~150 lines of copy-paste commands
- **Consistent documentation** — CLAUDE.md, PROJECT_PLAN.md, and INTEGRATION_TEST.md all reflect the current process

And the definitive result: 28/28 extracted, 28/28 verified, 27/27 integrated, 612,000 values ALL IDENTICAL.

## What We Learned

The user's instinct to push on things that didn't make sense was the driver for every discovery. When the explanation said "ElapsedTime selects invocations 1-20 because of sorted() bias," the user asked "but what about complex functions?" When the timing distribution was shown to be skewed, the user remembered seeing varied invocation numbers before adding the `--invocation` flag — which led to discovering the `model/` persistence issue. When the plan for the ElapsedTime build fix waffled, the user called it out and required a solid conclusion before proceeding.

The pattern: the most dangerous bugs are the ones that produce correct-looking output. "40/40 passed" looked right. Zero-input verification "passed." The CMake build "succeeded" (because VIT patched it on the next run). Each of these was technically correct but masked a real issue.

## Steps Ahead

**Phase 8 (ControllerBlocks.f90)** is the next major work item. PitchControl is already translated and verified but can't join the integration test until PitchSaturation is translated — that's the blocking dependency. ControllerBlocks.f90 contains PitchSaturation and other helper functions.

The infrastructure is now solid for Phase 8: extraction with correct scenarios, deduplicated state files, automation scripts, and the full 8-scenario integration test. The tooling improvements from this session — especially the scenario mapping and the understanding of what each scenario actually exercises — will directly inform which scenarios new ControllerBlocks functions need.
