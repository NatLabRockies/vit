# vit test-validate: Audit Findings — Issues to Fix
**Date:** 2026-04-26
**Status:** in-progress (issues identified, fixes pending)
**Scope:** vit test-validate generality, hardcoded values, architectural cleanup

## Background

`vit test-validate` was implemented and proven end-to-end on `AFI_ValidateInitInput` (7/7 tests, C++ ≡ Fortran). However, a rigorous audit found 8 issues — several are OpenFAST/AeroDyn-specific code that should be general, and one is an architectural problem (dual `vit_types.h` files).

The tool is in `vit/vit/test_validate.py` (~300 lines) with CLI registration in `vit/vit/cli.py`. Commits: `f2c8632` through `810eb1a`.

## Issue 1: Hardcoded Library Paths (CRITICAL)

**File:** `test_validate.py`, `generate_test_makefile()`, lines 257-264

**Problem:** The Makefile generator hardcodes AeroDyn-specific library paths:
```python
lines.append(f"LIBS = {build_dir}/modules/aerodyn/libbasicaerolib.a \\")
lines.append(f"       {build_dir}/modules/aerodyn/libaerodynlib.a \\")
lines.append(f"       {build_dir}/modules/aerodyn/libaeroacousticslib.a \\")
lines.append(f"       {build_dir}/modules/nwtc-library/libnwtclibs.a \\")
lines.append(f"       {build_dir}/modules/seastate/libseastlib.a \\")
lines.append(f"       {build_dir}/modules/inflowwind/libifwlib.a \\")
lines.append(f"       {build_dir}/modules/version/libversioninfolib.a \\")
lines.append(f"       -lopenblas -lstdc++ -lmvec -lm -ldl -lgfortran")
```

ROSCO has a completely different library structure (`libdiscon.so`). Any other Fortran project would have its own libraries.

**Fix:** Add a `test_validate.libs` (or similar) field to `vit.yaml` config that lists the libraries needed for test linking. VIT auto-populates a default by scanning the CMake build directory, but the user can override. The Makefile generator reads from config instead of hardcoding.

Alternatively, derive libraries from the CMake target's `link.txt` file (at `build/modules/<target>/CMakeFiles/<target>.dir/link.txt`). VIT already knows the CMake target name from `vit.yaml` (`build_system.target_name`).

**Impact:** Without this fix, `vit test-validate` only works on AeroDyn. ROSCO users or any other project can't use it.

## Issue 2: Hardcoded CHARACTER Buffer Size (8196)

**File:** `test_validate.py`, `generate_fortran_bridge()`, line 136

**Problem:**
```python
local_decls.append(f"        CHARACTER(8196) :: {arg.name}")
```

`8196` is `ErrMsgLen` from OpenFAST's `NWTC_Base.f90`. Other Fortran codebases use different error message lengths (ROSCO uses 1024 in some places). The value should not be hardcoded.

**Fix options:**
1. Read from `vit.yaml` config (e.g., `fortran.errmsg_len: 8196`)
2. Parse the CHARACTER length from the function signature if it has a parameterized length (`CHARACTER(ErrMsgLen)`)
3. Use a large default (e.g., 8192) with a comment that it can be adjusted

Option 1 is most explicit. Option 3 is simplest and "good enough" for most cases — CHARACTER buffers just need to be large enough, the exact value doesn't affect correctness for validation testing (we only compare ErrStat, not ErrMsg content).

**Impact:** Low for correctness (buffer just needs to be big enough), but the hardcoded value is project-specific knowledge that shouldn't be in VIT core code.

## Issue 3: Types Module Naming Convention (`_Types`)

**File:** `test_validate.py`, `generate_fortran_bridge()`, line 163

**Problem:**
```python
types_modules.add(f"{sig.module_name}_Types")
```

Assumes derived types live in a module named `<ModuleName>_Types`. This is an OpenFAST/NREL Registry convention. Other Fortran codebases may define types in the same module as the functions, in a separate `types` module with a different naming pattern, or in a shared types module.

**Fix:** Instead of guessing the types module name, scan the source file's USE statements for the module that defines the derived type. VIT's `fortran_parser.py` already extracts USE statements from function bodies. The bridge generator should:
1. Look at the target function's USE statements
2. Find which module exports the derived type
3. Add that module to the bridge's USE list

Fallback: if the type is accessible through the function's own module (re-exported), no extra USE is needed. The current `USE {sig.module_name}` (without ONLY) already gets everything the module re-exports. The `_Types` USE is only needed when the module has `PRIVATE` default and doesn't re-export the type — which is the OpenFAST pattern.

**Impact:** The bridge won't compile on projects that don't follow the `_Types` convention.

## Issue 4: `vit test-validate` Overwrites Existing Test Files

**File:** `cli.py`, `cmd_test_validate()`, around line 1235

**Problem:** Every invocation of `vit test-validate` regenerates `_test.cpp` with the skeleton, overwriting any tests the LLM already wrote. During implementation, this required repeatedly saving/restoring the test file with `cp /tmp/test_save.cpp`.

**Fix:** Before writing the skeleton, check if the file exists and contains non-skeleton content (e.g., has more than just TODO comments). If it does, skip writing and print a message: "Test file already exists with implementation — skipping skeleton generation. Use --force to overwrite."

