# Fix VIT Integration for CHARACTER(variable) Function Arguments

**Date:** 2026-04-02
**Status:** completed
**Scope:** VIT type mapper, interface generator, and Stage C integration — enable `vit integrate` for functions with parameterized CHARACTER arguments

## Background

Stage C translated WriteRestartFile, ReadRestartFile, and Debug (ROSCO_IO.f90). All three have `CHARACTER(size_avcOUTNAME)` arguments where the length comes from another parameter. This type was unsupported by VIT, so Stage C initially used manual integration in `integrate_all.sh`.

After fixing the type mapper to accept `CHARACTER(variable)` (committed `ef0e363`), we attempted to switch WriteRestartFile and Debug from manual integration to standard `vit integrate --apply`. This exposed that the type mapper fix only unblocked the **parser** — the **code generator** (`interface_gen.py`) had never handled CHARACTER function arguments before, and four separate issues surfaced.

## The Four Problems

### 1. Duplicate `extern "C"` wrappers

VIT's `_generate_standalone_cpp()` copies the user's .cpp file and appends its own `extern "C" { writerestartfile_c(...) }` wrapper. Our `restart.cpp` already defined `writerestartfile_c` → redefinition error.

**Fix:** Split `restart.cpp` into separate per-function files (`writerestartfile.cpp`, `readrestartfile.cpp`) with a shared header (`restart_fields.h` for the `checkpoint_fields` template). Remove `extern "C"` blocks from translations — VIT generates them. ReadRestartFile keeps its own `extern "C"` because it's manually integrated (callee dispatch).

### 2. Signature mismatch — unused arguments

VIT's `_generate_standalone_cpp()` builds the `extern "C"` wrapper using `build_c_params(sig)`, which includes ALL Fortran arguments. It then calls the user's function with the same parameter list. Our `WriteRestartFile` omitted `CntrPar` (unused) → compilation error.

**Fix:** Add unused args to function signatures: `controlparameters_view_t* /*CntrPar*/`.

### 3. Fortran declaration ordering — CHARACTER before its length param

`_generate_wrapper_subroutine` declares arguments in `sig.args` order. In ROSCO_IO.f90, `RootName` (CHARACTER) is declared before `size_avcOUTNAME` (INTEGER). The wrapper emitted `CHARACTER(size_avcOUTNAME) :: RootName` before `INTEGER :: size_avcOUTNAME` → undefined variable.

**Fix:** Reorder argument declarations in both `_generate_wrapper_subroutine` and `_generate_wrapper_function`: emit non-CHARACTER args first, then CHARACTER args. This ensures INTEGER length parameters are declared before CHARACTER args that reference them.

### 4. Declarations after executable statements

The CHARACTER conversion needs:
- Declarations: `CHARACTER(KIND=C_CHAR) :: RootName_c(size_avcOUTNAME)` + `INTEGER :: vit_i_RootName`
- Executables: `DO ... END DO` copy loop

Initially implemented as a single `_generate_character_preamble` that emitted both together, placed after `_generate_view_preamble`. But the view preamble's `CALL vit_populate_*` statements are executable — so the CHARACTER declarations appeared after executable code, which is illegal Fortran.

**Fix:** Split into two functions:
- `_generate_character_declarations()` — emitted in the declarations section (right after argument declarations, before any preambles)
- `_generate_character_copy_loops()` — emitted in the executable section (after view preamble, before the C CALL)

Also added `_get_char_len_expr()` helper to resolve the length parameter name with original case from the signature.

## What Went Wrong in the Process

The type mapper fix was clean and self-contained. But "switch to `vit integrate`" was treated as a simple follow-on when it was actually a new code path through VIT's code generator. CHARACTER function arguments had never been integrated before — all 47 prior functions use derived types, scalars, and arrays.

The first attempt fixed problems one at a time, each build revealing the next error. This wasted time and created messy state (kernel fixture contamination from stray `verify_all.sh` runs, half-applied changes). After three iterations, a revert and proper plan was needed.

**Key lesson:** When opening a new code path, trace it end-to-end before writing code. A dry run (`vit integrate WriteRestartFile --apply` on clean source, then inspect the generated wrapper without building) would have surfaced all four problems in 30 seconds.

## Files Changed

### VIT repo
| File | Change |
|------|--------|
| `vit/type_map.py` | Added `character_len_param` field, handle `CHARACTER(variable)` (committed earlier in `ef0e363`) |
| `vit/interface_gen.py` | Reorder arg declarations (3a), split CHARACTER preamble into declarations + copy loops (3b), pass `{name}_c` in call args (3c) |
| `tests/test_analyze_types.py` | 4 new parameterized CHARACTER test cases (committed earlier in `ef0e363`) |

### ROSCO repo
| File | Change |
|------|--------|
| `translations/IO/restart_fields.h` | New — shared checkpoint helpers (trim, write_field, read_field, checkpoint_fields template) |
| `translations/IO/writerestartfile.cpp` | New — WriteRestartFile only, VIT-compatible signature, no extern "C" |
| `translations/IO/readrestartfile.cpp` | New — ReadRestartFile only, VIT-compatible signature, keeps extern "C" (manual integration) |
| `translations/IO/debug_logger.cpp` | Removed extern "C", changed to non-static `void Debug(...)` with VIT-compatible signature |
| `translations/IO/restart.cpp` | Deleted (replaced by split files) |
| `rosco/controller/src/restart.cpp` | Deleted |
| `rosco/controller/src/debug_logger.cpp` | Deleted |
| `rosco/controller/CMakeLists.txt` | Removed manual Stage C entries (VIT adds writerestartfile.cpp + debug.cpp automatically) |
| `scripts/integrate_all.sh` | WriteRestartFile + Debug use `integrate()`, ReadRestartFile stays manual |
| `scripts/debug_test.sh` | Pass through LD_PRELOAD for FITPACK stack scrubber |
| `vit.yaml` | Updated cpp_file paths for split |

## Verification

- **598 unit tests pass**
- **50/50 integration** (WriteRestartFile + Debug via `vit integrate`, ReadRestartFile manual)
- **692,000 values IDENTICAL** across 10 scenarios
- **Debug test: 15,999 data rows IDENTICAL**
- Scenario 3 requires `LD_PRELOAD=libscrub.so` (pre-existing FITPACK determinism issue, not related to this change)
