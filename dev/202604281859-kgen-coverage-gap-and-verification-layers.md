# KGen Coverage Gap — How a Translation Bug Passed 20/20 IDENTICAL
**Date:** 2026-04-28
**Status:** completed
**Scope:** Verification methodology, KGen limitations, defense-in-depth

## The Paradox

ComputeTau2's KGen kernel verification reported **20/20 IDENTICAL** — every field in every case matched exactly. Yet integration into the production build caused catastrophic divergence (max_abs=5.73e+06 in `ad_MHK_RM1_Fixed_UA`). Investigation revealed a **real translation bug**: `0.26 * spanRatio * spanRatio` (C++ left-to-right: `(0.26 * spanRatio) * spanRatio`) differs from Fortran `0.26*spanRatio**2` (which computes `0.26 * (spanRatio * spanRatio)`). The different intermediate rounding produces 1-3 ULP differences in `k_tau` for certain `spanRatio` values.

KGen **verifies k_tau directly** — it's listed as an output field. The kernel reads the Fortran-computed reference `k_tau` from the state file and compares it to the C++-computed `k_tau`. If the bug were triggered in any of the 20 cases, KGen would have caught it. So how did it pass?

## Why KGen Missed It

The kernel captured 20 invocations from `ad_MHK_RM1_Fixed` (DBEMT_Mod=2, 30 nodes × 2 blades). These 20 invocations correspond to the first 20 (i,j) calls — nodes 1-20 on blade 1 at the first timestep. Each node has a different `spanRatio` value, producing a different `k_tau`.

The 20 captured `k_tau` values ranged from 0.274 to 0.390. The mismatching values from the debug comparison (nodes 18, 24, 26, 27, 30) had `k_tau` around 0.153 to 0.298.

**The bug only triggers for certain `spanRatio` bit patterns** where `(0.26 * spanRatio) * spanRatio` rounds differently from `0.26 * (spanRatio * spanRatio)`. For the majority of `spanRatio` values, both evaluation orders produce the same float64 — the two intermediate roundings cancel out. The 20 captured invocations happened to use `spanRatio` values where the results are identical.

Node 18 is within the first 20, and it DID show mismatches in the production simulation. But the mismatch depends on the specific `spanRatio` value at that node at that timestep. In DBEMT_Mod=2, `spanRatio = u%spanRatio` comes from the input (not the parameter array), and may vary by timestep. The first-timestep value at node 18 may not trigger the rounding difference, while later-timestep values do.

**Summary:** The translation bug was **input-dependent** — it only manifests for specific floating-point bit patterns. KGen's 20-invocation sample happened to miss every triggering input.

## The Uncomfortable Truth About KGen

KGen said 20/20 IDENTICAL. We trusted that. **It was wrong.**

Not wrong in the sense that KGen malfunctioned — it correctly reported that the 20 captured invocations produced identical results. But wrong in the sense that mattered: the translation had a bug, and KGen gave us a green light. That green light directly led to the wrong conclusion in dev note `202604281032` — "the translation is correct (20/20 IDENTICAL), so the problem must be compiler optimization, so PRIVATE functions can't be integrated." Every step of that reasoning was logical, and the premise was false.

**20/20 IDENTICAL does not mean the translation is correct. It means the translation is correct for those 20 inputs.** And 20 inputs out of 38,400 calls per simulation is 0.05% coverage. The bug triggered on specific `spanRatio` bit patterns — the kind of thing you'd only catch with exhaustive input coverage or by knowing to look for it.

### What KGen is actually good for

KGen catches structural bugs fast: wrong function signature, wrong type layout, wrong array indexing, wrong control flow. These fail on virtually any input. For ComputeTau2, KGen verified that the view struct marshalling, OPTIONAL parameter handling, and conditional branching were all correct. That's real value — without it, the integration would have crashed or produced garbage, and debugging would have started from zero.

But KGen is not a proof of correctness. It's a smoke test. A very good smoke test that catches most bugs — but a smoke test. **When KGen passes and baselines fail, the first assumption should be "the translation has a bug that KGen's sample didn't trigger," not "the infrastructure is broken."**

### What this means for the workflow

Both gates are essential, and neither can substitute for the other:

| Layer | What it catches | What it misses |
|-------|---------------|----------------|
| **KGen kernel (20 cases)** | Structural bugs, type mismatches, indexing errors, control flow errors | Input-dependent arithmetic differences not triggered by the 20 captured invocations |
| **17/17 baselines (bit-identical)** | ANY numerical difference that propagates to simulation output, including feedback-amplified sub-ULP differences | Bugs in code paths not exercised by the 17 test cases |
| **NREL regression (1e-2)** | Large-scale correctness errors | Sub-ULP precision issues (by design — it's a correctness gate, not a precision gate) |

The baseline gate (step 7: 17/17 bit-identical) is the actual correctness verification. It exercises the translation on hundreds of thousands of calls across 17 diverse test cases, with feedback loops that amplify any sub-ULP difference into a detectable signal. KGen is the fast first pass that catches the easy 95%. But the 5% it misses can be catastrophic.

**When KGen passes but baselines fail: investigate the translation first, not the infrastructure.** The ComputeTau2 experience proves this — hours were spent on assembly diffs, compiler flags, and module structure analysis before a simple dual-computation debug print found the actual bug in 30 seconds.

## Improving Coverage

The gap could be narrowed by:

1. **More invocations:** Capturing 100+ invocations instead of 20 increases the probability of hitting triggering inputs. Trade-off: extraction time and state file size.

2. **Multiple test cases per function:** Extracting from multiple test cases covers more input distributions.

3. **Targeted stress testing:** For expressions with known precision sensitivity (power operations, coefficient-times-variable patterns), generate synthetic test inputs that sweep ULP-difference boundaries.

But none of these eliminate the need for baseline verification. The correct mental model:

- **KGen = fast smoke test** (catches structural bugs in seconds)
- **Baselines = ground truth** (catches everything that reaches simulation output)
- **If they disagree, the translation has a bug — investigate the translation**
