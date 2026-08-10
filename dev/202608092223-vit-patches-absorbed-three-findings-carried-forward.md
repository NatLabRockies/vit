# `vit-patches/` is absorbed — and three VIT defects it was carrying

**Date:** 2026-08-09
**Status:** completed — all three fixed 2026-08-09, VIT `3f386d8`
**Scope:** VIT `test_validate` / `interface_gen` / `type_map`; retiring `translation-loop/vit-patches/`

## Why the directory existed, and why it is gone

`translation-loop/vit-patches/` held two `format-patch`es against VIT. They were written while the
ROSCO replication was live, and the checkout they belonged in
(`vit_translation/vit`) sat inside that run's tree, which could not be written to. So
they were staged in the loop repository instead — the wrong repository, deliberately and
temporarily.

Both are now in `unify-vit`, pushed to `origin`. The directory is deleted, because a second copy of
a fix that already shipped is not a backup: it is a second source of truth, and the one that rots is
always the one nobody runs.

**Verified before deleting, rather than assumed.** Every substantive line the two patches add was
checked against the branch: **167 of 167 present**, with a single exception —
`lines.append("LIBS += -lgfortran -lm")`. That one is absent because the fix was *generalised* after
the patch was cut: a compiler → runtime-libs mapping (`"gfortran": "-lgfortran -lm"`,
`"ftn": ...`) driven from config replaced the hardcoded literal. The line is gone; the fix is not.

(A first attempt at this check reported both patches clean via `git apply --check --reverse`. It was
wrong — `git apply` failed on context drift and the shell's `&&` keyed off the following `head`, so
the reassuring message printed regardless of the result. A verification that cannot fail is not one.)

The six `test-validate` defects the patches carry are recorded in dev note `202608091210` §6 and are
not restated here.

## The three findings that were NOT patched — now fixed

**All three are fixed in `3f386d8`**, verified on `CableControl` against the real tree, not only
on fixtures. Each section below keeps the diagnosis and records what the fix was.

These were appended to the directory's README as they were found and would have gone with it. Each
is a disagreement between two VIT generators about the same type, and each is still open.

### 1. `build_c_params` silently drops a derived-type argument it cannot map

A derived type that is neither C-compatible nor configured `strategy: view` hits a bare `continue`
in `build_c_params`, and the argument vanishes from the C++ call.

ROSCO's `LocalVariables` has one ALLOCATABLE CHARACTER field (`ACC_INFILE`), which is enough to make
it not C-compatible. So `build_c_params` omitted it — **while `generate_fortran_bridge` decomposed
the same argument into ~180 parameters, and the shipped `cablecontrol.cpp` took it as
`localvariables_t*`.** Three generators, three answers, one type. `vit test-validate`'s own skeleton
therefore declared a C++ function with four arguments directly above an `#include` of a translation
taking five.

Worked around by configuring `LocalVariables: strategy: view`, which is what today's `vit translate`
does automatically (`CLAUDE.md`, AeroDyn workflow) and which makes all three agree — the view
struct's `char* ACC_INFILE; int32_t n_ACC_INFILE;` matches the decomposed bridge exactly, where the
plain struct's `char ACC_INFILE[1024]` with no extent member did not.

**The fix is not the config.** A dropped argument cannot produce a call that matches the translation,
so this should be an error naming the argument and the reason, not a `continue`. The reason it has
not been made one: AeroDyn has genuine passthrough types that must keep being skipped, so the
predicate needs to distinguish "deliberately not crossed" from "no strategy configured", and that
distinction does not exist in the code today.

### 2. `vit test-validate` writes per-UNIT files into a per-MODULE directory

`vit_types.h` and `Makefile` are written to `translations/<Module>/`, but their *contents* are
per-unit: the struct header holds only the types that unit needs, and the Makefile names that unit's
objects. Two units in one module overwrite each other's.

Concretely: running it for `PIController` left a `vit_types.h` containing only `piparams_t`, and the
next `CableControl` build failed on `controlparameters_view_t` — a type VIT had itself generated an
hour earlier. The same collision makes `make -C <module> test` build whichever unit's Makefile
landed last, which is how a `CableControl` case file got fed to a `PIController` binary and exited
with a stream-desync rather than anything readable.

Mitigated on the consumer side — `scripts/vit_harness.py` re-runs `vit test-validate` for the unit
immediately before building — but the artifacts should be per-unit or per-unit-named.

### 3. `vit_complex_double` is emitted but defined only alongside a fresh header

`type_map` maps `COMPLEX(8)` to the C type name `vit_complex_double`, and that typedef exists only
inside a `vit_types.h` that VIT has just generated. Any consumer of the skeleton that does not also
regenerate the header gets an undeclared type.

This is how it presented: `LocalVariables` has `COMPLEX(DbKi) :: AWC_complexangle(3)`, the generated
`extern "C"` declaration named `vit_complex_double*`, and nothing in scope defined it. I recorded it
at first as "a type no VIT header defines", which was wrong — a freshly generated header defines it
three hundred lines up. The defect is the coupling, not the absence.

## What the fixes were

1. **The drop stays a drop; it stops being silent.** A genuine passthrough must keep being dropped
   from a production call, and nothing in the type model separates "deliberately not crossed" from
   "no strategy configured yet" — so `build_c_params` still skips. What is new is
   `dropped_derived_args()`, which reports each omission with its reason and the remedy, and a
   refusal in `generate_test_harness_skeleton` when the bridge needs an argument the C++ call
   lacks. **That check is local to test-validate and has no false positives**: there the harness
   must supply what the bridge passes, so a missing parameter means the skeleton cannot work
   whatever the intent elsewhere. It is gated on a registry actually being supplied — without one
   both sides are consistently degraded and the absence is a fact about the call, not the function,
   which is the same line `harness/vitbridge.py` draws.

   Verified by removing `LocalVariables: strategy: view` from ROSCO's `vit.yaml`:

       ERROR: CableControl: the Fortran bridge passes 1 argument(s) that build_c_params omits
       from the C++ call, so the two disagree about this function's arity and nothing built
       from them can link:
           LocalVar : TYPE(LocalVariables)
               ALLOCATABLE field(s), nested derived-type field(s); set `strategy: view` for
               this type in vit.yaml to cross it

   That is the same failure that used to arrive hours later as an unreadable link error.

2. **Output moved to `translations/<Module>/<unit>_test/`.** Per-unit, so all four collisions —
   `vit_types.h`, `Makefile`, the `test` binary, and `clean`'s `rm -f *.o test` — are fixed
   structurally rather than by a naming convention some future artifact could forget to follow.

3. **The complex typedefs go into the consolidated header unconditionally**, not only when a type
   in it happens to have a complex field. The include guard makes repeats harmless, and two unused
   typedefs cost less than one undeclared identifier a thousand lines from its cause.

**Six existing tests went red, and were right to.** Three passed no registry at all — the gated
case, not a disagreement. Three used a fixture with an ALLOCATABLE field and no view strategy,
which is precisely the configuration that cannot work; the fixture is now configured and every
decomposition assertion in it is untouched. A test that fails when a defect is fixed is a test that
was pinning the defect.

## References

- Dev note `202608091210` §6 — the six `test-validate` defects the patches carry
- Dev note `202608092050` — the harness work that surfaced all three of these
- `translation-loop` `VIT_INTEGRATION.md` §7.1 — the same three, in the integration record
