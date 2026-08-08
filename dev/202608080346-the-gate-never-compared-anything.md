# The Gate Never Compared Anything

**Date:** 2026-08-08
**Status:** completed — defect confirmed independently; contains the recommendation on how to proceed
**Scope:** everything. Dataset integrity and the setup I specified

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## Confirmed

`scripts/regress.sh` ran its comparison as `docker exec "$CONTAINER" python3 - <<'PY'`. Without `-i`,
`docker exec` does not attach stdin. The heredoc goes nowhere, `python3 -` reads an empty script,
runs nothing, and exits 0. `RC=$?` takes that 0 and prints `REGRESS PASS: 27/27 scenarios
bit-identical`.

Verified independently:

```
git log --follow -S"docker exec -i" -- scripts/regress.sh
  41436ec  Fix the gate: it never compared anything          ← first and only appearance
git log --follow -- scripts/regress.sh
  41436ec  2026-08-08  Fix the gate: it never compared anything
  bf25e35  2026-08-07  Replication setup: baseline fork, carried harness, 27-scenario gate
```

**The file has existed in exactly two states**, and `docker exec -i` appears only in the second. Every
one of the 38 functions was gated by the first.

What the gate proved: 27 scenarios ran to completion without raising. It never loaded a baseline,
never compared a channel, and had no path to failing on a numerical difference.

## This is my defect

`bf25e35` is the replication setup commit — the harness I specified. **Fifth setup omission**, after
the under-specified disposition set, the incomplete Phase 2 flags, the missing reset/integrate pair,
and the missing gate-extension practice.

The first four omitted *capabilities*: the run could not do something the campaign could. This one is
different in kind. It **silently removed the primary verification layer for the entire run** and
replaced it with a constant `PASS`. Everything downstream — every `integrated` disposition, every
"regress 27/27" in a commit message, every claim in these notes that a translation was confirmed by
simulation — inherited it.

I also read "REGRESS PASS: 27/27" in the agent's reports thirty-odd times and never asked what it had
compared. The agent's own account of the tell applies to me at least as much:

> a real gate run prints `channels compared: 351  mismatched: N`, and that line was absent from every
> single invocation. I read "REGRESS PASS" and didn't ask why the count was missing.

## The decisive check: the campaign's gate did not have this bug

This is what determines the answer to the open question, and it is the one thing the replication
cannot look up.

`INTEGRATION_TEST.md` in the campaign tree documents the comparison and its invocation:

```bash
docker exec vit-dev bash -c 'cd /workspace/ROSCO && python3 -c "
import numpy as np, sys
...
```

**`python3 -c` passes the script as an argument, not on stdin.** There is no heredoc and no
dependence on stdin attachment. The campaign's comparison could not have failed this way, and it
reported a count:

> **What is compared:** 672,000 float64 values across 9 scenarios, using `np.array_equal()`
> (byte-identical, not tolerance-based).
>
> **Expected output:** `ALL IDENTICAL — 672,000 total float64 values compared`.

So **bit-identity is a demonstrated, achieved bar on this codebase, with these tools, for 52
translated functions.** It is not aspirational and it is not a coincidence of tolerance.

(Scope note: the documented comparison covers 9 scenarios. I found no automated 27-scenario
comparison script in the campaign tree. The claim I am making is that the campaign's mechanism was
sound and reached zero mismatches over 672,000 values — not that it was run at 27 scenarios.)

## Recommendation: bit-identical, and fix the divergences

Four reasons, in order of weight.

**1. A tolerance gate would be a weaker bar than the thing being replicated.** The campaign hit
`np.array_equal()` — zero tolerance — over 672,000 values. If the replication re-gates on a
tolerance, every subsequent comparison between the two is meaningless, because they are no longer
measuring the same thing. The run's entire value is the comparison.

**2. The gross divergences are not drift.** 42 channels at relative error ≥ 1e-6, worst ≈ 1.07 — a
value differing from its baseline by more than the baseline's own magnitude. No defensible tolerance
admits that. Choosing one that did would be choosing a number to make the failures disappear.

**3. Responding to "the gate never compared" by lowering the bar is the exact failure this project
diagnoses.** The thesis is that apparatus reporting unearned success is the central risk in
LLM-assisted translation. The first real test of whether the project believes it is what it does when
its own apparatus turns out to have been doing precisely that.

