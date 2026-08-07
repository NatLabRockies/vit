# Auditing the State-Capture Build Wrapper: Nine Steps, Not One

**Date:** 2026-08-07
**Status:** completed — items 1–3 applied; item 4 (KGen fork fix) scoped and deliberately deferred
**Scope:** `vit/extract.py` (`generate_state_capture_build_script`), `CLAUDE.md` extraction troubleshooting, KGen fork

## Why this exists

Dev note `202608071058` audited three VIT-side patchers that looked like KGen workarounds, and
concluded two were legitimate and one (`kgen_utils.f90` injection) was a defensible workaround
worth a comment rewrite. **That audit was incomplete.** It grepped `extract.py` for `kgen_utils`
and stopped, so it saw one step where there are nine — and missed two further KGen defects.

This note is the full audit of the build wrapper, and it should be read before the next
extraction cycle.

## What the wrapper actually does

`generate_state_capture_build_script()` emits a bash script that KGen runs as `--cmd-build`. The
module docstring described it as "Injecting kgen_utils.f90 into the project build." The function
docstring listed five steps. There are nine:

| Step | Action | Whose problem |
|---|---|---|
| 1 | Copy `kgen_utils.f90` into the source tree | KGen gap, correctly ours |
| 2 | `sed` it into `CMakeLists.txt` | KGen gap, correctly ours |
| **2b** | `sed` assumed-size actuals into explicit sections at `kw_*` call sites | **KGen code-generator bug — fixable upstream** |
| 2c | Reset project SAVE guards between scenarios | Ours, config-driven |
| 2d | Touch sources to force cmake recompilation | Ours |
| **2e** | Transplant `kw_*` subroutines across cmake target boundaries | KGen limitation, correctly ours |
| 3 | Reconfigure cmake if `CMakeLists.txt` changed | Required (cmake caches its source list) |
| 4 | Run the real build, then install | — |
| 5 | Strip tagged persistence lines, restore the tree | — |

### The silent-failure chain (steps 1–3)

Worth stating plainly because it explains why this code is so defensive. KGen instruments source
with calls to `kgen_init_vars` / `kgen_check_save` but never arranges for `kgen_utils.f90` to be
compiled. **On Linux a shared library links with unresolved symbols.** So the build *succeeds*,
the simulation *runs*, and only the capture comes back empty. Nothing in the chain reports an
error.

Step 3 exists for a second link in the same chain: cmake caches its source list, so patching
`CMakeLists.txt` without reconfiguring means `cmake --build .` silently uses the old list and
never compiles the file you just added — same silent outcome, different cause.

Fixing steps 1–2 upstream would require KGen to understand arbitrary build systems, which is the
objection that killed `--auto-allocate` (dev note `202605011207`). A build-command wrapper is the
right shape here, not debt.

### Step 2e — also correctly ours

KGen follows `USE` chains and instruments files outside the cmake build target. Those files are
never compiled, so their generated `kw_*` subroutines surface as unresolved symbols. VIT
transplants them into the in-target file that defines the same Fortran `MODULE`
(`fix_cross_module_instrumentation`). An upstream fix would require KGen to model build-system
target boundaries — same objection as steps 1–2.

## Item 4: the one that does meet the "fix it in the fork" bar

**Step 2b is a genuine KGen code-generator defect.** KGen emits write subroutines with
assumed-shape `DIMENSION(:)` dummies, then passes assumed-size `(*)` actuals to them. That is
invalid Fortran — an assumed-size array cannot be passed to an assumed-shape dummy, because the
callee has no way to know the extent.

VIT's workaround rewrites the *actual* into an explicit section:

```
s/(varname, kgen_unit/(varname(1:N), kgen_unit/gI
```

applied by `sed` to every `.f90`/`.F90` in the source dir, with `N` from `assumed_size_arrays` in
`vit.yaml`.

**Why the fix belongs upstream.** KGen cannot infer `N` — that is exactly the assumed-size
story, where an array captured at an assumed extent of 2,000 was written to index 2,801. VIT
determines `N` from source evidence gated by exit code 10. But KGen does not need to infer it:
it could emit **explicit-shape dummies that take the extent as an argument**, and pass the extent
it already has at the call site. That removes a `sed` pass that currently rewrites every Fortran
file in the tree.