The Makefile and bridge can be regenerated freely (they're deterministic from the signature). Only the test file (which contains LLM-written code) should be protected.

## Issue 5: Dual `vit_types.h` Files (ARCHITECTURAL)

**Files:** `translations/vit_types.h` and `modules/aerodyn/src/vit_types.h`

**Problem:** Two files with the same name, different content, serving different pipeline stages:
- `translations/vit_types.h` — generated by `vit translate`, has ALL types including not-yet-integrated ones. Has a known bug: bad array syntax (`[1:3]` instead of `[3]`).
- `modules/aerodyn/src/vit_types.h` — generated by `vit integrate`, has only already-integrated types. Correct syntax.

The test-validate Makefile needs include path ordering tricks to find the right one. During the session, I manually added `AFI_InitInputType` to the production `vit_types.h` to work around the divergence.

**Root cause:** `vit translate` creates its own copy in `translations/` instead of updating the canonical file in the source directory. This was a design choice from early VIT development (keep translations self-contained) that doesn't scale.

**Fix:** There should be ONE canonical `vit_types.h` at `modules/aerodyn/src/vit_types.h` (or wherever the project's C headers live — configurable via vit.yaml). Both `vit translate` and `vit integrate` should update this single file. The `translations/vit_types.h` should be removed.

This also requires fixing the `vit translate` array syntax bug (`[1:3]` → `[3]`) so the canonical file has correct syntax.

**Impact:** Include path confusion, stale types, duplicate maintenance. Every new tool that needs type headers (test-validate, future tools) hits this issue.

## Issue 6: Unused `ErrMsg_stat` Bridge Parameter

**File:** `test_validate.py`, `generate_fortran_bridge()`, line 133

**Problem:** For CHARACTER arguments, the bridge generates:
```python
c_params.append((f"        INTEGER(C_INT), INTENT(OUT) :: {arg.name}_stat", f"{arg.name}_stat"))
```

This creates a parameter `ErrMsg_stat` in the bridge signature that is never written to. The Fortran bridge uses a local CHARACTER buffer and ignores the `_stat` parameter entirely. The C++ test passes `&msg_stat` which is never read.

**Fix:** Two options:
1. Remove the `_stat` parameter entirely — the bridge only returns ErrStat. But BIND(C) requires matching the extern "C" declaration in C++.
2. Use it: set `ErrMsg_stat` to the length of the error message, or to a hash, so C++ can verify the message was set.

Option 2 adds verification value. Option 1 is simpler. For now, document that the parameter is a placeholder and CHARACTER comparison happens at the integration-test level (16/16 baselines), not at the unit-test level.

Actually, the cleanest fix: remove the `_stat` parameter and change the bridge to only have `(ptr, ErrStat)`. The ErrMsg isn't needed for validation testing — ErrStat alone determines pass/fail. This simplifies both the bridge and the extern "C" declaration.

## Issue 7: `config_dir` Derived from `source_dir.parent` (Fragile)

**File:** `test_validate.py`, `generate_test_makefile()`, lines 227 and 238

**Problem:**
```python
config_dir = str(Path(source_dir).parent) if source_dir else "."
```

Assumes `config_dir` (where `vit.yaml` lives) is one directory above `source_dir`. This works for OpenFAST (`source_dir: modules` → config_dir = project root) but breaks if `source_dir` is nested differently (e.g., `source_dir: src/fortran`).

**Fix:** The `VitConfig` dataclass already has a `config_dir` field (set during `load_config()`). Pass `config.config_dir` to `generate_test_makefile()` instead of deriving it from `source_dir`.

**Impact:** Would break on projects with non-standard source directory nesting.

## Issue 8: OpenFAST Indentation Comment

**File:** `test_validate.py`, `generate_public_patch()`, line 79

**Problem:**
```python
indent = "   "  # Match typical OpenFAST indentation
```

Minor cosmetic issue — the comment references OpenFAST specifically. The indentation should either match the existing indentation in the file (auto-detected) or use a neutral default.

**Fix:** Auto-detect indentation from the existing PUBLIC declarations in the file. Or just remove the comment and use a reasonable default.

## Priority Order

1. **Issue 1 (hardcoded libraries)** — blocks use on any non-AeroDyn project
2. **Issue 5 (dual vit_types.h)** — architectural debt that affects multiple tools
3. **Issue 3 (types module convention)** — blocks bridge compilation on non-OpenFAST projects
4. **Issue 4 (overwrites test files)** — developer friction, loses work
5. **Issue 7 (config_dir derivation)** — breaks on non-standard layouts
6. **Issue 2 (CHARACTER buffer size)** — minor, just needs a reasonable default
7. **Issue 6 (unused _stat parameter)** — cleanup, no functional impact
8. **Issue 8 (indentation comment)** — cosmetic

## Files Involved

| File | Issues |
|------|--------|
| `vit/vit/test_validate.py` | 1, 2, 3, 6, 7, 8 |
| `vit/vit/cli.py` | 4 |
| `vit/vit/config.py` | 1 (new config field), 7 (pass config_dir) |
| `vit/vit/translate.py` | 5 (stop creating translations/vit_types.h) |
| `openfast/vit.yaml` | 1 (new libs config field) |
| `openfast/translations/vit_types.h` | 5 (remove) |
| `openfast/modules/aerodyn/src/vit_types.h` | 5 (make canonical) |

## What Works Well (Keep)

- **Prompt-based design** — the LLM writes tests, VIT provides raw materials. No brittle regex.
- **Dual-call pattern** — calling both C++ and Fortran, asserting agreement. Rigorous.
- **PRIVATE function patching** — compiling locally with PUBLIC added. Clean solution.
- **C_F_POINTER bridge for derived types** — correct approach for non-BIND(C) types.
- **Linking against production build** — no KGen needed, no project rebuild.
- **Test storage at `translations/<Module>/_test.cpp`** — right place, right convention.
