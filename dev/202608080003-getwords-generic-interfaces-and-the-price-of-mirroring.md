# GetWords: Generic Interfaces Hide Dead Code, and the Mirror Constraint Gets a Price

**Date:** 2026-08-08
**Status:** completed — function #28 integrated; all claims below verified read-only
**Scope:** extraction failure modes, Phase 2 analysis limits, call-site selection

## Placement

Same as the other replication notes: OpenFAST workspace, outside the replication's container mount
and outside the repository it commits to. **Do not merge into the ROSCO-workspace checkout until
the run has ended.**

## What the replication reported

#28 `GetWords` integrated. Kernel 21/21, differential harness 10/10, gate 27/27.
`ROSCO-replication d3c1928`, `vit 846fb1e`. Two claims: a new extraction-empty cause, and the
sixteenth tool fix (`CHARACTER(*)` array dummies had no VIT representation).

Everything checkable below was checked. **All of it holds.** What follows is what the replication
could not see.

## 1. The dead-code claim, verified exactly

`ROSCO_Helpers.f90:27-35` declares a generic interface carrying both families:

```fortran
INTERFACE ParseInput
    MODULE PROCEDURE ParseInput_Str      ! Un-based
    MODULE PROCEDURE ParseInput_Dbl      ! Un-based
    MODULE PROCEDURE ParseInput_Int      ! Un-based
    MODULE PROCEDURE ParseInput_Int_Opt  ! FileLines-based
    MODULE PROCEDURE ParseInput_Dbl_Opt
    MODULE PROCEDURE ParseInput_Str_Opt
END INTERFACE
```

Direct calls to any Un-based specific name, whole tree: **0**. Generic `CALL ParseInput(` /
`CALL ParseAry(` sites: **140**. Since `ReadControlParameterFileSub` passes `FileLines`, every one
of the 140 resolves to an `_Opt` variant, and the five Un-based specifics are unreachable:
`ParseInput_Int`, `ParseInput_Dbl`, `ParseInput_Str`, `ParseDbAry`, `ParseInAry`. The replication
said five; there are five.

## 2. A fifth way extraction comes back empty

`CLAUDE.md` lists four causes for "No state data captured": pragma at a definition rather than a
call site, wrong test case, source not clean, `kgen_utils.f90` never compiled in. This is a fifth
and it is structurally different from all four:

> **The pragma is correctly placed at a real call site inside a real subroutine, and the enclosing
> subroutine is never entered, because a generic interface routes every call to a sibling.**

Nothing about the pragma, the scenario, or the tree is wrong. The extraction is well-formed and the
answer is genuinely zero. The only signal distinguishing this from the other four is that the
*enclosing* procedure has no coverage — which is exactly what a coverage query would show, if the
query were run on the enclosing procedure rather than on the target.

Re-extracting at the sibling (`:233`, inside `ParseInput_Int_Opt`) returned 21 cases immediately.

**Belongs in `CLAUDE.md`'s extraction-pitfalls list after the run ends**, phrased as a diagnostic:
if extraction is empty and the pragma is correct, check coverage on the *enclosing* procedure
before suspecting the tooling.

## 3. Fourth incomplete Phase 2 flag: the call graph does not resolve generic interfaces

The Phase 2 inventory lists both the genuinely dead Un-based variants and the live `_Opt` ones
under "Uncalled" — 12 procedures, with no way to tell "dead" from "reached generically." A
call-graph pass that matches on specific procedure names cannot follow a generic dispatch, so every
member of every `INTERFACE` block looks uncalled.

That is the **fourth** structural property the Phase 2 analysis could not see:

| Flag | Error |
|---|---|
| `gate_can_verify` | lists only unreachable functions; misses executes-but-uncompared (`202608072329`) |
| alloc trait | reported 5, actually 14 (`202608072320`) |
| `file_io` | marks 24; only 14 are true unit I/O (`202608072334`) |
| "Uncalled" list | 12 procedures, conflating dead with generically-reached (**this note**) |

The pattern is now consistent enough to state as a result rather than an anecdote: **static call-graph
analysis ordered the first 27 functions correctly and was wrong in four separate ways about
properties the call graph does not carry.** Generic interfaces are the most general of the four —
they are a language feature specifically designed to break the name-to-callee mapping that a call
graph is built on, and any Fortran codebase using them has this hole.

## 4. What the campaign did with this entire layer: nothing

This is the part the replication is forbidden to see.

**No `getwords.cpp` in the campaign. No `parse*.cpp`. No `reademptyline.cpp`.** 55 `.cpp` files;
none of them is any member of this layer. `GetWords` is still Fortran in the campaign tree at
`ROSCO_Helpers.f90:1150`.

But `readcpfile.cpp` and `readcontrolparameterfilesub.cpp` both exist. The campaign translated the
*callers* and deleted the callees, by changing the parsing strategy:

```cpp
static void skipLines(std::ifstream& f, int n) {          // this is ReadEmptyLine
    std::string line;
    for (int i = 0; i < n; i++) std::getline(f, line);
}

static bool readRow(std::ifstream& f, double* dest, int n) {
    std::string line;
    if (!std::getline(f, line)) return false;
    std::istringstream iss(line);                          // this is GetWords
    for (int i = 0; i < n; i++) if (!(iss >> dest[i])) return false;
    return true;
}
```

