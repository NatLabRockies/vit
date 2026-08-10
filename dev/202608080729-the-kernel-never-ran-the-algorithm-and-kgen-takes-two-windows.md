# The Kernel Never Ran the Algorithm — and KGen Accepts Two Windows

**Date:** 2026-08-08
**Status:** completed — #45; answers the open question with a third option
**Scope:** kernel evidence, `invocation` configuration

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The finding, and it is the run's sharpest

The replication compiled branch counters into `interp2d` and extracted both windows:

| window | `xaxis` branch | `bilinear` branch | result range |
|---|---:|---:|---|
| `0:0:1-20` (default) | 80 | **0** | 0.46546 … 0.46565 |
| `0:0:20000-20020` | 80 | **84** | 0.39396 … 0.46565 |

**The default window never executes the bilinear interpolation** — twenty lines of the most
error-prone arithmetic in the function, and the reason the function exists. Every case lands exactly
on a `Beta_vec` axis point, because pitch is parked there early in the run, and takes the
`xq == xData(j)` early return, degrading to a 1-D lookup.

Verified in the source. `Functions.f90`, `interp2d`, 147 lines, seven return paths:

```
 81  interp2d = interp1d(yData,zData(:,j),yq,ErrVar) ; RETURN     ! xlow
 86  interp2d = interp1d(yData,zData(:,j),yq,ErrVar) ; RETURN     ! xhigh
 89  IF (xq == xData(j)) THEN     ! ← the path the default window takes, all 20 cases
 92  interp2d = interp1d(yData,zData(:,j),yq,ErrVar) ; RETURN
107  interp2d = interp1d(xData,zData(i,:),xq,ErrVar) ; RETURN     ! ylow
112  interp2d = interp1d(xData,zData(i,:),xq,ErrVar) ; RETURN     ! yhigh
115  IF (yq == yData(i)) THEN
118  interp2d = interp1d(xData,zData(i,:),xq,ErrVar) ; RETURN
140  interp2d = fxy(1)                                            ! ← bilinear, the only fall-through
```

### Why this defeats every check the run had

**It reports 20/20 IDENTICAL with 20 distinct values.** So neither the pass count nor the
distinct-value check added at #44 catches it. The outputs *do* vary — they vary across a 1-D lookup
while the 2-D algorithm sits unexecuted.

This is a **fourth** axis of kernel weakness, and it is worse than the other three because it is
invisible to the instruments built for them:

| Axis | Symptom | Caught by |
|---|---|---|
| vacuous `verify_fields` (`202608072233`) | nothing compared | field list inspection |
| non-discriminating input (`202608072250`) | outputs constant | distinct-value count (#44) |
| narrow output side (`202608080246`) | most writes uncompared | compared-field count |
| **the algorithm never ran** (this note) | **outputs vary, correct, and prove nothing** | **branch counters only** |

"20/20 IDENTICAL, 20 distinct values" is exactly what a good result looks like. Only instrumenting the
translation and counting branch entries distinguishes it from one.

## The open question, and there is a third option

The replication used the late window for `interp2d` alone, recorded it separately, and did **not**
change the project default — because doing so would give #46 onward a stronger kernel than #1–#45
had, breaking comparability of the first-pass verification rate the run exists to measure. It added a
mandatory post-verify branch-coverage step to the runbook instead, on the grounds that a runbook step
costs visible time while a silent setting change does not.

That reasoning is right, and both options it offered have the flaw it identified. **There is a third
option, and it is checkable in KGen's source.**

`KGen/kgen/kgconfig.py:823`:

```python
for line in opts.invocation:
    for invocation in line.split(','):
        t = invocation.split(':')
```

**KGen accepts comma-separated invocation windows.** `0:0:1-20,0:0:20000-20020` is valid grammar —
which is presumably how `interp2d` came back with **41 cases** rather than 20.

So the recommendation is the same shape as the gate-scenario one in `202608080700`:

> **Add a window. Do not move the window.**

- The `1-20` cases are still captured, so the early-window result remains comparable across all 78
  functions.
- The late-window cases are additional, so kernels get stronger from #46 onward without anything
  being taken away.
- Case indices identify which window a case came from, so `kernel_verify` can report the two
  populations separately — the `1-20` series stays a single comparable dataset end to end, and the
  late-window series is a second series that starts where it starts.

That preserves exactly what the replication was protecting and gives up nothing. **Keep the runbook
step too** — branch counters catch what neither window count can, as this function proves.

Cost is one longer capture run per function. Against 33 remaining functions of rising complexity,
including `CheckInputs`, that is cheap.

## What has actually been lost so far: nothing

Worth stating as plainly as the replication did:

> across #43–#45 none of [the translations were wrong]. The gate caught every path the kernel missed.

Three consecutive functions where the kernel's contribution was materially weaker than its pass line
suggested, and three consecutive first-pass-correct translations with a clean gate. **What is degraded
is the kernel's contribution to the evidence, not the correctness of the result.** The two-layer
design absorbed all three.

That is the honest framing, and it is more interesting than a failure would be: the layer
that looks rigorous is carrying less than it appears, and the layer that looks coarse is carrying
more.

## Translation notes worth keeping

**`zData`'s orientation is the trap**, and the Fortran naming actively misleads: `xData` indexes
columns and `yData` indexes rows, so `i`/`ii` are row indices and `j`/`jj` are column indices — the
reverse of how those letters read. Column slices are contiguous; row slices need gathering. Combined
with the `202608080506` array-section finding (row of a column-major array reaching a raw pointer),
this is the second function where the whole difficulty was index orientation at the boundary.

**Six of seven return paths report errors unprefixed.** Only the bilinear fall-through reaches
line 144's `ErrVar%ErrMsg = RoutineName//':'//TRIM(...)`; the six early `RETURN`s skip it. Confirmed
in the source. A NaN query is steered to the lower bound rather than flagged, and one error message
has its dimension numbers swapped relative to the value printed — reproduced exactly rather than
fixed, which is the right call and the fourth recorded deliberate non-fix.

Gate covers 4 of 7 paths; `xlow`, `xhigh` and the `yaxis` path are in the known-gaps table.

## Accounting

45 attempted: **40 integrated, 2 `integrated_unexercised`, 1 out of scope, 1 deferred, 1 blocked, 0
failed.** Translation correctness **100% first-pass**.

#46 `CheckInputs` is 857 lines of validation branches the 27 scenarios never take, with test-validate
proposed rather than kernel replay. Note that the window question does not bite there — the reason
kernel replay is the wrong tool for it is the same reason `0:0:1-20` would not have helped.

## The `invocation` default

The `invocation` default is a one-line configuration that shaped the evidentiary value of
every kernel in two campaigns, was never chosen deliberately, and is documented in the tool's own
source comment. And KGen already supported the additive fix.

## References

- Dev note `202608080640` — the `0:0:1-20` window; this is the case where it costs the most
- Dev note `202608080700` — close by addition, not modification; same principle, applied to the gate
- Dev note `202608080506` — the other index-orientation trap
- `KGen/kgen/kgconfig.py:823` — the comma-separated grammar
