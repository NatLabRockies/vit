# One Line of CMake: 95 Mismatches Were Never 38 Bad Translations

**Date:** 2026-08-08
**Status:** completed — HEAD is bit-identical, 351 channels compared, 0 mismatched
**Scope:** the resolution; amends a rule in `CLAUDE.md`

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## Resolved

351 channels compared, **0 mismatched**, at HEAD with all 38 functions integrated. Bit-identical, no
tolerance.

The 95 mismatches were one missing line of CMake.

## The root cause, verified

`rosco/controller/CMakeLists.txt` in the replication, as written at `bf25e35`:

```cmake
# VIT replication: -ffp-contract=off on every compiler for the whole
# translation phase. …  Carried-over configuration; recorded in
# analysis/replication/manifest.json.
set(CMAKE_Fortran_FLAGS "${CMAKE_Fortran_FLAGS} -ffp-contract=off")
set(CMAKE_C_FLAGS       "${CMAKE_C_FLAGS} -ffp-contract=off")
```

`git log -S"CMAKE_CXX_FLAGS"` on that file returns exactly one commit: `b908585`, today. **The C++
compiler — the one that builds every translation — was never given the flag.** g++ defaults to
`-ffp-contract=fast`, so on this aarch64 container every `a*b + c` in every `.cpp` fused into a
single FMA, one rounding step instead of two, against Fortran compiled with contraction off.

The comment says "on every compiler." The only compiler it needed to cover is the one omitted.

## The carry-over inverted the original

This is the part worth keeping. The campaign's `rosco/controller/CMakeLists.txt`:

```cmake
# VIT: -ffp-contract=off for bit-identical verification during translation phase.
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -ffp-contract=off")
```

**One line. `CMAKE_CXX_FLAGS`. Nothing else.**

The replication's "carried-over configuration" set the two flags the campaign did not have and
dropped the one it did. It is not a partial copy — it is the complement. A four-line comment
generalising the intent replaced the one line that implemented it, and the generalisation excluded
the specific case.

That is the **fifth** setup defect, and the second of a kind distinct from the first three: not
something left out, but something **re-derived from an understanding of the original rather than
copied from it**, and inverted in the re-derivation.

**Corrected count** (I numbered two different notes "fifth" — `202608080346` counted the incomplete
Phase 2 flags among my setup errors, which is wrong; those are outputs of an analysis the agent ran):

| # | Defect | Kind |
|---|---|---|
| 1 | Disposition set authorised only mirror-translation (`202608072320`) | omitted step |
| 2 | No `reset_to_clean.sh` / `integrate_all.sh` pair (`202608080128`) | omitted step |
| 3 | Gate-extension practice never carried (`202608080204`) | omitted step |
| 4 | `regress.sh` compared nothing (`202608080346`) | **re-derived, wrong** |
| 5 | `CMAKE_CXX_FLAGS` omitted (this note) | **re-derived, wrong** |

The first three cost time; the agent hit each and worked around it. The last two produced 38
functions of results that looked valid and were not, and no amount of doing the work correctly could
have surfaced either.

**Both of the run's silent, result-invalidating defects trace to `bf25e35`** — this and the gate that
never compared (`202608080346`). Same commit, same cause. It is also documented correctly in this
workspace's own `CLAUDE.md` line 30, *"sets `-ffp-contract=off` on both compilers"*, which the ROSCO
replication setup did not carry.

## This falsifies a rule in `CLAUDE.md`

Workflow Discipline currently says:

> **When KGen passes but baselines fail, investigate the translation first** — not the compiler, not
> the infrastructure.

Here that instinct is wrong, and the agent said so precisely:

> the kernel build was correct and production was wrong, and the same C++ source was compiled under
> two different FP regimes. That is the complete explanation for "20/20 IDENTICAL in the kernel,
> divergent in the gate" — and the translation was right in both builds; the builds disagreed.

