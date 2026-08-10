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

Four outputs differ per failing case. `LocalVar.CC_DesiredL` diverges at **element 1** — the
*second* cable group, the first index a contiguous read gets wrong; element 0 is bit-identical, as it
must be — and it propagates to `CC_ActuatedDL`, `LocalVar.FP` and `avrSWAP`. Seeds 0–4 fail 2, 2, 7,
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

## The mutation score found the R2 defect, and then found my misdiagnosis of it

`CableControl`, 71 mutants of the shipped translation:

| case set | score | killed |
|---|---|---|
| before the R2 fix (332 cases) | 0.803 | 57 / 71 |
| shipped (1376 cases, ladder and R6 stratified over flags) | **0.845** | 60 / 71 |

The 14 survivors of the first run are what found the R2 hole. Of the 11 that remain, **10 are
explained and 1 is a real gap** — each located at its source line rather than guessed at:

| survivor | line | why |
|---|---|---|
| `aviFAIL < 0` → `<= 0`; the same condition negated; `0` → `1` | 100 | the `ErrMsg` blindness |
| `char tmp[1024]` → `[1025]`; `1024` → `1025`; `sizeof - 1` → `- 2` ×2 | 101–104 | same block, same blindness |
| `double row_slice[n_cols]` → `[n_cols + 1]` | 49 | larger VLA, extra element never read — **equivalent** |
| `0, 0.0 // no InitialValue` → `1.0` | 73 | `has_InitialValue` is 0, so the callee ignores it — **equivalent** |
| `col < n_cols` → `<=` | 50 | writes one past the VLA; `interp1d` gets `n_cols` as the length and never reads it |
| `for (int I_GROUP = 0; ...)` → `I_GROUP = 1` | 44 | **real.** Skips cable group 0 in the open-loop fill; needs `CC_Mode == 2 ∧ Ind_CableControl[0] > 0` |

Seven of the eleven are the CHARACTER-field gap `vitbridge.py` already records: CHARACTER
*arguments* cross the bridge, CHARACTER *fields of a derived type* do not, so nothing in the
error-message block is compared by any harness on this boundary. The last row is the useful output:
the score does not merely say 0.845, it names **the one input condition the rules still
under-reach** — and it is the same conjunction the escape needed.

### The misdiagnosis, and why it is worth writing down

Every R6 neighbour is ULP scale (`nextafter`, `L - |L|·1e-12`) while `harness/mutate.py`'s
`const_tweak` perturbs a constant by ±1. Seeing `500 → 501` survive, I concluded the operator set
and the input rules disagreed about scale, and widened R6 with `L ± 1`.

**Both halves of that were wrong, and only measurement showed it.**

- `nextafter(500)` distinguishes `> 500` from `> 501` perfectly well. The mutant was alive because
  the *whole R6 stage ran at one value of every flag*, and the threshold is inside `CC_Mode == 1`.
  Stratification alone kills it.
- The widened R6 was scored on the real unit and changed **nothing**: same 0.845, same eleven
  survivors. It also made `harness/independence.py` report `R_ladder` as **dead** on the tiny
  campaign — everything the ladder uniquely killed was killed by the widened R6, which by the
  repository's own doctrine makes them variants of one principle rather than two rules.

So it is not shipped. Three attempts at this one mutant, in order: rotate the flags (no change,
measured), widen R6 (no change, measured, and costs a rule its independence), stratify (0.803 →
0.845). **Two of the three fixes I was confident in did nothing**, and the only reason that is
known is that each was scored on the real unit rather than reasoned about.

Cost, since §6.4 budgeted it as "seconds-per-mutant, not milliseconds": ~45 minutes for 71 mutants
on a 467-parameter unit, dominated by recompiling the generated harness at `-O2`, not by the case
count. Adding cases is nearly free; adding mutants is not.

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

## The corpus: two of the five "escapes" never happened

`escape_coverage()` — the sixth exit criterion, and the number the method is meant to be judged on
— is now computable. Getting there meant checking each entry of `VIT_INTEGRATION.md` §8's escape
list against the record instead of against the list, and **two of the five are not escapes**:

- **`ForeAftDamping`** (with `FloatingFeedback`) contributed a *gate blindness*, not a wrong
  translation: their gain is `0.0` in every scenario, so the gate cannot see a scaling error, which
  is exactly what the miniature's `scaled_sum` reproduces. The unit verified 40/40, and no wrong
  `foreaftdamping.cpp` exists in either workspace or in the replication tree.
- **`FlapControl`** appears in the record only for extraction difficulties and two *upstream
  Fortran* bugs — an uninitialised `K` in the `Flp_Mode==2` init block, and an uninitialised `n` in
  `PreFilterMeasuredSignals`. The "dropped `* R2D`" attribution has no source anywhere.

Both had already been copied out of §8 into later documents, including my own summary this session.
**A labelled positive that never happened inflates the denominator of the one number the method is
judged on**, so recording them would have been worse than recording nothing.

What is real, and what is now in the corpus:

| escape | artifact | passed | verdict |
|---|---|---|---|
| `CableControl` — contiguous read of a strided row | translation | kernel 42/42, gate 429/0 | **caught**, 1376 cases |
| `NonDecreasing` — `<= 0` written `< 0` | translation | all 27 scenarios | **missed**, by construction |
| `ComputeTau2` — `a*x**2` written `a*x*x` | translation | KGen 20/20 | not yet run |
| `CheckInputs` — wrapper with no copy-back | **integration** | kernel 1/1 over 426 fields, gate 429/0 *both ways* | unreachable by any input rule |

`scripts/corpus_rosco.py` assembles each from a campaign tree at run time rather than vendoring it,
so the only campaign text stored in the loop repository is the **wrong** line — the opposite of an
answer key. Each entry cites the dev note that documents it, and a test asserts that.

### Three states, not two

`escape_coverage()` reported `1/3` on its first run, which was three different things wearing one
label. It now distinguishes:

- **caught / missed** — a harness was run against the recorded escape and the answer was observed.
- **unmeasured** — nobody has run it. `ComputeTau2` is here. Counting an unknown as a miss makes the
  rules look weaker than anything yet shows; omitting it silently makes them look stronger. Same
  rule as NOT_APPLICABLE never printing like a pass.
- **unreachable** — `CheckInputs`' defect is in the *integration wrapper*, and the harness calls the
  translation. No choice of inputs reaches it, so it is out of the denominator and printed anyway;
  the instrument for it is re-running the purpose-built harness against the integrated build
  (`SPEC.md` §8.2). It stays in the corpus because it is the most expensive escape the reference
  campaign has.

Result: **1 of 2 measured reachable escapes caught.**

### The miss is the finding

`NonDecreasing` is separated from its accepted translation by exactly one input — an array with two
equal adjacent elements — and **R5's last clause makes every generated array pairwise distinct**.
Measured, offline, on the generator itself: **0 plateaus in 201 cases.** It is not bad luck; the
rule set cannot produce the input.

**The clause that catches `CableControl` is the clause that hides `NonDecreasing`.** R5's
distinctness exists because equal array elements make a wrong gather agree with a right one; a
plateau is exactly what an equality-boundary predicate needs. Closing this is a change to R5 in
`SPEC.md`'s normative set — not a range file, not a generator tweak — and after today it should not
land without a mutation score behind it. `tests/test_corpus_escapes.py` pins the bind, so relaxing
R5 turns that test red rather than quietly changing what the corpus reports.

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
