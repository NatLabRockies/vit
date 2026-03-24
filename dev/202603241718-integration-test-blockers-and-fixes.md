# Integration Test Blockers and Fixes
**Date:** 2026-03-24
**Status:** in-progress (2 of 3 blockers fixed, 1 architectural issue remaining)
**Scope:** Integration of 28 verified C++ translations into the real ROSCO build

## Context

After verifying all 9 Phase 7B translations (40/40 each) and completing the 10-function Controllers regression, we attempted the full integration test: reset ROSCO to clean state, integrate all 28 functions, rebuild, and diff against the pure-Fortran baseline. Three blockers emerged, each hiding behind the previous one.

## Blocker 1: View-Type Stash in Integration Context (FIXED)

**Error:** `Symbol 'vit_original_controlparameters' not found in module 'vit_controlparameters_view'`

**Root cause:** The view-type stash (`vit_original_controlparameters`) was added to the view populator module for kernel verification — callee bridges need it to access the original Fortran type. But:
- The `vit_controlparameters_view.f90` in the ROSCO source tree was generated before the stash was added
- The wrapper generation (`generate_fortran_wrapper`) unconditionally added stash references
- In integration, there are no callee bridges — C++ calls C++ directly via `_c` entry points

**Fix:** Added `is_kernel=False` parameter to `generate_fortran_wrapper()`, threaded through to `_generate_view_preamble()` and `_get_view_use_statements()`. Stash code (USE import and pointer assignment) is only emitted when `is_kernel=True`. `kernel_verify.py` passes `is_kernel=True`; `integrate.py` uses the default `False`.

**Commit:** `c782d62` (VIT)

## Blocker 2: save_config() Drops Config Sections (FIXED)

**Error:** `assumed_size_arrays` config in vit.yaml disappeared after `vit integrate --apply`

**Root cause:** `save_config()` in `config.py` reconstructs the YAML from scratch using only fields it knows about. It builds a `data` dict from `VitConfig` fields and dumps it via `yaml.dump()`. The `assumed_size_arrays` and `dll_persistence` config sections were parsed on load (into `config.assumed_size_arrays` and `config.dll_persistence`) but never written back by `save_config()`.

Every call to `record_translation()` (which `vit integrate --apply` and `vit verify` both call) triggers `save_config()`, silently destroying these config sections.

This also explained why the config was missing from vit.yaml in the first place — earlier sessions that ran `vit verify` or `vit integrate` destroyed it.

**Fix:** Added `assumed_size_arrays` and `dll_persistence` to the `kgen` section in `save_config()`, conditional on non-empty values.

**Commit:** `c782d62` (VIT), `7971730` (ROSCO — added config back to vit.yaml)

## Blocker 2b: avrSWAP Kind Mismatch in Integration (FIXED)

**Error:** `Type mismatch in argument 'avrswap': passed REAL(4) to REAL(8)`

**Root cause:** `avrSWAP` is `REAL(ReKi)` where `ReKi = SELECTED_REAL_KIND(P=6, R=37) = C_FLOAT = REAL(4)`. The BIND(C) interface expects `REAL(C_DOUBLE) = REAL(8)`. In kernel verification, this was handled by a post-generation regex patch (`_patch_assumed_size_kind_mismatch`). Integration had no equivalent.

**Fix:** Moved the conversion from a kernel-only post-patch to inline generation in `generate_fortran_wrapper()`. Added `assumed_size_arrays` parameter. The wrapper now detects assumed-size array kind mismatches via `_find_kind_mismatch_arrays()` and emits a BLOCK with temporary conversion:

```fortran
BLOCK
    REAL(C_DOUBLE) :: vit_tmp_avrswap(2000)
    vit_tmp_avrswap = REAL(avrSWAP(1:2000), C_DOUBLE)
    CALL pitchcontrol_c(vit_tmp_avrswap, ...)
    avrSWAP(1:2000) = REAL(vit_tmp_avrswap, KIND(avrSWAP))
END BLOCK
```

This works in both kernel and integration contexts.

