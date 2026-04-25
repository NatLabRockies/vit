# Extraction Debugging Postmortem: Process Failures and Root Cause Analysis
**Date:** 2026-04-25
**Status:** completed
**Scope:** Retrospective on multi-hour debugging session, two interacting bugs, and systematic process failures

## Summary

A session intended for routine extraction of ComputeUA360_CnOffset became a multi-hour debugging marathon. Two independent bugs — a cmake reconfigure omission and a wrong line number in documentation — interacted to make every diagnostic test confirm the wrong hypothesis. This postmortem traces every step, identifies where reasoning went wrong, and documents the actual root causes and fixes.

The session also produced the gcov line coverage infrastructure that permanently eliminates test case guesswork for all future extractions.

## Timeline of Events

### Phase 1: Initial Extraction Attempt

**Action:** Attempted to extract ComputeUA360_CnOffset at line 1482 with `ad_BAR_SineMotion_UA4_DBEMT3` (UA_Mod=4).

**Result:** "No state data captured."

**Reasoning:** ComputeUA360_CnOffset is called from ComputeUA360_updateSeparationF, which is called from CalculateUACoeffs. CalculateUACoeffs is "on the UA path," so a UA test case was chosen.

**What was actually wrong:** Two things:
1. The cmake reconfigure bug meant kgen_utils.f90 was never compiled into the binary, so state capture was silently disabled regardless of test case.
2. The code path analysis was partially incorrect — the ComputeUA360_* functions are actually called for all non-cylinder airfoils during init, as later proven by gcov data showing `ad_BAR_SineMotion` hits line 1482 5,800 times.

### Phase 2: Control Test Failure → Wrong Hypothesis

**Action:** Tried to extract the "known working" Calculate_Cn at line 956 with `ad_BAR_SineMotion` as a control test.

**Result:** Also failed with "No state data captured."

**Reasoning:** "If even a known-working extraction fails, the infrastructure itself must be broken."

**What was actually wrong:** The control test parameters were wrong. EXTRACTION_PARAMS.md recorded line 956, but the actual prior extraction used line 974. Line 956 is inside `if (UA_f_cn)` which requires `InclUAdata=TRUE` in the airfoil files — the BAR airfoils have `InclUAdata=FALSE`, so CalculateUACoeffs exits before reaching line 956. The control test was invalid, but I didn't know because I trusted the documentation.

**Critical error:** Treating documentation written from memory as ground truth, then using it to rule out the actual root cause.

### Phase 3: Infrastructure Investigation (Hours)

Believing the infrastructure was broken, I investigated six areas:

1. **strace not installed** — Installed strace in the container. KGen uses strace for compilation flag discovery, and without it the compflag phase fails. But VIT's pre-generated `include.ini` bypasses this entirely. Installing strace was unnecessary and introduced environmental confusion (later removed at user's request).

2. **cmake not recompiling kgen_utils.f90** — Discovered that VIT's build wrapper patches CMakeLists.txt but doesn't reconfigure cmake. With AeroDyn's incremental build command (`cmake --build .`), cmake's cached source list doesn't pick up the new file. Added a cmake reconfigure step. **This was a real bug that needed fixing**, but it wasn't sufficient to explain the failures alone.

3. **Build wrapper timing/ordering** — Added detailed logging (`/tmp/vit_wrapper.log`) to trace when kgen_utils.f90 was copied and CMakeLists was patched. The logs showed the wrapper was invoked correctly but kgen_utils wasn't being compiled. Useful diagnostics.

4. **State Makefile flow** — Read KGen's `extractor/main.py` line by line to understand the save/replace/build/recover/run sequence. Confirmed the flow was correct — the binary should have instrumented code baked in.

5. **Binary symbol inspection** — Ran `nm aerodyn_driver | grep kgen` and found kgen symbols were present. This proved the instrumented code was compiled but the call site wasn't reached.

6. **kgen_init_vars/kgen_check_save logic** — Traced the state capture trigger mechanism. Confirmed the logic was correct — if the call site is reached, state is written.

**Time cost:** ~3-4 hours of investigation, most of which was correct but unnecessary.

**Why this happened:** The invalid control test (Calculate_Cn at line 956) ruled out "wrong call site" as a hypothesis. With that ruled out, infrastructure was the only remaining explanation. Every piece of infrastructure I checked was either working correctly or had a real but non-primary bug, which reinforced the wrong hypothesis.

