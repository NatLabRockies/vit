# Generalize the Dormant ROSCO/DISCON Carryovers in the VIT Tool

**Date:** 2026-07-23
**Status:** implemented 2026-09-30 (C2 in `cc1facd`, C1, C3 and C4 in the commit that changed this line). **C4 as planned below is wrong:** the stray `USE` names the call site's enclosing procedure (`DISCON`), not the extracted function (`sig.name`), so stripping `USE <sig.name>` would have left all five of ROSCO's DISCON-site kernels broken. The implemented rule strips `USE X` wherever the kernel defines `X` as a procedure and never as a module. ROSCO regression, 2026-09-30: all 42 golden kernels build and match (22 pass; 20 are NON_DISCRIMINATING, the red-test policy, unchanged by this work); the five DISCON-site kernels also needed `PUBLIC` removed from files that define no module, a separate fix in the following commit. ROSCO's `vit.yaml` must now list `translations/rosco_constants.h` in `shared_files`.
**Scope:** VIT tool generality — `kernel_verify.py`, `integrate.py`, `config.py`; the "no codebase-specific logic" design rule

## TL;DR

While reviewing VIT's "no codebase-specific logic" claim,
we found four **ROSCO/DISCON-specific string literals still living in the VIT tool
package** (not in generated output). All four are **dormant no-ops for the AeroDyn
campaign**, which is why the "no fork" discipline never forced their removal. All four can
be engineered away by routing to signals the tool *already* computes (`shared_files`
config, `target_name` config, `sig.name` / `sig.is_function` from the parsed signature).
One of them (the `USE discon` strip) is not mere cleanup — generalizing it pre-handles
AeroDyn's own C-binding entry points, a real forward-looking gap. Estimated ~half a day
including regression. **ROSCO is the real regression test here, not AeroDyn** (the
carryovers are ROSCO's active path; AeroDyn only proves they stay dormant).

## Background / how this surfaced

This came out of a design discussion about VIT. Someone
objected to the "custom Fortran harness" verification method for `DBEMT_InitStates_AllNodes`
("why not just add simulation coverage / build full OpenFAST?"). Investigating that led to
a broader question: are the interop artifacts the harness uses
(`vit_dbemt_*_view` modules, `vit_populate_dbemt_*`, `vit_copy_scalars_to_dbemt_otherstatetype`)
AeroDyn-specific code?

**Answer to that question (important framing): NO — those are generated *output*, not tool
logic.** They physically live in the OpenFAST workspace
(`openfast/modules/aerodyn/src/vit_dbemt_*_view.f90` and per-kernel copies under
`openfast/kernel/*/`), never in `vit/vit/`. Their names are 100% templated from the Fortran
type name with no `dbemt` literal in the generator:

- `view_populator.py:79` — `module_name = f"vit_{name_lower}_view"`
- `view_populator.py:80` — `sub_name = f"vit_populate_{name_lower}"`
- `view_populator.py:157` — `reverse_name = f"vit_copy_scalars_to_{name_lower}"`

Point the same generator at a ROSCO type → `vit_rosco_pitchcontrol_view`. So for the
generated artifacts, the "no codebase-specific logic" rule holds cleanly: general
generator, necessarily module-named output (like a compiler emitting object files named
after your source).

**The real finding was elsewhere.** Grepping `vit/vit/*.py` for target names
(`dbemt|aerodyn|airfoilinfo|rosco|discon`) turned up mostly docstring examples and comments,
but **four genuine target-named literals in executable tool logic** — all ROSCO/DISCON, none
AeroDyn. Those are what this note is about.

## The four carryovers (exact locations, verified 2026-07-23 @ vit HEAD `30a9349`)

### C1 — `kernel_verify.py:168` — ROSCO literal in the shared-files fallback
```python
# Use project-specific list from config, with legacy fallback
if shared_files:
    shared_filenames = [Path(f).name for f in shared_files]     # line 166
else:
    shared_filenames = ["rosco_constants.h", "vit_nwtc.h", "vit_nwtc.cpp"]   # line 168
```
- **What it does:** copies shared headers/sources into each kernel dir during verification.
- **Why dormant for AeroDyn:** the `if shared_files:` branch is taken — AeroDyn's `vit.yaml`
  declares `shared_files` (see below), so the fallback list is never reached.
- **Severity:** cosmetic. Dead ROSCO literal.

### C2 — `config.py` / `integrate.py` — `target_name` default `"discon"`
```python
config.py:44   "target_name": "discon",
config.py:79   target_name: str = "discon"
config.py:167  target_name=build.get("target_name", "discon"),
integrate.py:91  target_name: str = "discon",
```
- **What it does:** default CMake target name when config omits it.
- **Why dormant for AeroDyn:** AeroDyn's `vit.yaml` sets `target_name: aerodyn_driver`
  (`vit.yaml:17`), overriding the default everywhere.
- **Severity:** cosmetic. ROSCO-flavored default.

### C3 — `integrate.py:157–161` — hardcoded walk-up copy of `rosco_constants.h`
```python
# Walk up to find rosco_constants.h in translations/ root          # line 157
for parent in [constants_header] + list(constants_header.parents):
    candidate = parent / "rosco_constants.h"                        # line 159
    if candidate.exists():
        dest = Path(src_dir) / "rosco_constants.h"                  # line 161
        ...
```
- **What it does:** during integration, finds and copies the shared constants header into
  the source dir.
- **Why dormant for AeroDyn:** `rosco_constants.h` doesn't exist in the AeroDyn tree, so the
  loop finds nothing → no-op. AeroDyn's constants (`vit_aerodyn_constants.h`) are handled via
  `shared_files`.
- **Severity:** functional-but-dead. This one is a genuine hardcoded filename with **no
  config fallback** in the integrate path — the biggest of the four, because it *should* be
  routing through `shared_files` and currently isn't.

### C4 — `kernel_verify.py:1424–1432` — hardcoded `USE discon` strip
```python
# Remove USE statements for DISCON module.
# When extracting from DISCON.F90, KGen generates the kernel driver
# with `USE discon, ONLY: discon` — but DISCON is a subroutine, not   # line 1426
# a module. The kernel driver calls discon() directly via EXTERNAL.   # line 1427
content = re.sub(
    r'^\s*USE\s+discon\b.*$',                                         # line 1429
    '! VIT: removed USE discon (subroutine, not module)',            # line 1430
    content, flags=re.MULTILINE | re.IGNORECASE,
)
```
- **What it does:** when a kernel is extracted at a **C-binding entry-point procedure**
  (ROSCO's `DISCON`), KGen wrongly emits `USE <name>, ONLY: <name>` for a symbol that is a
  *subroutine*, not a module. This strips that invalid `USE`.
- **Why dormant for AeroDyn:** AeroDyn kernels contain no `USE discon`, so the regex never
  matches → no-op.
- **Severity:** **this is the one that is more than cleanup.** The real condition is generic
  ("KGen emitted `USE <X>` where X is the extracted procedure, not a module"), and AeroDyn
  has its own C-binding entry points (the framework-interface / ADI C-binding procedures the
  project plan earmarks for the full-OpenFAST coupled path). If any of those is ever
  kernel-extracted, KGen will emit the same bad `USE <entryname>` and today's ROSCO-literal
  regex will not catch it. Generalizing C4 fixes ROSCO *and* pre-handles AeroDyn's coupled
  entries.

## Why these survived (design-philosophy context)

Not difficulty — **dormancy**. VIT's development rule is "root-cause fixes only" and
"extend when encountered" (YAGNI). Because all four are no-ops for AeroDyn, no failure ever
forced the cleanup, and the "no fork" constraint held anyway. Precedent: the Phase-C audit
(dev note `202604301926`) explicitly deferred an unrelated generality gap with "No current
use case. Extend when encountered (YAGNI)." So proactively generalizing dormant literals is
a *judgment call*, justified here specifically because VIT's design rule is an absolute
"no codebase-specific logic" claim and C4 has real forward-looking value.

## The generalization plan (each routes to an existing signal — no new infrastructure)

Signals already available at these call sites (verified):
- **`shared_files`** — from config/`vit.yaml`; already threaded into `kernel_verify.py`
  (consumed at line 166). AeroDyn `vit.yaml:159` declares:
  `vit_nwtc.h`, `vit_nwtc.cpp`, `vit_aerodyn_constants.h`, `vit_fortran_intrinsics.h`,
  `vit_types.h`.
- **`target_name`** — config field (AeroDyn: `aerodyn_driver`).
- **`sig.name` / `sig.is_function`** — parsed signature, in scope in `kernel_verify.py`
  (already used at lines 409, 416, 2518).

| # | Fix | Signal | Effort |
|---|-----|--------|--------|
| C1 | Delete the ROSCO literal from the dead fallback (`kernel_verify.py:168`). Make fallback empty or config-required. | `shared_files` (already primary) | Trivial (1 line) |
| C2 | Change `target_name` default to a neutral placeholder, or drop the default so config must supply it (`config.py:44,79,167`; `integrate.py:91`). | `target_name` config | Trivial |
| C3 | Thread `shared_files` into `generate_integration_plan` and iterate it to copy shared headers into `src_dir`, replacing the single-filename walk-up (`integrate.py:157–161`). | `shared_files` config | Small — add a param, thread through the one call path in `cli.py`, replace the loop |
| C4 | Replace the `discon` literal with `sig.name`; guard the strip on "entry point is a bare procedure" (use `sig.is_function`/procedure-kind). Strip `USE <sig.name>` generically. (`kernel_verify.py:1424–1432`) | `sig.name`, `sig.is_function` | Small — swap literal, add procedure-kind guard |

### C3 detail (the one with real plumbing)
`generate_integration_plan(...)` (`integrate.py:86–`) does **not** currently receive
`shared_files`. Add a `shared_files: Optional[list] = None` param, pass it from the CLI
integrate path (`cli.py` `cmd_integrate` — locate where `generate_integration_plan` is
called), and in the plan body replace the `rosco_constants.h` walk-up with: for each name in
`shared_files`, walk up from `cpp_impl_path` to locate it and emit a copy patch into
`src_dir` if not present. This mirrors exactly what `kernel_verify.py:164–172` already does,
so factor a small shared helper if convenient (e.g., `resolve_shared_files(base, shared_files)`).

### C4 detail (the forward-looking one)
The current regex hard-codes `discon`. The general intent: after KGen generates the kernel
driver, if it contains `USE <X>` where `X` == the extracted procedure name and that
procedure is an entry point (not a module), strip it. Minimum viable generalization:
`re.sub(rf'^\s*USE\s+{re.escape(sig.name)}\b.*$', ...)`, gated so it only fires for
procedure entry points (avoid stripping a legitimate `USE <module>` that happens to share a
name — in practice entry-point names like `DISCON` don't collide with module names, but the
`is_function`/procedure-kind guard makes it safe). Confirm `sig` is in scope at line ~1429
(it is elsewhere in the same function via `sig.name`/`sig.is_function`).

## Verification plan (do NOT skip — ROSCO is the real test)

The whole point is that C3/C4 are on **ROSCO's active path**. AeroDyn only proves dormancy.
So:

1. **ROSCO regression (primary):** re-run a ROSCO extraction that goes through the `DISCON`
   entry point and confirm C4 still strips the bad `USE` via `sig.name` (kernel still builds
   and verifies). Re-run a ROSCO integration and confirm C3 still copies
   `rosco_constants.h` via `shared_files` (ROSCO `vit.yaml` must list it — **check/add
   `rosco_constants.h` to ROSCO's `shared_files`** as part of C1/C3, since removing the
   fallback literal means ROSCO now depends on the config entry). ROSCO workspace:
   `vit_translation/`.
2. **AeroDyn dormancy (secondary):** run the AeroDyn `17/17` bit-identical baseline gate
   (`scripts/verify_aerodyn_baselines.sh all`) and re-run one AeroDyn kernel verify + one
   integrate to confirm nothing regressed (the dormant paths stay dormant).
3. **Audit grep:** after the change, `grep -rniE 'dbemt|aerodyn|airfoilinfo|rosco|discon'
   vit/vit/*.py` should return only docstring examples/comments — no target-named strings in
   executable logic.

**Pre-req to check first:** ROSCO's `vit.yaml` `shared_files` — does it list
`rosco_constants.h`? If not, C1/C3 will break ROSCO until it's added. Verify before deleting
the fallback literal.

## Implications for the design rule

The design rule states the tool admits "no codebase-specific logic" and that periodic audits
search the tool for target-specific assumptions. That is
true in *substance* (generation logic is general; the AeroDyn campaign ran without a fork,
and these literals stayed dormant — which is itself evidence for transferability), but the
*absolute* phrasing is slightly stronger than the code today (C3/C4 are target-named
special-cases). Implementing this note makes the absolute claim hold literally, and C4
closes a real forward-looking gap.

## Next-session checklist

- [ ] Check ROSCO `vit.yaml` `shared_files` includes `rosco_constants.h`; add if missing.
- [ ] C1: delete ROSCO literal from `kernel_verify.py:168` fallback.
- [ ] C2: neutralize `target_name` default (`config.py:44,79,167`; `integrate.py:91`).
- [ ] C3: thread `shared_files` into `generate_integration_plan`; replace `rosco_constants.h`
      walk-up (`integrate.py:157–161`); update `cli.py` call site.
- [ ] C4: generalize `USE discon` strip to `USE <sig.name>` with procedure-kind guard
      (`kernel_verify.py:1424–1432`).
- [ ] ROSCO regression: DISCON extraction/verify + integration copy of `rosco_constants.h`.
- [ ] AeroDyn dormancy: `17/17` baselines + one verify + one integrate.
- [ ] Audit grep clean (only docstrings/comments remain).

## Cross-references

- Generated-artifact templating: `vit/view_populator.py:79–80,157`.
- Existing `shared_files` consumption pattern to mirror: `kernel_verify.py:164–172`.
- YAGNI / "extend when encountered" precedent: dev note `202604301926` (Phase-C audit).
- Why the harness question started this: custom-harness method for `DBEMT_InitStates_AllNodes`
  (dev notes `202604301834`, `202604301844`; `docs/VERIFICATION_METHODS.md` §4).