`ReadCpFile` is **Track B** — hand-written idiomatic C++, not VIT-translated. Confirmed: the
campaign has 43 kernel fixtures and `ReadCpFile` is not among them. It was never kernel-verified;
its only evidence is the 27-scenario gate.

Once the file handle is a `std::ifstream`, `GetWords`, `ReadEmptyLine`, and all ten `Parse*`
variants stop being functions that need translating and become expressions of the standard library.
The campaign did not route *around* a blocker here. It made the blocker not exist.

### This independently validates the `ReadEmptyLine` disposition

`202608072334` argued from the Fortran alone that `ReadEmptyLine` must be translated together with
`ReadCpFile`, once the handle is C++-owned. The campaign's `skipLines()` is precisely that function,
written precisely that way. The replication reasoned to the same answer without being able to see
it. That is the strongest evidence yet that the resource-ownership ordering criterion is real and
derivable, not a post-hoc rationalisation of a stuck cycle.

## 5. The mirror-translation constraint now has a price

`GetWords` cost, measured:

| | GetWords | Clean-cycle median (n=16) | Ratio |
|---|---:|---:|---:|
| Wall clock | 970 s | 400 s | 2.4× |

Plus the run's sixteenth tool fix: `CHARACTER(*)` array dummies had no VIT representation at all.
`Words(NumWords)` carries an element length and an element count independently, and three
generators assumed the C signature dropped `len_Words`, so the inter-element stride was never
passed. The Fortran wrapper dropped the `DIMENSION` and declared a scalar; the staging buffer
indexed `x(i:i)`, which is not valid for an array of strings; the production bridge repeated the
same error. Separately, `kernel_verify`'s `[VIT_ARRAY]` diagnostic referenced a symbol KGen only
declares for numeric types, producing a link failure. Four defects, one root cause.

**All of that — 970 s and four generator defects — was spent mirroring a function the campaign
deleted with one line of `std::istringstream`.**

The honest reading is not that the effort was wasted. It is that the effort **is the price of the
constraint**, and it is now measured for the first time. `AddToList` (`202608072320`) showed the
constraint exists; it was dispositioned out before any cost was incurred. `GetWords` is the first
case where the replication paid the bill in full and shipped the mirror. The difference between the
two is not principled — `std::vector` dissolves `AddToList` exactly as `std::istringstream`
dissolves `GetWords` — it is that the replication had already committed to translating `GetWords`
before the shape of the work became clear.

What was bought is real: `CHARACTER(*)` array dummies are a general Fortran pattern, and VIT now
handles them. The campaign never needed that capability because it exercised a disposition the
replication's procedure does not authorise. **The capability and the constraint are the same fact
seen from two sides**, which is the sharpest version of the Track A / Track B finding
(`202608072149`) the run has produced.

## 6. Call-site selection selects against input diversity

The kernel again did not discriminate: 21/21 cases with `NumWords == 2` and a `DISCON.IN` line as
input. One shape, one output variable. Third instance after `202608072233` and `202608072250`.

Here the cause is visible in the source and is not bad luck. The eleven `GetWords` call sites split
cleanly:

| Sites | Argument | NumWords |
|---|---|---|
| 176, 246, 328, 409, 484, 548 — the scalar `ParseInput_*` variants | `Words, 2` | **always 2** |
| 649, 772, 919, 1058 — the `Parse*Ary*` variants | `Words_Ary, AryLen + 1` | **varies with array length** |
| 1211 — `FindLine` | `Words, WordInd` | varies |

Extraction at any scalar-parse site can only ever produce `NumWords == 2`. **The shape of the
fixture was determined by the call site before a single case was captured.** Five of the eleven
sites would have varied it.

And the selection heuristic points the wrong way. The runbook picks the call site with the most
hits. Parsing a `DISCON.IN` is overwhelmingly scalar reads, so hit count is maximised at exactly the
sites that hold the shape constant. **Choosing a call site by coverage count systematically selects
against input diversity**, because the hottest call site is usually the most stereotyped one.

This is the first of the three non-discriminating-kernel cases with a concrete remedy: when a
function has call sites that differ in the *shape* of what they pass, extract from more than one, or
extract from the site with the most varied arguments rather than the most hits. That is a change to
the runbook, not to KGen — the tool captured faithfully what the chosen site does.

The replication caught the gap itself and built a 10-case differential harness covering the
seven-character delimiter set, delimiter runs, truncation, under- and over-supply of words,
`len_words == 1`, and the buffer-end word Fortran silently drops (mirrored rather than fixed). That
is the second time the agent has recognised unaided that a green kernel did not establish what it
appeared to. Worth recording as behaviour, not just outcome.

## 7. Accounting

`GetWords` is the second-dearest function of the run. `GetRoot` remains dearest.

The replication also corrected its own STATUS count (28 attempted against 27 recorded outcomes) and
recorded the reason in `DECISIONS.md`. Third self-caught dataset error.

## References

- Dev note `202608072320` — `out_of_scope` vs `blocked`; `AddToList`, the same situation unpaid
- Dev note `202608072334` — resource-ownership ordering; validated here by `skipLines()`
- Dev note `202608072250` — non-discriminating input; second instance
- Dev note `202608072233` — a kernel that compared nothing; first instance
- Dev note `202608072149` — Track A/Track B
- Dev note `202608072355` — cost units; the cost ranking here uses them