### Phase 4: Prior Session Context Reveals the Truth

The user provided the prior session's conversation transcript. Key revelations:

1. Calculate_Cn was extracted at **line 974**, not 956
2. The vit.yaml `run_cmd` was switched per-extraction in the prior session
3. FindBoundingTables used `ad_MHK_RM1_Fixed`, Compute_iLoweriUpper used `ad_BAR_SineMotion_UA4_DBEMT3`

**Action:** Tried Calculate_Cn at line 974 with `ad_BAR_SineMotion`.

**Result:** 20 cases captured immediately.

This proved the infrastructure was working (after the cmake reconfigure fix) and the problem was always the call site line number.

### Phase 5: ComputeUA360_CnOffset Resolution

**Action:** Based on (still-flawed) code path analysis, chose `ad_Kite_OLAF` (UA_Mod=3) for ComputeUA360_CnOffset.

**Result:** 21 cases captured.

**Later discovery:** The gcov coverage data from clean source shows `ad_BAR_SineMotion` hits line 1482 5,800 times. The default test case would have worked all along — the only thing preventing it was the cmake reconfigure bug. Choosing `ad_Kite_OLAF` was based on flawed code path analysis and was unnecessary.

## Root Causes

### Bug 1: cmake reconfigure omission (infrastructure)

**What:** VIT's build wrapper patches CMakeLists.txt to add `kgen_utils.f90`, but didn't run `cmake ..` to refresh cmake's cached source list. With AeroDyn's incremental build command (`cmake --build .`), the new file was never compiled. State capture instrumentation was present in the source but the kgen_utils_mod functions were unresolved, causing state capture to silently fail.

**Why it existed:** ROSCO's build command does a full clean build (`rm -rf * && cmake .. && cmake --build .`), which reconfigures cmake every time. AeroDyn's build command is incremental (`cmake --build .`). The wrapper was designed for ROSCO's pattern and never tested with incremental builds.

**Fix:** Added cmake reconfigure step to the build wrapper when CMakeLists.txt is modified (`extract.py`, Step 3).

**Why it wasn't caught earlier:** The prior session's extractions succeeded — possibly because a stale cmake cache from manual debugging happened to include kgen_utils.f90, or because a full cmake reconfigure was triggered by some other mechanism during that session.

### Bug 2: Wrong line number in EXTRACTION_PARAMS.md (documentation)

**What:** Calculate_Cn's call site was recorded as line 956 instead of the actual line 974. Line 956 is inside the `if (UA_f_cn)` branch which is only reached for non-cylinder airfoils with `InclUAdata=TRUE`. The BAR airfoils have `InclUAdata=FALSE`, making line 956 unreachable with any BAR test case.

**Why it existed:** EXTRACTION_PARAMS.md was written at the end of the prior session from memory, not from actual extraction logs or kernel fixture inspection.

**Impact:** When used as a control test, line 956 failed with `ad_BAR_SineMotion`, making it appear that the infrastructure was broken rather than the line number being wrong.

### Interaction

Either bug alone would have been diagnosed quickly:
- Bug 1 alone: The control test at the correct line 974 would succeed, proving infrastructure works, and pointing to a build issue for the new function.
- Bug 2 alone: The correct line number would work, and the control test wouldn't be needed.

Together: every test failed, confirming the wrong hypothesis that infrastructure was broken. The cmake reconfigure fix was necessary but not sufficient — and fixing it didn't make the (still wrong) control test pass, so it appeared to not help.

## Process Failures

### 1. Trusted documentation over primary sources

EXTRACTION_PARAMS.md was treated as ground truth. The actual ground truth is:
- The kernel fixture's `kernel_driver.f90` (contains the wrapped call site)
- `git show <commit>:vit.yaml` (actual settings used)
- The gcov coverage data (definitive line-level execution counts)

**Prevention:** Never trust hand-written extraction records. Use automated verification: check the kernel driver, check git history, query coverage data.

### 2. Invalid control test accepted as valid

The control test (Calculate_Cn at line 956) used parameters from the faulty documentation. When it failed, I concluded "infrastructure is broken" instead of questioning the test parameters.

**Prevention:** A control test is only valid if its parameters are independently verified. For extraction, this means verifying the line number against the kernel fixture or coverage data before running.

### 3. Manual code path analysis is unreliable

