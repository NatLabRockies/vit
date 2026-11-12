# Proposal: The Verification Record Keeps the Latest Result

**Date:** 2026-11-11
**Status:** in-progress (decided 2026-11-11; `vit run` removed, the rest not yet implemented)
**Scope:** `record_translation` and the `translations` section of `vit.yaml`; `vit verify`, `vit integrate`, `vit status`; `vit run`, removed; the callees `vit translate` offers

## Why

Dev note `202609302022` left one defect open: a later verification that fails, or that cannot
discriminate, does not change what `vit status` reports. It was reported twice on 2026-09-30 and
never decided; the author asked for a full proposal. This note is that proposal, with the author's
decisions at the end. It is completed, with what was done, when the rest is implemented.

## What the code does now

**One field holds two facts.** Each function's entry in `vit.yaml` `translations` has a `status`,
written by `record_translation` (`vit/config.py:391`) and ranked `blocked` < `scaffold` <
`translated` < `verified` < `integrated` (`_STATUS_RANK`). A new status lower than the old one is
not written; only the other fields are updated. The rule exists so that verifying again after
integration does not turn `integrated` back into `verified`. But the status is both how far the
function has got (its C++ is in the build) and what the evidence says (its C++ matched the
Fortran), and only the first should never go down.

**A NON_DISCRIMINATING result is hidden by an earlier pass.** `vit verify` records it as status
`non_discriminating` (`vit/cli.py:1636`), which is not in the ranking, so it ranks below everything
and never replaces `verified` or `integrated`. It also writes `cases_passed` but not `cases_total`.
Reproduced through `record_translation` and `cmd_status`: a function verified 20/20 with a
demonstrated red test, then verified again as NON_DISCRIMINATING with 12 cases passing, is
reported as

```
  ✓ drag: verified (12/20 cases)
```

with a tick, the new pass count over the old total, and its `red_test: not_discriminating` unread.
A function whose first verification is NON_DISCRIMINATING shows `? lift: non_discriminating`.

**A failed verification is not recorded at all.** When cases fail, `cmd_verify` prints them and
returns 1 (`vit/cli.py:1663`) without calling `record_translation`; so does a kernel setup failure
(`vit/cli.py:1606`). A function that passed last week and fails today still shows its old pass.

**An edited translation keeps its old pass.** Nothing records which C++ was verified. A `.cpp`
changed after `vit verify` keeps the record of the version that passed.

**`vit run` records "verified" without verifying.** `cmd_run` records
`"verified": not args.skip_verify` at integration (`vit/cli.py:1970`). When it finds no kernel
directory it prints "Step 2: Skipped" and still records `verified: true`, which `vit status` shows
as `integrated (verified)`. When it does verify, it runs no red test and records no case counts.
It also lags `vit integrate`: none of `--copy-arrays`, `--reverse-copy`, `--auto-allocate` or
`--dry-run`, and no copying of the shared files beside the integrated source, so a translation that
needs a shared header could integrate and then fail to build. No test drives it, the documents name
it only in the command tables, and the ROSCO campaigns' cycle logs record 479 `vit verify` runs and
no `vit run`.

**`vit status` is the only reader, and always exits 0.** Nothing else checks the status:
`vit integrate` integrates whatever the record says, and the work order does not read it. So the
defect misreports progress; it does not change a build. But `vit status` is how a person or an
agent checks progress, and its exit code says nothing.

**One other reader of the keys.** `vit translate` offers each recorded function that the target
calls as a callable `_c` function in the prompt (`vit/cli.py:1333`), whatever its status: today a
NON_DISCRIMINATING record already counts, and so does `verified` that was never integrated, though
only integration puts the `_c` entry point into the build. `vit verify` also adds the keys to its
callee search, but it adds every procedure in the source as well (`vit/kernel_verify.py:1179`), so
the record makes no difference there.

**Real records.** The two campaign workspaces, both on VIT from before the red test was recorded,
hold 32 (AeroDyn) and 49 (ROSCO) entries, all `integrated`, none with `red_test`. Any change has to
read such entries correctly.

