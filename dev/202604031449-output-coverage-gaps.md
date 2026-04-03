# Output Array Coverage Gaps
**Date:** 2026-04-03
**Status:** in-progress
**Scope:** Verification coverage — which controller outputs are actually compared

## Finding

Our simulation comparison saves and compares only 5 arrays per scenario: `bld_pitch`, `gen_torque`, `gen_speed`, `gen_power`, `nac_yaw`. These come from `call_controller`'s 3 return values (`torque`, `pitch`, `nac_yawrate`) plus derived `gen_power`. But the ROSCO controller writes many more values to `avrSWAP` that we never capture or compare.

## Uncaptured Outputs

### Individual blade pitch commands
- `avrSWAP[41]` = blade 2 pitch (this IS our `bld_pitch`)
- `avrSWAP[42]`, `avrSWAP[43]` = blade 1, blade 3 pitch — **NOT captured**
- For IPC (Scenarios 6, 8, 18), each blade gets a different pitch command. We only verify blade 2. A bug in blade 1 or blade 3 IPC indexing would be invisible.

### Flap control outputs
- `avrSWAP[120-122]` = flap angle commands for blades 1-3 — **NOT captured**
- Scenarios 3, 4, 7, 16 exercise Flp_Mode 1-3 but we never check the flap output. The flap translation could produce wrong values and we'd never see it in our comparison.

### Cable control outputs
- `avrSWAP[CC_GroupIndex]` and `avrSWAP[CC_GroupIndex+1]` = actuated cable length and delta — **NOT captured**
- Scenarios 3, 7, 24 exercise CC_Mode 1-2 but cable outputs aren't compared.

### Structural control outputs
- `avrSWAP[StC_GroupIndex]` = structural control force — **NOT captured**
- Scenarios 3, 7, 24 exercise StC_Mode 1-2 but structural outputs aren't compared.

### Generator contactor
- `avrSWAP[34]` (index 35) = generator contactor status — **NOT captured**

### Trivially verified arrays (0 == 0)
- `nac_yaw`: ALL ZERO in 24 of 25 scenarios. Only Scenario 7 has non-trivial yaw. Scenario 2 exercises Y_ControlMode=2 but its yaw output goes into pitch corrections, not yaw rate — so `nac_yaw` is zero.
- `bld_pitch`: ALL ZERO in Scenarios 10, 13, 23 (by design — pitch control disabled or OL torque-only).
- `gen_power`/`gen_torque`: ALL ZERO in Scenario 13 (VS_FBP=1 at low wind speed).

## Impact Assessment

### High concern
- **Individual blade pitch (blades 1 and 3)**: IPC produces different commands per blade. Only blade 2 is verified. A blade-indexing bug in the IPC translation would be invisible.
- **Flap outputs**: 4 scenarios exercise flap control but the actual flap commands are never compared. The entire flap output path is unverified at the simulation level.

### Medium concern
- **Cable and structural control outputs**: CC_Mode=2 and StC_Mode=2 interpolate from OL files and write to avrSWAP. The outputs aren't compared — only the downstream effect on gen_torque/gen_speed (which may be minimal for cable/structural modes).

### Low concern
- **`nac_yaw` all-zeros**: YawRateControl is KGen-verified (20+ cases with non-zero inputs). The simulation-level zero output is because the 1-DOF sim doesn't model yaw dynamics. Scenario 7 provides non-trivial yaw verification.
- **Generator contactor**: Simple status flag, unlikely to differ.

## Recommended Fix

Modify `call_controller` or the save logic to capture all blade pitch commands (3 values instead of 1), flap outputs (3 values), and cable/structural outputs. This requires:

1. After `call_controller`, read additional `avrSWAP` values from `controller_int.avrSWAP`
2. Add them to the `save_and_print_results` dict
3. Re-run full verification with the expanded arrays

The `controller_int.avrSWAP` array is accessible after each `call_controller` call — we just need to read the right indices.

## Commits

- ROSCO `4df703f`: Fix Scenario 2 to save all 5 arrays (found nac_yaw is all-zero despite Y_ControlMode=2)