I manually traced nested conditionals (UA_f_cn, InclUAdata, cylinder-polar checks) to determine which test case exercises which call site. This analysis was wrong in multiple ways:
- Missed that `InclUAdata=FALSE` causes an early exit
- Missed that the ComputeUA360_* path runs for all non-cylinder airfoils regardless of UA mode
- Confused which conditional block contained which call sites

**Prevention:** Use the gcov coverage data. It's definitive. Manual code path analysis should only be used as a sanity check, never as the primary source.

### 4. Did not check git history early

The user had to suggest checking git history. If I had run `git show d43cf2d16:vit.yaml` at the start, I would have seen the actual settings. If I had read the kernel fixture's `kernel_driver.f90`, I would have seen the actual line number.

**Prevention:** Before any extraction attempt, always verify: (1) query coverage data for the call site, (2) if coverage data is unavailable, check the kernel fixture or git history for the last successful extraction of that function.

### 5. Installed software without clear justification

Installing strace was a reflexive reaction to seeing a KGen error, without first verifying whether VIT's existing workaround (pre-generated `include.ini`) handled it. The installation added environmental confusion and was later removed.

**Prevention:** Before modifying the container environment, verify the actual impact of the issue. VIT was designed to work without strace — the `include.ini` bypass existed for exactly this reason.

## Fixes Applied

| Fix | File | Type | Necessary? |
|-----|------|------|-----------|
| cmake reconfigure in build wrapper | `vit/extract.py` | Bug fix | **Yes** — real bug, would have caused silent failures on all future AeroDyn extractions |
| Build wrapper debug logging | `vit/extract.py` | Diagnostics | Yes — useful for future debugging |
| EXTRACTION_PARAMS.md corrections | `openfast/EXTRACTION_PARAMS.md` | Documentation | Yes — prevented future misuse of wrong line numbers |
| strace installation | container | Environment | **No** — removed afterward, was unnecessary |
| gcov coverage infrastructure | `openfast/scripts/generate_coverage.sh`, `vit/scripts/parse_gcov.py`, `vit/scripts/query_coverage.py` | Infrastructure | **Yes** — permanently eliminates test case guesswork |

## gcov Coverage Infrastructure

The most important outcome of this session. Three scripts that produce definitive per-line execution counts for all AeroDyn source files across all 17 test cases:

- `openfast/scripts/generate_coverage.sh` — builds with `--coverage`, runs all 17 test cases, collects gcov JSON
- `vit/scripts/parse_gcov.py` — consolidates into `line_coverage.json` (4.3MB, 69 files, 182K line entries)
- `vit/scripts/query_coverage.py` — CLI queries: `--line FILE:LINE`, `--call-sites FUNC`, `--best FILE:LINE`, `--summary`

**Critical detail:** Coverage data must be generated from **clean source** (after `reset_to_clean.sh`), not integrated source. Integrated source has wrappers that shift line numbers. The clean-source line numbers are what KGen uses for extraction.

**Usage before any extraction:**
```bash
python3 /workspace/vit/scripts/query_coverage.py --call-sites FunctionName
```
This returns every call site with the exact clean-source line number and which test cases exercise it, with hit counts. Pick the test case with the most hits.

## Key Verification Results (Clean Source)

| Function | Line | Test Cases (hits) | Notes |
|----------|------|-------------------|-------|
| Calculate_Cn | 974 | ad_BAR_SineMotion (29), 13 others | Main call site, non-cylinder branch |
| Calculate_Cn | 956 | ad_EllipticalWingInf_OLAF (1), 2 others | UA_f_cn branch, only cylinder polars |
| ComputeUA360_CnOffset | 1482 | **all 16 test cases** (98-17400) | Default test case works fine |
| FindBoundingTables | 1693 | ad_MHK_RM1_Fixed (367K), 3 MHK/VAWT | Multi-table path only |
| Compute_iLoweriUpper | 1085 | ad_BAR_SineMotion_UA4_DBEMT3 (29) | `.not. UA_f_cn` branch, UA_Mod=4 only |

## Related

- Dev note `202604250532-extraction-test-case-debugging.md` — written mid-session, before full resolution
- Dev note `202604241004-first-aerodyn-extraction.md` — original Calculate_Cn extraction
- `openfast/EXTRACTION_PARAMS.md` — corrected extraction parameters
- `vit/analysis/aerodyn/line_coverage.json` — generated coverage data (not committed)
