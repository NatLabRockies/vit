# A Generated Harness That Compiles, Runs and Kills

**Date:** 2026-08-09
**Status:** in-progress — `VIT_INTEGRATION.md` §9 steps 1–3 and 5 done; step 4 blocked on one named thing
**Scope:** method; the loop↔VIT integration; a correction to `202608090118`

## Placement and isolation

The replication was paused (last cycle event 06:02, `vit-dev` at 0% CPU with nothing but PID 1).
Everything below ran in a **new container, `vit-harness`**, built from `vit-dev-image:latest` —
byte-identical toolchain to `vit-dev` — mounting `harness-scratch`, which is outside
both workspaces. `vit-dev` was never exec'd into except to read `ps`. The replication's cycle log
was unchanged, byte for byte, before and after.

**The frozen campaign cannot serve as a Fortran reference.** It finished: `src/` holds 55 `.cpp`
files, `CMakeLists.txt` lists only `.cpp`, and the build produces **53 C++ objects and zero Fortran
ones**. A harness built there would compare the translation against itself — the exact trap
`test_validate.py`'s own comment warns about. The reference is therefore built from **ROSCO at
`974290e`**, the parent of the campaign's first VIT commit (`e8010f0`), plus the two source
adjustments the campaign itself made to prepare types for C (`ErrMsg` deferred-length →
`CHARACTER(1024)`; the matching `avcMSG` broadcast in `ExtControl.f90`). Reproducible from git, and
the Fortran is real.

## 1. It works, on real code

```
PIController: 19 C parameter(s), 5 extent(s), 6 output(s)
  note: piP: expanded 10 param(s) from 5 field(s)
  note: inst: indexes piP_ITerm (read from the translation)

HARNESS PASS: checked 1169  failed 0  inadmissible 0
killed 20 of 20 scoreable (0 declared equivalent)   score 1.000
```

Six of six rules applied. Every output compared **bitwise** — the return value, all five
1024-element `piParams` arrays, and `inst`. The mutants were generated from the shipped
`picontroller.cpp` and compiled, linked and run against the same 1169 cases.

New: `harness/emit.py`, `scripts/vit_harness.py`, `scripts/vit_mutate.py`.

**Worth noting in the kill data.** Five of the nine `[idx] → [idx + 1]` mutants were caught by
**1, 1, 6, 8 and 15 cases out of 1169.** Those are the ones the index ladder exists for. A harness
that pinned `inst` — as every kernel replay does, because the simulation picks the instance — would
have let them through while reporting the same green.

## 2. Two VIT gaps, fixed, and why they are not workarounds

Neither was a harness problem; both are in `vit test-validate`, and per `CLAUDE.md` they were fixed
there rather than routed around. Patch staged at `translation-loop/vit-patches/` — **not applied to
`vit_translation/vit`, which is inside the replication's tree.**

1. **`LIBS += -lgfortran -lm`.** The harness links with `$(CXX)`, so nothing pulls in the Fortran
   runtime. A target built as a Fortran shared library links it implicitly and the omission never
   shows; the object-file fallback links raw `.o` files and every Fortran I/O, string or intrinsic
   call in the reference is then undefined.
2. **Callee bridges.** `picontroller.cpp` calls `saturate_c`, which nothing defined — so
   `test-validate` worked only on leaf functions, which are the easy ones. Bridges are now generated
   from the translation's own `<name>_c(` calls, reusing `generate_kernel_callee_bridge`.
   **They call the original Fortran callee**, so both sides share one callee implementation; linking
   `saturate.cpp` instead would put C++ `saturate` on one side and Fortran `saturate` on the other,
   where a mismatch is unattributable and two compensating errors can cancel.

## 3. Three defects in my own bridge, each disabling the rule it feeds

Found by looking at what `map_signature` actually produced for `PIController`, not by reasoning:

| defect | consequence |
|---|---|
| a fixed-size array field (`REAL(DbKi) :: ITerm(1024)`) fell through to the scalar branch | `extents` came back **empty**, so R5 reported NOT APPLICABLE on the one unit whose structure *is* an array |
| fields of a `TYPE(...), INTENT(INOUT)` argument defaulted to `intent="in"` | R4 compared the **return value alone** — the saturation blindness `run.py`'s docstring names `PIController` for |
| `LOGICAL` crossing as `int` carried no enumerated values | R2 had nothing to act on **anywhere in a real campaign** |

All three passed the 12 existing integration tests, because none of them asserted anything about
those parameters. My own green, establishing nothing.

**Fixed-size extents are pinned, not varied** (`Param.fixed_extent`, `lo == hi`): the type fixes
1024, so varying it would index past the end of a C array, and counting it among "extents varied"
would claim coverage R5 does not have. R5's report now separates *varied* from *FIXED by the type*
and says **"NO index parameter identified"** when it cannot find one.

**`inst` is recovered from the translation, not guessed.** `infer_indexes()` reads
`int idx = *inst - 1;` and `piP->ITerm[idx]` and resolves the alias. Any subscript it cannot trace
to a parameter is reported as unexercised rather than skipped.

## 4. A correction to `202608090118` §6, and the reason it happened

That note recorded, as a **structural blindness of the boundary**, that *CHARACTER arguments cross as
a local bridge buffer and are never compared*. **It is false for the checkout the campaign runs.**

The two VIT checkouts have diverged: **30 commits exist only in `vit_translation/vit`,
57 only in the OpenFAST workspace's.** I measured against the OpenFAST one. The campaign's own VIT
fixed this on **2026-08-07 — the day before I wrote the claim** — in `8e5973b`, *found translating
`PathIsRelative`, function 23 of the replication I am auditing*. Measured:

```
PathIsRelative -> [('char*', 'GivenFil'), ('int', 'len_GivenFil')]
```

What is actually blind, and correctly classified this time:

- **CHARACTER *fields* of derived types** are dropped. `ErrVar` reaches C++ as 3 of its 4 fields;
  the missing one is `ErrMsg`, where ROSCO puts every error string. `CheckInputs` — one of the five
  documented escapes — is largely that message. **A VIT gap of the same shape as the one `8e5973b`
  closed**, not a law.
- **Passthrough derived types** are omitted. This one is structural.
- **The generator has no string kind.** Work, on this side of the seam.

Two lessons, and the second is the general one.

**Measure against the checkout the campaign runs.** `tests/test_vit_integration.py` imported the
OpenFAST VIT while parsing ROSCO's sources — a defect in the test file itself, and the reason a
wrong claim survived 12 passing tests. It now selects the campaign's checkout and **asserts the
choice**, so the mismatch cannot recur silently.

**Be most suspicious of the word "structural".** It converts a bug someone could fix into a limit
nobody will attempt. Three of the four things I had filed under it were work.

## 5. The failure that would have been a false finding

The first green run reported **3 failures out of 1169**, all 1 ULP. They were not defects: my
`cmake` gave the reference `-O3` with FMA contraction enabled while the harness compiled C++ with
`-ffp-contract=off`. Rebuilding the reference the way the campaign does took it to 0.

Had I reported before checking, it would have read as *"the generated harness found three defects the
gate missed in a function that passed every layer"* — the most attractive possible result, and
wrong. **Any harness on this boundary must state the flags both sides were built with.**

## 6. Six defects in `vit test-validate`, found by trying to reach `CableControl`

Two were reported in §2. Four more turned up in the **decomposed-derived-type path**, and together
they mean something worth stating plainly: **`vit test-validate` had never been usable on any unit
taking `LocalVariables`** — most of ROSCO's controllers, `CableControl` among them. Every failure
arrived at build time with nothing pointing back at the generator.

| # | defect | effect |
|---|---|---|
| 3 | a **nested derived-type field** emitted a Fortran *comment* (`! TODO: nested derived type TYPE(piParams)`) while still naming itself in the argument list | *"Symbol `vit_localvar_piP` has no IMPLICIT type"* — the bridge did not compile at all |
| 4 | a **CHARACTER field** declared `CHARACTER(1024), VALUE` | gfortran rejects it outright |
| 5 | a **fixed-size array field** fell through to the scalar branch | see below |
| 6 | the ISO_C kind written as the literal `REAL(C_DOUBLE)` in three branches; **assumed-*size*** arrays are not assumed-*shape* and fell through to the scalar branches | every `REAL(ReKi)`/`REAL(SiKi)` argument got a `REAL(8)` dummy; `avrSWAP(*)` was declared as a scalar |

**Defect 5 is the one to remember.** The bridge declared one `REAL(C_DOUBLE), VALUE` and assigned it
to the whole array, so **every element received the same value**; and being `VALUE` it could not be
written, so there was **no copy-back** and the field was invisible as an output.

`CableControl` writes `LocalVar%CC_ActuatedL(I_GROUP)` — its principal result. A harness built on
that bridge could neither vary those elements independently nor read any of them back.

**That is the per-element blindness of the #55 defect, rebuilt inside the tool meant to catch it.**
The #55 stride collapse produced wrong *per-element* values; a bridge that broadcasts one scalar into
every element and never returns them cannot see per-element differences at all. Two independent
layers of the apparatus lost the same distinction, for unrelated reasons.

All six are fixed in `translation-loop/vit-patches/`, and **the `CableControl` bridge now compiles for
the first time** — 1280 lines, ~500 parameters, `vit_CntrPar_OL_CableControl_ptr/_rows/_cols`
reachable.

## 7. What remains, and the exact blocker

`VIT_INTEGRATION.md` §7's fourth criterion — *a generated harness kills a known escape* — is **not
met**. `CableControl`'s artifacts now generate cleanly (1280-line bridge, three callee bridges,
`vit_CntrPar_OL_CableControl_ptr/_rows/_cols` reachable), and the blocker is one specific thing:

**The bridge takes a decomposed flat list; the C++ takes struct pointers.** For an argument whose
type has ALLOCATABLE fields, `test_validate` emits ~500 `vit_<arg>_<field>_ptr` / `_n` parameters,
while `cablecontrol.cpp` takes `controlparameters_view_t*`. `emit.py` emits one call shape and needs
two, plus the allocation model for pointer fields — which is `statevary.py`.

Both sides are driven by the same per-field data, and both conventions are now known:

| side | shape |
|---|---|
| Fortran | `_decomposed_param_names(arg, type_info)` — **the same helper the bridge itself uses**, so the two agree by construction rather than by both re-deriving names |
| C++ | the view struct: `double* OL_CableControl; int32_t n_OL_CableControl_rows; int32_t n_OL_CableControl_cols;` |
| non-decomposed args | `<arg>_ptr` (`void*`) on the Fortran side, a struct pointer on the C++ side |

Each side needs **its own buffers**, or the second call sees the first's mutations.

Next session starts there. Everything else it needs is built, compiled and measured.

## References

- `translation-loop` — `harness/emit.py`, `scripts/vit_harness.py`, `scripts/vit_mutate.py`
- `translation-loop/vit-patches/` — the two VIT fixes, staged, with the rationale
- Dev note `202608090118` — corrected here at §4
- Dev note `202608090039` — the M13 reflection
