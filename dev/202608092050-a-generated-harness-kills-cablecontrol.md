# A generated harness kills CableControl's escape — and the rules that let it through first

**Date:** 2026-08-09
**Status:** completed
**Scope:** translation-loop `harness/`, VIT `test-validate`, the ROSCO escape corpus

## What happened

`VIT_INTEGRATION.md` §7's fourth exit criterion — *a generated harness kills a known escape* — is
met. Three runs, one unit, same rules and same comparison in all three:

| translation | stated ranges | result |
|---|---|---|
| shipped (correct) | `ranges.toml` | **PASS** 332 checked, 0 failed |
| **escaped** (contiguous read) | `ranges.toml` | **FAIL** 332 checked, **2 failed** |
| escaped | `ranges-asfound.toml` (one cable group) | PASS 332 checked, 0 failed |

The escape is defect #55 reconstructed verbatim: `CableControl` slices
`CntrPar%OL_CableControl(I_GROUP, :)` out of a column-major rank-2 array, and the wrong translation
reads it contiguously (`[I_GROUP * n_cols + col]`) instead of at stride `n_rows`. It passed kernel
replay 42/42 and the 33-scenario gate 429/0.

The first divergence is `LocalVar.CC_DesiredL` element 1 — the *second* cable group, the first index
a row-major read gets wrong — propagating to `CC_ActuatedDL` and `avrSWAP`. Seeds 0–4 fail 2, 2, 7,
8, 8 cases; the correct translation passes all five. `PIController` still passes, 1201/0.

**The third row is the result, not the second.** Pinning the cable-group count to 1 makes only row
index 0 of `OL_CableControl` reachable, and at index 0 a stride-`n_rows` gather and a contiguous read
address the same memory. That is the configuration every campaign scenario had —
`ALLOCATE(OL_CableControl(N_OL_Cables, ...))` where `N_OL_Cables` counts the groups with
`Ind_CableControl > 0` (`ReadSetParameters.f90:749`). On that domain the escaped translation passes
the *same 332 cases*. **What made #55 invisible was the input domain, not the verification method.**
That has been the project's claim since `PLAN.md` §7.6; this is the first time it has been shown
rather than argued, on a real defect, with the control run alongside.

## The part that matters more than the kill

**The finished harness passed the escaped translation on its first run.** 292 cases, 0 failures,
every rule reporting "applied".

That is the exact failure this project keeps finding in other people's greens, produced by the
apparatus built to detect it. Measuring instead of adjusting found two defects in the rule set,
both general:

### R2 put every flag value in a case, and nothing else in that case

`CC_Mode == 2` guards the whole open-loop branch. R2 emitted one case per declared flag value, so
that branch was entered in **1 of 292 cases**, at base values for all 28 other varied parameters —
and the escaped line inside it was reached **0 times**, while the coverage report said
`applied  R2_flag_values  5 value(s) across 2 flag(s)`.

A branch entered at one point of the input space is a branch tested at one point. R2's stated
category is *dead branch*, and it was closing it in the weakest sense the words allow.

A flag value is now a **stratum**: each value is held while everything else is redrawn. Additive in
the number of flags — the full cross product is exponential and is not what the rule claims. The
random fill also varies flags now, which it did not: the one stage that varies many inputs at once
had been exploring a single branch of every mode selector in the signature.

### An array ramped across its whole range has a fixed sign per element

