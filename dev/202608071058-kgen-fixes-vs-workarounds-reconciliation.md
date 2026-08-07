# KGen Fixes vs. VIT-Side Workarounds: Reconciling `CLAUDE.md` With the Code

**Date:** 2026-08-07
**Status:** completed — item 1 investigated and fixed; one follow-up comment rewrite outstanding
**Scope:** KGen fork, `vit/kernel_verify.py`, `vit/extract.py`, `CLAUDE.md` KGen rule

## What prompted this

Reviewing the February 2026 founding transcript (see dev note `202608071048`) surfaced the two
manual fixes that seeded VIT's kernel-prep layer:

> "I had to update the Makefile by adding `saturate_cpp.o` to `ALL_OBJS`"
> "I also had to update this line in Controllers.f90: `USE kgen_utils_mod !, ONLY: ...`"
> "It seems that KGen didn't pull all the right dependencies in."

The same transcript records "identifying and patching four bugs in KGen's code generator" on day
one. Six months later, `CLAUDE.md` carries a strict rule:

> "If investigation reveals a genuine KGen bug, fix it in the KGen repo — do not work around it
> with custom test harnesses or alternative verification. KGen is in our control; workarounds
> leave the bug for the next function."

This note checks the rule against the code. **The headline is that the standard has largely
been honored** — but three VIT-side patchers remain, one of which may rest on a false premise.

## The fork record: the standard is being followed

`KGen` carries at least six VIT-era fixes, made upstream in the fork rather than worked around:

| Commit | Fix |
|---|---|
| `0705f2d` | Surface build/run errors during state capture instead of swallowing them |
| `c57cd7b` | Disable ETime profiling by default (VIT specifies invocation range explicitly) |
| `4676161` | Fix backslash escape bug in Fortran string literal parser |
| `31d653c` | Fix PROCEDURE POINTER parsing for Fortran 2003+ |
| `b5fd702` | Fix `parse_bind` crash on `TYPE, BIND(C)` declarations |
| `6144156` | Correct handling of complex and deferred-length character types |

So genuine parser and runtime defects **do** get fixed at the source. Any framing of this as a
project-wide standards violation is wrong, and an earlier read of mine that implied one was
overstated.

**Two corrections fall out of this table:**

1. **An earlier description misattributed upstream work to our fork — corrected 2026-08-07.** It said the fork
   "adds `BIND(C)` parsing, procedure-pointer support, and assumed-size array handling." Two of
   those are ours (`b5fd702`, `31d653c`). **Assumed-size array support is not** — it is upstream
   NCAR commit `384009d`, "resolved issue-08 by adding support for `Assumed_Size_Spec`," outside
   the VIT-era window entirely.

   The likely source of the error: VIT *does* have assumed-size **extent inference**
   (evidence-based, gated by exit code 10), which is ours — but it is a VIT
   feature, not a KGen fork change. The two got conflated, and the conflation ran in the
   direction of claiming credit for someone else's fix, which is the worst direction for it to
   run.

   Corrected to name what the fork actually changed: `BIND(C)` and procedure-pointer parsing,
   character and complex kinds, and error propagation during capture. The list also stops
   understating — the seven VIT-era commits cover more than the original three items claimed.

   **The seven VIT-era commits** (everything after the last upstream commit):
   `0705f2d` error surfacing during capture · `c57cd7b` ETime profiling default ·
   `4676161` string-literal backslash bug · `31d653c` PROCEDURE POINTER parsing ·
   `b5fd702` `parse_bind` on `TYPE, BIND(C)` · `d76be2a` explicit python2.7 in `bin/kgen` ·
   `6144156` complex and deferred-length character types

2. **The same description catalogued a defect that had since been fixed — corrected 2026-08-07.** It stated
   "KGen's runner ignores subprocess return codes, so build failures during capture manifest
   only as absent state files," in the present tense, as a live hazard. Commit `0705f2d` had
   already fixed it. Now reads "...which we fixed in the fork rather than detected around,"
   which turns a complaint about a dependency into a demonstration of the root-cause
   standard.

