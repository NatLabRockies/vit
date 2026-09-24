# Infrastructure Cleanup: Five Fixes Before Scaling Phase 1
**Date:** 2026-04-24
**Status:** completed
**Scope:** VIT pipeline bugs and AeroDyn tooling gaps exposed during derived type work

## Summary

Post-mortem audit of the FindBoundingTables/Compute_iLoweriUpper session identified five infrastructure issues that would cause repeated problems during Phase 1 scaling. All five fixed and verified.

## Fix 1: `preprocessor_defines` preservation in `save_config()`

**File:** `vit/config.py`

**Problem:** `save_config()` built the `fortran` YAML section but omitted `preprocessor_defines`. Called by `record_translation()` after every `vit verify` and `vit integrate --apply`, it silently dropped the field. The field was correctly loaded (line 170) and stored in VitConfig (line 89) — but never written back. This caused `preprocessor_defines` to vanish from `vit.yaml` after every verification, requiring manual re-addition at least 3 times during the session.

**Fix:** Added conditional inclusion in the `fortran` dict, following the existing pattern used for `assumed_size_arrays`:
```python
**({"preprocessor_defines": config.preprocessor_defines} if config.preprocessor_defines else {}),
```

**Verification:** Ran `vit verify` on Calculate_Cn, confirmed `preprocessor_defines` survives in vit.yaml.

## Fix 2: `vit integrate` header regeneration from type registry

**File:** `vit/integrate.py`

**Problem:** `vit integrate` walked up from the `.cpp` implementation file looking for a pre-existing `translations/vit_types.h`, then copied it to `src/`. This meant the header was never regenerated — the LOGICAL→int8_t type mapping fix was invisible until manually deleting and regenerating. `generate_consolidated_types_header()` existed in `derived_type_parser.py` (used by `kernel_verify.py`) but `integrate.py` never called it.

**Fix:** Replaced the filesystem-walk copy logic with a call to `generate_consolidated_types_header()`. Uses `existing_content=old_content` to accumulate types across sequential integrations (Calculate_Cn adds no types, FindBoundingTables adds AFI_ParameterType + AFI_Table_Type + AFI_UA_BL_Type, Compute_iLoweriUpper adds AFI_Table_Type which is already present — the `#ifndef` guard skips duplicates).

**Key design detail:** The original plan used `existing_content=""` to force full regeneration, but this broke sequential integration — each `vit integrate` only has its own function's type registry, so a full regeneration would drop types from prior integrations. Using `existing_content=old_content` preserves existing types while appending new ones.

**Verification:** Deleted `src/vit_types.h`, re-integrated all 3 functions sequentially, confirmed all types accumulated correctly: `afi_ua_bl_type_t`, `afi_table_type_view_t`, `afi_parametertype_view_t`.

## Fix 3: `vit integrate` populator overwrite

**File:** `vit/integrate.py`

**Problem:** The check `if os.path.exists(populator_path): continue` skipped populator generation unconditionally if the file existed — even if it was a 3-line stub left from extraction cleanup. This caused link failures when stubs were present.

**Fix:** Removed the 3-line skip block entirely. Populator `.f90` files are auto-generated (`DO NOT EDIT` header) and never hand-edited. Always regenerating is correct and harmless — if the content is identical, the file write is a no-op.

**Verification:** Created stub populators, ran `vit integrate`, confirmed they were overwritten with full populators.

## Fix 4: `reset_to_clean.sh` for AeroDyn

**File:** `openfast/scripts/reset_to_clean.sh` (new)

**Problem:** Each extraction required manual `git checkout`, stub creation, and artifact cleanup. This was error-prone — KGen-modified `AirfoilInfo_Types.f90` and `AeroDyn_Driver.f90` slipped into a commit, stale line numbers from integrated source caused four failed extraction attempts, and the `.integrated` backup was captured while KGen was instrumenting.

**Key design detail:** `git checkout -- AirfoilInfo.f90` restores to the committed (integrated) version, which still has wrappers. The script uses `git checkout $PRE_INTEGRATION_COMMIT -- file` to restore the upstream version without wrappers. The commit hash (`edab46311`) is the last upstream commit before VIT integration.

**What the script does:**
1. Restores integrated Fortran source files to pre-integration upstream state
2. Creates `// stub` C++ files for CMakeLists-referenced `.cpp` files
3. Creates empty Fortran MODULE stubs for view populator `.f90` files
4. Cleans KGen artifacts (elapsedtime/, model.ini, include.ini, kgen.log, *.kgen_org)

**What it does NOT touch:** CMakeLists.txt, vit.yaml, translations/, kernel/

**Verification:** Ran the script, built aerodyn_driver with stubs — compiles successfully.

## Fix 5: PROJECT_PLAN_AERODYN.md updates

**File:** `vit/PROJECT_PLAN_AERODYN.md`

**Changes:**
- Phase 1 work items: added checked boxes for view struct infrastructure, FindBoundingTables, Compute_iLoweriUpper
- Updated leaf functions count (10 remaining, was 12)
- Added "Known VIT limitations" section documenting ALLOCATABLE TYPE() array access and single-level nested TYPE() embedding
- Added change log entries

## Commits

- **VIT** `2ff0107`: Fixes 1-3 + Fix 5 (config, integrate, project plan)
- **OpenFAST** `f110fa19d`: Fix 4 (reset_to_clean.sh) + regenerated integration files

## Related

- Dev note `202604241905` — derived type functions proven (identified these five gaps)
- `PROJECT_PLAN_AERODYN.md` — Phase 1 known limitations section
