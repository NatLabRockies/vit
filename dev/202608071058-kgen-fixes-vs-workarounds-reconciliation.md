# KGen Fixes vs. VIT-Side Workarounds: Reconciling `CLAUDE.md` With the Code

**Date:** 2026-08-07
**Status:** in-progress — investigation needed on item 1 before any change
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

### 1. `FC_0` / `FC_FLAGS_SET_0` — **premise is questionable, investigate before acting**

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

**Do not delete the patcher on the strength of this note.** Investigate first:

- Does VIT populate `kernel_option['FC']` / `['FC_FLAGS']` when invoking KGen? Check
  `vit/extract.py` and the `vit.yaml` schema in `vit/config.py`.
- Reproduce: run an extraction with the option supplied and inspect the generated Makefile for
  `FC_0`.
- If VIT simply isn't passing it, the fix is to pass it, and the patcher becomes dead code. If
  KGen genuinely emits an undefined reference on a path we legitimately use, fix it in the fork
  (a default, or a hard error naming the missing option — never a silent undefined variable).

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

## Proposed resolution

1. **Investigate item 1.** Highest value — it may be a live bug in our own invocation.
2. **Rewrite the item 3 comment** to record the rejected alternative rather than implying debt.
3. **Add a carve-out to `CLAUDE.md`.** The current rule admits no exceptions, but items 2 and 3
   are legitimate and permanent. Suggested amendment after the existing KGen paragraph:

   > Three VIT-side patchers are *not* covered by this rule and should not be "fixed upstream":
   > C++ object and `-lstdc++` injection into the kernel Makefile (KGen has no C++ concept —
   > this is VIT's integration layer), and the `kgen_utils.f90` build-command wrapper (fixing it
   > in KGen would require KGen to understand arbitrary build systems). See dev note
   > `202608071058`. Everything else that looks like a KGen bug should be investigated as a
   > usage error first, then fixed in the fork.

   Without this, a future session reads a rule stricter than the codebase and either wastes
   time or stops trusting the rule.

## Next steps

- [ ] Trace `kernel_option['FC']` through `vit/config.py` → `vit/extract.py` → KGen invocation
- [ ] Reproduce a clean extraction and inspect the generated Makefile for `FC_0`
- [ ] Depending on the finding: pass the option and delete the patcher, or fix the fork
- [ ] Rewrite the `extract.py:22-25` comment
- [ ] Amend `CLAUDE.md` with the carve-out

## References

- Dev note `202608071048` — the founding transcript this came from; same
  unverified-assertion-survives-by-inertia pattern
- Dev note `202605011207` — `--auto-allocate` rejected for encoding coding style; the same
  reasoning applies to why item 3 may be correct as a wrapper
- Dev note `202605010623` — KGen call-site pragma requirement; the canonical "it was a usage
  error, not a KGen bug" case
- `CLAUDE.md` — "When KGen extraction fails, it is almost never a KGen bug"
