# Executes, But Not Compared: A Third Category the Gate Cannot Speak To

**Date:** 2026-08-07
**Status:** in-progress — corrects a reasoning step at function #26; taxonomy of what the gate cannot speak to
**Scope:** `plan.json` `gate_can_verify`, `regress.sh` scope

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The correction

The replication reported `CurDate` (#26) as having "a call site that never runs", concluding that
`Echo = 0` in all shipped configs makes the function unreachable, and therefore that "the gate
gives zero coverage of this function."

**Verified read-only, and the premise is false. `CurDate` executes in every scenario.**

It has three call sites, not one:

| site | guard | runs |
|---|---|---|
| `ReadSetParameters.f90:291` | `IF (CntrPar%Echo > 0)` | never — `Echo = 0` in all 18 configs |
| `ROSCO_IO.f90:1103` | `IF (CntrPar%LoggingLevel > 0)` | **every scenario** |
| `ROSCO_IO.f90:1111` | `IF (CntrPar%LoggingLevel > 1)` | two configs |

```
LoggingLevel across shipped configs:   16 × 1,  2 × 2   (never 0)
.RO.dbg files written:                 27       (one per scenario)
```

The extraction was pointed at the dead site, which is why nothing was captured — the runbook's own
rule is to pick the call site with the most hits.

**The conclusion still holds, by a different mechanism.** Extracting at `ROSCO_IO.f90:1103` would
also have produced no usable kernel, because `CurDate()` is consumed directly by `WRITE` and so
never lands in a variable KGen captures. That is `CurTime`'s shape exactly, and it is now caught by
`eca3e99`. Same outcome, different cause.

And the gate genuinely gives no coverage — but because **`regress.sh` compares `.npz` channel
arrays and never reads the text files**, not because the function is unreached. A wrong month name
would change all 27 `.RO.dbg` headers and pass 27/27.

## The taxonomy this makes visible

Three distinct reasons the gate cannot speak to a function, which the run has now produced one of
each:

| Category | Example | Why the gate is silent | Fixable |
|---|---|---|---|
| Unreachable in any configuration | `ExtController`, `UpdateZeroMQ` | never executes | no — needs a new configuration |
| **Executes, output not compared** | **`CurDate`, `CurTime`** | **`.RO.dbg` is outside the gate's scope** | **yes — cheaply** |
| Executes, compared, input non-discriminating | `PathIsRelative` | every candidate reading agrees on that input | no — needs a discriminating input |

Only the middle one is cheap to close, and it is closable by a comparison step the project has
already identified as missing: `DECISIONS.md` records that "every `file-output` entry needs a
comparison step built for it." Comparing the `.RO.dbg` headers — or just their non-timestamp fields
— converts `CurDate`, `CurTime` and every `file-output` function from ungated to gated.

**This is the first function where that absence is load-bearing rather than theoretical.**

## Consequences

**The earlier unreachability finding is unaffected.** It states that `ExtController` and
`UpdateZeroMQ` are unreachable in every shipped configuration, which remains true; `CurDate` does
not join that set. No correction needed.

**`plan.json`'s `gate_can_verify: false` needs the second category as well as the first.** It flags
only `UpdateZeroMQ` and `ExtController` — the unreachable ones — and has no way to express
"executes but its output is not in the compared channels." The replication session recorded the
Phase 2 gap rather than retrofitting `plan.json`, which is right: the plan should stand as the plan
that was actually made.

**Residual risk is not only about input coverage.** The obvious argument is that it concentrates in
unexercised inputs. Across this run the gate has been unable to speak for three structurally
different reasons, and only one of them is about input coverage. A reader who accepts the
coverage argument will still overestimate what 27/27 certifies.

## What the cycle got right regardless

**The fails-green defect and how it was found.** `vit extract` reported `Cases: 1` for a site that
never runs; the counted file was `AddToList.0.0.1`, left by the previous extraction.
`count_kernel_cases` matched `.+\.\d+\.\d+\.\d+$` — any function's state — while its own docstring
said `<function>.0.0.1`. Had the cycle continued, the kernel would have replayed `AddToList`'s
captured state against `CurDate`. Fixed in `6e66366`, name-scoped and case-insensitive, five tests.

Audited across every kernel directory: **isolated to `CurDate`**, `own=0 total=1`, no earlier count
inflated (dev note `202608072320`).

**How it was caught is the finding.** Not by a test, not by the gate — by *static analysis
disagreeing with the tool*. The session had already concluded the site was dead and ran the
extraction only to convert an inference into a recorded fact. Trusting the tool over the reading
would have hidden it entirely.

That is the same judgement that caught `NonDecreasing` and `PathIsRelative`: reading the source and
asking what the tool's answer *should* be, then noticing when it isn't. Three of the run's most
consequential findings came from that habit and none from an automated check.

**The disposition.** `CurDate` is real formatting logic — an English month table with fixed field
positions — not a workaround for a missing facility, so §9b says mirror it. `strftime`'s `%b` is
locale-dependent, the same class of error as `std::toupper` in `Conv2UC`. The day comes from two
raw characters of `ccyymmdd` and so is zero-padded; an integer format without a width shortens the
string and shifts every field after it.

**The harness reports SKIPPED with a reason** where a scenario cannot exercise a property, rather
than passing silently — the same discipline as refusing to count `1/1` beside `20/20`.

## References

- Dev note `202608072250` — the non-discriminating input; the third category
- Dev note `202608072233` — the vacuous kernel; `CurTime`'s shape, which `CurDate` shares
- Dev note `202608072320` — the stale-state audit; scope of the `count_kernel_cases` defect
