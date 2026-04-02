# Stage B Retrospective: Translations, Tooling, and Lessons

**Date:** 2026-04-02
**Status:** completed
**Scope:** Reflection on the full Stage B process — what we built, what we learned, and how it shapes the remaining phases

## What We Did

We started this session with Stage B — translating three config/validation functions that KGen can't handle. That was the immediate task. But the session became something more significant than the translations themselves.

**The translations are done and verified.** 1,500 lines of Fortran replaced with 2,042 lines of C++, 692,000 values identical across 10 scenarios. CheckInputs, ReadCpFile, ReadControlParameterFileSub — all working. That's 44 of ROSCO's functions now in C++.

**But the real work was what happened after.** We pushed on the fact that we did it all manually — no VIT tooling, just handwritten wrappers and raw Python one-liners. That led to a multi-hour investigation into what VIT should be able to do for non-KGen functions, which produced six new capabilities. The most important insight wasn't any individual feature — it was discovering that `vit integrate` already worked for Stage B functions and we just didn't try it. We assumed KGen was a prerequisite. It wasn't.

**The auto-allocate exercise was instructive in a different way.** We built a 526-line module with thorough safety analysis, and it saves writing maybe 3 lines manually. That's a bad ratio. But the process of investigating it — cataloging all 18 ALLOCATE statements, classifying them into safety categories, identifying the ParseAry overload issue — that understanding is worth having. The lesson is that not every investigation should result in code. Sometimes the investigation IS the deliverable.

## The Bigger Picture

**Zooming out to the project:** ROSCO has roughly 60-70 functions total. We've translated 44. The remaining functions fall into Stages C-E: restart I/O, debug logging, platform modules, and the SetParameters orchestrator. These are all non-KGen functions — they'll use the tooling we just built (sim-compare, reverse-copy, auto-allocate where applicable). The pattern is established. The next stages should go faster because we've turned the Stage B manual process into VIT capabilities.

**The deeper question this session surfaced:** There's a tension between "build the tool" and "do the work." Building VIT capabilities is an investment — it takes longer upfront but makes future work faster and more reliable. Doing the work manually is faster for a one-off but doesn't compound. Stage B was 3 functions, which felt like "just do it manually." But Stages C-E have more functions, and every Stage B shortcut we took would have been repeated. The tool investment pays off across the remaining phases.

## What We'd Do Differently

Use `vit translate` for scaffolding from the start. Try `vit integrate` before assuming it won't work. Build the sim-compare command BEFORE doing the first Stage B function, not after. The order should be: tool first, then work — not work first, then realize the tool was needed.