**Why it was not done today, and this is the important part.** Three reasons, in order:

1. The `kw_*` generator builds declarations through KGen's AST builder
   (`part_append_genknode` with `attrs` dicts in
   `kgen/extractor/plugins/gencore/gen_write_in_module.py`), not from a template. It is not a
   one-line template edit, and I did not locate the exact emission site.
2. **KGen has no unit test that would catch a regression here.** `test/`, `kgen/kgtest.py`, and
   `base/kgen_test.py` exist but nothing covers generated-declaration shape.
3. Verifying a change means a full extraction cycle against clean source — `reset_to_clean.sh`
   → extract → `integrate_all.sh` → rebuild → baselines, roughly 15+ minutes, and it leaves the
   tree mid-cycle if anything fails. The tree is currently in integrated state.

This sits under the entire verification story. A generator change that produces kernels which
build but capture subtly different state would be the worst possible defect class in this
project. **Do it deliberately, at the start of the next extraction cycle, when the tree is
already clean and a baseline run is already planned.**

### A process note on how this nearly went wrong

While writing the step-2b docstring I marked it `[KGen code-generator bug -- FIXED UPSTREAM
2026-08-07]` and described a fix I was *about to* make. I caught it before committing, but it
would have shipped a comment asserting a fork change that does not exist — the fourth instance
today of the pattern this whole session has been about (including the assumed-size
misattribution and `FC_0` "KGen never defines these"). Documentation written in anticipation is
indistinguishable, later, from documentation written from observation. Write it after.

## What was changed

**1. Docstrings (`vit/extract.py`).** Module docstring no longer claims one step. Function
docstring enumerates all nine with provenance, and records the step-2 `sed` fragility (below).

**2. `CLAUDE.md` extraction troubleshooting.** "No state data captured" listed three causes;
added a fourth — `kgen_utils.f90` never compiled — with how to check it. It is the hardest to
diagnose precisely because every stage reports success.

**3. Debug logging.** Eight unconditional writes to a hardcoded `/tmp/vit_wrapper.log` (449
lines, 25 KB in the container, last written 2026-05-01). Now `VIT_LOG="${VIT_WRAPPER_LOG:-/dev/null}"`
— opt-in, no fixed path, silent by default. Kept rather than deleted precisely because the
failures here are silent; `VIT_WRAPPER_LOG=/tmp/vit.log` is the first thing to reach for when
capture comes back empty.

**4. Not done.** See above.

## Known fragility: the CMakeLists `sed`

```
sed -i '0,/^\( *src\/[A-Za-z_]*\.f90\)$/s//\1\n    src\/kgen_utils.f90/'
```

Assumes sources are under `src/`, use a lowercase `.f90` extension, and have names free of digits
and hyphens. It matches ROSCO, and it matches AeroDyn (`src/UnsteadyAero.f90`,
`modules/aerodyn/CMakeLists.txt:34`) — verified, not assumed. **No live bug.**

But a project laid out differently gets the silent failure. This is now cause 4 in `CLAUDE.md`.
If VIT is ever pointed at a third codebase, check this first.

## Next time extraction is exercised

- [ ] Fix step 2b in the KGen fork: explicit-shape `kw_*` dummies taking the extent as an
      argument. Start at `gen_write_in_module.py`; find the `kw_*` emission site.
- [ ] Verify with a real extraction, then delete the step-2b `sed` from `extract.py`
- [ ] Consider a KGen regression test for generated-declaration shape — its absence is what
      makes this change expensive
- [ ] Re-check the step-2 `sed` if the target project changes

## References

- Dev note `202608071058` — the incomplete audit this corrects; also the `FC_0` fix
- Dev note `202608071048` — the founding transcript; origin of these patchers
- Dev note `202605011207` — `--auto-allocate` rejected for encoding build-system specifics; the
  precedent for why steps 1–2 and 2e are correctly ours
- Dev note `202605010623` — KGen call-site pragma requirement; cause 1 of "No state data captured"
