# VIT Reference

## Configuration (`vit.yaml`)

Each project has a `vit.yaml` at its root: `openfast/vit.yaml` (AeroDyn, active) and `ROSCO/vit.yaml` (ROSCO, historical).

Key fields:
- `kgen.path`, `build_cmd`, `run_cmd`, `clean_cmd`: KGen extraction pipeline commands
- `kgen.invocation`: which invocations to capture (`0:0:1-20` = process 0, thread 0, invocations 1-20)
- `fortran.kind_aliases`: maps project kinds to standard types (e.g., `ReKi: REAL(8)`)
- `fortran.flags`: compiler flags propagated to kernel Makefile (must include `-ffp-contract=off`)
- `fortran.preprocessor_defines`: passed as `-D` flags to KGen for `#ifdef` resolution
- `types`: derived type handling strategy (`view` for ALLOCATABLE types, default `bind_c`)
- `translations`: tracks verified/integrated functions with case counts and kernel paths
- `verification.tolerance`: max acceptable floating-point difference (`1.0e-14`)
- `shared_files`: lists shared headers/sources copied to all kernel directories during verification
- `build_system.cmake_file`: VIT auto-detects which `add_library` target contains the source file

AeroDyn-specific differences from ROSCO:
- `source_dir: modules` — broad, so KGen can find NWTC Library sources for USE resolution
- `fortran.flags: -cpp -fdefault-real-8 -fdefault-double-8 -ffp-contract=off` — matches OpenFAST project build
- `fortran.preprocessor_defines: [OPENFAST_DOUBLE_PRECISION, HAS_FORTRAN2008_FEATURES]`

## CLI Commands Reference

VIT ships 13 subcommands.

**Global flags** (before the subcommand name):
- `--config PATH` / `-c PATH` — use a non-default `vit.yaml` location
- `--verbose` / `-v` — verbose logging (stacktraces on error, extra progress detail)

**Primary commands:**

| Command | Purpose | Notable flags |
|---------|---------|---------------|
| `vit extract` | Instrument Fortran + capture KGen state | `-f FILE`, `-l LINE`, `--run-args`, `--dry-run`, `--assumed-size NAME=SIZE` |
| `vit verify` | Run kernel verification of C++ against captured state | `-f FILE`, `--kernel-dir DIR`, `--no-field-log` |
| `vit integrate` | Generate/apply integration patches | `-f FILE`, `--apply`, `--reverse-copy`, `--auto-allocate`, `--dry-run` |
| `vit translate` | Generate C++ scaffold + translation prompt | `-f FILE`, `--module NAME` |
| `vit sim-compare` | Compare two directories of `.npz` simulation arrays | `--tolerance FLOAT` |
| `vit analyze-types` | Scan/fix derived types for C interop | `--fix {character,logical,complex,bind_c}`, `--scan-allocatable`, `--apply`, `--type`, `--field`, `--length`, `--rewrite` |
| `vit test-validate` | Generate test harness + prompt for validation functions | `-f FILE`, `--module NAME`, `--run`, `--force` |

**Utility commands:**

| Command | Purpose |
|---------|---------|
| `vit init` | Initialize a `vit.yaml` template |
| `vit find` | Locate call sites for a function across the source tree |
| `vit parse` | Parse a Fortran function signature and print extracted types/args |
| `vit interface` | Generate Fortran↔C interface files (used internally by `integrate`) |
| `vit run` | Full pipeline wrapper: interface → verify → integrate |
| `vit status` | Show translation status from `vit.yaml` |

## Exit Code Protocols

### Exit Code 10 — Assumption Confirmation

When VIT encounters a value it has inferred from source evidence but cannot be certain is correct, it signals via **exit code 10** and a `[VIT:CONFIRM]` block on stdout.

**When to use which approach:**
- **Has evidence, inferred a value** → exit code 10 (confirmation needed)
- **No evidence, arbitrary fallback** → hard error (exit 1)
- **Technical/performance default** → safe to hardcode

**Claude's responsibilities:**
1. Parse the `[VIT:CONFIRM]` block(s)
2. Present the assumption to the user — what VIT inferred, from where, what value it will use
3. Wait for the user to confirm or provide an alternative value
4. Re-run VIT with the confirmed value as an explicit CLI argument

**Claude must NEVER auto-confirm an assumption.**

Example:
```
VIT outputs: [VIT:CONFIRM] block with assumed_value: 1024, inferred from ALLOCATE

Claude: VIT inferred a buffer size of 1024 from the ALLOCATE call.
        Does 1024 look correct?

User: 1024 is correct.

Claude runs: vit analyze-types --fix character ... --length 1024 --apply
```

### Exit Code 11 — Manual Rewrite Needed

When VIT can automatically rewrite most references but encounters code it cannot safely transform, it signals via **exit code 11** and a `[VIT:MANUAL]` block on stdout. **Nothing is written to disk.**

