# Verification Methods Reference

Five verification approaches for AeroDyn translations. Each suits a different class of function.

## 1. KGen Kernel Verification (Physics Functions)

The default verification path. KGen captures function inputs/outputs during a real simulation; VIT replaces the Fortran with C++ in the kernel and compares bit-identically.

**When to use:** Functions with floating-point arithmetic — the bulk of AeroDyn.

**Workflow:**
```bash
# Extract (inside vit-dev-openfast container, from /workspace/openfast)
vit extract FuncName -f modules/aerodyn/src/File.f90 -l LINE

# Verify (20/20 IDENTICAL expected)
vit verify FuncName translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90 --kernel-dir kernel/FuncName

# Integrate
vit integrate FuncName translations/Module/funcname.cpp -f modules/aerodyn/src/File.f90 --apply [--reverse-copy]

# Rebuild + regression test (17/17 must pass)
cd build && cmake .. && cmake --build . --target aerodyn_driver -j$(nproc)
cd /workspace/openfast && ./scripts/verify_aerodyn_baselines.sh all
```

## 2. Test-Validate (Validation Functions)

Prompt-based dual-call testing for functions whose interesting behavior is error-branch coverage, not floating-point arithmetic.

**When to use:** Functions named `Validate*`, `Check*`, or functions that only call `SetErrStat` with no math. KGen kernel verification only exercises the happy path (valid inputs → ErrStat=0), leaving all error branches untested.

**How it works:** VIT extracts raw materials (Fortran source, C++ translation, struct definitions), generates a test harness skeleton and a Fortran BIND(C) bridge, and assembles a prompt. The LLM writes branch-coverage tests. Each test calls both C++ and Fortran and asserts they produce the same `ErrStat`.

**What VIT generates:**
- **Test skeleton** (`<name>_test.cpp`) — boilerplate: includes, TestResult struct, main runner, extern "C" bridge declaration. LLM fills in `make_valid_input()` and per-branch test functions.
- **Fortran bridge** (`<name>_bridge.f90`) — BIND(C) subroutine that receives derived types as `TYPE(C_PTR)`.
- **Patched source** — if the function is PRIVATE, a local copy with `PUBLIC :: FuncName` added.
- **Makefile** — compiles patched source + bridge + test, links against production `.a` libraries.

**Workflow:**
```bash
# 1. Generate scaffold + prompt
vit translate FuncName -f modules/aerodyn/src/Source.f90 --module ModuleName

# 2. Write C++ implementation

# 3. Generate test harness + prompt (does NOT overwrite existing test file)
vit test-validate FuncName translations/Module/funcname.cpp \
  -f modules/aerodyn/src/Source.f90 --module ModuleName

# 4. Write test implementations using the prompt

# 5. Build and run tests (inside container)
vit test-validate FuncName translations/Module/funcname.cpp \
  -f modules/aerodyn/src/Source.f90 --module ModuleName --run

# 6. Integrate + rebuild + baselines (same as KGen path)
vit integrate FuncName translations/Module/funcname.cpp \
  -f modules/aerodyn/src/Source.f90 --apply
cd build && cmake --build . --target aerodyn_driver -j$(nproc)
cd /workspace/openfast && ./scripts/verify_aerodyn_baselines.sh all
```

**File overwrite protection:** Re-running `vit test-validate` skips the test file if it contains real implementations (no `// --- TODO:` markers). Use `--force` to overwrite. Bridge, Makefile, and patched source are always regenerated.

**Test storage:** `translations/<Module>/<name>_test.cpp`, alongside the translation, bridge, and Makefile.

## 3. File-Output Verification (Debug/Write Functions)

Dual-call testing that compares file output instead of ErrStat.

**When to use:** Functions whose purpose is writing formatted output to files. Typically named `Wr*`, `Write*`, `Debug*`, `Output*`. Neither KGen nor test-validate works for these.

**How it works:** The test constructs mock data, calls both C++ and Fortran implementations (each writing to a separate file), then diffs the files.

**What to write (hand-crafted, no VIT command):**
- **Test harness** (`<name>_filetest.cpp`) — constructs mock data, calls C++ → `output_cpp.dat`, calls Fortran bridge → `output_f90.dat`, runs byte-level comparison.
- **Fortran bridge** (`<name>_filetest_bridge.f90`) — BIND(C) subroutine that receives mock data as C-compatible types, builds native Fortran types, calls the original Fortran function.
- **Makefile rules** — same pattern as test-validate. Add to existing `translations/<Module>/Makefile`.

**Key challenges:**
- **Format string matching:** Fortran `WRITE` format descriptors (`F20.6`, `I17`, `ES17.8`) must be matched exactly by C++ `fprintf`/`snprintf` format strings.
- **Timestamp lines:** Fortran `CurDate()`/`CurTime()` produce timestamps that differ between runs. Either skip these lines in the diff, or mock the timestamp.
- **NWTC I/O utilities:** Fortran uses `GetNewUnit`, `OpenFOutFile`, `WrFileNR`. C++ uses `fopen`/`fprintf` directly.