## Proposal

### 1. Two facts, two fields

- **`status` is the stage**: `translated`, `verified` or `integrated`, still never lowered. A pass
  moves it to `verified`; integration moves it to `integrated`. `non_discriminating` stops being a
  status.
- **`last_verify` is the latest verification**, replaced on every run, whatever the result:

  ```yaml
  translations:
    drag:
      status: integrated
      cpp_file: drag.cpp
      last_verify:
        result: passed          # passed | failed | non_discriminating | setup_failed
        at: 2026-11-11T19:04:00Z
        cpp_sha256: 9f2c…       # the translation that was verified
        cases_passed: 20
        cases_failed: 0
        cases_total: 20
        cases_skipped: 0
        red_test: demonstrated  # demonstrated | not_discriminating | not_constructed
        red_test_detail: …
        kernel_coverage: …
        kernel_dir: kernel/drag
  ```

  `result` is `passed` only when the cases passed and the red test was demonstrated: the same rule
  `vit verify` uses for its exit code. `not_constructed` stays `non_discriminating`, as the CLI
  already prints it; `red_test` keeps the difference.

A first verification that does not pass creates an entry with status `translated`, so `vit status`
lists it. The verification fields now at the top of an entry (`cases_passed`, `red_test`, …) move
into `last_verify`; the integration fields (`cpp_file`, `verification: simulation`) stay where they
are.

### 2. `vit status` reports the latest result, and its exit code says so

```
  ✓ drag: integrated — verified 20/20 cases, red test demonstrated
  ✗ lift: integrated — last verify FAILED, 12/20 cases (2026-11-11)
  ✗ wake: verified — last verify NON_DISCRIMINATING: the kernel did not catch a changed translation
  ! span: verified — drag.cpp has changed since it was verified
  ? tip: integrated — no verification recorded
```

- A tick only for a latest result of `passed` whose `cpp_sha256` matches the file as it is now.
- `!` when the C++ has changed since the pass.
- `?` for an entry with no `last_verify` (see 5).
- Exit 1 when any function's latest result is not a pass, or its C++ has changed; exit 0 otherwise.
  A script or an agent can then rely on `vit status` as a check.

### 3. `vit run` is removed

`vit run` repeated `vit verify` and `vit integrate`, and had fallen behind both (above). The
proposal first had its verification use `vit verify`'s code; the author chose to remove the
command instead. It is removed outright rather than deprecated: the repository has not been
released, so no one has a script to move off it. `vit verify` then `vit integrate --apply` does
what it did. This also closes the open item "`vit run` verifies without a red test". Done (below).

### 4. `vit integrate` says what it is integrating

Before patching, `vit integrate` prints the latest result for the function, and warns when it is
not a pass of this `.cpp`: never verified, failed, NON_DISCRIMINATING, or changed since. It still
integrates; see decision B. The record notes the result integration followed
(`integrated_after: passed | failed | … | none`), so a later reader can tell.

### 5. Older entries

Entries written before this change are read, not rewritten:

- `status` `verified` or `integrated` with `cases_passed` at the top level becomes a `last_verify`
  of `passed` with those counts and no `cpp_sha256`. `vit status` shows it with "(recorded before
  the translation was fingerprinted)" and exits 0 for it.
- `integrated` with `verified: true`, or nothing, has no verification on record: `?`, and `vit
  status` exits 0 for it, since there is no evidence either way. The campaign workspaces' 81
  entries all fall here.
- `status: non_discriminating` becomes `translated` with a `last_verify` of `non_discriminating`.

The new form is written the next time VIT records something for the function.

### 6. `vit translate` offers only integrated callees

A callee goes into the prompt as callable only when its status is `integrated`, which is when its
`_c` entry point exists in the build. Without this, item 1's new `translated` entries would be
offered too. See decision D.

## Tests

Each must fail against the current code. Through `cmd_verify` with `setup_kernel_cpp`
and `run_kernel_verify` stubbed in `vit.cli` (as `test_extract_fails_when_it_captured_nothing`
stubs KGen), and through `record_translation` and `cmd_status`:

1. Verified 20/20, then NON_DISCRIMINATING: `✗`, `last_verify.result` is `non_discriminating`, and
   `vit status` exits 1. Fails now: `✓ drag: verified (12/20 cases)`, exit 0.
2. Verified, then failed cases: recorded as `failed` with its counts. Fails now: nothing recorded.
3. Integrated, then failed: status stays `integrated`, latest `failed`, `✗`.
4. Setup failure: recorded as `setup_failed`.
5. A first verification that fails: an entry with status `translated`.
6. A `.cpp` edited after a pass: `!`, exit 1.
7. `vit integrate` after a failed verify: the warning and `integrated_after: failed`.
8. Each kind of older entry in 5 shows as described.
9. `vit translate` does not offer a `translated` or `verified` callee, and does offer an
    `integrated` one.

Then the quickstart in the container: the whole cycle, then the translation broken on purpose and
`vit verify` run again. `vit status` must show `✗ … FAILED` and exit 1, before and after
integration.

## Documents

- `GETTING_STARTED.md` line 193: the new `vit status` line; say that it exits 1 when the latest
  evidence is not a pass.
- `VIT_REFERENCE.md`: the `translations` row (line 47) and the `vit status` row (line 72); the
  record's fields; the `vit integrate` warning.
- `README.md`: the `vit status` row.
- `vit run`'s rows in `README.md` and `VIT_REFERENCE.md`: removed (below).
- Dev note `202609302022`: its status line points here.

## Alternatives considered

- **Let the status go down.** A failing verification after integration would set the status to
  something below `integrated`, though the failing C++ is still in the build. The record would then
  misstate the build instead of the evidence.
- **Derive the result from telemetry.** The cycle log can be turned off (`VIT_TELEMETRY=0`), lives
  in the directory VIT ran in, and its `status_for` counts any passing run (it measures what a
  function cost to translate). It is not a record of the latest evidence. It is left as it is.
- **Keep every verification, not only the latest.** `vit status` needs only the latest, the cycle
  log already has the history, and `vit.yaml` would grow with every run.
- **Refuse to integrate without a current pass.** See decision B.
- **Keep `vit run`, verifying with `vit verify`'s code, or deprecate it first.** See decision C.

## Decisions

The author's, on 2026-11-11:

- **A. The design (1 and 2):** a stage that never goes down, plus the latest result, replaced on
  every run. Accepted.
- **B. `vit integrate` when the latest result is not a pass of this `.cpp`:** warn and record it.
  Refusing unless a flag is given was rejected: integrations verified by full simulation
  (`--reverse-copy` records `verification: simulation`) have no kernel pass and would need the flag
  every time, and a flag passed every time checks nothing.
- **C. `vit run`:** removed (3). Proposed instead: verifying with `vit verify`'s code.
- **D. `vit translate`:** offers only `integrated` callees (6).
- **E. Commits:** not yet decided.

## Done so far

- `vit run` removed: the subcommand, `cmd_run`, its dispatch, and its line in `vit/cli.py`'s
  docstring. With no translations recorded, `vit status` used to say "Use 'vit run …'"; it now
  names `vit verify` and `vit integrate --apply`. Its rows in `README.md` and `VIT_REFERENCE.md`
  are gone. Dev notes that mention it are left as written.
- `tests/test_cli.py::test_there_is_no_vit_run`: `run` is not in `vit --help`, and `vit run` exits
  with argparse's "invalid choice". It fails with `cmd_run` restored. The suite: 1131 passed, 1
  skipped.

## References

- Dev note `202609302022` (the defect, left open; its fix 5 made integration keep the
  verification record)
- `vit/config.py` `record_translation`, `_STATUS_RANK`; `vit/cli.py` `cmd_verify`,
  `_run_red_test`, `cmd_integrate`, `cmd_status`, `cmd_translate`; `cmd_run` (removed)
- `tests/test_config.py` `TestRecordTranslation`; `tests/test_cli.py`
  `test_status_reports_an_integrated_function_as_verified`
