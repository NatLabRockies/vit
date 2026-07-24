# AFI_Init as an Explicit Special Case: Completing AirfoilInfo (Demonstration)
**Date:** 2026-07-24
**Status:** completed — applied to the tree and committed (17/17 baselines)
**Scope:** OpenFAST AeroDyn (AirfoilInfo module completeness), init-function policy

## What this is

A non-destructive demonstration that AirfoilInfo's initialization function `AFI_Init`
can be re-integrated as C++ — as an explicitly-labeled hand-written special case, not via
a tool mechanism — and that its leaf helper `CheckValuesAreUniqueMonotonicIncreasing` can
stand as its **own independently-compiled, independently-verified C++ unit** rather than an
inlined copy. Together this makes the module genuinely **20-of-20 in C++ as 20 separate
build units**, passing the full baseline gate. **17/17 AeroDyn baselines pass bit-identical.**
The tree was restored to its reverted state afterward; this note records the result and the
case for adopting it.

## Why revisit the revert

Dev note `202605011207` reverted `AFI_Init` (and `DBEMT_Init`) to Fortran. Re-reading the
record separates two distinct concerns that the revert bundled:

1. **Tool fragility (legitimate, unchanged).** VIT's `--auto-allocate` mechanism — built for
   `DBEMT_Init` — extracted allocation logic by regex-matching OpenFAST coding-style patterns
   (`ErrStat2`/`ErrMsg2`, Registry `Copy` loops, backward `IF/END IF` guard tracking). An audit
   correctly found this was *coding-style-specific*, i.e. codebase-specific logic drifting into
   the tool, which violates VIT's core standard ("no codebase-specific logic… general
   mechanism with per-project configuration"). Keeping `--auto-allocate` out of the init path is
   the right call and is **not** disturbed here.

2. **A value judgment (revisited).** The revert also concluded init functions shouldn't be
   translated incrementally at all — the allocation wrapper (60–190 lines) exceeds the translation
   (25–100 lines) for zero runtime benefit, so "defer to wholesale conversion." This is where the
   `AFI_Init` revert is worth reconsidering.

**Key correction to the record:** `AFI_Init` was *never* done via `--auto-allocate`. It was
*already* a hand-written two-pass special case (`afi_init_pass1_c`/`pass2_c` + a hand-written
Fortran allocation wrapper). So the tool-fragility objection (1) never applied to `AFI_Init`; it
was reverted only under the value judgment (2).

## The precedent: ROSCO did exactly this and kept it

ROSCO's `ReadControlParameterFileSub` is a hand-written two-pass ALLOCATE function
(`ReadControlParameterFileSub_pass1`/`_pass2`) that ships in the pure-C++ `integrated` build
(`src/ReadSetParameters/readcontrolparameterfilesub.cpp`). ROSCO's reflection note explicitly
sanctions "manual integration for ~10 functions where the pattern was unique… `integrate_all.sh`
captured these one-off patterns reproducibly," naming *two-pass ALLOCATE protocols* among them.

So hand-writing `AFI_Init`'s wrapper:
- **does not touch the tool** (the no-codebase-specific-logic rule governs the tool, not
  per-function hand-integration recorded in a script), and
- is the established ROSCO edge-case pattern — "a special case, called what it is."

AeroDyn and ROSCO simply made opposite calls on the same pattern: ROSCO hand-wrote its two-pass
ALLOCATE init/IO functions and shipped them in the incremental C++ build (cleaned up in the
wholesale conversion); AeroDyn reverted them. The `AFI_Init` revert optimized for migration
efficiency; re-integrating optimizes for module completeness and for making the "first
complete module" claim literally true.

## Demonstration

In-place, fully reversible (the baseline harness — r-test submodule, committed baselines,
hardcoded `/workspace/openfast`, existing build — lives in the main tree, so a separate worktree
was impractical; the tree was restored to byte-identical afterward and the reverted binary rebuilt).

Steps:
1. Restored the `AFI_Init` C++ two-pass wrapper into `AirfoilInfo.f90` (from pre-revert `20bf69efd`;
   that file differs from HEAD *only* in `AFI_Init`).
2. Restored `afi_init.cpp` (`pass1`/`pass2`), **but removed the inlined `static` `CheckValues…`
   helper** and replaced it with a forward declaration, so the call resolves to the standalone unit.
3. Added the standalone verified translation `checkvaluesareuniquemonotonicincreasing.cpp`
   (`translations/AirfoilInfo/`, `bool …(const double*, int)`, includes `vit_nwtc.h` for `EqualRealNos`)
   into the build tree.
4. Added **both** `src/afi_init.cpp` and `src/checkvaluesareuniquemonotonicincreasing.cpp` to
   `modules/aerodyn/CMakeLists.txt` (left `dbemt_init.cpp` out — `DBEMT_Init` stays Fortran).
   `vit_translated.h` already declares `afi_init_pass1_c`/`pass2_c`.