## The three remaining VIT-side patchers

### 1. `FC_0` / `FC_FLAGS_SET_0` — **RESOLVED 2026-08-07: the patcher stays, and it had a bug**

> **The investigation below reached the wrong conclusion, and the record is kept so the
> reasoning is visible.** The premise ("KGen never defines these") is indeed false — but the
> inference drawn from it, that the patcher masks a VIT config gap and could be deleted by
> passing `--kernel-option FC=`, was also wrong. See **Resolution** at the end of this item.

`vit/kernel_verify.py:653,667` asserts:

> "Define `FC_0` and `FC_FLAGS_SET_0` (KGen bug: uses but never defines these)"
> "Note: `FC_0` and `FC_FLAGS_SET_0` are USED but NEVER DEFINED."

**This appears to be false as stated.** `kgen/extractor/main.py` does emit both:

```
313:  self.write(f, 'FC_0 := %s' % Config.kernel_option['FC'])
318:  self.write(f, 'FC_0 := %s' % Config.add_mpi_frame["mpifc"])
326:  self.write(f, 'FC_FLAGS_SET_0 := %s' % Config.kernel_option['FC_FLAGS'])
```

They are emitted **conditionally**, gated on `Config.kernel_option['FC']` / `['FC_FLAGS']` being
populated. So the likely truth is not "KGen never defines these" but "KGen doesn't define them
when we fail to pass `--kernel-option FC=...`" — i.e. a VIT invocation gap that the patcher has
been masking since February.

This is precisely the pattern `CLAUDE.md` warns about: *"Every 'KGen bug' in this project has
turned out to be a usage error."* The comment claiming a KGen bug is itself the kind of
unverified assertion that survives because nobody re-checks it — same failure mode as the
misrecorded agent model in dev note `202608071048`.

#### Resolution (investigated 2026-08-07)

**VIT does not pass `FC`** — confirmed, there is no `FC`/`FC_FLAGS` anywhere in `vit/config.py`
or `vit/extract.py`; the invocation carries only `--cmd-build` and `--cmd-run`. So that part of
the diagnosis held.

**But KGen defines them anyway, from a third source I had not read.** `main.py:311-330` writes
`FC_0` from *any of*: an explicit `kernel_option['FC']`, the MPI frame, **or compilers
auto-detected during instrumentation** — and likewise `FC_FLAGS_SET_0` from
`kernel_option['FC_FLAGS']` or detected compiler options. In VIT's usage the third path is the
live one. "Never defines these" was false in a way that passing the option would not have fixed.

**And the patcher is not a workaround at all.** Its `if existing:` branch *overwrites* what KGen
wrote. That is its actual job: the kernel must compile the way production does, or the
comparison measures the compiler instead of the translation. The flags it forces are
`-fvect-cost-model=unlimited` (the vectorization remedy — the cause behind five kernel cases
that failed with no defect in the C++) and the project's `fortran_flags` (literal precision).
Deleting it would reintroduce a documented class of spurious failures. It belongs with item 2:
correct by design, not debt.

**The investigation did find a real bug, in the branch logic.** `compilers` and
`compiler_options` are two independently-populated `OrderedDict`s (`main.py:232-233`) iterated
separately, so a Makefile with `FC_0` defined and `FC_FLAGS_SET_0` *not* is reachable. The old
code coupled the two branches:

```python
if existing:            # FC_FLAGS_SET_0 present -> overwrite
elif not FC_0 present:  # neither present -> insert both
```

In the divergent case `existing` is None and `FC_0` *is* present, so **neither line was
inserted** and the kernel silently built with `FC_FLAGS_SET_0` empty — no `-O2`, no
vectorization matching, no `fortran_flags`. That does not fail loudly. It produces a kernel that
can pass or fail for the wrong reason, which is the one thing kernel verification must never do.

Fixed by handling the two definitions independently. Two regression tests added
(`test_flags_defined_when_only_fc_0_present`, `test_existing_fc_0_not_duplicated`); the first
fails against the old code with `assert []` and passes after. Suite 660/660.

