# Scenario 3 Non-Determinism Fix
**Date:** 2026-03-26
**Status:** completed
**Scope:** Scenario 3 determinism fix, FITPACK workaround, vit_sim.py

## What Was Done

Implemented a workaround in `vit_sim.py` that eliminates the Scenario 3 non-determinism caused by scipy's FITPACK `bispev`/`fpbisp` uninitialized stack variable bug (dev note `202603261512`).

## The Fix

Two components, both applied via monkey-patch in `vit_sim.py` at module load time:

### 1. Stack scrubber (`scrub_stack.c`)

A tiny C shared library that zeros 64KB of stack:

```c
#include <string.h>
void scrub_stack(void) {
    volatile char buf[65536];
    memset((char*)buf, 0, sizeof(buf));
}
```

Located at `ROSCO/rosco/controller/src/scrub_stack.c`. Built manually in the container:
```bash
gcc -shared -fPIC -o rosco/lib/libscrub.so rosco/controller/src/scrub_stack.c
```

The `.so` is gitignored (binary); only the `.c` source is committed. Must be rebuilt if the container is recreated.

### 2. Monkey-patch in `vit_sim.py`

At module load, replaces `RotorPerformance.interp_surface` and `interp_gradient` with versions that:

1. **Cache the `RectBivariateSpline`** on the instance (lazy init on first call). This prevents the FITPACK constructor from refilling the stack between the scrub and the evaluation.

2. **Call `scrub_stack()`** immediately before each cached spline evaluation. This zeros the stack region where `fpbisp`'s uninitialized variable resides, ensuring deterministic results.

Both components are required. The initial attempt with scrub-only (no caching) failed because the `RectBivariateSpline` constructor itself calls FITPACK, refilling the stack with new residuals between the scrub and the `bispev` evaluation. The caching eliminates the constructor call from the hot loop, so the scrub directly precedes the evaluation.

## Why Not Modify `turbine.py`?

The fix is localized to `vit_sim.py` (our test script) rather than `turbine.py` (upstream ROSCO toolbox) because:
- This is a workaround for a scipy bug, not a ROSCO fix
- It adds a ctypes dependency on a custom C library — not appropriate for upstream
- It's easy to remove once scipy fixes the underlying FITPACK issue
- The monkey-patch is transparent — it doesn't change the API or return values

## Verification

### 30-run determinism test
```
s3_1:  md5=e078402d620bd512b407fe261545cc71
s3_2:  md5=e078402d620bd512b407fe261545cc71
...
s3_30: md5=e078402d620bd512b407fe261545cc71
```

**30/30 identical.** Previously, this test produced a ~50/50 split between two states.

### All scenarios functional
All 6 scenarios run successfully with the fix applied. The monkey-patch only affects `interp_surface` and `interp_gradient` — no other code paths are modified.

## Why the First Attempt Failed

The initial monkey-patch applied the stack scrubber but called the ORIGINAL `interp_surface` (which creates a new `RectBivariateSpline` on every call):

```python
def _scrubbed_interp_surface(self, pitch, TSR):
    _scrub_lib.scrub_stack()          # zeros 64KB of stack
    return _orig_interp_surface(...)  # creates RectBivariateSpline → FITPACK constructor → refills stack
                                      # then evaluates → bispev reads constructor's residuals, not zeros
```

The call chain was: scrub → constructor (refills stack) → evaluation (reads refilled stack). The scrub was ineffective because the constructor put new values on the stack before the evaluation could read the scrubbed zeros.

The working fix caches the spline so the constructor runs only once:

```python
def _scrubbed_interp_surface(self, pitch, TSR):
    if not hasattr(self, '_vit_surface_spline'):
        self._vit_surface_spline = RectBivariateSpline(...)  # once
    _scrub_lib.scrub_stack()          # zeros 64KB of stack
    return self._vit_surface_spline(pitch, TSR)  # evaluation reads scrubbed zeros
```

## Files Changed

| File | Repo | Change |
|------|------|--------|
| `rosco/controller/src/scrub_stack.c` | ROSCO | New file: C stack scrubber |
| `Examples/vit_sim.py` | ROSCO | Monkey-patch: cache + scrub for `interp_surface`/`interp_gradient` |

ROSCO commit: `034c656`

## Impact on Integration Testing

Scenario 3 can now be included in byte-identical integration testing alongside Scenarios 1, 2, 4, 5, 6. The `INTEGRATION_TEST.md` document should be updated to reflect that all 6 scenarios are fully deterministic with the scrubber fix.

## Container Setup Note

If the container is recreated, `libscrub.so` must be rebuilt:
```bash
gcc -shared -fPIC -o /workspace/ROSCO/rosco/lib/libscrub.so /workspace/ROSCO/rosco/controller/src/scrub_stack.c
```

If `libscrub.so` is absent, `vit_sim.py` runs without the scrubber (the `if os.path.exists(...)` guard skips the monkey-patch). Scenario 3 would revert to non-deterministic behavior.

## Related Dev Notes

- `202603261512-scipy-fitpack-bispev-uninitialized-variable.md` — scipy bug report writeup
- `202603261506-scenario3-nondeterminism-definitive-root-cause.md` — root cause proof (stack scrubber diagnostic)
- `202603261447-scenario3-nondeterminism-root-cause.md` — investigation (FPCR hypothesis disproven)
- `202603261254-scenario3-nondeterminism-100-run-study.md` — 100-run study establishing the two-state flip