5. Incremental build of `aerodyn_driver` (clean, no warnings). Both `afi_init.cpp.o` **and**
   `checkvaluesareuniquemonotonicincreasing.cpp.o` compiled as independent objects.
6. `verify_aerodyn_baselines.sh all`.

Result:

```
Summary: 17 passed, 0 failed
```

All 17 cases (including the UA/DBEMT/OLAF/MHK paths that exercise the spline coefficients
`AFI_Init` computes, and the multi-table paths that call `CheckValues…`) are bit-identical.
`DBEMT_Init` was untouched and remained Fortran.

## `CheckValues…`: standalone unit, not inlined

The pre-revert integration inlined `CheckValuesAreUniqueMonotonicIncreasing` as a `static` helper
in `afi_init.cpp` (`202604271433` §110, justified as "rather than adding a build dependency"). That
is the anti-pattern VIT's own discipline forbids (CLAUDE.md: *"Never inline a function… If a
function exists in Fortran, call it through a bridge or translate it — don't copy its body. Every
instance of 'I'll just inline this' in this project has been wrong."*). This demonstration corrects
it: `CheckValues…` is compiled as its own unit and *called*, not copied. It is cheap and clean here
because it is a pure leaf (`const double*, int → bool`, no allocation, no view struct), its **only**
caller is `AFI_Init`'s C++ `pass1` (so no Fortran bridge is needed — a C++ declaration suffices), and
it was already translated with a `test-validate` harness (11/11 on record). Net effect: the module
is a true **20/20 of separately-compiled, separately-verified units**, and the verified translation —
not a hand-copy that can silently drift — is what ships.

**Linkage gotcha (learned during integration):** `CheckValues…` must be declared with **C++
linkage** — a plain forward declaration at its call site in `afi_init.cpp`. It must **not** go in
`vit_translated.h`: that header is one big `extern "C"` block (for the Fortran⇄C bridge functions),
and a C-linkage declaration against the C++ definition produces an *undefined reference* at link.
Since `CheckValues…` is only ever called from C++ (inside `pass1`), C++ linkage is correct and no
bridge is involved.

## Effect on the completeness claim

- Before: 18/20 AirfoilInfo functions compiled as C++ (`AFI_Init` and its inlined
  `CheckValues…` in Fortran). The "first complete module / all 20 translated" wording overstates
  this.
- With this change: **20/20 in C++ as 20 separately-compiled, separately-verified units** (not
  "19 + 1 inlined fragment"), module genuinely complete, no fragile tool logic, direct ROSCO
  precedent.

## Scope and honesty

- This is **not** a blanket reversal of `202605011207`. The init-function policy and the
  `DBEMT_Init` revert stand. This re-integrates **`AFI_Init` only**, for AirfoilInfo completeness,
  as a deliberately-labeled special case.
- The ~190-line two-pass wrapper is **transitional scaffolding**, exactly like ROSCO's — it
  disappears when AirfoilInfo converts wholesale to pure C++ (`ALLOCATABLE` → `std::vector`), at
  which point no bridge is needed.
- If adopted, record it in `integrate_all.sh` (the ROSCO-style manual-integration ledger) and
  update the AirfoilInfo count in `CLAUDE.md` (currently stale at "20"; the accurate current build
  is 18, and this change makes it a true 20).

## What was applied (this commit)

1. `AirfoilInfo.f90` — restored the `AFI_Init` C++ two-pass wrapper (from `20bf69efd`).
2. `afi_init.cpp` — restored, inlined `static CheckValues…` removed, replaced with a **C++ forward
   declaration** of the standalone unit at the call site (not `vit_translated.h` — see linkage
   gotcha above).
3. `checkvaluesareuniquemonotonicincreasing.cpp` — the verified translation, added to
   `modules/aerodyn/src/`.
4. `CMakeLists.txt` — added `src/afi_init.cpp` and `src/checkvaluesareuniquemonotonicincreasing.cpp`.
5. `vit.yaml` — restored the `AFI_Init` entry (only; `DBEMT_Init` stays out).
6. `scripts/integrate_all.sh` — added both `.cpp` to the Step-3 committed-restore list and updated
   the comment (`reset_to_clean.sh` auto-stubs new `.cpp` via glob, so it needs no change).
7. Rebuilt + `verify_aerodyn_baselines.sh all` → **17/17**. `CLAUDE.md`'s "all 20 functions in C++"
   is now accurate (twenty separate build units), so it was left as-is.

`CheckValues…` was **not** re-run through `vit test-validate` here — the committed `.cpp` is byte-for-byte
the previously-verified translation (11/11 on record), and the end-to-end gate is the bit-identical
17/17. Re-running `test-validate` is a reasonable extra confirmation if desired.

## Related

- `202605011207` — init-function revert (the decision this qualifies for `AFI_Init`)
- `202604271433` — original `AFI_Init` two-pass integration (17/17)
- ROSCO `PROJECT_PLAN_ROSCO.md` — `ReadControlParameterFileSub` two-pass, hand-integrated, pure-C++
