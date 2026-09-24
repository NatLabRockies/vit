# vit test-validate: All 8 Audit Issues Fixed
**Date:** 2026-04-26
**Status:** completed
**Scope:** vit test-validate generality, hardcoded values, architectural cleanup

## Background

The `vit test-validate` command was implemented and proven end-to-end on `AFI_ValidateInitInput` (7/7 tests, C++ ≡ Fortran). A rigorous audit (dev note `202604261142`) found 8 issues — several OpenFAST/AeroDyn-specific hardcodings that would block use on other projects, one architectural problem (dual `vit_types.h`), and several cleanup items.

This note documents the resolution of all 8 issues across two commits.

## Issue 5: Dual `vit_types.h` (Fixed First — Separate Commit)

**Commit:** VIT `75f8632`, OpenFAST `6801a5ef1`

Two copies of `vit_types.h` existed with the same filename but different content — `translations/vit_types.h` (written by `vit translate`) and `modules/aerodyn/src/vit_types.h` (written by `vit integrate`). They had drifted: different types present, different field representations for the same type (`int8_t` vs `int32_t` for `AFI_UA_BL_Default_Type` LOGICAL fields), and a broken `[1:3]` array syntax in the translations copy.

Investigation showed the translations/ copy was not consumed by any VIT tool at runtime. `vit verify` and `vit integrate` both generate their own fresh copies. The only consumer was a test-validate include-path hack.

**Resolution:** Deleted `translations/vit_types.h`. `vit translate` no longer writes a types header. `vit test-validate` generates `vit_types.h` directly into the test directory (same pattern as `kernel_verify.py`). Production copy at `modules/aerodyn/src/` is the only persistent file. Full details in dev note `202604261210`.

## Issue 1: Hardcoded Library Paths (CRITICAL)

**File:** `test_validate.py` lines 250-258 (was)

The Makefile generator hardcoded 8 AeroDyn-specific library paths (`libbasicaerolib.a`, `libaerodynlib.a`, etc.). Any non-AeroDyn project would need different libraries.

**Resolution:** New function `_parse_link_libraries(build_dir, target_name)` that:
1. Globs for `{build_dir}/**/CMakeFiles/{target_name}.dir/link.txt`
2. Parses the CMake link command — extracts `.a` paths (resolved relative to the link.txt directory) and `-l` flags
3. Skips compiler, object files, output target, `-Wl` rpath options, and `-D`/`-f`/`-O` flags
4. Returns the library list in link order (critical for resolving circular dependencies)

The Makefile generator calls this with `config.target_name` (already in `vit.yaml` as `build_system.target_name`). If link.txt is not found, emits a `# WARNING` comment with empty `LIBS =` for manual fill-in.

**Verification:** The generated Makefile now shows all libraries from the actual CMake link command, including `libaerodyn_driver_subs.a` and `libadilib.a` that weren't in the original hardcoded list, and the system `libopenblas.so` with its full absolute path.

## Issue 3: `_Types` Module Naming Convention

**File:** `test_validate.py` lines 160-165 (was)

The bridge generator hardcoded `USE {module_name}_Types` — an OpenFAST Registry convention. Other Fortran codebases may define types differently.

**Resolution:** New function `_get_module_use_statements(source_text)` that extracts module-level USE statements from the Fortran source file (between `MODULE <name>` and the first `CONTAINS`/`SUBROUTINE`/`FUNCTION`). The bridge generator:
1. Reads the source file
2. Extracts all module-level USE statements
3. Filters out `ISO_C_BINDING` and `ISO_FORTRAN_ENV` (already added or irrelevant)
4. Filters out the function's own module (already USEd)
5. Adds remaining USE statements to the bridge

This correctly picks up `USE AirfoilInfo_Types` from the module header of `AirfoilInfo.f90` without assuming the `_Types` convention. It also picks up `USE NWTC_LAPACK` and any other module-level imports.

**Bug caught during implementation:** The initial module-name filter used substring matching (`sig.module_name.upper() in use_upper`), which incorrectly filtered `AirfoilInfo_Types` as containing `AirfoilInfo`. Fixed to exact-match the module name token.

## Issue 4: Overwrites Existing Test Files

**File:** `cli.py` lines 1225-1227 (was)

Every `vit test-validate` invocation regenerated the test skeleton, destroying LLM-written test implementations. During the original implementation session, this required manual `cp /tmp/test_save.cpp` workarounds.