**Claude's responsibilities:**
1. Parse the `[VIT:MANUAL]` block(s)
2. Read the flagged source line(s) and understand the context
3. **Propose a rewrite** to the user
4. Wait for approval
5. Re-run VIT with `--rewrite` argument

**Claude must NEVER auto-approve a manual rewrite.**

### Exit Code Summary

| Code | Meaning | Nothing written | Claude action |
|------|---------|-----------------|---------------|
| 0 | Success | (changes applied) | Done |
| 1 | Error | Yes | Fix the error |
| 10 | Confirmation needed | Yes | Present inferred value, wait for confirmation, re-run with override |
| 11 | Manual rewrite needed | Yes | Propose rewrite, wait for approval, re-run with `--rewrite` |

## Known Limitations

### Derived types

Two strategies, configured per type in `vit.yaml`:

- **BIND(C) structs** (default): Types with only scalar/fixed-size fields are passed directly as C struct pointers. `vit analyze-types` scans types and offers automated fixes (`--fix character`, `--fix logical`, `--fix complex`, `--fix bind_c`).

- **View structs** (for ALLOCATABLE types): Types with ALLOCATABLE arrays use the pointer+size view pattern. A generated view struct exposes ALLOCATABLE fields as `type* field; int n_field;` pairs. A generated Fortran populator fills the view at runtime using `C_LOC(LBOUND(...))` + `SIZE()`. Configured in `vit.yaml` under `types: { TypeName: { strategy: view } }`.

- **ALLOCATABLE TYPE() array marshalling:** When an ALLOCATABLE field contains an array of derived types (e.g., `p%Table(:)`) and the inner type also has `strategy: view`, the populator creates a module-level SAVE array of child view structs. C++ can index arbitrary elements: `p->Table[i].field`.

- **LBOUND-based C_LOC:** The populator uses `C_LOC(src%field(LBOUND(...)))` instead of `C_LOC(src%field(1,1,1))` for arrays with non-default lower bounds.

- **Derived type registry is auto-discovered and recursive**: `build_derived_type_registry()` searches the source directory for TYPE definitions, then recursively discovers nested TYPE(X) fields.

- **USE statement matching is string-based**: Checks if the type name appears in the USE line text. Works for `USE Module, ONLY: TypeName` but would miss bare `USE Module`.

### Assumed-shape arrays

VIT generates a separate `n_<name>` size parameter for every assumed-shape array arg, even when arrays are semantically the same length.

### C++ → C++ call path

During the translation phase (mixed Fortran/C++ binary):
- **Fortran callers** → Fortran wrapper → `_c` function → C++ implementation
- **C++ callers** → `_c` function directly via shared header (no wrapper)
- **Shared header**: `vit integrate --apply` emits/updates a header with `extern "C"` declarations
- **`vit translate`**: Scaffolds include `_c` declarations for already-translated callees

### Kernel callee bridges

During kernel verification, if the target function calls another already-translated function, VIT auto-generates a BIND(C) bridge wrapping C→Fortran. VIT detects translated callees by scanning source and cross-referencing `vit.yaml`.

- **View-type stash:** Callee bridges that need view-type arguments use a module-level SAVE variable "stashed" by the kernel wrapper.
- **Callee PUBLIC visibility:** VIT auto-injects `PUBLIC` statements for callees hidden by `PRIVATE` default in KGen-reduced modules.
- **C++ callee auto-detection:** VIT scans C++ files for function definitions. Three skip rules: same-file, shared-file, helper callees.

### KGen extraction pitfalls

- **Call site must be reachable at runtime**: KGen produces zero state files if the call site is never executed.
- **Invocation ordering**: KGen counts invocations per enclosing subroutine. Earlier scenarios consume the capture range.
- **Project Fortran flags**: KGen kernel Makefiles don't inherit project flags. VIT's `fortran.flags` propagates them.
- **Assumed-size arrays**: VIT auto-detects and proposes sizes via confirm protocol (exit code 10). Pre-configure `kgen.assumed_size_arrays` in `vit.yaml` to skip confirmation. **The size must cover ALL indices the function writes to** — undersizing causes silent heap corruption.
- **PROCEDURE POINTER declarations**: Fixed in our KGen fork (commit `31d653c`).
- **Cross-module instrumentation**: KGen may instrument files outside the cmake target. VIT auto-detects and transplants `kw_*` subroutines. See EXTRACTION_INTERNALS.md Section 6 (F2).
- **Compile-error-driven kernel repair**: VIT's `_repair_kernel_build()` loop auto-fixes missing constants, undefined symbols, and linker errors.
- **Vectorized math library (libmvec)**: KGen-reduced modules change vectorization heuristics. VIT adds `-fvect-cost-model=unlimited` and `-lmvec -lm`. See dev note `202604260357`.
