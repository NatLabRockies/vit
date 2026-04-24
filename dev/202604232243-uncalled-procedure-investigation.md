# Uncalled Procedure Investigation — From 210 False Positives to 0 Unknowns
**Date:** 2026-04-23
**Status:** completed
**Scope:** Accuracy of the AeroDyn structural inventory's "dead code" detection

## Summary

The initial fparser structural inventory (dev note `202604232137`) reported 210 "dead code" procedures. User review challenged this: "Are we sure this is dead code, and not just code that is being called from somewhere outside of AeroDyn?" Investigation revealed three categories of false positives, each requiring a different fix. After three rounds of refinement, all 67 uncalled procedures are fully classified with zero unknowns remaining. Only 9 procedures (268 lines) are genuinely dead.

## The problem

The initial `dead_code.json` was a list of procedures with zero call sites within the AeroDyn source tree. This was presented as "dead code" — misleading for three reasons discovered during investigation:

### Problem 1: Function calls in expressions were invisible

The call graph only tracked `CALL subroutine(...)` statements. Fortran functions called as expressions — `x = VelocityIsZero(v)`, `if(have_nan(...))`, `phi = smoothStep(...)` — were completely invisible.

**Proof:** `VelocityIsZero` appeared as "dead" but is called 5+ times in BEMT.f90 as `if (VelocityIsZero(u%Vy(j,k)...))`.

**Root cause in fparser:** Function calls in expressions are represented as `Part_Ref` or `Structure_Constructor` AST nodes, not `Call_Stmt`. The choice between these two depends on context — `Part_Ref` for most cases, `Structure_Constructor` when fparser interprets the syntax as a type constructor (ambiguous without semantic analysis, since `foo(a, b)` could be a function call, array subscript, or type constructor in Fortran).

**Fix:** Track `Part_Ref` and `Structure_Constructor` nodes alongside `Call_Stmt`. Only record edges when the target name matches a known procedure in `proc_lookup`. This is conservative — some array accesses will match procedure names — but the false positive rate is low since array names rarely collide with procedure names.

**Impact:** Added 229 new call graph edges (from 3222 to 3451), resolving ~40 false "dead" entries.

### Problem 2: CONTAINS-block internal procedures

Procedures defined in a parent procedure's CONTAINS block (e.g., `Fatal`, `Cleanup`) were listed as top-level procedures. Since they're only callable from within their parent, they never appear as callees in the module-level call graph and always show as "dead."

**Example:** `AD_Init` contains a `Fatal` subroutine and a `Cleanup` subroutine. These are called as `CALL Fatal(msg)` from within `AD_Init`, but not from anywhere else. fparser's `walk(module, Subroutine_Subprogram)` returns them alongside top-level procedures.

**Fix:** Detect nested procedures by checking if a procedure node appears inside another procedure node. Tag them `is_internal: true` and exclude from uncalled analysis.

**Impact:** Removed ~97 false "dead" entries (190 CONTAINS-block procedures total, 97 of which were in the dead code list).

### Problem 3: Function name extraction bug

For functions with typed return values like `PURE INTEGER(KIND=IntKi) FUNCTION OutParamFlags(ind)`, the `first_name()` helper was returning `IntKi` (from the kind specifier) instead of `OutParamFlags` (the actual function name). This caused phantom "procedures" named `ReKi`, `IntKi`, `R8Ki` to appear in the inventory.

**Root cause:** `first_name()` returns the first `Name` AST node found anywhere in the statement. For `Function_Stmt`, the kind specifier's name appears before the function name in the AST walk order.

**Fix:** Use `stmt.items[1]` directly, which fparser populates with the function name regardless of prefix/type specifiers.

**Impact:** Eliminated ~10 phantom procedure entries.

## After fixing the three problems: 67 uncalled, 19 unknown

The three fixes reduced the list from 210 to 67. Of these, 48 were auto-classified by module/name heuristics:
- 11 C-binding entry points (module name contains "c_binding")
- 12 driver entry points (module name contains "driver" or "dvr")
- 17 test functions (module name contains "test" or procedure name contains "TEST")
- 8 framework interface (PUBLIC with standard OpenFAST prefix like `AD_`, `BEMT_`)

The remaining 19 were classified "unknown." User requested: "We need to get to the bottom of all of those 19 unknowns."

## Manual investigation of 19 unknowns

Searched the entire OpenFAST tree (not just AeroDyn) for each procedure name using `grep -rn`. Results:

### Commented-out calls (7 procedures)

These have call sites in the source, but the calls are commented out with `!call` — developer debug/diagnostic code left in place for future use:

| Procedure | Where commented out | Purpose |
|-----------|-------------------|---------|
| `ui_expansion_order2` | FVW_VortexTools.f90:1527, 1731 | Higher-order multipole expansion (FMM tree) |
| `Output_Gamma` | FVW_Wings.f90:494 | Debug output of circulation distribution |
| `print_tree` | FVW_Tests.f90:374, 390, 396, 420 | Debug output of FMM tree structure |
| `print_mean_4d` | FVW.f90:947-961 | Debug output of wind velocity means |
| `backupStates` | UnsteadyAero_Driver.f90:189 | State backup for driver restart |
| `restoreUAStates` | UnsteadyAero_Driver.f90:220 | UA state restore |
| `restoreLDStates` | UnsteadyAero_Driver.f90:216 | LinDyn state restore |

### Callback functions (2 procedures)

Passed as function arguments to SLATEC numerical integration routines. This calling convention (`CALL slatec_qk61(f_int1, a, b, ...)`) passes the function by name as a callback — not a `CALL` or expression reference, so invisible to AST-based call tracking:

| Procedure | Call site |
|-----------|----------|
| `f_int1` | `CALL slatec_qk61(f_int1, a, b, answer, ...)` at AeroAcoustics_TNO.f90:193 |
| `f_int2` | `CALL slatec_qk61(f_int2, limits(1), limits(2), ...)` at AeroAcoustics_TNO.f90:81 |

### Called from PROGRAM block (1 procedure)

Our parser only scans MODULE procedures. `print_help` is called from the `PROGRAM` block in `UnsteadyAero_Driver.f90:68`, which is outside any module:

| Procedure | Call site |
|-----------|----------|
| `print_help` | `call print_help()` at UnsteadyAero_Driver.f90:68 (inside `PROGRAM UA_Driver`) |

### Genuinely dead code (9 procedures, 268 lines)

Defined but never referenced anywhere in the entire OpenFAST source tree — not called, not commented out, not passed as callback:

| Procedure | Module | Lines | What it does |
|-----------|--------|-------|-------------|
| `Compute_dX` | AeroDyn | 79 | Linearization state perturbation helper. Never wired into any Jacobian routine |
| `BEMTU_Test_ACT_Relationship` | BEMTUnCoupled | 53 | Test of actuator-theory relationship. Never called from any test harness |
| `Aero_Tests` | AeroAcoustics | 28 | Acoustic model test. Never called |
| `find_nan_3D` | FVW_SUBS | 25 | Debug NaN detector for 3D arrays. Never wired up |
| `find_nan_1D` | FVW_SUBS | 22 | Debug NaN detector for 1D arrays. Never wired up |
| `getAirfoilOrientationMatrix` | BEMTUnCoupled | 19 | Orientation matrix builder. Superseded by other code path |
| `ComputePhiFromInductions` | BEMT | 18 | Phi computation from inductions. Unused alternative |
| `computeAirfoilAOA` | BEMTUnCoupled | 12 | Angle of attack computation. Unused utility |
| `GetEulerAnglesFromOrientation` | BEMTUnCoupled | 12 | Euler angle extraction. Unused utility |

## Automated classification

After manual verification confirmed the patterns, the script was updated to detect all categories automatically via source text analysis:

1. **Commented-out calls:** Scan for lines matching `!.*call.*<name>` or `!.*<name>(` across all source files
2. **Callback usage:** Scan for procedure name appearing as an argument inside a `CALL` statement (regex: `call \w+\(...name...\)`)
3. **PROGRAM block calls:** Track `PROGRAM` / `END PROGRAM` blocks and detect calls within them
4. **Genuinely dead:** No non-definition, non-comment reference found anywhere in the source tree

The automated classification produces identical results to the manual investigation.

## Final breakdown

| Category | Count | Lines | Translation impact |
|----------|-------|-------|-------------------|
| Test functions | 17 | ~900 | Out of scope |
| Driver entry points | 12 | ~1,046 | Out of scope (unless drivers are in scope) |
| C-binding entry points | 11 | ~2,189 | Translate (called from C/C++) |
| Genuinely dead | 9 | 268 | Exclude from scope |
| Framework interface | 8 | ~677 | Translate (called by OpenFAST glue code) |
| Commented-out debug | 7 | ~246 | Translate if parent is in scope |
| Callbacks | 2 | 88 | Translate (passed to SLATEC) |
| PROGRAM block | 1 | 11 | Out of scope |
| **Total** | **67** | **~5,425** | |
| **Unknown** | **0** | **0** | |

## What this means for the translation plan

1. **Only 9 procedures (268 lines) are genuinely dead** and can be excluded from translation scope. This is negligible against the 507 hand-written top-level procedures.

2. **C-binding and framework interface procedures (19 total) must be translated** — they form AeroDyn's external API. These are called from outside AeroDyn and must preserve identical behavior.

3. **Callback functions require special handling.** `f_int1` and `f_int2` are passed by name to SLATEC's `qk61` integration routine. In C++, this translates to function pointers — straightforward but needs to be called out in the plan.

4. **The 29 driver/test procedures are out of scope** for translation — they're test infrastructure, not production physics code. However, the drivers (`AeroDyn_Driver`, `UnsteadyAero_Driver`) are the run targets for KGen extraction, so they must continue working during the translation phase.

5. **Commented-out debug code should be translated if its parent procedure is in scope.** These are dormant helpers that NREL developers use during debugging — removing them would diverge from upstream. They're small (246 lines total) and trivial to translate.

## Lessons for inventory tooling

1. **"Dead code" is almost always "uncalled from within this scope."** Rename the artifact and add caveats. Done.

2. **Fortran's syntax ambiguity is real.** `foo(a, b)` can be a function call, array subscript, or type constructor. fparser resolves this differently depending on context (`Part_Ref`, `Structure_Constructor`, `Section_Subscript`). Any analysis that only tracks one node type will miss cases.

3. **Callback calling conventions are invisible to AST-based call tracking.** When a function name appears as an argument to another function, it's a callback — but it looks like any other Name node in the AST. Detection requires semantic analysis or pattern matching against known callback-accepting functions.

4. **PROGRAM blocks are a blind spot** for module-focused analysis. Standalone programs (`PROGRAM ... END PROGRAM`) contain procedure calls that don't appear in any module's call graph.

## Related

- Dev note `202604232137-aerodyn-structural-inventory.md` — initial inventory
- `vit/scripts/fortran_inventory.py` — the inventory script (three rounds of fixes applied)
- `vit/analysis/aerodyn/uncalled_procedures.json` — the final classified list
