# Getting Started with VIT on ROSCO

## What This Is

VIT (Verified Incremental Translator) is an agent-driven pipeline for verified Fortran→C++ translation in scientific codebases. It captures runtime state from real simulations, verifies that C++ translations produce bit-identical results, and integrates them into the codebase.

VIT is designed to be driven by a coding agent — Claude Code, OpenCode, Codex, Cursor, or any agent that can run shell commands and follow project instructions. The agent orchestrates VIT's CLI commands, handles interactive decision protocols, and mediates between VIT and you. You bring the Fortran/C++ domain expertise; the agent handles the tooling.

ROSCO is an NREL wind turbine controller written in Fortran. It is the test case for VIT. You will walk through the full translation workflow — extracting functions, writing (or generating) C++ translations, verifying them against captured runtime state, and integrating them into the codebase.

---

## Repos

Three repos, cloned into a shared workspace directory:

```
vit_translation/
  vit/           → git@github.com:NatLabRockies/vit.git       (the tool)
  ROSCO/         → git@github.com:kevinmenear/ROSCO.git        (the codebase — fork of NREL/ROSCO)
  KGen/          → git@github.com:kevinmenear/KGen.git             (fork of NCAR/KGen)
```

**What goes where:**
- VIT tool code, tests, dev notes, PROJECT_PLAN.md → **VIT repo**
- C++ translations, vit.yaml, kernel fixtures, TRANSLATION_TIPS.md → **ROSCO repo**

---

## Environment Setup

All VIT commands, ROSCO builds, and KGen operations run inside a Docker container. KGen's Fortran parser does not work on macOS — never run VIT commands directly on the Mac.

