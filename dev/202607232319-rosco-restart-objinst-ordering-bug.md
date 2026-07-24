# ROSCO C++ restart bug: objInst reset ordered before ReadRestartFile

**Date:** 2026-07-23
**Status:** completed (bug found, root-caused, fixed, verified)
**Scope:** ROSCO C++ DISCON restart path (`translations/discon.cpp`); verification methodology

## TL;DR

The ROSCO pure-C++ restart (checkpoint/restart via `avrSWAP` status `iStatus -8/-9`) was
**not** faithful to Fortran — a **silent** bug, because the 27-scenario simulation gate never
issues `-8`/`-9`. Under a clean apples-to-apples comparison (integrated pure-C++ vs
modified-Fortran, both provenance-verified) the continuous path was bit-identical but the
**restart path diverged by ~4.2e6** (6/13 output arrays).

**Root cause:** `discon.cpp` reset the `objInst` filter/limiter *instance counters* to 1
**before** the `iStatus==-9` `ReadRestartFile`. Those counters are in the checkpoint, so
`ReadRestartFile` restored their end-of-step (high) values, clobbering the reset. The
controllers then indexed the wrong `FP`/`piP`/`rlP` per-instance state slots on the restart
step → wrong filter/rate-limiter state → divergence. Fortran resets the counters in
`SetParameters`, which runs *after* `ReadRestartFile`, so it is correct.

