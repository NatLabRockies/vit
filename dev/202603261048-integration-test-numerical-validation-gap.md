# Integration Test Numerical Validation Gap — Discovery and Fix
**Date:** 2026-03-26
**Status:** completed
**Scope:** vit_sim.py, integration test methodology, clean build test protocol

## The Discovery

During post-test review of the 27-function integration test, a probing question — "what exactly is the diff comparing?" — revealed that the integration test had been validating the wrong thing since the beginning of the project.

The `diff baseline_output.txt integrated_output.txt` command compared the stdout text captured from `python3 vit_sim.py`. That stdout contained:
- 310 lines of Fortran ROSCO initialization messages (printed by DISCON during `ControllerInterface` setup, before any scenario runs)
- ~40 lines of scenario headers, "Writing new controller parameter file", "Shutting down", and "PASSED" messages
- Zero lines of numerical simulation output

The actual simulation results — `gen_torque`, `bld_pitch`, `gen_speed`, `gen_power`, `nac_yaw` arrays with thousands of values per scenario — were computed by the `Sim.sim_ws_series()` loop, stored in Python memory on the `Sim` object, and never written anywhere. The `sim_ws_series()` method doesn't print per-timestep data. The ROSCO Fortran library prints initialization messages during `ControllerInterface.__init__()` but nothing during the simulation loop itself.

This means every clean build test since Phase 1 validated:
- The library compiles and links ✓
- DISCON initializes without errors ✓
- The simulation loop runs without crashing ✓
- The library unloads without errors (mostly — Python cleanup segfaults are cosmetic) ✓
- **Numerical correctness of controller outputs ✗ — never checked**

The numerical correctness proof rested entirely on KGen verification (40/40 IDENTICAL per function). KGen verification proves each function in isolation against captured runtime state. But the integration test was supposed to prove the whole system works together — that wrappers, struct passing, view types, C++→C++ call paths, and the full closed-loop simulation all produce correct results. It didn't.

## Why It Went Unnoticed

1. **The diff was always empty.** An empty diff looks like success. Nobody questioned what the diff was actually comparing because the expected result (empty) matched the observed result (empty).

2. **The stdout had content.** 467 lines of output made it look like there was substantial data being compared. The 310 initialization lines are numerically dense (generator speed, pitch angle, power, wind speed) — they look like simulation output but are actually from a completely separate phase.

3. **KGen verification provided genuine confidence.** Each function was individually proven bit-identical against real runtime state. The integration test felt like a formality — "of course it works, we verified each piece." But integration can introduce bugs that per-function verification doesn't catch (wrong struct layout, incorrect wrapper argument ordering, stale view pointers).

4. **The baseline and integrated runs produced the same stdout.** Both runs initialize DISCON the same way (same parameters, same library entry point), so the 310 initialization lines are always identical. Both runs complete all 6 scenarios without crashing, so the "PASSED" lines are always identical. The diff was always empty for tautological reasons — it was comparing things that couldn't differ.

## The Fix

### vit_sim.py changes

Added `--output-dir` argument. When provided, each scenario saves its full simulation result arrays to `.npz` files after the simulation completes:

```python
np.savez(os.path.join(output_dir, f'scenario_{N}.npz'),
         gen_torque=sim_N.gen_torque, bld_pitch=sim_N.bld_pitch,
         gen_speed=sim_N.gen_speed, gen_power=sim_N.gen_power,
         nac_yaw=sim_N.nac_yaw)
```

Also prints MD5 checksums of each array to stdout, so the text diff now has meaningful numerical content:
```
  scenario_3 gen_torque: md5=abc123... n=16000
  scenario_3 bld_pitch:  md5=def456... n=16000
```

Scenario 2 (manual loop, not using Sim class) saves its local arrays (`gen_torque`, `bld_pitch`, `gen_speed`) similarly.

### Array comparison

After both runs, compare every value:

```python
for s in [1, 2, 3, 4, 5, 6]:
    b = np.load(f'baseline_arrays/scenario_{s}.npz')
    i = np.load(f'integrated_arrays/scenario_{s}.npz')
    for key in sorted(b.files):
        assert np.array_equal(b[key], i[key])
```

`np.array_equal` compares raw float64 bytes — any difference, even 1 ULP, is detected.

### Validation results

Re-ran the full integration test with the new comparison:

| Scenario | Arrays | Values | Result |
|----------|--------|--------|--------|
| 1 (standard, 1000s) | 5 | 200,000 | IDENTICAL |
| 2 (wrap_360, 100s) | 3 | 12,000 | IDENTICAL |
| 3 (mode coverage, 400s) | 5 | 80,000 | IDENTICAL |
| 4 (PIIController, 100s) | 5 | 20,000 | IDENTICAL |
| 5 (ResController, 400s) | 5 | 80,000 | IDENTICAL |
| 6 (NotchFilterSlopes, 100s) | 5 | 20,000 | IDENTICAL |
| **Total** | **28** | **412,000** | **ALL IDENTICAL** |

Every gen_torque, bld_pitch, gen_speed, gen_power, and nac_yaw value at every timestep across all 6 scenarios is byte-identical between pure Fortran and 27-function integrated C++.

## What This Means

The 27-function integration is numerically correct. The C++ translations produce the exact same closed-loop simulation behavior as the original Fortran across all controller modes. This was always true — but now we've actually proven it, instead of accidentally proving that DISCON prints the same init messages regardless of whether functions are in Fortran or C++.

## Broader Lesson

**Test what matters, not what's easy to capture.** The stdout diff was easy to set up and produced a satisfying "empty diff = success" result. But it was testing the wrong layer. The actual simulation outputs were in memory, one `np.savez` call away from being captured, and nobody thought to add that call because the existing test "worked."

This is a general pattern in scientific computing: the interesting data is often in arrays that the program computes but doesn't print. A test that only checks printed output can miss silent numerical errors. The fix is always the same: serialize the arrays and compare them directly.

## Commits

| Repo | Commit | Description |
|------|--------|-------------|
| ROSCO | `7294c23` | Add --output-dir to vit_sim.py |
| VIT | `fd43bfd` | Update clean build test with array comparison |

## Related Dev Notes

- `202603261013-phase-7b-integration-test-27-functions.md` — the integration test that prompted this investigation
- `202603241718-integration-test-blockers-and-fixes.md` — earlier integration attempt that also used the stdout-only diff
