# What Was Copied Carried a Hash. What Was Described Carried Prose. Both Defects Are in the Prose.

**Date:** 2026-08-08
**Status:** completed — read-only classification of the replication setup
**Scope:** provenance records

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## The question

Both silent, result-invalidating defects of the run were in `bf25e35`, the setup commit
(`202608080346`, `202608080404`). Both were in configuration that was **re-derived from an
understanding of the campaign rather than copied from it.** So: which parts of the harness were
copied, and which were re-implemented?

Answerable read-only from `analysis/replication/manifest.json` plus the campaign tree.

## The classification

| Artifact | How it arrived | Verifiable by | Defect |
|---|---|---|---|
| `TRANSLATION_TIPS.md` | copied | **sha256** — manifest `2e978a6bc454965a` matches campaign `bc62ca8` exactly | none |
| `Examples/vit_sim.py` | copied + one documented edit | **sha256** — manifest `ecb5fbdf1db0003e`, unchanged since setup | the edit *fixed* a real hazard |
| `vit.yaml` | copied + documented edits | prose (no hash of the result) | none found |
| `Controllers.f90`, `Filters.f90` | applied from a named campaign commit `dba7738` | **commit ref** | none |
| 27 `baseline_arrays/*.npz` | generated on the fork | **self-validating** — clean source reproduces them | none |
| `scripts/regress.sh` | **re-implemented** | prose only | **the gate compared nothing** |
| `CMakeLists.txt` FP block | **re-implemented** | prose only | **`CMAKE_CXX_FLAGS` omitted** |

**Two entries were re-implemented from a description. Both contained a defect. Every entry that was
copied, referenced by commit, or generated is clean.**

## The manifest documented both defects accurately

This is the part worth keeping, and it is not what I expected to find.

`toolchain.fp_contract`:

> "off, set on **Fortran and C** flags in `rosco/controller/CMakeLists.txt` before baselines were
> generated"

That is a precise, honest record of the configuration — and the configuration was wrong. The campaign
set `CMAKE_CXX_FLAGS` and nothing else.

`gate`:

> `"comparison": "numpy array_equal, bit-identical, not tolerance-based"`
> `"self_test": "A rerun against freshly generated baselines returns 27/27 bit-identical."`

The comparison method is described correctly — it is what the heredoc *would* have done had stdin
been attached. The self-test result was produced by the gate that compared nothing.

**Both entries are accurate descriptions of the wrong thing, and neither is falsifiable from inside.**

## The generalisation

The manifest recorded a **hash** for everything it copied and **prose** for everything it
re-implemented. That difference is the whole story:

- The hashes are still checkable today. I verified `TRANSLATION_TIPS.md` byte-for-byte against the
  campaign at `bc62ca8` an hour ago, months of project time after it was written.
- The prose was checkable only by running the thing it described, which is exactly what nobody did
  until function #38.

**A provenance record catches drift only where it records something a machine can compare.** Hash
what you copy and the record verifies itself; describe what you re-implement and the record is
self-consistent, honest, and inert. The manifest is a good artifact — it caught three real setup
problems and documented them — and it could not catch these two, because it was describing rather
than comparing.

The concrete version, for anyone standing up a replication or a fork of a verification harness:
**re-implemented configuration needs a behavioural test, not a description.** The one-line check would
have been a deliberately-broken run — change one baseline value, confirm the gate goes red — which is
the rule the replication arrived at independently after the fact:

> a green result must be able to go red — break a new harness on purpose once

That rule and this classification are the same finding from opposite ends. The manifest tells you
*which* artifacts need it: the ones whose provenance is a sentence rather than a hash.

The setup's two failures were not carelessness — the manifest shows unusual rigour, with
hashes, commit pins, a container image digest, compiler versions, and three setup hazards found and
recorded. The failures were in the two artifacts where rigour took the form of an accurate
description instead of a comparable value. **Provenance discipline that stops at prose does not
survive re-implementation**, and the boundary between the parts that survived and the parts that did
not is exactly the boundary between hashed and described.

## References

- Dev note `202608080346` — the gate that never compared
- Dev note `202608080404` — the CMake flag; the campaign's one line
- `ROSCO-replication/analysis/replication/manifest.json` — the record examined here