**Resolution:** Before writing the skeleton, check if the file exists and contains real implementations (detected by absence of `// --- TODO:` markers — skeleton files have these, implementations don't). If real content exists:
- Print: `Test file exists with implementation — skipping: <path>`
- Print: `Use --force to overwrite.`
- Skip the skeleton write only. Bridge, Makefile, and patched source are still regenerated (they're deterministic from the signature).

Added `--force` flag to the `test-validate` subcommand argparser to allow explicit overwrite.

## Issue 7: `config_dir` Derived from `source_dir.parent`

**File:** `test_validate.py` lines 226-227 (was)

`config_dir = str(Path(source_dir).parent)` assumed source_dir is exactly one level below the project root. Breaks on `source_dir: src/fortran` or similar.

**Resolution:** Added `config_dir` as an explicit parameter to `generate_test_makefile()`. The caller passes `config.config_dir` (set correctly during `load_config()` from the `vit.yaml` location). Removed the fragile derivation. The shared_files path resolution block now uses the passed `config_dir` directly.

Also added `target_name` parameter (needed for Issue 1's link.txt parsing).

## Issue 2: CHARACTER Buffer Size 8196

**File:** `test_validate.py` line 136 (was)

Hardcoded `CHARACTER(8196)` — OpenFAST's `ErrMsgLen` from `NWTC_Base.f90`.

**Resolution:** Changed to `CHARACTER(8192)` — a round power-of-2 that's large enough for any project. Added comment: `! Local buffer (not compared across language boundary)`. The exact value doesn't affect test correctness — only `ErrStat` is compared between C++ and Fortran.

## Issue 6: Unused `ErrMsg_stat` Bridge Parameter

**File:** `test_validate.py` lines 131-133 (was)

For CHARACTER arguments, the bridge generated an `INTEGER(C_INT), INTENT(OUT) :: ErrMsg_stat` parameter that was never written to. The C++ test passed `&msg_stat` which was never read.

**Resolution:** Removed the `_stat` parameter entirely. For CHARACTER args, the bridge:
- Does NOT create a C-side parameter
- Declares a local `CHARACTER(8192)` buffer
- Passes it to the Fortran call
- Returns nothing to C++ (ErrStat alone determines pass/fail)

This simplifies both the bridge signature (`InitInput_ptr, ErrStat` — 2 params instead of 3) and the C++ `extern "C"` declaration. Updated the existing test file (`afi_validateinitinput_test.cpp`) to match the new 2-parameter signature.

Also updated `generate_test_harness_skeleton()` to skip CHARACTER args in the bridge parameter list (emit `continue` instead of generating a `_stat` parameter).

## Issue 8: OpenFAST Indentation Comment

**File:** `test_validate.py` lines 79-80 (was)

`indent = "   "  # Match typical OpenFAST indentation` with hardcoded 47-space padding for `::` alignment.

**Resolution:** Auto-detect indentation from existing PUBLIC declarations in the source file:
1. Take the last existing `PUBLIC` declaration line
2. Extract its leading whitespace as `indent`
3. If `::` is present, compute the column position and pad the new `PUBLIC` declaration to match

The generated `PUBLIC :: AFI_ValidateInitInput` now aligns exactly with the existing declarations in the source file (column 47 for `::` in AirfoilInfo.f90, matching `PUBLIC                                       :: AFI_Init`).

## Files Changed

| File | Changes |
|------|---------|
| `vit/vit/test_validate.py` | +`_parse_link_libraries()`, +`_get_module_use_statements()`, `config_dir`/`target_name` params, auto-indent, removed `_stat`, 8192 buffer |
| `vit/vit/cli.py` | +`--force` flag, skip-if-exists logic, pass `config_dir`/`target_name`/`source_file` |
| `openfast/.../afi_validateinitinput_test.cpp` | Updated extern decl and call site (2-param bridge) |
| `openfast/.../afi_validateinitinput_bridge.f90` | Regenerated (dynamic USE, no `_stat`, 8192) |
| `openfast/.../Makefile` | Regenerated (dynamic library paths from link.txt) |
| `vit/dev/202604261142-...audit-findings.md` | Status → completed |

## Verification

- **Unit tests:** 590 passed, 8 failed (all pre-existing, none related to test-validate)
- **Test build + run:** 7/7 pass (happy_path + 6 error branches, C++ ≡ Fortran)
- **File overwrite protection:** Confirmed "Test file exists with implementation — skipping" message
- **Generated Makefile LIBS:** Dynamic from CMake link.txt (15 entries, correct link order)
- **Generated bridge:** 2 parameters (no `ErrMsg_stat`), `USE AirfoilInfo_Types` from source scan, `CHARACTER(8192)`
- **Generated PUBLIC patch:** Auto-aligned `::` at column 47, matching existing declarations

## What's General Now

After these fixes, `vit test-validate` has no project-specific hardcodings:
- **Libraries:** derived from CMake target's link.txt
- **Types modules:** derived from source file's USE statements
- **Buffer sizes:** generic power-of-2 default
- **Bridge parameters:** only what's needed (no unused placeholders)
- **Indentation:** auto-detected from source
- **File safety:** won't destroy existing implementations
- **Path resolution:** uses `config_dir` from vit.yaml, not fragile parent derivation

The command should work on any Fortran project with a CMake build system and a `vit.yaml` configuration.
