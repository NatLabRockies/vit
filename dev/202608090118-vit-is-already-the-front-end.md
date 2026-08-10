# VIT Is Already the Front-End, and the Measurement That Follows

**Date:** 2026-08-09
**Status:** completed — M14; spec in `translation-loop/VIT_INTEGRATION.md`
**Scope:** method; the loop↔VIT integration

## Placement

OpenFAST workspace, outside the replication's container mount. The work itself is in
`translation-loop` at `b526ee2`. **Do not merge into the ROSCO-workspace checkout until
the replication run has ended.** Everything below was measured against the *frozen* campaign
(`vit_translation/ROSCO`), read-only — parsing and text mutation, no builds, no writes.

## 1. The claim that was wrong

`PLAN.md` M11–M13 recorded that `harness/` needs Fortran and C++ front-ends written, and that this
was "the next work and it is not small."

**It is false.** M11's own entry says the opposite — *"`vit test-validate` already generates the
bridge, skeleton, patched source and Makefile, so the hand-written part was only ever the inputs"* —
and I contradicted it one turn later, reasoning from memory about a codebase I had just read.

Second instance of the same error in two days. It belongs with `202608090039`'s §4: the corrections
come from a person asking, and this one came from the user asking *"but isn't that the point of
VIT?"*

## 2. What VIT already provides

| capability | symbol |
|---|---|
| intent, rank, dims, optionality | `vit/fortran_parser.py` — `FortranArg` |
| **the exact C++ call shape** | `vit/interface_gen.py` — `build_c_params` |
| BIND(C) bridge; PRIVATE patch; Makefile vs production archives; skeleton | `vit/test_validate.py` |
| C view structs; captured state | `derived_type_parser.py`, `view_populator.py`, `vit extract` |

**`build_c_params` already emits what the six rules need**, because it and they answer the same
question from opposite ends:

- `n_<x>` / `n_<x>_rows` / `n_<x>_cols` — R5's extent handle, synthesised because a Fortran
  assumed-shape descriptor never reaches C++
- `has_<x>` for OPTIONAL args — R2's enumerated parameter, which **the miniature does not have at
  all**, so R2 has never had anything to act on until now
- pointers for `INTENT(OUT/INOUT)` — R4's outputs

**The seam is one function.** `generate_test_validate_prompt` asks an LLM to write
`make_valid_input()`. That, and only that, is what M11 replaces.

## 3. The measurement that decides the architecture

```
CableControl(avrSWAP, CntrPar, LocalVar, objInst, ErrVar)
   C params:  float* avrSWAP
```

**One parameter.** The rank-2 array whose stride collapse caused the #55 defect — `OL_CableControl` —
is not an argument at all. It is a *field* of `CntrPar`.

| type | fields | allocatable | rank-2 |
|---|---:|---:|---:|
| `ControlParameters` | **213** | 68 | **3** |
| `LocalVariables` | 168 | 0 | 0 |
| `piParams` | 5 | 0 | 0 |

Exactly three rank-2 arrays exist in the whole parameter type, and two are `OL_CableControl` and
`OL_StructControl` — the #55 defect and the open item it left.

**Therefore: perturb captured state, do not construct it.** Inventing 213 fields produces
inadmissible combinations; both implementations then fail identically and the comparison is green
having established nothing — the project's own failure mode rebuilt inside the fix for it. VIT
already captures real state. The generator's job is to make its *shape* non-degenerate:
admissibility comes free from the capture, and the fields with a varying extent are a list.

## 4. Built, and validated against production code

- **`harness/vitbridge.py`** maps `CableControl` to **449 params, 71 detected extents, all three
  rank-2 arrays reachable by name.** `PIController` maps to 14 with `piP` expanded.
- **`harness/cppmutate.py`** produces the #55 defect from the shipped `cablecontrol.cpp` —
  `[col * n_rows + I_GROUP]` → `[col]` — **without being told where to look.** Masks comments,
  string literals and preprocessor lines first, so no mutation can land in prose.
- **`tests/test_vit_integration.py`** — 12 tests against the real tree. The only external validity
  the repository has; everything else runs on eight units I wrote containing defects I planted.

## 5. Three defects, all mine

1. **A synthesised extent name collided with a real field.** `ControlParameters` carries an
   allocatable `WE_CP` *and* a scalar `WE_CP_n`. `Signature.validate()` caught it. The fix is better
   than the bug: **detect the existing extent field rather than invent one** — the codebase already
   uses that convention, and varying the field the code actually reads is more faithful.
2. **Without a type registry the mapping reported false blindness.** `build_c_params` omits every
   derived type when none is supplied, so `piP` read as "structurally unobservable" when it is an
   observable `piparams_t*` with one. Absence rendering as a value, inside the bridge. It now
   refuses to claim observability without a registry.
3. **The mutator rewrote pointer declarations** (`double* xData` → `double`). Those fail to compile
   and so count as killed — not wrong, but noise: a mutant testing the compiler rather than the
   instrument.

Also: `apply_type_strategies` takes plain strings. Passing the YAML shape `{"strategy": "view"}`
**silently does nothing**, which is how I first got `ControlParameters` reported as passthrough.

## 6. Two structural blindnesses of this boundary

Properties of the apparatus, not defects in VIT, and both now reported per unit:

- **CHARACTER arguments are never compared.** The bridge declares them as a local
  `CHARACTER(8192)` and passes nothing to C++ — its own comment says *not compared across the
  language boundary*. **A defect confined to an error message is invisible to any harness built
  here.**
- **Passthrough derived types vanish.** `build_c_params` skips non-C-compatible types entirely.

## 7. Cost correction

*"A mutant is a function call"* was true of the Python front-end and **does not port**. A C++ mutant
needs one translation unit recompiled and relinked against the production archives — seconds, not
milliseconds. That is a normal translate-iterate step, which a campaign already pays dozens of times
per function, so the number is measured; but any budget must use it rather than the miniature's.

## 8. What remains, and the first target

`statevary.py` (perturb captured state), `emit.py` (cases into VIT's skeleton), and
`vit harness` / `vit mutate`. All need the container.

**ROSCO first, not AeroDyn.** It is the only codebase carrying the labelled escape set this method
needs — `CableControl`, `ForeAftDamping`, `FlapControl`, `ComputeTau2`, `CheckInputs` — each
documented, each having passed every layer. Those turn internal consistency into external validity,
and `escape_coverage()` is computable the moment they are in the corpus. AeroDyn's 176 derived types
hit the hard problem immediately.

**Exit criterion that matters** (`VIT_INTEGRATION.md` §7): *a generated harness kills a known
escape.* Everything else can be true while the harness establishes nothing.

## References

- `translation-loop/VIT_INTEGRATION.md` — the full spec, pipeline, sequencing
- Dev note `202608090039` — the M13 reflection this extends
- Dev note `202608081128` — the kernel verifies the translation, not the integration
