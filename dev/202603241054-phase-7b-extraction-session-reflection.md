# Phase 7B Extraction Session Reflection
**Date:** 2026-03-24
**Status:** completed
**Scope:** Phase 7B extraction campaign, infrastructure assessment, forward planning

## Where We Started This Session

We came in with 19 functions verified and a plan to extract the 9 remaining Phase 7B controller subroutines. The infrastructure was proven — 501 tests passing, view types working, callee bridges generalized, verification idempotent. It should have been routine: extract, translate, verify, integrate.

Instead, we spent the entire session on extraction alone — and uncovered a cascade of issues that each required its own investigation and fix.

## What Actually Happened

**We hit five distinct blockers, each hiding behind the previous one:**

1. **KGen's parser couldn't handle Fortran 2003 PROCEDURE POINTER declarations.** Someone at NCAR started implementing support (the `Procedure` class, the `# TEMP` comments), but left it incomplete. We finished their work — 8 changes across 3 files. This was the most straightforward fix.

2. **KGen's state capture code generator can't handle assumed-size arrays.** `avrSWAP(*)` passed to a write function expecting `DIMENSION(:)`. We solved this with a configurable build wrapper patch — `assumed_size_arrays` in vit.yaml. General solution, not KGen-specific.

3. **Stale KGen artifacts contaminate extractions.** Pragmas left in other source files, `elapsedtime/` directories, `.kgen_org` backups — all caused silent extraction failures. Three separate cleanup fixes.

4. **DLL SAVE variable persistence.** This was the deepest and most insidious issue. Python's ctypes doesn't truly unload shared libraries, so Fortran SAVE variables persist between KGen's profiling and state capture phases. This caused:
   - ROSCO's `AlreadyInitialized` guard rejecting reinitialization
   - KGen's invocation counters being stale
   - `aviFAIL=-1` deadlock (SetParameters can't be called when aviFAIL < 0, but SetParameters is the only thing that resets aviFAIL)

5. **ROSCO parameter validation errors.** Three separate validation failures in Scenario 3 (`StC_GroupIndex < 2801`, `IPC_ControlMode + Flp_Mode` conflict, missing `F_FlCornerFreq`) — each setting `aviFAIL=-1` silently, disabling ALL control code for ALL subsequent calls. No error output because DISCON clears the error message at the end of each call.

Each issue only became visible after fixing the previous one. The assumed-size fix revealed the stale artifact problem. Fixing artifacts revealed the DLL persistence problem. Fixing DLL persistence revealed the validation errors. Fixing validation errors finally produced state files.

## What We Built

Beyond the extraction campaign itself, the infrastructure improvements are substantial:

- **KGen parser fix** — PROCEDURE POINTER support, permanent fix in the KGen fork
- **Configurable assumed-size array patching** — `kgen.assumed_size_arrays` in vit.yaml
- **Configurable DLL persistence workaround** — `kgen.dll_persistence` in vit.yaml, zero ROSCO-specific code in VIT
- **`--run-args` CLI option** — per-extraction run command customization
- **Simplified extraction protocol** — single `git checkout` per file, no manual sed
- **Stale artifact cleanup** — pragmas, elapsedtime/, .kgen_org files all cleaned automatically
- **Build wrapper idempotency** — CMakeLists.txt backup guards, kgen_utils dedup

## What Concerns Me

**The extraction protocol is fragile.** It requires a specific combination of git commits for each source file, and the right `--run-args` for each extraction target. This is documented, but it's manual knowledge. A single wrong commit or missing flag produces 0 state files with no useful error message. We should consider automating this — perhaps a `vit extract-all` command that reads extraction targets from a config file and handles the source state management.

**KGen's architecture fights us.** Every issue we fixed was a workaround for a KGen limitation or assumption. KGen assumes the DLL is freshly loaded for state capture (it isn't). KGen assumes arrays aren't assumed-size (they can be). KGen assumes one pragma per codebase (there can be stale ones). We're building an increasingly thick layer of patches around KGen's behavior. Each patch works, but the aggregate complexity is real.

**The debugging methodology was expensive but effective.** The DLL persistence investigation required adding Fortran WRITE statements to instrumented source files, manually running state/Makefiles, and tracing through multiple interleaved control flow paths. It took many iterations because each fix only partially resolved the issue. The systematic approach — adding debug prints at strategic points, running, checking output — was the only way to untangle it. But it consumed significant time.

## The Steps Ahead

**Immediate next: translation.** All 10 Phase 7B functions have golden extraction fixtures (IPC, ActiveWakeControl from the earlier campaign, ForeAftDamping from Phase 4D, and the 7 we just extracted). The translation phase should be more straightforward — `vit translate` generates the scaffold, we write/refine the C++, `vit verify` checks it. The infrastructure for this is proven (19 functions already translated and verified).

**The translation order matters less now.** The generalized callee bridge system means any function can be translated in any order — untranslated callees get bridges automatically. But simpler functions first (FloatingFeedback at 30 lines) still makes sense for building confidence.

**After translation: integration and the full clean build test.** All 28 functions integrated, rebuild, diff against baseline. This is the definitive proof that the translation is correct. With `-ffp-contract=off`, the diff must be empty.

**Phase 8 and beyond.** ControllerBlocks.f90 functions (PitchSaturation, Shutdown, etc.) are next. The extraction infrastructure we built this session — assumed-size patching, DLL persistence workaround, simplified protocol — will carry forward. The hardest infrastructure problems are solved.

## The Broader Pattern

This session reinforced the pattern from Phase 4D: the real work is at the seams between systems. KGen, Python ctypes, ROSCO's DLL interface, Fortran's SAVE semantics, cmake's incremental builds — each works correctly in isolation. The failures emerge from their interactions, and the fixes must respect all of their constraints simultaneously.

The discipline of keeping VIT general (moving ROSCO-specific code to configuration) and validating assumptions (the Filters.f90 bug audit) pays off not just for code quality but for understanding. When the tool is clean, the debugging surface is smaller. When assumptions are validated, you don't waste time chasing phantom issues.

We're in a good position. The extraction infrastructure is robust, tested against the hardest cases (DISCON.F90's DLL interface, Controllers.f90's nested call sites), and documented in detail. The translation phase can begin.
