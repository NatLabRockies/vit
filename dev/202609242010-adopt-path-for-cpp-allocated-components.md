# Adopt Path: C++ That ALLOCATEs View Components, With No Hand-Written Wrapper
**Date:** 2026-09-24
**Status:** completed (uncommitted; evaluation tree is openfast worktree `openfast-adopt`, branch `adopt-eval`)
**Scope:** `vit/view_populator.py`, `vit/interface_gen.py`, `vit/derived_type_parser.py`, `tests/test_adopt_roundtrip.py`, `tests/fixtures/adopt/`

## Problem

Some routines ALLOCATE components of a derived-type argument. Every `INTENT(OUT)` initializer does, because its argument arrives with all components deallocated. A view hands C++ each ALLOCATABLE component as (pointer, extents) into Fortran's own storage. C++ has nothing to write into and cannot allocate a Fortran allocatable. So every such routine got a **hand-written two-pass wrapper**: C++ reports sizes, Fortran allocates, then C++ fills.
- ROSCO: `ReadControlParameterFileSub`.
- AeroDyn: **two**, `ReadAFfile` and `AFI_Init`. Dev note `202609221200` §14 counted one; it is corrected there.

These were the one place where the agent wrote boundary code.

Two tool mechanisms existed, and neither covered these cases:
- **`--auto-allocate`** regex-copied the original's ALLOCATE statements. It was rejected as coding-style-specific (`202605011207`).
- **`--copy-arrays` / `vit_view_in_<type>`** (rosco-r2, `9c77367`) has three problems:
  - It DEALLOCATEs every component before copying. It is a use-after-free for any component the C++ did not replace, so the caller had to name the argument exactly.
  - It turns unallocated components into allocated-empty ones.
  - It hits `ERROR STOP` on ALLOCATABLE TYPE() arrays and rank ≥ 3. That is exactly AFI's `Table(:)` and `SplineCoefs(:,:,0:3)`.

## Design

**One call.** The C++ allocates with `vit_adopt_alloc_lb(nbytes, rank, lb1, lb2, lb3)` (or `vit_adopt_alloc`, which uses all-ones bounds), fills in column-major order, and sets the component's pointer and extents in the view. The generated wrapper then calls the view module's `vit_adopt_<type>`, which:
1. ALLOCATEs the Fortran component with those extents and lower bounds;
2. copies the data in;
3. frees the buffer.

No side effect runs twice.

**Per-field decision from a pre-call snapshot, not from naming arguments.** The wrapper calls `vit_snapshot_<type>` after populating the view.
- Pointer and extents unchanged: written in place or untouched; left alone.
- Pointer changed: adopted.
- Pointer set to NULL: deallocated.

This is what makes the path safe on every component, and it fixes `--copy-arrays`' use-after-free. ALLOCATABLE TYPE() arrays of view types are adopted **one level deep**. Children's components go through `vit_adopt_<child>` and their scalars through `vit_copy_scalars_to_<child>`; deeper nesting stops loudly.

**Lower bounds travel in a 32-byte header in front of the buffer.** The header holds a magic number, the base pointer, the rank and the lower bounds; `vit_types.h` defines it, header-only.
- The view struct has no lower-bound members. Adding them would change its C layout under headers already generated: `generate_consolidated_types_header` keeps existing blocks, and `vit translate` keeps existing populators, so a stale side would mismatch silently.
- The first attempt read lower bounds from the source's ALLOCATE statements. It failed on AeroDyn: every field is also allocated as `x(LB(1):UB(1))` by the Registry's Copy/Unpack routines, so every field came back undetermined. Stating the bounds in the C++, the same way the translated ALLOCATE does, is the general answer.

**Frees with libc `free()`** through a BIND(C) interface. So a view module never adds an undefined reference that a build has to satisfy, and there is no build-system change.

**Refused, never guessed** (`ERROR STOP` naming the field):
- a buffer without the header (for example plain malloc);
- a header of the wrong rank;
- a replaced pointer equal to another component's pre-call storage;
- the same pointer with different extents;
- a replaced pointer on a component kind that is not adopted: CHARACTER arrays, rank > 3, opaque TYPE(), deferred-length CHARACTER.

**Wrapper wiring:**
- Automatic for `INTENT(OUT)` view arguments, together with the scalar reverse copy.
- For `INOUT` arguments, the ones `--copy-arrays` names now go through adopt instead of `vit_view_in`.
- `USE ... ONLY:` lists are now wrapped one name per line. The longer lists passed 132 columns, which the first mutation run caught.