**Lesson, and it is the third instance today.** A comment asserting "KGen bug: uses but never
defines these" sat unchallenged for six months and was wrong on three counts, while the code
underneath it was right and quietly carried a defect the comment's framing actively concealed —
if you believe you are patching around someone else's bug, you do not audit your own branch
logic. Same shape as the misrecorded agent model (dev note `202608071048`) and the assumed-size
misattribution: an unverified assertion survives because it explains something plausibly.

### 2. C++ object in `ALL_OBJS` — **not a workaround; this is correctly VIT's job**

`vit/kernel_verify.py:12` ("C++ object must be in `ALL_OBJS`, not just have a compilation rule")
and the `-lstdc++` link-flag injection are **not** KGen bugs. KGen has no concept of C++ — it was
built for Fortran performance kernels. Teaching a Fortran kernel build to link a C++ translation
unit is exactly the integration layer VIT exists to provide. Leave it, and don't let a future
cleanup pass mistake it for a workaround.

It is an example of the boundary: KGen supplies
capture-and-replay; the mixed-language build is VIT's.

### 3. `kgen_utils.f90` injection — **genuine gap, fixable upstream**

`vit/extract.py:10,22-25`:

> "KGen instruments code with calls to `kgen_utils` functions but doesn't add `kgen_utils.f90`
> to the project's build system. The instrumented shared library then fails to link. We wrap
> `--cmd-build` in a script that injects `kgen_utils.f90` into the build."

This one is a real defect and the workaround is real: KGen emits calls to a module it never
arranges to compile. Wrapping the build command is a symptom fix — it accommodates our build
system and would need re-doing for the next one.

That said, the upstream fix is genuinely harder than it looks: KGen would have to understand the
target's build system well enough to add a source file, which is the same "encodes the target's
coding conventions" problem that got `--auto-allocate` rejected (dev note `202605011207`). A
build-command wrapper may be the *correct* general answer precisely because it makes no
assumption about the build system.

**Recommendation:** keep the wrapper, but rewrite the comment. It currently reads as an
apology for a workaround; it should read as a deliberate design choice with the alternative
stated and rejected, so the next reader doesn't "fix" it.

## Resolution

**None of the three patchers is a KGen workaround.** All three are VIT doing its own job:

| Patcher | Verdict |
|---|---|
| `FC_0` / `FC_FLAGS_SET_0` | Forces kernel build flags to match production. Correct; had a branch bug, now fixed |
| C++ `ALL_OBJS` + `-lstdc++` | KGen has no C++ concept; this is the integration layer |
| `kgen_utils.f90` injection | Real defect, but the upstream fix would need KGen to understand arbitrary build systems |

So `CLAUDE.md`'s rule does not need a carve-out for items 1 and 2 — nothing there is being worked
around. Item 3 is the only genuine workaround, and it is defensible for the same reason
`--auto-allocate` was rejected: the alternative encodes build-system specifics into a general
tool. **The rule stands as written.** What needs changing is the comments that mislabel VIT's own
design decisions as other people's bugs.

## Next steps

- [x] Trace `kernel_option['FC']` through `vit/config.py` → `vit/extract.py` → KGen invocation
- [x] Fix the coupled-branch bug and add regression tests (660/660)
- [x] Rewrite the `kernel_verify.py` docstring and inline comment
- [ ] Rewrite the `extract.py:22-25` comment for item 3 — state the rejected alternative
      (KGen understanding arbitrary build systems) rather than reading as debt
- [ ] Consider a one-line note in `CLAUDE.md` pointing at this note, so the next session that
      sees a "KGen bug" comment in `kernel_verify.py` knows it was audited

## References

- Dev note `202608071048` — the founding transcript this came from; same
  unverified-assertion-survives-by-inertia pattern
- Dev note `202605011207` — `--auto-allocate` rejected for encoding coding style; the same
  reasoning applies to why item 3 may be correct as a wrapper
- Dev note `202605010623` — KGen call-site pragma requirement; the canonical "it was a usage
  error, not a KGen bug" case
- `CLAUDE.md` — "When KGen extraction fails, it is almost never a KGen bug"
