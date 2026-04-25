# Extraction Test Case Debugging: Hours Lost to Wrong Test Case Selection
**Date:** 2026-04-25
**Status:** completed
**Scope:** AeroDyn extraction infrastructure — test case selection, cmake reconfigure fix, EXTRACTION_PARAMS correction

## Summary

A full session was spent debugging extraction failures that all traced to the same root cause: **using the wrong test case for the target function's code path**. KGen extraction produced zero state files — the binary was built correctly with instrumented code, the simulation ran successfully, but the call site was never reached because the mode flags in the chosen test case didn't enable the code path containing the target function.

This is a systemic problem. With 507 AeroDyn procedures gated by combinations of `UA_Mod`, `AFTabMod`, `Wake_Mod`, `DBEMT_Mod`, and data-dependent conditions like `InclUAdata` and cylinder-polar detection, guessing which of the 17 test cases exercises a particular function is unreliable. Each failed extraction takes ~8 minutes, creating a costly trial-and-error loop.

## Timeline

### Phase 1: Assumed infrastructure was broken

After compaction, attempted to extract `ComputeUA360_CnOffset` with `ad_BAR_SineMotion_UA4_DBEMT3` (UA_Mod=4). Failed with "No state data captured." Assumed the extraction infrastructure itself was broken because:
- The EXTRACTION_PARAMS.md (written at end of prior session) recorded `ad_BAR_SineMotion` as the test case for `Calculate_Cn` — implying non-UA test cases work
- Tried extracting `Calculate_Cn` with `ad_BAR_SineMotion` at line 956 → also failed
- Concluded the infrastructure was fundamentally broken

### Phase 2: Deep infrastructure investigation (hours)

Investigated extensively:
1. **strace not installed** — installed it, didn't fix the issue
2. **cmake not recompiling kgen_utils.f90** — added cmake reconfigure step to build wrapper (real fix, but not the primary cause)
3. **cmake DependInfo cache stale** — added reconfigure after CMakeLists.txt patching
4. **Wrapper timing/ordering** — added detailed logging to wrapper script
5. **State Makefile flow** — read KGen's extractor/main.py line by line
6. **Binary symbol inspection** — confirmed kgen symbols were present in binary
7. **kgen_init_vars/kgen_check_save logic** — traced the state capture trigger mechanism

All of this was correct investigation — the cmake reconfigure fix IS needed for AeroDyn (ROSCO didn't need it because its build command did a full `rm -rf * && cmake ..` clean build). But it wasn't the primary cause.

### Phase 3: User provided prior session context

The user shared the prior session's conversation transcript. Key findings:

1. **Calculate_Cn was extracted at line 974, not 956.** Line 956 is inside `if (UA_f_cn)` — only reached when `UA_Mod ∉ {4, 6}`. Line 974 is in the `else` branch — reached for all non-cylinder airfoils regardless of UA mode. The EXTRACTION_PARAMS.md recorded line 956, which was wrong.

2. **FindBoundingTables used `ad_MHK_RM1_Fixed`**, not `ad_BAR_SineMotion`. The vit.yaml was switched for each extraction in the prior session.

3. **Compute_iLoweriUpper used `ad_BAR_SineMotion_UA4_DBEMT3`** with line 1115 (inside `if (.not. UA_f_cn)` which IS true for UA_Mod=4).

### Phase 4: Correct test case identification

Once the prior context was available, identified the correct test case for `ComputeUA360_CnOffset`:

- The `ComputeUA360_*` functions are called from line 957-959, inside `if (UA_f_cn)` at line 949
- `UA_f_cn = UAMod /= UA_HGM .AND. UAMod /= UA_Oye` (line 883)
- UA_HGM = 4, UA_Oye = 6
- **UA_Mod=4 (HGM) makes `UA_f_cn = FALSE`** — the ComputeUA360 branch is NOT taken
- Need `UA_Mod ∈ {2, 3, 5, 7}` for `UA_f_cn = TRUE`
- Additionally need `InclUAdata = TRUE` in the airfoil file (otherwise CalculateUACoeffs exits at line 800)
- `ad_Kite_OLAF` has `UA_Mod=3` (Minnema/Pierce) AND `InclUAdata=TRUE` → correct test case

### Phase 5: Verification

- `Calculate_Cn` at line 974 with `ad_BAR_SineMotion`: **20 cases** ✓ (confirms infrastructure works)
- `ComputeUA360_CnOffset` at line 1482 with `ad_Kite_OLAF`: **21 cases** ✓

## Root Causes

### Primary: No systematic way to determine which test case exercises a function

The coverage map in CLAUDE.md lists mode settings per test case, but doesn't map those modes to specific code paths inside functions. The mode gating logic is nested and non-obvious:
- `UA_Mod=4` enables UA but makes `UA_f_cn=FALSE`, which takes the NON-ComputeUA360 path
- `InclUAdata` is a per-airfoil-file flag, not a simulation mode — different test cases use different airfoil files
- Cylinder-polar detection (`maxval(Cd) == minval(Cd)`) is data-dependent, not mode-dependent

### Secondary: EXTRACTION_PARAMS.md had wrong data

The document was written at the end of the prior session from memory, not from actual extraction logs. Calculate_Cn's line number and test case were both wrong.

### Tertiary: cmake reconfigure needed for incremental builds

AeroDyn's build command (`cmake --build .`) is an incremental build, unlike ROSCO's (`rm -rf * && cmake ..`). When VIT's wrapper adds `kgen_utils.f90` to CMakeLists.txt, cmake's cached source list doesn't pick it up without a reconfigure. This was a real bug — fixed by adding `cd build && cmake ..` to the wrapper when CMakeLists is modified — but was masked by the test case issue.

## Fixes Applied

### 1. cmake reconfigure in build wrapper (`vit/extract.py`)

Added Step 3 to the build wrapper: if CMakeLists.txt was backed up (indicating modification), run `cmake ..` in the build directory before building. This ensures cmake picks up the newly added `kgen_utils.f90`. Without this, the instrumented source compiles but kgen_utils functions are unresolved, and state capture silently fails.

```python
# Step 3: Reconfigure cmake if CMakeLists was modified
if cmake_file_abs and build_dir:
    build_dir_abs = os.path.abspath(build_dir)
    script_lines.extend([
        'if [ -f "${CMAKE_FILE}.vit_backup" ]; then',
        f'    cd "{build_dir_abs}" && cmake .. >> /tmp/vit_wrapper.log 2>&1',
        'fi',
    ])
```

### 2. Wrapper debug logging (`vit/extract.py`)

Added logging for kgen_utils.f90 copy success/failure and cmake reconfigure to `/tmp/vit_wrapper.log`. This makes future extraction debugging much faster — check the log first instead of guessing.

### 3. EXTRACTION_PARAMS.md correction (`openfast/EXTRACTION_PARAMS.md`)

Fixed Calculate_Cn entry: line 974 (not 956), caller is `CalculateUACoeffs (else branch)`.

### 4. strace installed in container

`apt-get install strace` — allows KGen's strace-based compilation flag detection to work (previously failed silently, VIT's include.ini workaround covered it, but having strace avoids the silent failure).