## Evaluation

**Round trip (`tests/test_adopt_roundtrip.py`, 7 tests).**
- The fixture has AFI's shape: rank-1 arrays, and an ALLOCATABLE `Table(:)` whose elements hold rank-1, rank-2 and rank-3 `(n-1, 2, 0:3)` arrays.
- The original Fortran and VIT's generated wrapper plus a C++ translation are compared on:
  - every allocation status, bound and value, bit for bit;
  - error branches that return part-allocated;
  - an INOUT call that replaces one component, writes one in place and leaves one untouched.
- It passes on macOS and, under AddressSanitizer (ASan) with leak detection, in the container.
- Red tests all fail as they should: no adopt call; a dropped lower bound (bounds mismatch); plain malloc, wrong rank, a Fortran-storage pointer and an in-place extent change (each refused); a skipped free (LeakSanitizer); freeing the data pointer instead of the base (ASan double-free).
- The old `vit_view_in` path cannot run this shape at all: it stops on `Table`.

**AeroDyn, generated wrappers only.** Worktree at `db0796299`, branch `adopt-eval`.

| Stage | Change | 17 baselines |
|---|---|---|
| 0 | unmodified HEAD (hand-written wrappers) | 17/17 |
| 0' | ReadAFfile, AFI_Init restored to original Fortran (`edab46311`) | 17/17 |
| 1 | `vit integrate ReadAFfile ... --reverse-copy --copy-arrays p`; single-call C++ | **17/17** |
| 1-red | adopt call removed from the ReadAFfile wrapper | 0/17 |
| 2 | `vit integrate AFI_Init ...` (no flags; INTENT(OUT) adopts automatically); single-call C++ calling `readaffile_c` directly | **17/17** |
| 2-red | adopt call removed from the AFI_Init wrapper | 0/17 |
| 2-red' | SplineCoefs lower bound stated as 1 instead of 0 | 17/17: **the gate cannot see it** |

- The last row is a real blind spot. In the integrated build no Fortran code indexes `SplineCoefs` (grep: none); every reader is C++ through the view, which does not depend on lower bounds. So lower-bound fidelity is guarded by the round-trip test, not by the baselines.
- An ASan build of `aerodyn_driver` (libasan linked, runs terminated normally) ran `ad_MHK_RM1_Fixed` (multi-table, secondVals), `ad_BAR_SineMotion_UA4_DBEMT3` (UA; CalculateUACoeffs on C++-owned child views) and `ad_B1n2_OLAF`. There were 0 ASan errors and no leak report.
- vit test suite: **976 passed** (969 existing + 7 new).

**Hand-written code this replaces had a latent defect:** fixed buffers `numalf_out(100)`, `ncoefstab_out(100)`, `secondVals_buf(100)`, `spline_dim1/2(100)`, with no bounds check. An airfoil file with more than 100 tables would overflow the stack. The generated path has no such limit.

## Limits

- One level of ALLOCATABLE TYPE() nesting. Deeper nesting stops with a message.
- ALLOCATABLE CHARACTER arrays and rank > 3 are refused if replaced.
- Lower-bound fidelity rests on the translation stating them, checked by the round-trip test. The AeroDyn gate cannot observe them (above).
- Echo to a Fortran unit (`UnEc`/`UnEcho`) is not reachable from C++. The hand-written versions already dropped ReadAFfile's echo; AFI_Init's single echo header line is now dropped too. None of the 17 cases writes an echo file whose content is compared.
- Not re-run: ROSCO `ReadControlParameterFileSub` in rosco-r2, which was integrated with the old `--copy-arrays`. Its wrapper would now go through adopt. Its C++ must then allocate with `vit_adopt_alloc`, or the new refusal fires.
- `AFI_Init` / `ReadAFfile` are verified by simulation only, as before. Init code runs once and KGen cannot sample it.

## Files

- **VIT:** `view_populator.py` (`_adopt_routines`, snapshot arrays, header type, libc `free` interface); `interface_gen.py` (`_adopt_arg_names`, `_generate_adopt_epilogue`, wrapped USE lists; `--copy-arrays` now adopts); `derived_type_parser.py` (`_adopt_alloc_lines` in every generated header, including existing ones).
- **Tests:** `tests/test_adopt_roundtrip.py`, `tests/fixtures/adopt/`.
- **AeroDyn evaluation** (worktree, uncommitted): `translations/AirfoilInfo/{readaffile,afi_init}.cpp` rewritten as single calls. The generated wrappers are in `modules/aerodyn/src/AirfoilInfo.f90`.