**4. The debugging path is already open.** The bisect places the first divergence at
`ColemanTransform` with a last-bit error — 1 sample in 16,000, absolute 4.3e-19 against a scale of
0.49 — amplified across timesteps by control feedback. That is a known dynamic in this project
(`202604260357`, the DBEMT libmvec case, the Scenario 16 2⁻⁷⁰ residual), and it has been tracked to
root cause before. The counts are non-monotonic across commits (#3 → 0, then 22, 17, 95), which means
several functions contribute and some cancel — unpleasant, but it also means most of the 38 are
probably clean and the work is concentrated.

**On the 51 sub-ULP channels:** under a bit-identical bar these are failures too, and they should be.
The campaign reached zero. A last-bit difference in a feedback loop is exactly what produced the 1.07
one, forty timesteps later.

## What survives, and what does not

Stated precisely, because the temptation is to over-condemn.

**Survives intact:**

- **All kernel verification.** `kernel.exe` performs its own comparison and `verify_fields.csv` has
  real content. Unaffected by this defect.
- **All test-validate results**, for the same reason.
- **All timing and token telemetry**, and therefore the entire cost model in `202608072355` and
  `202608080148`. The gate *ran*; only its comparison didn't. Wall clock and tokens are real.
- **Every campaign-side finding in these notes.** `ExtController`'s false stamp (`202608080058`), the
  30 width-mismatched bridges (`202608080128`), the gate coverage regression (`202608080204`), the
  campaign's silent normalisations. All of those are inspections of the campaign tree and do not
  depend on the replication's gate at all.

**Does not survive:**

- Every "regress 27/27" in a commit message or in these notes.
- The `integrated` disposition wherever it was read as *the gate exercises this*.
- **My claim in `202608080246` that "the gate is the only evidence for every write this function
  makes."** `ResController`'s four 1024-element history arrays have no evidence from any layer —
  the kernel compared one field and the gate compared nothing.

**Strengthened rather than weakened:** every argument in these notes of the form *the kernel is
narrow and the gate carries the rest*. The gate carried nothing. `202608072250`, `202608080003`,
`202608080148` and `202608080246` all become sharper, and the two-layer verification story
needs rewriting rather than patching — for the replication. The campaign's two layers both
worked.

## The rule the agent drew, which is the right one

> a green result must be able to name what it compared — the new gate prints the comparison log and
> refuses to report a pass unless `channels compared:` appears in it.

That is the generalisation of every fails-green finding in this project, and it is the first one
stated as a mechanism rather than an observation. **A pass that cannot name its evidence is not a
pass.** It is the concrete recommendation, and it would have caught this defect,
the `ExtController` stamp, the empty-script probe, the mistyped `event` key, and the container with
no `~/.claude` — five distinct failures in this project alone, all of the form *nothing happened and
that read as success*.

## Eighth fails-green shape, and the one that subsumes the others

Not "the check was weaker than claimed" but **"the check did not execute, and its absence was
indistinguishable from its success."** The same shape as the mistyped `event` key, the container
without transcripts, and my own scoped-out window — all within about six hours, in three different
tools, by three different mechanisms.

## Also in the turn, and consequential

**The committed `Examples/DISCON*.IN` files are not the scenario inputs.** All 27 scenarios call
`write_discon()` with a patches dict, so eight scenarios sharing `DISCON.IN` leave only the last
one's values on disk. That invalidates a reachability check used since #3, and with it **two of my
notes' claims, both in the direction of understating coverage**: `PIIController`'s unreachability
(`202608080204`) and `SetpointSmoother`'s dead `ELSE` (`202608080128`).

Both need re-deriving from the patches dicts rather than from the committed files. I read
`Flp_Mode` and `SS_Mode` straight out of `Examples/DISCON*.IN` and treated them as what the gate
runs. They are not.

**This does not touch the campaign-side half of `202608080204`** — that `DISCON_flp.IN` carried
`Flp_Mode = 2` throughout the campaign and 3 afterwards is git history, and the campaign's 20
captured kernel cases for `PIIController` are on disk. The gate-regression finding stands. What needs
re-checking is only the claim about what the *replication's* current gate reaches.

`#37 ResController` and `#38 PowerControlSetpoints` are integrated and first-pass with 20/20 kernels;
those parts hold. `vit_fortran_write.h` came with a 4,087-case differential test that caught three
bugs including negative zero.

## References

- Dev note `202608080218` — my correction; the same failure shape, one layer down
- Dev note `202608080246` — the "gate carries the rest" claim this invalidates
- Dev note `202608080204` — the reachability claim needing re-derivation; campaign half unaffected
- `ROSCO/INTEGRATION_TEST.md` — the campaign's comparison; `python3 -c`, `np.array_equal`, 672,000 values