**Prerequisites:** Docker Desktop or [Colima](https://github.com/abiosoft/colima) on macOS. The container image is ARM (Apple Silicon). If you're on an Intel Mac, let us know.

1. **Clone the repos** into a single workspace directory:
   ```bash
   mkdir -p ~/vit_translation && cd ~/vit_translation
   git clone git@github.com:NatLabRockies/vit.git
   git clone git@github.com:kevinmenear/ROSCO.git
   git clone git@github.com:kevinmenear/KGen.git
   ```

2. **Load the shared Docker image:**
   ```bash
   docker load < vit-dev-image.tar.gz
   ```

3. **Create and start the container** with your workspace mounted as `/workspace`:
   ```bash
   docker run -d --name vit-dev \
       -v ~/vit_translation:/workspace \
       vit-dev-image
   ```
   Replace `~/vit_translation` with wherever you cloned the repos.

4. **Install VIT and ROSCO** inside the container:
   ```bash
   docker exec -it vit-dev bash -c "pip3 install -e /workspace/vit --break-system-packages"
   docker exec -it vit-dev bash -c "pip3 install -e /workspace/ROSCO --break-system-packages"
   ```
   This creates editable installs so the container uses your local clones.

5. **Verify everything works:**
   ```bash
   docker exec -it vit-dev bash -c "vit --help"
   docker exec -it vit-dev bash -c "python2.7 --version"
   docker exec -it vit-dev bash -c "gfortran --version | head -1"
   docker exec -it vit-dev bash -c "cd /workspace/ROSCO && vit verify saturate translations/Functions/saturate.cpp -f rosco/controller/src/Functions.f90 --kernel-dir kernel/saturate 2>&1 | tail -3"
   ```
   The first three commands confirm the toolchain is present. The last command runs a real verification — it should show `40/40 passed`.

6. **Start the container** if it's stopped:
   ```bash
   docker start vit-dev
   ```

Commands in the container look like:
```bash
docker exec -it vit-dev bash -c "cd /workspace/ROSCO && <command>"
```

---

## The Agent-Driven Workflow

VIT is not a standalone CLI tool you memorize commands for. It is designed to be driven by a coding agent that reads the project documentation and orchestrates the pipeline interactively with you.

### How it works

1. Your coding agent reads `CLAUDE.md` in the VIT repo root — this is the full operating manual (~50KB) covering the development environment, workflow steps, testing protocols, known limitations, and everything the agent needs to drive VIT correctly.

2. You tell the agent what you want to do ("translate the saturate function", "verify this C++ file", "prepare a clean ROSCO baseline").

3. The agent runs VIT commands inside the Docker container and interprets the results.

4. When VIT encounters a decision that requires human judgment, it signals the agent with structured exit codes:
   - **Exit code 10 (Confirmation needed):** VIT inferred a value from source evidence but isn't certain. The agent presents the assumption — what was inferred, from where, what value will be used. You confirm or provide an alternative. The agent re-runs with the confirmed value.
   - **Exit code 11 (Manual rewrite needed):** VIT can auto-rewrite most references to a field but found code it can't safely transform (e.g., complex arithmetic). The agent proposes a replacement, you approve or adjust, and the agent re-runs with the override.

5. These protocols keep you in the loop for every decision that requires judgment — VIT never silently guesses.

### Agent configuration

- **Claude Code:** CLAUDE.md is read automatically from the repo root. No extra setup needed.
- **Other agents (OpenCode, Codex, Cursor, etc.):** Load the contents of `vit/CLAUDE.md` into the agent's system context — project instructions, custom instructions, or whatever mechanism the agent provides for persistent context. This file is the source of truth for how to use VIT.

---

## Initialize Your Agent's Context

**This is the most important step.** Before doing any work, have your agent read the project documentation, then use it as a teacher to build your understanding.

Tell your agent:

> "Read these files to understand the VIT project: GETTING_STARTED.md, PROJECT_PLAN.md, guide_ROSCO.md, and the dev notes in vit/dev/. Then let's discuss — I want to understand the workflow before we start."

Then ask questions:

- "Walk me through the VIT pipeline — what does each step do?"
- "How does the Docker container work? Why can't I run VIT on Mac?"
- "What are exit codes 10 and 11? Give me an example of each."
- "Show me what functions have been translated so far."
- "What does a typical translation session look like end to end?"
- "What is KGen and why does VIT need it?"
- "How does the verification work — what does 40/40 IDENTICAL mean?"

The agent has full context from CLAUDE.md, the project plan, the dev notes, and the guide. It can explain any aspect of the project, show you the current state, and walk you through workflows. This is faster and more effective than reading 50KB of documentation yourself.

---

## Prepare a Clean ROSCO

The ROSCO fork you received includes bug fixes, type compatibility changes, `vit.yaml` configuration, and translation infrastructure. But to walk through the translation workflow from scratch, you need **clean Fortran source files** — no integration wrappers from previous translations.

Tell your agent:

> "Prepare ROSCO for a clean build — reset the source files to their pre-integration state and build a pure-Fortran baseline."

The agent knows how to do this (it's documented in CLAUDE.md). It will:

1. **Reset Fortran source files** to upstream, removing any C++ integration wrappers
2. **Preserve what must stay:** bug fixes (two uninitialized variables in Flp_Mode=2 research code), type compatibility changes (ROSCO_Types.f90 with BIND(C)), CMakeLists.txt settings (`-ffp-contract=off` for deterministic floating-point)
3. **Create C++ stubs** for files referenced in CMakeLists.txt
4. **Build clean Fortran** and run the simulation to capture a baseline output

The baseline output (`baseline_output.txt`) is your reference. After translating and integrating any function, you rebuild and diff against it. An empty diff means the C++ produces byte-identical results to the original Fortran.

---

## Your First Translation

Once the baseline is captured, pick a function and walk through the full pipeline. `saturate` is the simplest starting point — three scalar arguments, one line of logic (`MIN(MAX(val, minval), maxval)`).

Tell your agent:

> "Let's translate the saturate function. Walk me through the full pipeline."

The agent will drive five steps:

1. **Extract** (`vit extract`) — KGen instruments the Fortran code, rebuilds, runs the simulation, and captures the runtime inputs/outputs of `saturate` across 20 invocations. This produces a kernel directory with state files.

2. **Translate** (`vit translate`) — VIT generates a C++ scaffold with the correct function signature and a translation prompt containing the original Fortran source, struct definitions, and project-specific tips. You write the algorithm (or the agent generates it via LLM and you review).

3. **Verify** (`vit verify`) — VIT wires the C++ into the KGen kernel, compiles it, and runs verification against all captured state. You're looking for `40/40 passed` — every captured invocation produces bit-identical output.

4. **Integrate** (`vit integrate --apply`) — VIT patches the Fortran module (adds an interface block and wrapper), updates CMakeLists.txt (enables C++, adds the source file, links stdc++), and generates the `extern "C"` wrapper file.

5. **Rebuild and diff** — Rebuild ROSCO with the C++ integrated and compare simulation output against the baseline. Empty diff = success.

As the Fortran/C++ expert, your role is to evaluate the translations, understand what the code does, and make judgment calls when VIT or the agent asks. The agent handles the tooling orchestration — running commands, interpreting output, managing files.

---

## Progressing Through Functions

After `saturate`, the existing translations in `ROSCO/translations/` serve as references for what verified C++ looks like at different complexity levels. Work through functions in order of increasing complexity:

**Simple scalars:**
- `saturate`, `wrap_180`, `wrap_360` — pure functions, scalar args, no state

**Arrays and error handling:**
- `interp1d`, `sigma` — assumed-shape arrays, ErrorVariables struct pointer

**Struct pointers (filters):**
- `LPFilter`, `HPFilter`, `SecLPFilter` — FilterParameters struct pointer, instance-indexed state fields

**OPTIONAL arguments:**
- `NotchFilter`, `NotchFilterSlopes` — Fortran OPTIONAL args handled via sentinel values

**Struct read/write (controllers):**
- `PIController`, `PIIController`, `ResController` — piParams/resParams struct pointers with fields updated on each call

Each follows the same pipeline: extract → translate → verify → integrate → rebuild → diff. The complexity increases (struct pointers, instance-indexed state, OPTIONAL arguments, cross-module calls) but the workflow is identical. The agent adapts automatically — it generates the right interface layer, struct headers, and kernel patches for each case.

---

## Key Files

| File | Location | Purpose |
|------|----------|---------|
| `CLAUDE.md` | `vit/CLAUDE.md` | Agent operating manual — your agent reads this to know how to drive VIT |
| `PROJECT_PLAN.md` | `vit/PROJECT_PLAN.md` | Full translation roadmap — every function, its status, and the phase structure |
| `README.md` | `vit/README.md` | Pipeline overview, CLI reference, current status |
| `guide_ROSCO.md` | `vit/guide_ROSCO.md` | Detailed technical walkthrough of the saturate pipeline |
| `dev/` | `vit/dev/` | Development notes — project history, decisions, and lessons learned |
| `TRANSLATION_TIPS.md` | `ROSCO/TRANSLATION_TIPS.md` | Lessons from translation failures (automatically included in `vit translate` prompts) |
| `vit.yaml` | `ROSCO/vit.yaml` | Project configuration — KGen paths, kind aliases, compiler flags, translation status |
| `kernel/` | `ROSCO/kernel/<func>/` | Golden kernel fixtures — pre-captured state for fast verification without re-extraction |
| `translations/` | `ROSCO/translations/<module>/` | Reference C++ translations organized by Fortran source module |
