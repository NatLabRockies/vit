# Development notes

Dated records of the decisions, investigations and corrections behind VIT and its two case studies,
ROSCO and AeroDyn: 340 notes, written from February to September 2026.

## Naming and citing

Each note is named `YYYYMMDDHHMM-descriptor.md`, for the time it was written. Elsewhere in the
repository a note is cited by that timestamp alone, as in "dev note `202604250803`"; `ls
dev/202604250803*` finds it.

Each note carries a standard header: a title, then `**Date:**`, `**Status:**` (usually completed,
in-progress or abandoned) and `**Scope:**`.

## Reading them

Notes are records, not documentation. Each describes the code, the paths and the state of the work
on the day it was written, and is not updated afterwards, with one exception: when a later finding
changes a note, its status line says so and points to where the correction is recorded. Read the
status line before relying on a note.

- **Commit IDs** can belong to VIT, the KGen fork, the ROSCO fork or the OpenFAST fork; the text says
  which.
- **`CLAUDE.md`** in a note means the operating manual of the workspace the note was written in, as
  it was then: ROSCO's, later AeroDyn's.
- **Paths** are as they were. These files have since moved or been removed:

| In the notes | Now |
|---|---|
| `CLAUDE.md` (the AeroDyn manual) | `docs/case-studies/aerodyn/AGENT_MANUAL.md` |
| `PROJECT_PLAN.md`, later `PROJECT_PLAN_ROSCO.md` | `docs/case-studies/rosco/PROJECT_PLAN.md` |
| `PROJECT_PLAN_AERODYN.md` | `docs/case-studies/aerodyn/PROJECT_PLAN.md` |
| `docs/NWTC_UTILITIES.md` | `docs/case-studies/aerodyn/NWTC_UTILITIES.md` |
| `analysis/aerodyn/` | `docs/case-studies/aerodyn/data/` |
| `scripts/query_coverage.py`, `scripts/parse_gcov.py` | `docs/case-studies/aerodyn/scripts/` |
| `scripts/join_telemetry.py` | `python3 -m vit.telemetry join` (`vit/telemetry.py`) |
| `GETTING_STARTED.md`, `CREATING_PROJECT_PLANS.md` | `docs/` |
| `guide_ROSCO.md` | Removed; `docs/GETTING_STARTED.md` walks through the cycle on `examples/quickstart` |
| `SCENARIO16_RATE_LIMITER_ARTIFACT.md` | Removed; its explanation was superseded by dev note `202607240344` |
| `docs/CONTAINER_SETUP.md`, `docker/Dockerfile.openfast*` | Removed; `docker/Dockerfile` is the one image, described in `docker/README.md` |
| `examples/rosco/` | Removed; `examples/quickstart/` |
