# User Onboarding: GETTING_STARTED.md and Docker Image Export
**Date:** 2026-03-23
**Status:** completed
**Scope:** Documentation, onboarding

## Context

VIT needed a clear onboarding path for new users. The existing documentation (CLAUDE.md, README.md, guide_ROSCO.md) is comprehensive but fragmented — there was no single entry point that walked someone through environment setup, agent configuration, and their first translation. Additionally, the Docker container environment needed to be exportable as a shareable image.

## What Was Created

### GETTING_STARTED.md

New onboarding document in the VIT repo (`vit/GETTING_STARTED.md`). Covers:

1. **What VIT is** — agent-driven pipeline, ROSCO as test case
2. **Repos** — three-repo workspace layout (VIT, ROSCO fork, KGen fork), what goes where
3. **Environment setup** — full step-by-step: clone repos, load shared Docker image, create container, install editable packages, verify with a real 40/40 test
4. **Agent-driven workflow** — how VIT interacts with coding agents, exit codes 10/11, agent configuration for Claude Code vs other agents
5. **Agent-as-teacher pattern** — have the agent read PROJECT_PLAN.md, guide_ROSCO.md, GETTING_STARTED.md, and dev notes, then engage in Q&A to build understanding
6. **Clean ROSCO preparation** — starting from the fork with bug fixes/type changes, resetting to pre-integration state, building a pure-Fortran baseline
7. **First translation walkthrough** — saturate as starting point, the five pipeline steps
8. **Progression path** — functions ordered by complexity (scalars → arrays → struct pointers → controllers)
9. **Troubleshooting** — ARM/Intel mismatch, broken editable installs, destroyed container
10. **Building from scratch** — full container build from ubuntu:24.04 (system packages, Python 2.7 from source, pip installs)
11. **Key files reference** — table of every important file and its purpose

### Docker Image Export

The running `vit-dev` container was committed to an image and exported:
- `docker commit vit-dev vit-dev-image`
- `docker save vit-dev-image | gzip > vit-dev-image.tar.gz` (689MB compressed)

The image contains Ubuntu 24.04 (ARM), gfortran, g++, cmake, Python 3.12, Python 2.7 (built from source), and all pip dependencies. The editable installs for VIT and ROSCO point to `/workspace/` — new users re-run `pip install -e` after mounting their own workspace.

### Comprehensive Documentation Update

In the same session, README.md and guide_ROSCO.md received a comprehensive update:
- Reframed VIT as agent-driven (not standalone CLI)
- Pipeline diagram updated: EXTRACT → TRANSLATE → VERIFY → INTEGRATE
- `vit translate` shown as primary workflow (replaces "YOU PROVIDE THE TRANSLATION")
- Agent-interactive protocols (exit codes 10/11) documented as key design decision
- CLI reference reordered to pipeline flow, all flags current
- `analyze-types` description broadened to show all 4 fix types
- Stale references fixed (CodeScribe removed, `04_simple_sim.py` → `vit_sim.py`)

## Design Decisions

### Agent-as-teacher over documentation-first

Rather than expecting users to read 50KB+ of docs, the onboarding directs them to load context into their agent and then ask questions interactively. This is faster, more adaptive, and exercises the same agent-driven workflow they'll use for actual translation work.

### Agent-agnostic instructions

CLAUDE.md is Claude Code-specific (read automatically from repo root). For other agents, GETTING_STARTED.md explains that the contents of CLAUDE.md should be loaded into whatever project/custom instructions mechanism the agent provides. The rest of the workflow is identical regardless of agent.

### Clean ROSCO as starting point

New users receive the ROSCO fork (with bug fixes, type changes, vit.yaml, translations/, kernel/) but are instructed to reset Fortran source to pre-integration state before translating. This lets them walk through the full workflow from extraction onward while still having reference translations and golden fixtures available.

### Docker image sharing vs Dockerfile

Chose to share a committed container image rather than a Dockerfile because:
- The environment has significant manual setup (Python 2.7 from source, specific pip package versions)
- A Dockerfile would need network access to install packages (blocked by corporate TLS inspection in some environments)
- The image is 689MB compressed — manageable for file sharing
- Build-from-scratch instructions exist in GETTING_STARTED.md as a fallback

## Repo Changes

- VIT repo moved to `git@github.com:NatLabRockies/vit.git` (from NREL GitHub Enterprise)
- KGen uses the fork (shebang already fixed), not upstream NCAR/KGen
