# A Kernel Passed Having Compared Nothing

**Date:** 2026-08-07
**Status:** in-progress — fixed in VIT `eca3e99`; campaign exposure characterised below
**Scope:** `vit/kernel_verify.py`

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## What happened

`CurTime` (#21) extracted one case. `vit verify` reported `✓ VERIFICATION PASSED: 1/1`. Run by
hand, the kernel said:

```
Number of output variables:            0
Verification PASSED with CurTime.0.0.1
```

**KGen compared zero variables and reported success.** `CurTime()`'s result is consumed directly by
a `WRITE`, so it never lands in a variable KGen captures. VIT's parser skipped the count lines
entirely, so a green pass on a function that *cannot be kernel-verified at all* was
indistinguishable from a real one.

Fixed in `eca3e99`: an explicit zero now fails and points at `test-validate`. A *missing* count
line is deliberately **not** treated as zero, because other output shapes do not print it and
absence is not evidence. `vit verify` on `CurTime` now correctly reports FAILED.

That distinction is the right call. The failure mode being repaired is silent success; inventing a
second silent failure to repair it would be worse.

## This is the worst instance of the run's dominant pattern

Sixth occurrence, and the first inside the gate rather than around it:

| # | Instance | Where |
|---|---|---|
| 1 | `libdiscon.so` resolving to the original tree | around |
| 2 | `cmake --build \| tail` masking a failed build | around |
| 3 | Four gate scenarios green while computing nothing | around |
| 4 | Kernel verifying a wrapper integration does not generate | around |
| 5 | `1/1 PASSED` indistinguishable from `20/20 PASSED` | around |
| 6 | **Kernel PASSED having compared zero variables** | **the gate itself** |

This is not a limitation of the gate's *reach*. It is the gate having **no floor**.
Every argument about what bit-identical replay certifies presumes replay compared
something. `eca3e99` supplies the floor.

## What this means for the 43 kernel-verified ROSCO functions

The ROSCO campaign's kernel replay verified 43 functions bit-identically against 20–25 captured invocations
each. Until now that rested on `PASSED` having been printed. Checked against preserved artifacts in
the campaign tree, read-only:

**22 of 43 kernels retain a `verify_fields.csv`. None is vacuous.** Smallest is `ResController` at
21 compared fields; `ColemanTransform` runs to 4,620.

The other 21 had their CSVs removed by `verify_all.sh`, so vacuity cannot be confirmed from disk.
Their shapes make it implausible:

```
18 SUBROUTINEs  ActiveWakeControl, AeroDynTorque*, CableControl, ComputeVariablesSetpoints,
                FlapControl, ForeAftDamping, IPC, PitchControl, PowerControlSetpoints,
                ReadAvrSWAP, RefSpeedExclusion, SetpointSmoother, Shutdown, Startup,
                StateMachine, StructuralControl, VariableSpeedControl, WindSpeedEstimator,
                YawRateControl
 3 FUNCTIONs    AeroDynTorque, FloatingFeedback, PitchSaturation
```

Every subroutine there mutates `LocalVar` or `objInst` — that is what a ROSCO control routine does
— so KGen has captured state to compare. The vacuous case requires outputs that never land in a
tracked variable, which is precisely `CurTime`'s shape: a result consumed directly by a `WRITE`.
The three functions return values assigned at their call sites.

**Honest statement: 22 confirmed non-vacuous from preserved artifacts, 21 unconfirmable but
structurally implausible, zero known vacuous.**

## Two judgments in this cycle worth endorsing

**The count stays at 20 verified plus one by other means, not 21.** `CurTime` reads a wall clock, so
there is no fixed output to compare, and the 27/27 does not verify it either: `regress.sh` compares
`.npz` channel arrays while `CurTime`'s output goes to the `.RO.dbg` text header. The gate proves
the integration perturbed nothing else. Refusing to round up is the same discipline as refusing to
let `1/1` sit beside `20/20` (dev note `202608072158`), and the third time this run that the
measurement was declined rather than flattered.

**The `test-validate` oracle is better than the replay it replaces**, not a weaker substitute. Four
independent properties, 4/4:

1. exact 8-byte `dd:dd:dd` format with in-range fields
2. agreement with the Fortran called back to back, retried — a second-boundary race survives a
   retry, a systematic error does not
3. agreement with an independent `localtime()` reading, which excludes *both implementations
   calling the same wrong clock*
4. the seconds field actually advances, which excludes a constant satisfying the other three

Property 3 is the one that matters: `localtime_r` versus `gmtime_r` yields a well-formed string
wrong by the UTC offset, with nothing downstream to catch it. This is a genuine oracle for a
nondeterministic function.

## Cost, and the first amortization evidence

`CurTime`: 413 s — near the clean-cycle median while carrying a VIT fix. The
`test-validate` infrastructure paid for under `Int2LStr` and `NonDecreasing` (dev notes
`202608072158`, `202608072214`) is now working, and this is the first cycle showing those
investments amortize.

Clean-cycle population remains n=13.

## References

- Dev note `202608072158` — `1/1 PASSED`; evidence strength not reported. **This is the same defect
  one level deeper: there the count was unreported, here the count was zero.**
- Dev note `202608072214` — `NonDecreasing`; the escaping defect class
