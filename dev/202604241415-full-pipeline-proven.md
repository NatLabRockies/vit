# Full VIT Pipeline Proven on AeroDyn — Extract, Verify, Integrate
**Date:** 2026-04-24
**Status:** completed
**Scope:** Phase 1 proof-of-concept complete — Calculate_Cn through full pipeline

## Summary

The complete VIT pipeline (extract → verify → integrate) is proven on AeroDyn. Calculate_Cn is the first AeroDyn function to pass all three stages:

- **Extract:** 20 cases captured from `ad_BAR_SineMotion` test case
- **Verify:** 20/20 IDENTICAL with full Fortran↔C++ field log comparison
- **Integrate:** simulation output bit-identical to clean Fortran baseline (both built with `-ffp-contract=off`)

This answers OQ2 (KGen works on AeroDyn) and partially answers OQ8 (VIT works on AeroDyn for simple leaf functions).

## Integration Details

`vit integrate` produced 5 changes:
1. Created `modules/aerodyn/src/calculate_cn.cpp` — C++ source with `extern "C"` wrapper
2. Added `USE ISO_C_BINDING` to AirfoilInfo module
3. Added INTERFACE block for `calculate_cn_c` BIND(C) subroutine
4. Replaced Calculate_Cn body with wrapper that calls `calculate_cn_c`
5. Created `modules/aerodyn/src/vit_translated.h` shared header

## CMakeLists.txt Changes (Manual)

VIT did not auto-patch CMakeLists.txt. The following manual edits were needed:

```cmake
# Added before add_library:
enable_language(CXX)

# Added to basicaerolib sources:
src/calculate_cn.cpp

# Added stdc++ to link:
target_link_libraries(basicaerolib ifwlib nwtclibs stdc++)
```

VIT's `integrate` command needs enhancement to handle AeroDyn's multi-library cmake structure. For ROSCO, VIT patched a single CMakeLists.txt with a single library target. AeroDyn has multiple library targets (`basicaerolib`, `aerodynlib`, `adilib`, etc.) — VIT needs to identify which target contains the source file being integrated and patch that target's source list.

This is a VIT enhancement for the future. For now, the CMakeLists.txt edits are manual and straightforward.

## Verification Method

Compared simulation output from two builds, both using `-ffp-contract=off`:

1. **Baseline:** clean Fortran (no integration), built with `-ffp-contract=off`
2. **Integrated:** Calculate_Cn replaced with C++ wrapper, built with `-ffp-contract=off`

Binary comparison of `.outb` output: **IDENTICAL**.

The committed r-test baselines were produced by NREL without `-ffp-contract=off`, so they can't be used for bit-identical comparison during the translation phase. We need our own baselines built with matched flags.

## What's Proven

| Stage | Status | Notes |
|-------|--------|-------|
| KGen extraction on cmake executable | Working | 12 VIT bugs fixed for AeroDyn |
| KGen state capture with preprocessor defines | Working | `-D OPENFAST_DOUBLE_PRECISION` passed to KGen |
| Kernel verification (Fortran) | Working | ReKi=R8Ki resolved via ISO_FORTRAN_ENV |
| Kernel verification (C++) | Working | 20/20 IDENTICAL with field log comparison |
| Integration (source patching) | Working | Wrapper with resolved kinds (REAL(8) not REAL(ReKi)) |
| Integration (build) | Working | Manual CMakeLists.txt edit needed |
| Simulation output comparison | Working | Bit-identical to Fortran baseline |

## What's Not Yet Proven

- Functions with derived type arguments (next target: FindBoundingTables or similar)
- Functions that call other AeroDyn functions (callee bridges)
- Multiple integrated functions (sequential integration)
- All 16 regression test cases (only tested ad_BAR_SineMotion)
- VIT's auto-CMakeLists patching for AeroDyn

## Related

- Dev note `202604241400` — first full verification (20/20 IDENTICAL)
- Dev note `202604241337` — FMA hypothesis confirmed
- Dev note `202604241004` — first extraction
- Dev note `202604241051` — VIT adaptation issues catalog
- `openfast/vit.yaml` — AeroDyn VIT configuration
- `openfast/translations/AirfoilInfo/calculate_cn.cpp` — the C++ translation
