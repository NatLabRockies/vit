# Why VIT builds on KGen and not fortlab/ekea

**Date:** 2026-08-07
**Status:** completed
**Scope:** Verification substrate — dependency choice, provenance

## Why this note exists

Nothing about the fortlab evaluation was ever written down. A grep across both the
ROSCO and OpenFAST workspaces for `fortlab`, `ekea`, and `grnydawn` returns zero hits
outside KGen's own git history. The decision to build on KGen was therefore
reconstructible only from memory, and it surfaced as a question from a colleague. This
note fixes that before the memory degrades further.

## How it came up

KGen's main developer is now at ORNL, and the actively maintained successor of KGen is
hosted there.

KGen was built at NCAR for performance-kernel work (Kim, Dennis, Kumar, Simha, ICCS
2016). That attribution is historically correct and was not the issue. The open question
was the successor: people at DOE labs are the group most likely to know Youngsung Kim
personally and to ask why we forked a decade-old tool.

## The actual history, per the author

Two accounts were given, a few hours apart, and both are probably true:

1. **Sequencing.** fortlab was tried first and could not be gotten working. A collaborator
   already had KGen running, so the project pivoted back to KGen.
2. **Maturity.** KGen is more battle-tested than fortlab.

(1) is what happened. (2) is the reason that survives scrutiny — but see the Python 2.7
caveat below.

## What fortlab and ekea actually are (investigated 2026-08-07)

**`grnydawn/ekea`** — "E3SM Kernel Extractor and Analyser." Specialized to E3SM *by
design*: the CLI requires an `ocn` or `eam` subcommand (MPAS Ocean / E3SM Atmosphere)
and an E3SM case directory, so that a user need only name a case and a code region. That
specialization is the entire product. ROSCO is a Bladed-style `DISCON` shared library and
AeroDyn runs under a standalone OpenFAST driver; neither presents anything ekea's
interface accepts. **ekea could not have been used at all.**

**`grnydawn/fortlab`** — "Fortran Analysis Laboratory," self-described as "a Python
framework that makes it easy for users to *create* kernel-based tools for Fortran
applications." Built-in apps: `compileroption`, `timinggen`, `resolve`, `kernelgen`,
`vargen`. It is a framework, not a turnkey extractor — the turnkey layer is exactly what
it leaves to the caller, and that layer is what VIT already is (pragma placement,
dependency discovery, build wrappers, state-file hygiene, NaN-safe comparison). **Even
on a working fortlab, VIT's wrapper would have had to be written.**

Maintenance data (GitHub API, 2026-08-07):

| repo | last push | stars / forks |
|---|---|---|
| `grnydawn/fortlab` | 2025-03-28 | 0 / 0 |
| `grnydawn/ekea` | 2022-10-06 | 0 / 0 |
| `NCAR/KGen` | 2019-08-27 | 31 / 19 |

## Corrections to the comment

- **"Main developer now at ORNL"** — supported. Youngsung Kim co-authors ORNL's SAM++
  E3SM-MMF porting paper (Lyngaas, Norman, Kim, 2022).
- **"Successor exists"** — supported. fortlab (2025) and ekea (2022).
- **"Hosted by ORNL"** — *not supported.* Both live under Kim's personal GitHub account
  (`grnydawn`). No ORNL-org KGen successor was found.
- **"Actively maintained"** — *overstated.* fortlab is more recently touched than KGen
  (2025 vs 2019) but has 0 stars and 0 forks; it is one person's research code. KGen has
  31 forks and a peer-reviewed paper.

## The counter-argument, recorded honestly

**KGen is Python 2.** Its source still uses bare `print` statements (`kgen/kgutils.py`
lines 179--180, 228), and `vit-dev-image` compiles Python 2.7.18 from source purely to
run it (`docs/CONTAINER_SETUP.md`). Python 2 has been EOL since January 2020.

So the maturity comparison genuinely cuts both ways: KGen has the users, the forks and
the citation; fortlab runs on a live runtime. A claim that "we chose KGen because
it is more battle-tested" invites the reply "you chose the dead-runtime tool over the
maintained one," and the argument shifts from describing a workflow to defending a
ranking. That is why we make no comparative claim.

## Not confirmed

Whether fortlab's `vargen` performs true runtime state capture (instrument the
application, run it, write state files for replay) the way KGen does. The docs list
`vargen` and mention "data generation for kernel execution" but never describe the
mechanism, and the per-app documentation pages did not render on fetch. **We can say
fortlab would not have spared us the integration layer; we cannot say it was technically
incapable.** Do not claim the latter.

## If this is revisited

The reason to move would be the Python 2.7 dependency, not capability — it is the single
ugliest thing in the toolchain and the most likely to break. Before attempting a port:

1. Establish whether `vargen` does instrumented runtime state capture. If it does not,
   the port is a rewrite of KGen's core, not a substrate swap.
2. VIT's fork adds `BIND(C)` parsing, procedure-pointer support, and assumed-size array
   handling. Each would need an equivalent.
3. The two-run comparison design (dev note on `vit verify`) is VIT's, not KGen's, and
   carries over unchanged.
4. Budget the fixture cost: every existing kernel directory under `kernel/` was produced
   by the current fork.

Related: `202605010526` (pragma at call site vs definition), `202605010623` (KGen usage
errors are not KGen bugs).
