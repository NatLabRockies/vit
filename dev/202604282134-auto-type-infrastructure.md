# Auto-Generate Type Infrastructure During `vit translate`
**Date:** 2026-04-28
**Status:** completed
**Scope:** VIT enhancement, developer workflow

## Problem

Every new derived type encountered during translation required four manual steps before the workflow could proceed:

1. Add `TypeName: { strategy: view }` to vit.yaml
2. Add the C view struct to vit_types.h
3. Create the Fortran view populator module (vit_{typename}_view.f90)
4. Add the populator to CMakeLists.txt

VIT already had functions to generate all four artifacts — `generate_consolidated_types_header`, `generate_view_populator`, `_generate_cmake_patches` with `extra_fortran_sources`. But they were only called during `vit verify` (for kernel builds) and `vit integrate` (for production builds), not during `vit translate`. The user had to do steps 1-3 manually before `vit translate` would even work, and step 4 manually after `vit integrate`.

For DBEMT Phase A, the ComputeTau1 translation required manually creating `DBEMT_InputType` and `DBEMT_MiscVarType` entries, writing the view struct to vit_types.h, and creating `vit_dbemt_inputtype_view.f90` — all of which VIT had the information to do automatically.

## Solution

Enhanced `cmd_translate` in `cli.py` to auto-generate type infrastructure when new types are encountered:

### 1. Auto-detect view strategies

After building the type registry, scan each type for ALLOCATABLE fields. If a type has ALLOCATABLE fields and no strategy is configured in vit.yaml (case-insensitive comparison), auto-set `strategy: view` and persist via `save_config()`.

```python
new_strategies = []
for type_name, type_info in derived_types.items():
    alloc_fields = [f for f in type_info.fields if f.is_allocatable]
    existing_keys_upper = {k.upper() for k in config.type_strategies}
    if alloc_fields and type_name.upper() not in existing_keys_upper:
        config.type_strategies[type_name] = 'view'
        new_strategies.append(type_name)
if new_strategies:
    save_config(config)
    derived_types = _build_registry(sig, config)
```

### 2. Write/update vit_types.h

After generating the scaffold, call `generate_consolidated_types_header()` with the existing vit_types.h content. The function handles deduplication via include guards — it only appends new type definitions.

Written to the same directory as the Fortran source file being translated (`Path(args.file).parent / "vit_types.h"`), matching the existing convention.

### 3. Create view populator .f90 files

For each view-type in the registry, generate the populator module if it doesn't already exist. Uses `generate_view_populator()` which produces a complete Fortran MODULE with:
- BIND(C) mirror type
- Populator subroutine (scalars + ALLOCATABLE via C_LOC/SIZE)
- Reverse-copy subroutine
- Kernel stash pointer

Existing populators are preserved (no overwrite).

### 4. CMakeLists.txt (already automated)

`vit integrate --apply` already passes `view_populator_names` to `_generate_cmake_patches` as `extra_fortran_sources`. The function checks if each filename is already in the target's source list and adds it if not. No change needed.

## Testing

Tested with `vit translate DBEMT_InitStates`, which takes two unconfigured types (`DBEMT_ContinuousStateType` with ALLOCATABLE `element(:,:)`, `DBEMT_OtherStateType` with ALLOCATABLE `areStatesInitialized(:,:)`, `n(:,:)`, `xdot(1:4)`):

```
Auto-configured view strategy for DBEMT_ContinuousStateType (ALLOCATABLE fields detected)
  Auto-configured view strategy for DBEMT_OtherStateType (ALLOCATABLE fields detected)
  Updated: modules/aerodyn/src/vit_types.h
  Created: modules/aerodyn/src/vit_dbemt_continuousstatetype_view.f90
  Created: modules/aerodyn/src/vit_dbemt_otherstatetype_view.f90
```

Confirmed vit.yaml updated, vit_types.h extended with new view structs, both populator modules generated. No manual steps required.

Also confirmed no regression: `vit translate ComputeTau2` (existing type, already configured) runs without auto-configuring duplicates.

## Case sensitivity fix

The type registry returns uppercase names (from the Fortran parser), while vit.yaml uses mixed case (from manual entries). The initial implementation compared these case-sensitively, causing duplicate entries (`DBEMT_ParameterType` vs `DBEMT_PARAMETERTYPE`). Fixed with case-insensitive lookup: `{k.upper() for k in config.type_strategies}`.

## Impact on workflow

Before: 7-step workflow required 3 manual prerequisite steps before step 2 (translate) and 1 manual step after step 5 (integrate).

After: `vit translate` handles everything. The translator runs the command and gets a working scaffold with all type infrastructure in place. The 7-step workflow is now truly 7 steps, no hidden prerequisites.

## File changed

`vit/vit/cli.py` — `cmd_translate` function, plus `save_config` added to imports.