`_fill_array` produced a monotone ramp across the declared range, which is what makes elements
pairwise distinct (R5's last clause). It also fixes every element's *sign*: on `[-4, 4]` with three
elements the ramp is always about `[-2, 0, 2]`, so `Ind_CableControl(1) > 0` — the guard inside the
branch — was false in every case ever generated.

This is R5's degenerate shape moved into the data: the extent varies and the values do not. Randomised
cases now ramp across a random sub-interval, so the array is sometimes wholly positive. Still
monotone and distinct, deliberately: a breakpoint table must stay ordered for the reference's own
search to mean anything, and equal neighbours divide by zero in an interpolation.

After both fixes: `CC_Mode == 2` in 27 of 332 cases, the escaped line reached in 2 with more than one
row, and the escape dies.

## `statevary.py` is not what §6.2 described

§3.1 concluded "perturb captured state, do not construct it", on the grounds that a constructed
`ControlParameters` is usually inadmissible and an inadmissible case makes *both* implementations
fail identically — a green establishing nothing.

Measuring the bridge changed the argument. `vit test-validate` **decomposes** a derived type with
ALLOCATABLE fields: the bridge takes one parameter per field and builds a fresh Fortran value from
them **on every call**. An unset allocatable is therefore unallocated, not garbage; an unset scalar
is zero, not whatever the last call left there. Admissibility is free on every field the unit does
not read.

What still needs judgement is the fields it *does* read — and those need bounds whether the base
state was captured or constructed. `CC_Group_N` is a trip count over a 12-element array;
`CC_GroupIndex` indexes `avrSWAP`; `objInst%instPI` indexes a 1024-slot state array. A capture
supplies none of those bounds once R5 starts varying the extents around them. **Capture removes less
work than it appeared to**, and it costs a dependency on reading KGen's binary state format from
Python — a format whose only reader is Fortran.

So the module decides, per parameter, `varied` or `held`:

- **the read set comes from the reference's Fortran body, not the translation.** §6.2 said the .cpp
  "names them", which is true and circular: a translation that *dropped* a read would tell the
  harness not to vary the field whose omission is the defect. Both are one regex over one function,
  so the safe source costs nothing.
- a bare argument used without `%` (`CALL interp1d(..., ErrVar)`) marks the whole argument read.
  Over-approximating costs cases; under-approximating costs coverage silently.
- **`held` is the honest word.** Not "covered by the capture", not "tested" — a field the harness
  supplied one value for because the reference never looks at it. For `CableControl`: 29 varied of
  467, **438 held**, and the artifact says a defect confined to one of the 438 is invisible to it.

Admissible ranges live in a committed `harness/ranges.toml` per campaign — 10 entries for
`CableControl`, each with the reason in a comment. Every varied parameter left on the ±1e3 default
is printed. `statevary.constrain()` exists because `Param.type` was being set by the mapping and
never resolved: a range could be written down, filed, and have no effect on any generated value.

## Three more places VIT's generators disagree with each other

The previous session found six defects reaching a *compiling bridge*. Reaching a compiling **C++
call** found three more.

1. **`build_c_params` silently drops a derived-type argument that is neither C-compatible nor
   configured `strategy: view`.** `LocalVariables` has one ALLOCATABLE CHARACTER field
   (`ACC_INFILE`), so `build_c_params` omitted it — while the bridge generator decomposed it into
   ~180 parameters and the shipped `cablecontrol.cpp` took it as `localvariables_t*`. Three
   generators, three answers, one type. A dropped argument cannot produce a C++ declaration that
   matches the translation, so this should be an error rather than a `continue`.

   Fixed by configuring `LocalVariables: strategy: view`, which is what today's `vit translate` does
   automatically (`CLAUDE.md`, AeroDyn workflow). The view struct's `char* ACC_INFILE; int32_t
   n_ACC_INFILE;` then matches the decomposed bridge exactly, where the plain struct's
   `char ACC_INFILE[1024]` with no extent member did not.

2. **`vit test-validate` writes `vit_types.h` and `Makefile` per UNIT into a per-MODULE directory.**
   Two units in one module overwrite each other's struct header. Running it for `PIController` left
   a `vit_types.h` holding only `piparams_t`; the next `CableControl` build then failed on a type VIT
   had generated for it an hour earlier.

3. **`vit_complex_double`** is what `type_map` emits for `COMPLEX(8)`, and it is defined only inside
   a freshly generated `vit_types.h`. Any consumer of the skeleton that does not also regenerate the
   header gets an undeclared type. (I recorded this as "a type no VIT header defines" before checking
   a current header — it is defined, in the one VIT writes alongside the skeleton.)

## Where the code is

- `translation-loop/harness/statevary.py` — new; read set, dispositions, `constrain`
- `translation-loop/harness/emit.py` — the decomposed call shape, float32 stream elements, C-struct
  member scanning, per-side allocatable buffers
- `translation-loop/harness/generate.py` — R2 strata, sub-range array fills, int-array fills, plan
  awareness
- `translation-loop/harness/vitbridge.py` — assumed-size arrays from `vit.yaml`
- `translation-loop/tests/test_statevary.py` (25), `tests/test_emit_decomposed.py` (13)
- `harness-scratch/ROSCO/harness/ranges.toml`, `ranges-asfound.toml`,
  `translations/Controllers/cablecontrol_escape.cpp` — the fixture. Scratch tree, not a campaign.

## What is left

`escape_coverage()` — the sixth criterion. The corpus has no ROSCO entries, so the number the method
should be judged on ("of the defects that escaped, how many would the rules catch now?") is still not
computable. #55 is now one labelled positive with a measured verdict; the other four
(`ForeAftDamping`, `FlapControl`, `ComputeTau2`, `CheckInputs`) are not in `harness/corpus.py`.

Also: `vit harness` / `vit mutate` are still scripts rather than `vit/cli.py` subcommands, and the
three VIT defects above are recorded but not patched.
