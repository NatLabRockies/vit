# A ROSCO Capability Broken in Transit to AeroDyn, Found Only by Re-Running ROSCO

**Date:** 2026-08-07
**Status:** in-progress — fix applied in the replication run; AeroDyn-side check outstanding
**Scope:** `vit/type_map.py`, `vit/interface_gen.py`

## Placement

Same rationale as dev note `202608071913`: this lives in the OpenFAST workspace because the
ROSCO-workspace checkout is bind-mounted into the replication's container and is the repository
that session commits to. **Do not merge into that checkout until the run has ended.**

## What happened

Translating the filter block (`HPFilter` and the rest of #8–#16), the replication hit a bridge
that declares `reset` as `INTEGER(4)` while the Fortran caller passes `LOGICAL(4)`. The generated
wrapper passed the value straight into a `_c` interface expecting `INTEGER(C_INT)` with no
conversion emitted.

The replication session diagnosed it correctly: `type_map.py` maps `LOGICAL(4)` to
`INTEGER(C_INT)`, which is sound, but the call generator's conversion branch fires only when *the
C side is LOGICAL*. With the C side an integer the branch never fires and the value passes raw.
The reverse direction (C to Fortran) already converts via `(/= 0)`; the forward direction did not.
Fixed for `INTENT(IN)`, verified 20/20 with a `MERGE` conversion emitted, regression test added.

## The part that matters: this is a regression, not a latent gap

The original ROSCO campaign translated these same filters successfully. Its generated wrapper, at
commit `e0c31ec`, reads:

```fortran
LPFilter_result = REAL(lpfilter_c(..., INT(iStatus, C_INT), LOGICAL(reset, C_BOOL), inst, ...), DbKi)
```

The conversion was there. `LOGICAL(4)` mapped to `C_BOOL` / `int8_t`, so the "C side is LOGICAL"
branch fired and did the right thing.

On **2026-04-25**, commit `bde2259` — *"6 infrastructure fixes for CalculateUACoeffs: LOGICAL
mapping, array-return bridges, callee flush-reload, CHARACTER bridges, field comparison"*, AeroDyn
work — changed it:

```diff
-    "LOGICAL(4)": TypeMapping("LOGICAL(4)", "4", "C_BOOL", "int8_t", "LOGICAL(C_BOOL)", 1)
+    "LOGICAL(4)": TypeMapping("LOGICAL(4)", "4", "C_INT", "int32_t", "INTEGER(C_INT)", 4)
```

The mapping change is defensible on its own terms: default Fortran `LOGICAL` is four bytes, and
the commit says so. What it did not account for is that the call generator keys its conversion on
the *C* type rather than on the Fortran type, so changing the C type silently disabled the
conversion.

**An AeroDyn-era change broke a ROSCO-era capability, and it went unnoticed for three and a half
months because ROSCO was never re-run.** ROSCO had already been migrated to pure C++, so its
Fortran bridges no longer existed to break; the damage was invisible until a fresh ROSCO
translation regenerated them.

## Why this is the most useful thing the replication has produced

More useful than any cost number so far, and it is not a cost number.

VIT was carried to a second codebase without forking it, and the no-fork constraint acts as a
test designed to expose non-generality rather than as an achievement. This
sharpens that claim in a way that is uncomfortable and correct: **the constraint held, the tool
was never forked, and carrying it forward broke the first codebase anyway.** Generality asserted
from n=2 is exactly this fragile, and nothing surfaced it except re-running codebase one.

That is an argument for replication as regression testing, made from evidence rather than
assertion.

The campaign's own verification discipline did not catch this, because the
discipline gates *translations*, not the *tool*. Nothing re-runs a finished codebase after the
tool changes. The harness cannot gate itself; this is that
observation with a concrete instance and a date attached.

## Outstanding

- [ ] **`LOGICAL, INTENT(OUT)` almost certainly has the same defect.** `FindLine` (replication
      plan #47) is the one ROSCO instance and needs different machinery; the replication session
      correctly deferred it. The C-to-Fortran direction converts via `(/= 0)`, but nothing
      verifies the OUT path round-trips under the four-byte mapping.
- [ ] **Check AeroDyn for live instances.** AeroDyn was translated entirely under the new mapping,
      so any `LOGICAL` argument crossing a bridge there has been going through the unconverted
      path since April. The 17/17 baselines pass, which bounds the damage, but bit-identical
      baselines only certify the inputs exercised — dev note `202608071913`. Run
      this check after the replication ends; it is independent of ROSCO.
- [ ] **Consider keying the conversion on the Fortran type, not the C type.** The root cause is
      that the generator asks "is the C side LOGICAL" when the question is "is the Fortran side
      LOGICAL and does the C side differ." A mapping change should not be able to silently disable
      a conversion.

## References

- `bde2259` (2026-04-25) — the mapping change that caused it
- `e0c31ec` — the original campaign's correct `LOGICAL(reset, C_BOOL)` wrapper
- Dev note `202608071913` — telemetry accounting; same placement rationale
- Dev note `202608071634` — the replication plan