**Workflow:**
```bash
# 1. Write C++ translation (using vit translate scaffold)
# 2. Write Fortran bridge
# 3. Write C++ test harness
# 4. Add Makefile rules
# 5. Build and run (inside container):
cd /workspace/openfast/translations/AirfoilInfo && make test_<name>_filetest && ./test_<name>_filetest
# 6. Integrate + rebuild + baselines
```

**Test storage:** `translations/<Module>/`. Files named `<name>_filetest.cpp`, `<name>_filetest_bridge.f90`.

## 4. Custom Fortran Test Harness (Zero-Coverage Functions)

Dual-call testing using a hand-written Fortran program. For functions with ALLOCATABLE types that have zero gcov coverage — KGen can't extract (no state data), baselines pass trivially (function never called), and `vit test-validate`'s generated bridge can't handle view types with ALLOCATABLE fields (C_F_POINTER layout mismatch).

**When to use:** Functions that are never called in the 17 test cases (e.g., Jacobian-path-only functions, initialization functions called before timestepping). The function may be a thin wrapper around already-verified callees, but the wrapper's loop bounds and argument passing still need verification.

**How it works:** A Fortran program owns all memory (real Fortran derived types), creates deep copies via Registry routines, calls both original Fortran and C++ (via view populators + BIND(C) wrapper), and compares results field-by-field.

**Pattern (from DBEMT_InitStates_AllNodes):**
```fortran
! 1. Allocate real Fortran types with test data
CALL setup_types(NumNodes, NumBlades, DBEMT_Mod, u, p, x_f90, os_f90)

! 2. Deep copy for C++ path
CALL DBEMT_CopyContState(x_f90, x_cpp, MESH_NEWCOPY, ErrStat, ErrMsg)
CALL DBEMT_CopyOtherState(os_f90, os_cpp, MESH_NEWCOPY, ErrStat, ErrMsg)

! 3. Call original Fortran
CALL DBEMT_InitStates_AllNodes(u, p, x_f90, os_f90)

! 4. Call C++ via views
CALL vit_populate_*(x_cpp, x_view)  ! etc.
CALL func_c(C_LOC(u_view), C_LOC(p_view), C_LOC(x_view), C_LOC(os_view))
CALL vit_copy_scalars_to_*(x_view, x_cpp)  ! reverse-copy

! 5. Compare field-by-field
```

**Key challenges:**
- **ALLOCATABLE fields:** Can't use C_F_POINTER to cast view structs to Fortran types — must create real Fortran types and use Registry Copy routines for deep copies.
- **Fortran LOGICAL comparison:** Use `.NEQV.` not `/=`. Assignment uses `.TRUE.`/`.FALSE.` not integer literals.
- **Registry routine names:** Shortened (e.g., `DBEMT_CopyContState` not `DBEMT_CopyContinuousStateType`).
- **The `_c` wrapper:** The C++ translation needs an `extern "C"` wrapper for Fortran to call it. Add this to the translation file.

**Test storage:** `translations/<Module>/test_<name>.f90` + `translations/<Module>/Makefile.test_<name>`.

## 5. Simulation Baseline Verification

17 AeroDyn regression test cases with committed baselines (`openfast/baselines/aerodyn/`). Run after integrating ANY function.

```bash
# Bit-identical gate (must pass 17/17)
bash scripts/verify_aerodyn_baselines.sh all

# NREL correctness gate (1e-2 tolerance)
bash scripts/run_nrel_regression.sh

# Informational drift reporter
bash scripts/check_aerodyn_nrel_drift.sh
```

New baselines must be generated from clean Fortran (not from the integrated C++ build).

## FMA Contraction and Bit-Identical Verification

`-ffp-contract=off` disables FMA contraction during the translation/verification phase, ensuring gfortran and g++ produce bit-identical results for equivalent expressions. Without this, compilers schedule FMA differently, causing 1-ULP differences that accumulate in closed-loop feedback.

**This flag is for verification, not permanent.** Once all of AeroDyn is pure C++, FMA can be re-enabled.

**If a translation shows `IN_TOL` (instead of `IDENTICAL`) with `-ffp-contract=off`**, it likely indicates a real translation bug.

**Debugging persistent kernel failures (same cases fail regardless of code changes):** If the same N cases fail with the same diffs whether using C++ or pure Fortran (replace the VIT wrapper with the `.kgen` original), the issue is NOT in the translation — it's in the kernel compilation environment. The most common cause: **vectorized vs scalar math library** — production links `libmvec` (SIMD cos/sin) while the kernel may not. Check with `nm production_binary | grep _ZGV` vs `nm kernel.exe | grep _ZGV`. VIT's `-fvect-cost-model=unlimited` + `-lmvec -lm` flags address this. See dev note `202604260357`.

## Kernel Vectorization Flags

VIT adds `-fvect-cost-model=unlimited` to kernel Fortran compilation and `-lmvec -lm` to the linker. KGen-reduced modules have fewer functions than the full production build, which changes gfortran's vectorization cost heuristics — loops vectorized in production (using libmvec SIMD) may not be vectorized in the kernel (falling back to scalar libm). The flags force consistent vectorization.