**Commit:** `c782d62`, `822d0f0` (VIT)

## Blocker 3: Untranslated Callee in Integration (OPEN)

**Error:** `OSError: undefined symbol: pitchsaturation_c`

**Root cause:** PitchControl's C++ translation calls `pitchsaturation_c()` — the C entry point for PitchSaturation. But PitchSaturation is a ControllerBlocks.f90 function (Phase 8), not yet translated. In the kernel, this works because VIT auto-generates callee bridges (Fortran BIND(C) wrappers that call the original Fortran function). In integration, there are no bridges — every `_c` entry point must be provided by an actual translated C++ file.

**The dependency chain:**
```
PitchControl (C++, Phase 7B)
  └─→ calls pitchsaturation_c()
        └─→ PitchSaturation (Fortran, NOT translated — Phase 8)
              └─→ undefined symbol at runtime
```

Other Phase 7B functions that PitchControl calls (IPC, ForeAftDamping, FloatingFeedback, ActiveWakeControl) are all translated — their `_c` entry points exist. But PitchSaturation is in ControllerBlocks.f90 and was never part of Phase 7B.

**The fundamental issue:** VIT's kernel verification and integration have different callee requirements:
- **Kernel:** Bridges auto-generated for ALL callees (translated or not) — C++ can call any function
- **Integration:** Only translated functions have `_c` entry points — C++ can only call translated functions

This means **a function cannot be integrated until ALL its callees are also translated**, or VIT generates integration-time bridges.

**Options:**
1. **Translate PitchSaturation first** — simplest. It's a Phase 8 function but has a known golden fixture (PitchControl's kernel already bridges it). Extract, translate, verify, then integrate PitchControl.
2. **Generate integration bridges** — analogous to kernel bridges. For each untranslated callee, generate a Fortran BIND(C) wrapper in the integration build. This would be a new VIT feature.
3. **Defer PitchControl integration** — integrate the other 27 functions (all of which have no untranslated callees), defer PitchControl to after Phase 8.

Option 3 is the safest for now. The 27-function integration test would validate the infrastructure, and PitchControl joins once PitchSaturation is ready.

Option 2 is the most general — it eliminates the ordering constraint entirely. But it requires designing and implementing integration bridges, which is non-trivial (different from kernel bridges because they must compile into the real CMake build).

## State of the Integration

With blockers 1 and 2 fixed:
- All 28 functions integrate successfully (`vit integrate --apply` × 28 = 28 successes)
- The build compiles and links with 27 of 28 functions
- PitchControl fails at runtime due to missing `pitchsaturation_c`
- Config sections survive through the integrate/save cycle

## Commits This Session

### VIT repo
| Commit | Description |
|--------|-------------|
| `c782d62` | Fix integration wrappers: is_kernel parameter, save_config preserves config, inline BLOCK conversion |
| `822d0f0` | Thread assumed_size_arrays through integration pipeline |

### ROSCO repo
| Commit | Description |
|--------|-------------|
| `7971730` | Add assumed_size_arrays and dll_persistence to vit.yaml |

## Lessons Learned

1. **Kernel and integration are different contexts with different requirements.** Features added for kernel verification (stash variables, post-generation patches) don't automatically work in integration. Every kernel feature needs an integration counterpart or a clean "not needed in integration" guard.

2. **Config persistence is critical infrastructure.** `save_config()` silently destroying sections is a class of bug that causes cascading failures. The fix is simple (write back what you read), but the symptom (config disappears after unrelated operation) is hard to trace.

3. **Callee dependency ordering matters for integration but not verification.** KGen verification handles untranslated callees via auto-generated bridges. Integration has no such mechanism. This means the integration order is constrained by the call graph — a function can't be integrated until all its callees are.

4. **The git checkout trap.** Running `git checkout <commit> -- file` to reset source files can interact with uncommitted changes to other files in unexpected ways. In our case, `vit integrate --apply` modified vit.yaml (translations section), then `git checkout` for source files appeared to leave vit.yaml alone, but a subsequent `save_config()` call from integrate overwrote it with the version missing the new config sections.