## Infrastructure Gap: Dynamic Call Tree

The session exposed a fundamental gap: **no way to programmatically determine which test case exercises which function**. Current state:
- **Static call graph** (fparser, 3,451 edges) — knows caller→callee relationships but NOT mode gating
- **Coverage map** (CLAUDE.md) — knows mode settings per test case but NOT which functions each mode gates
- **Missing**: the connection between these two — which mode values reach which call sites

### Proposed solution: Dynamic call tree from all 17 test cases

1. Build `aerodyn_driver` with `-finstrument-functions` (gfortran flag that inserts entry/exit callbacks into every function)
2. Link with a small C tracer that logs `{function_addr, caller_addr}` to a file
3. Run all 17 test cases (~2 minutes total, each takes ~7 seconds)
4. Post-process: `addr2line` resolves addresses to `file:line`, build per-test-case call trees
5. Output: `test_case_coverage.json` — definitive "function X called N times by test case Y" map

Before any extraction, look up the function → pick the right test case → extract once. Optionally validate with a lightweight `vit probe` command.

### Why this is essential for Phase 1 scaling

- 507 hand-written procedures to translate
- ~247 are leaf functions (first targets)
- Many are gated by mode combinations (`UA_Mod × AFTabMod × Wake_Mod × DBEMT_Mod × InclUAdata × ...`)
- Each failed extraction = 8 minutes wasted
- Trial-and-error approach: potentially 3-5 attempts per function × 247 functions = 750-1250 wasted extractions = 100-170 hours of wasted compute time
- Dynamic call tree: 2 minutes one-time cost, eliminates all guessing

## Correct Test Case Assignments (Updated)

| Function | Test Case | Why |
|----------|-----------|-----|
| Calculate_Cn (line 974) | ad_BAR_SineMotion | Non-cylinder airfoil, else branch of UA_f_cn |
| FindBoundingTables | ad_MHK_RM1_Fixed | AFTabMod=2 (multi-table) |
| Compute_iLoweriUpper | ad_BAR_SineMotion_UA4_DBEMT3 | UA_Mod=4, `.not. UA_f_cn` branch |
| ComputeUA360_CnOffset | ad_Kite_OLAF | UA_Mod=3, `UA_f_cn=TRUE`, InclUAdata=TRUE |
| ComputeUA360_* (all) | ad_Kite_OLAF | Same gating as CnOffset |

## Key Lesson

**Never guess test cases. Build infrastructure to determine them systematically.** The cost of building a dynamic call tree (2 minutes + tooling) is negligible compared to the cost of even one debugging session like this one (hours of engineer time, multiple failed extractions). This should be the NEXT task before any more extractions.

## Related

- Dev note `202604241004-first-aerodyn-extraction.md` — original extraction success (before this session's failures)
- Dev note `202604241905-derived-type-functions-proven.md` — FindBoundingTables and Compute_iLoweriUpper
- `openfast/EXTRACTION_PARAMS.md` — per-function extraction parameters (corrected)
- `vit/CLAUDE.md` — test case coverage map (mode settings only, not function-level reachability)
