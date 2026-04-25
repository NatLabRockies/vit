# View-Struct INOUT Reverse Copy Fix + ComputeUASeparationFunction_zero Complete
**Date:** 2026-04-25
**Status:** completed
**Scope:** VIT kernel verification infrastructure, AeroDyn Phase 1

## The Blocker

ComputeUASeparationFunction_zero failed kernel verification at 0/20. The C++ translation correctly modified scalar fields in the view struct (`p->UA_BL.alphaBreakUpper`, `CnBreakUpper`, `alphaBreakLower`, `CnBreakLower`), but KGen's comparator read the **original Fortran type** which was never updated. The view struct is a one-way copy: Fortran type -> C view struct. ALLOCATABLE array modifications work fine (the view holds `C_LOC` pointers into Fortran memory), but scalar fields are **copies** — C++ writes to the copy, the original never changes.

Field log confirmation: `alphabreakupper,scalar,OUT_TOL,0.0000000000000000,-3.7022135648364547E-002` — the output was 0.0 (original Fortran, never updated) vs -0.037 (expected from C++ modification).

## Root Cause

In `kernel_verify.py` line 1110:
```python
wrapper_body = generate_fortran_wrapper(sig, kind_aliases, derived_types, is_kernel=True)
```

The `reverse_copy` parameter was not passed — it defaulted to `False`. VIT already had full reverse-copy infrastructure for the integration context (`--reverse-copy` flag on `vit integrate`), including:
- `vit_copy_scalars_to_<type>` subroutine in the generated view populator module
- USE statement generation with optional reverse-copy import
- Call emission after the C++ call for INOUT/OUT view-type arguments

All of this was guarded by `if reverse_copy and view_args:` — the kernel wrapper just never set the flag.

## The Fix

**One line change** in `kernel_verify.py` line 1110:
```python
wrapper_body = generate_fortran_wrapper(sig, kind_aliases, derived_types, is_kernel=True, reverse_copy=True)
```

Passing `reverse_copy=True` unconditionally is safe because:
1. Functions without view types: no-op (guard: `if reverse_copy and view_args`)
2. Functions with IN-only view types: no-op (guard: `if arg.intent in ("INOUT", "OUT")`)
3. Functions with INOUT view types where only ALLOCATABLE arrays are modified (e.g., Calculate_C_alpha): harmless no-op copy (scalars written back with same values)
4. Functions with INOUT view types where scalars are modified (e.g., ComputeUASeparationFunction_zero): **the fix** — scalars copied back to original Fortran type before KGen comparator runs

The generated kernel wrapper now includes:
```fortran
USE vit_afi_table_type_view, ONLY: ..., vit_copy_scalars_to_afi_table_type
...
CALL computeuaseparationfunction_zero_c(C_LOC(p_view), ColUAf, cn_cl, SIZE(cn_cl))
CALL vit_copy_scalars_to_afi_table_type(p_view, p)
```

The `vit_copy_scalars_to_afi_table_type` subroutine copies all 46 scalar fields (5 top-level + 41 nested UA_BL fields) from the view back to the original type. This includes the four fields ComputeUASeparationFunction_zero modifies.

## Verification

After fix:
- **ComputeUASeparationFunction_zero: 20/20 PASSED** (was 0/20)
- Calculate_C_alpha: 20/20 PASSED (regression OK)
- Calculate_Cn: 20/20 PASSED (regression OK)
- FindBoundingTables: 21/21 PASSED (regression OK)
- Compute_iLoweriUpper: 20/20 PASSED (regression OK)
- ComputeUA360_CnOffset: 21/21 PASSED (regression OK)

## Integration

ComputeUASeparationFunction_zero integrated with `--reverse-copy`:
- Integration wrapper includes `vit_copy_scalars_to_afi_table_type` call after C++ returns
- `vit_afi_table_type_view.f90` created in `modules/aerodyn/src/` (view populator module)
- Build: success
- Regression: 16/16 baselines pass

## Impact

This fix unblocks ALL remaining AirfoilInfo functions that modify view-type INOUT arguments. Most of the remaining UA functions modify `p%UA_BL.*` scalars or `p%Coefs` array entries — the reverse-copy handles the scalar side, and `C_LOC` handles the array side.

Sixth AeroDyn function fully integrated. First to require the reverse-copy infrastructure in kernel verification.

## Commits

| Repo | What |
|------|------|
| VIT | `reverse_copy=True` in `kernel_verify.py` line 1110 |
| OpenFAST | ComputeUASeparationFunction_zero integrated, `vit_afi_table_type_view.f90` created |