VIT's kernel Makefile has always passed `-ffp-contract=off`. So the two verification layers compiled
identical C++ under different floating-point rules, and the discrepancy between them was *created by
the apparatus*, not revealed by it.

**Proposed amendment, for after the run:** before suspecting the translation, confirm that the kernel
build and the production build compile the C++ with the same flags. It is a one-line check
(`grep ffp-contract` both build systems, or read the `CXX_FLAGS` line in `compile_commands.json`) and
it costs seconds. The existing rule stays — it was right for `ComputeTau2`'s `a*x**2` — but it now has
a documented counterexample and a cheaper check that runs first.

## The magnitude lesson

I recommended bit-identity partly on the grounds that *42 gross divergences at worst ≈ 1.07 are real
bugs, not drift*. **The recommendation was right and that reasoning was wrong.** It was drift — a
single unfused multiply-add, 1 sample in 16,000, absolute 4.3e-19 against a scale of 0.49 — amplified
by near-total cancellation and then carried forward by the control feedback loop.

The correct statement is the agent's:

> the size of the final divergence says nothing about the size of the cause.

A relative error of 1.07 is equally consistent with a broken algorithm and with one rounding step in
one expression forty timesteps earlier. Reasoning backwards from magnitude to cause is not valid in a
feedback system, and I did it. Seventh claim of mine corrected in this run.

What *does* hold, and is why the recommendation stood: a tolerance chosen to admit 1.07 would have
admitted anything, and bit-identity was achievable — which the fix demonstrates far better than my
argument did.

## How it was found, and why the method worked

The runbook's own dual-computation diagnostic — compute both ways in the wrapper, compare raw bits
per call:

```
axT  f/c   BE561E22CAAAAAAA   BE561E22CAEA2408
axY  f/c   4030CFFD96F60E1E   4030CFFD96F60E1E   ← identical
```

Same expression shape, one with `cos` and one with `sin`; one diverged and one did not. **That rules
out the algorithm and points at the compile**, because an algorithmic error cannot be selective
between two structurally identical expressions.

Worth recording as a diagnostic principle in its own right: when two expressions of the same shape
disagree in whether they diverge, the cause is below the source level.

## Attribution: joint, not per-function — and the agent said so unprompted

Per-commit history was not re-established. An old `rosco/controller/src` does not build against the
current mixed file set, and the one attempt **silently gated the stale library** — the `rebuild.sh`
trap from `202608080218` firing again. It caught it and discarded the result rather than report it as
confirmation.

So the honest claim is about the tree, not the sequence: **all 38 functions are jointly bit-identical
at HEAD.** The agent's framing of the trade is exactly right and I would not improve on it — joint
verification is stronger in one respect, since nothing can be masked by a later change, and weaker in
another, since it is not per-function attribution. Any future "function N passed its gate" must come
from a run after `b908585`.

Baselines were never affected: pure Fortran, no C++ compiled in, which is why clean source reproduced
them exactly throughout.

## What this restores

Of the things `202608080346` listed as void:

- **"Every regress 27/27" is still void as a per-commit claim.** That does not change.
- **The translations themselves are vindicated.** 38 functions, 30/30 first-pass, jointly
  bit-identical against a real comparison. The dataset's central correctness result is now *better*
  supported than it was when everyone believed the broken gate.
- **`integrated` as a disposition** now means something again, at the tree level.

The two coverage claims still needing re-derivation from the `write_discon()` patches dicts —
`PIIController`'s unreachability and `SetpointSmoother`'s dead `ELSE` — are unaffected by this and
still open.

## References

- Dev note `202608080346` — the gate that never compared; same setup commit
- Dev note `202608080218` — the `rebuild.sh` trap that fired again during re-gating
- `ROSCO/rosco/controller/CMakeLists.txt` — the campaign's one line
- `vit/CLAUDE.md` line 30 — the rule stated correctly in this workspace and not carried