**Fix:** move the `objInst` reset in `discon.cpp` to *after* the `-9` `ReadRestartFile`
(matching Fortran's ordering). **Verified:** Test B (C++ restart vs modified-Fortran restart)
now PASSES bit-identically on both the continuous and restart paths.

## How it was found (and a false start worth recording)

The restart pair was one of ROSCO's 10 simulation-only functions and had **no** behavioral
verification (the gate never exercises `-8`/`-9`). A dedicated harness
(`ROSCO/Examples/vit_restart_test.py`) drives the real `-8` (write checkpoint) / `-9` (restart
read) DISCON dispatch and compares outputs.

Two verification bars were tried:
- **Test A — roundtrip self-consistency** (does a restarted run reproduce the uninterrupted
  run?): FAILS for BOTH C++ and Fortran, because ROSCO restart is checkpoint-restore +
  `SetParameters` re-init, not a bit-exact clone. So roundtrip self-consistency is the WRONG
  bar.
- **Test B — cross-language equivalence** (C++ restart output vs Fortran restart output): the
  RIGHT bar. The checkpoint file formats differ by design, but a faithful C++ restart must
  reproduce the Fortran restart's *outputs*.

**False start (recorded so it isn't repeated):** an early manual run reported "C++ restart ==
Fortran, worst = 0.0" and the restart pair was briefly declared verified. That was wrong. It
compared against an **ambiguous/stale `libdiscon.so`**: ROSCO's real C++ `DISCON` entry point
exists only on the `integrated` branch (`src/discon.cpp`, 633 lines), while `main`'s
`discon.cpp` is a 1-line stub — so rebuilds from `main` produced a DISCON-less library and lib
provenance got confused across many reset/build cycles. **Lesson: verify binary provenance
(`nm -D … | grep DISCON`, `ldd` for libgfortran vs libstdc++) before trusting any restart
result.** The clean apples-to-apples redo exposed the real ~4.2e6 divergence.

## Localization: serialization was proven CORRECT, then ruled out

Before finding the ordering bug, the checkpoint serialization was audited exhaustively and
**ruled out**:
- **Field list identical.** An expansion-aware diff of the Fortran `WriteRestartFile` WRITE
  sequence (310 explicit fields) vs the C++ shared `checkpoint_fields()` in
  `restart_fields.h` (loops expanded, nested types included) matched **310 = 310, same order**.
  (The C++ uses one shared field list for write and read — the field-ordering-safe design.)
- **Array/matrix sizes match.** `FP` FilterParameters are `[1024]` in both; `WE.P` is
  `[3][3]`, `WE.xh`/`WE.K` are `[3][1]`; `piP`/`rlP` state arrays are `[1024]`. `write_field`
  uses `sizeof`, so full arrays are serialized. No scalar-vs-array truncation.

So the bug was NOT a dropped/reordered/truncated field. That pointed at the restore/dispatch.

## Root cause (the ordering bug)

Diagnostic: at the first restart step the C++ `gen_torque` was ~1000 below both its own
continuous value and the Fortran restart value, and stepped by ~1000/step afterward — the
signature of a **rate limiter** ramping from a wrong previous value. The rate limiter
(`ratelimit`) and the filters read their per-instance state as `FP/piP/rlP[objInst.instX]`.
`objInst.instX` is reset to 1 at the start of each timestep and incremented per call, so a
given call maps to a stable slot.

`discon.cpp` dispatch order (buggy):
```
per-timestep init:  objInst.instLPF = 1; … objInst.instRL = 1;   // reset counters
if (iStatus == -9): ReadRestartFile(… &objInst …);               // RESTORES counters (high)
ReadAvrSWAP(…);                                                    // does not touch objInst
SetParameters(…);                                                 // does not reset objInst
control block: WindSpeedEstimator / …                            // indexes FP/piP/rlP[objInst]
```
`objInst` is in the checkpoint (`restart_fields.h`), so on `-9` `ReadRestartFile` overwrote
the reset with the checkpointed end-of-step counter values (e.g. `instRL` = number of
rate-limiter calls per step). The control block then indexed the wrong state slots.

Fortran (correct) order: `ReadRestartFile` (DISCON.F90:74) → `ReadAvrSWAP` (81) →
`SetParameters` (85), and the counter reset lives *inside* `SetParameters`
(`ReadSetParameters.f90:177-185`) — i.e. **after** the restore. `ReadAvrSWAP` uses no `objInst`
in either language, so only the reset-vs-restore order matters.

## Fix

`translations/discon.cpp`: move the 9-line `objInst.instX = 1;` reset from the "per-timestep
init" block (before the `-9` check) to **immediately after** the `iStatus==-9`
`ReadRestartFile` block (before `ReadAvrSWAP`/`SetParameters`). One block moved down ~30 lines;
no logic change. Works for all paths:
- normal (`iStatus>=0`): `-9` block skipped; reset runs each step as before;
- restart (`-9`): restore then reset → counters = 1 → correct slots;
- first call (`0`): reset runs; correct.

Alternative considered and rejected: dropping `objInst` from the checkpoint read. Rejected
because write and read share one field list — skipping it in the read would desync the stream.
Moving the reset is minimal and matches Fortran exactly.

## Verification

Built the integrated pure-C++ ROSCO with the fix; ran `vit_restart_test.py` against it with the
committed modified-Fortran reference (`restart_baseline/`):
```
[Test B] continuous vs Fortran reference:  13/13 arrays PASS (2399 values each, identical)
[Test B] restart    vs Fortran reference:  13/13 arrays PASS (1200 values each, identical)
RESTART GATE (Test B — C++ restart ≡ Fortran restart): PASS
```
The restart pair is now genuinely bit-identical to Fortran across a checkpoint/restart cycle.

## Scope / where the fix lives

- Canonical fix: `ROSCO/translations/discon.cpp` (restart-validation branch).
- The `integrated` branch's `src/discon.cpp` needs the same move on re-integration (the fix was
  applied there transiently only to build+verify; not committed on `integrated`).
- No VIT-tool change: this is a translation bug (a hand-written C++ dispatch ordering), not a
  generator defect. VIT did not write `discon.cpp`'s dispatch; the human/agent did.

## Implications

- **Accounting.** The ROSCO restart pair was previously listed as "integrated" implying
  equivalence; it was silently wrong and is now genuinely verified. This is a clean example of
  a silent failure: a function that passed every gate because *no gate
  exercised it* — the exact risk of simulation-only verification (see the earlier
  four-functions analysis). It also demonstrates the value of building the targeted `-8/-9`
  harness rather than leaving the pair on code-review only.
- **Methodology.** Two reusable lessons: (1) the correct restart bar is cross-language output
  equivalence, not self-consistency; (2) verify binary provenance before trusting a
  restart/verification pass — a stale/ambiguous library produced a confident wrong "0.0".

## Follow-ups

- [x] **Re-integrated** the fix into `integrated` `src/discon.cpp` (ROSCO commit `bbd58e3`,
      pushed). 27-scenario gate re-run: unchanged — 348/351 arrays identical (2.56M non-trivial
      values), the only diffs the known Scenario 16 `2^-70` artifact. No regression (the fix
      touches only the `-9` path the gate never hits).
- [x] **Audit done — CLEAN.** Cross-checked every assignment to a checkpointed field in the
      C++ `DISCON` body against the checkpoint field list: `objInst` was the *only* checkpointed
      field reset before `ReadRestartFile` (the bug). No other restore-before-reset hazard
      exists. The bit-identical Test B pass corroborates the restart dispatch is fully correct.
      (Method: parse `field_op(f, LocalVar/objInst->…)` from `restart_fields.h`, flag any
      `LocalVar./objInst.<field> =` assignment before the `readrestartfile_c` call.)
- [x] **Carried over to `integrated`** (ROSCO commit `ac393c2`): the restart harness +
      modified-Fortran reference (`Examples/restart_baseline/`) and the PreFilterMeasuredSignals
      `verification: kgen` (21/21) record now live on the deployed branch; Test B verified PASS
      there.
- [ ] Consider whether `objInst` even belongs in the checkpoint (Fortran checkpoints it but
      immediately resets it; it is effectively dead state in the checkpoint). Removing it from
      both write and read would be a cleaner design, but is a format change — defer.

## Cross-references

- Harness + reframe + the false-0.0 retraction: `ROSCO/Examples/vit_restart_test.py`,
  ROSCO commit history on `restart-validation`.
- Why restart was unverified in the first place: the ROSCO simulation-only-10 / four-functions
  analysis (this session).
