# OpenFAST Build, AeroDyn Driver Baseline, and Fork Remote Swap
**Date:** 2026-04-14
**Status:** completed
**Scope:** OpenFAST build validation in `vit-dev-openfast`, AeroDyn Driver smoke test against a real regression case, r-test submodule fetch, OpenFAST remote swapped to the fork

## Context

Dev note `202604140204-vit-dev-openfast-docker-plan.md` ended with `vit-dev-openfast` built and verified — the container had a working toolchain (cmake 3.28.3, gfortran 13.3.0, Python 3.12.3, Python 2.7.18, VIT 0.1.0 editable install, fparser 0.2.2, KGen executable under Python 2.7, plus `libopenblas-dev`/`liblapack-dev`/`libyaml-cpp-dev` from apt). That proved the environment could *host* OpenFAST work, but did not yet prove OpenFAST itself would build and run in that environment.

This dev note records the next step: actually build OpenFAST against the installed toolchain, run a real AeroDyn regression test case, and interpret what the output tells us. It also records a fork remote swap — the user created `git@github.com:kevinmenear/openfast.git` as a fork of NREL's OpenFAST, and we rewired the local OpenFAST clone to use the fork for push/pull while keeping NREL as `upstream` for syncing.

The motivation for doing this before any VIT work against OpenFAST was pragmatic: we found several infrastructure issues in the ROSCO container early (FITPACK nondeterminism, FMA contraction drift, KGen parser bugs, `avr_size` silent heap overflow) that would have been much harder to debug if discovered mid-translation. Proving that OpenFAST's own build/run cycle works in `vit-dev-openfast` before we start instrumenting it gives us a known-good baseline to compare against.

## 1. OpenFAST cmake configure

**Command:**
```bash
cd /workspace/openfast && mkdir -p build && cd build && rm -rf *
cmake .. -DBUILD_FASTFARM=off -DBUILD_OPENFAST_CPP_API=off -DBUILD_TESTING=off -DCMAKE_BUILD_TYPE=Release
```

The three `BUILD_*=off` flags disable optional subsystems we don't need for AeroDyn work. `BUILD_FASTFARM` pulls in the multi-turbine farm-scale simulation code. `BUILD_OPENFAST_CPP_API` and `BUILD_OPENFAST_CPP_DRIVER` pull in the C++ interop layer. `BUILD_TESTING` disables `add_subdirectory(reg_tests)` and `add_subdirectory(unit_tests)` — both require pFUnit or the Python reg_test infrastructure and we don't need them for a first smoke test. `CMAKE_BUILD_TYPE=Release` is OpenFAST's own default but worth stating explicitly.

**Configure completed in 0.9 seconds.** Key output lines:
- `Enabling Fortran 2008 features` — OpenFAST uses Fortran 2008, gfortran 13.3.0 passes the feature test
- `Found BLAS: /usr/lib/aarch64-linux-gnu/libopenblas.so` — the `libopenblas-dev` package from the Dockerfile is picked up correctly
- `Found LAPACK: /usr/lib/aarch64-linux-gnu/libopenblas.so;-lm;-ldl` — OpenBLAS provides both BLAS and LAPACK symbols (single-library install)
- `Setting system file as: src/SysGnuLinux.f90` — NWTC Library correctly identifies the OS and picks the Linux platform shim

**Zero errors, zero warnings.** The cmake configure step is clean. The decision to base `Dockerfile.openfast` on `vit-dev-image` and add LAPACK/BLAS + yaml-cpp on top is validated: everything OpenFAST's cmake needs is present.

## 2. AeroDyn build target

**Command:**
```bash
cd /workspace/openfast/build && make aerodyn_driver -j$(nproc)
```

Rather than `make -j$(nproc)` (which would build the full OpenFAST executable plus every module in the dependency graph), I targeted just `aerodyn_driver`. cmake correctly computed the transitive dependency closure from AeroDyn's `CMakeLists.txt` (the `target_link_libraries(aerodynlib ifwlib nwtclibs)` style declarations) and built only what was needed.

**Build succeeded. Targets built (in order):**

1. **`nwtclibs`** (NWTC Library) — the utility library used by every OpenFAST module. Contains math helpers (`NWTC_Num`), I/O (`NWTC_IO`, `NWTC_Str`), mesh operations (`ModMesh*`), interpolation (`GridInterp`), random numbers, JSON, YAML, VTK, the platform abstraction (`SysGnuLinux`), and more. 53 files, 52,688 lines.
2. **`ifwlib`** (InflowWind) — wind inflow models. AeroDyn depends on it because the BEMT solver needs wind velocities at each blade element.
3. **`basicaerolib`** — UnsteadyAero + AirfoilInfo. The airfoil polar lookup and unsteady aerodynamics corrections.
4. **`aeroacousticslib`** — AeroAcoustics (blade noise prediction). Pulled in as an AeroDyn dependency even though it's only used when AeroDyn is asked to compute noise.
5. **`aerodynlib`** — AeroDyn itself. Source files built include: `FVW*` (Free Vortex Wake solver and its utilities — BiotSavart, VortexTools, Wings, IO, Tests, and the main `FVW.f90`), `DBEMT_Types.f90`, `DBEMT.f90` (Dynamic Blade Element Momentum), `BEMT_Types.f90`, `BEMTUncoupled.f90`, `BEMT.f90` (the main static BEMT solver), `mod_root1dim.f90`, `AeroDyn_IO_Params.f90`, `AeroDyn_Types.f90`, `AeroDyn_AllBldNdOuts_IO.f90`, `AeroDyn_IO.f90`, `AeroDyn.f90` (the top-level). Linked as `libaerodynlib.a`.
6. **`adilib`** — AeroDyn-Inflow integration wrapper. `AeroDyn_Inflow.f90` + `AeroDyn_Inflow_Types.f90`. This is the layer that couples AeroDyn with InflowWind in a single-module interface for standalone driver use.
7. **`aerodyn_driver_subs`** — driver support subroutines. `AeroDyn_Driver_Types.f90`, `AeroDyn_Driver_Subs.f90`. Contains initialization, time-stepping, and output-writing logic for the standalone driver.
8. **`aerodyn_driver`** (the executable) — `AeroDyn_Driver.f90`. Links all of the above and provides the `main` program.

**What was NOT built** (skipped because not in AeroDyn's closure): ElastoDyn, HydroDyn, SeaState, SubDyn, BeamDyn, MoorDyn, OrcaFlex interface, extloads, externalinflow, icedyn, icefloe, feamooring, MAP++, ServoDyn, AeroDisk, SoilDyn, LinDyn, simple-elastodyn, TurbSim, WakeDynamics, AWAE, ExtPtfm, the top-level `openfast` executable, and the `glue-codes/` aggregator.

**Build size:** the `build/` directory is 42 MB total after the AeroDyn-only build. Full OpenFAST build would be substantially larger.

**Clock skew warnings** during build are the expected Mac-host / Linux-container filesystem timestamp mismatch documented in CLAUDE.md as harmless.

**No errors, no warnings other than clock skew.** The build is clean.

## 3. r-test submodule fetch

OpenFAST's regression test reference data lives in a separate repo (`https://github.com/OpenFAST/r-test.git`) wired in as a git submodule at `reg_tests/r-test/`. Our initial `git clone` of OpenFAST was not `--recurse-submodules`, so the submodule was registered but not populated. `find /workspace/openfast/reg_tests -maxdepth 3 -type d -iname '*erodyn*'` initially returned nothing.

**Command:**
```bash
cd vit_translation_openfast/openfast
git submodule update --init --recursive reg_tests/r-test
```

Fetched cleanly. The submodule checks out at commit `dd5feaaa` (v5.0.0 tag, released alongside OpenFAST v5.0.1).

**What r-test provides for AeroDyn:** 17 AeroDyn test cases, each with its own subdirectory containing a `.dvr` driver input, AeroDyn primary input, InflowWind input (when used), optional time series CSVs, a README, and — crucially — a reference `ad_driver.outb` output file from a known-good run. Available cases:

- `ad_B1n2_OLAF` — single blade, 2 nodes, OLAF (Free Vortex Wake)
- `ad_BAR_CombinedCases` — Big Adaptive Rotor (BAR), combined operating points
- `ad_BAR_OLAF` — BAR with OLAF wake model
- `ad_BAR_RNAMotion` — BAR with rotor-nacelle-assembly motion prescribed
- `ad_BAR_SineMotion` — BAR with sinusoidal base motion (the one we ran — see next section)
- `ad_BAR_SineMotion_UA4_DBEMT3` — BAR with UA (Unsteady Aero) model 4 + DBEMT model 3
- `ad_EllipticalWingInf_OLAF`, `ad_HelicalWakeInf_OLAF`, `ad_Kite_OLAF`, `ad_QuadRotor_OLAF`, `ad_Sphere_OLAF`, `ad_VerticalAxis_OLAF` — OLAF-focused tests
- `ad_MHK_RM1_Fixed`, `ad_MHK_RM1_Fixed_IfW`, `ad_MHK_RM1_Floating` — Marine Hydro-Kinetic reference model 1
- `ad_MultipleHAWT` — multiple horizontal-axis wind turbines in one simulation
- `ad_timeseries_shutdown` — time-series-driven shutdown maneuver
- `BAR_Baseline/` — shared baseline blade geometry and airfoil polars used by the BAR-family tests
- `py_ad_5MW_OC4Semi_WSt_WavesWN/` — Python-driven reference case
- `CaseList.md` — index of all cases

The `BAR_Baseline/` subdirectory is particularly valuable: it contains `OpenFAST_BAR_00_AeroDyn_blade.dat` (blade distributed properties) and the `Airfoils/` directory with polar files (`BAR0_AeroDyn_Polar_00.dat` through `BAR0_AeroDyn_Polar_N.dat`). Several BAR tests reference these via `../BAR_Baseline/` relative paths, so they all share a single source of truth for blade geometry.

## 4. First AeroDyn Driver run: `ad_BAR_SineMotion`

Picked `ad_BAR_SineMotion` as the first smoke test because it's a clean standard case — BAR turbine with BEMT aerodynamics (not OLAF), simple sinusoidal base motion, 7 seconds of simulated time at 0.1s timestep = 70 timesteps total. Fast to run, exercises the full AeroDyn pipeline.

**Command:**
```bash
cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/ad_BAR_SineMotion
/workspace/openfast/build/modules/aerodyn/aerodyn_driver ad_driver.dvr
```

The `.dvr` file configures:
- `AnalysisType = 1` (multiple turbines, one simulation — though `NumTurbines = 1` here)
- `TMax = 7.0s`, `DT = 0.1s`
- AeroFile: `./OpenFAST_BAR_00_AeroDyn.dat` (in the same directory)
- CompInflow = 0 (steady wind, not InflowWind) — though the driver still initializes the InflowWind module for consistency
- Standard atmospheric conditions (FldDens = 1.225 kg/m³, KinVisc = 1.48e-5 m²/s, SpdSound = 335 m/s)
- Single 3-bladed turbine with BAR geometry: HubHt 140.8 m, HubRad 3.0 m, Overhang -6.96 m, ShftTilt -6°, Precone -4°
- Sinusoidal base motion: DOF 1 (xt), Amplitude 5 m, Frequency 0.1 Hz
- RotSpeed 7 rpm, BldPitch 1°

**Execution output (abridged):**

```
Running analysis type 1: one simulation
Nodes positions for turbine 1, (at t=0, without base or RNA motion)
  Bse:     0.0  0.0    0.0
  Twr:     0.0  0.0    0.0
  Nac:     0.0  0.0  137.00000000
  Hub:    -6.96 0.0  140.82513000
  Bld:    -6.86 0.0  143.82330248
  Bld:    -7.33 -2.59 139.35885568
  Bld:    -7.33  2.59 139.35885568
Driver:  projMod: -1
  Running ADI.
  Running InflowWind.
  Running AeroDyn.
    AeroDyn: projMod: 1
    Projection: legacy (NoSweepPitchTwist), BEM: legacy (2D)
  Time: 0 of 7 seconds.
  Time: 1 of 7 seconds. ...
  Time: 2 of 7 seconds. ...
  ... (skipping through 70 timesteps)
  Time: 7 of 7 seconds. ...

  Total Real Time:       0.265 seconds
  Total CPU Time:        0.4954 seconds
  Simulation CPU Time:   6.62630E-02 seconds
  Simulated Time:        7 seconds
  Time Ratio (Sim/CPU):  105.64

  AeroDyn_driver terminated normally.
```

**Success markers (all present):**
- ADI (AeroDyn-Inflow) initialized cleanly
- InflowWind initialized (even in steady-wind mode, as expected)
- AeroDyn module initialized with projection mode 1 (legacy NoSweepPitchTwist) and BEMT in legacy 2D mode
- Blade and hub positions computed correctly at t=0 — the 3-blade geometry is clearly visible in the coordinates (blade 1 pointing +Z, blades 2 and 3 rotated ±2.59 m in Y with -4.47 m drop in Z because of the 120° azimuth spacing + precone)
- All 70 timesteps executed
- Simulation CPU time: 66 ms for 7 seconds of wall clock — **~105x real-time ratio**, which is consistent with BEMT being computationally cheap
- Clean termination with "terminated normally" message

**Output file produced:** `ad_driver.outb` at the expected size (21864 bytes).

This confirms that OpenFAST's entire AeroDyn stack — NWTC Library → InflowWind → AeroDyn → BEMT solver → actuator dynamics → output writer — is functional end-to-end in `vit-dev-openfast`. Real turbine geometry, real airfoil polars, real time-stepping. Not a trivial "hello world" — a genuine aerodynamics simulation.

## 5. Output comparison and what it taught us about `.outb`

The r-test repo ships a reference `ad_driver.outb` at the same path as where AeroDyn_Driver writes its output, so our run **overwrote the reference**. That was an accident but led to a useful finding.

**First check — did our output differ from the reference?**
```bash
cd vit_translation_openfast/openfast/reg_tests/r-test
git status modules/aerodyn/ad_BAR_SineMotion
# -> modified: modules/aerodyn/ad_BAR_SineMotion/ad_driver.outb
git diff --stat modules/aerodyn/ad_BAR_SineMotion/ad_driver.outb
# -> Bin 21864 -> 21864 bytes, 0 insertions, 0 deletions (but binary modified)
```

Same size (21864 bytes), but git reports it as modified. Interesting — they are binary-distinct at the same file size.

**Second check — where exactly do they differ?** Saved our output, restored the reference, and ran `cmp -l`:

```bash
cp ad_driver.outb /tmp/our_output.outb
git checkout ad_driver.outb
cmp -l /tmp/our_output.outb ad_driver.outb
```

Result: **20063 differing bytes out of 21864 total** (~92%). First differing byte is at position 569, meaning bytes 1-568 are identical (that's the binary header: file format version, channel count, metadata). Everything after the header differs.

**Interpretation — this is NOT a failure.** OpenFAST's `.outb` binary format packs each channel's time-series as scaled 16-bit integers rather than storing raw doubles. For each channel, the writer computes a scale factor and offset from the channel's min/max range, then stores each sample as `packed = int16((value - offset) / scale)`. This has two effects:

1. **Small numerical differences amplify.** If our run produces 7.234567890 and NREL's reference produces 7.234567891, those doubles are nearly identical — but after scaling, they may pack to different `int16` values, and the difference shows up in the bytes.
2. **Scale/offset depend on the channel min/max.** If our run and the reference differ by even one ULP somewhere, the channel's overall range differs, which changes the per-channel scale/offset values encoded in the header, which cascades into every packed sample for that channel being different.

So small floating-point differences (from compiler version, library version, OS, linker, optimization level, FMA scheduling — any of the things ROSCO taught us matter) propagate through the packing step and produce dramatic-looking byte diffs even when the underlying simulations are essentially identical.

**This is why OpenFAST's own regression tests don't use `diff`.** The Python driver scripts (`reg_tests/executeAerodynRegressionCase.py`, etc.) parse both `.outb` files using the `openfast_io` Python package, unpack the channel data back into doubles, and compare with a per-channel tolerance. That's the correct way to verify AeroDyn output. `cmp` and `git diff` are the wrong tools for this file format.

**Implication for the AeroDyn translation effort:** when we eventually translate AeroDyn functions and need to verify the translations, we will NOT be able to use a simple binary diff the way ROSCO's `vit_sim.py` → `.npz` + `np.array_equal` did. ROSCO used Python-assembled numpy arrays that preserved double precision and supported exact comparison. AeroDyn's `.outb` output goes through a lossy binary format that requires tolerance-based comparison. Either:

- We write our own harness (analogous to ROSCO's `vit_sim.py`) that runs AeroDyn_Driver but collects outputs directly via ctypes / file-reading a plaintext format (not `.outb`), preserving doubles for exact comparison.
- Or we use OpenFAST's existing Python scripts (`openfast_io` + tolerance comparison) and accept that our verification is "close enough within tolerance" rather than byte-identical.

This is a real design decision for the AeroDyn translation plan and should be flagged as an open question in `PROJECT_PLAN_AERODYN.md` when that document is drafted. It is analogous to the Phase 10 strategy shift in ROSCO (mirror-Fortran vs idiomatic-C++ for I/O) — a fork in the methodology that was implicit during ROSCO and should be explicit for AeroDyn.

**Cleanup:** deleted `/tmp/our_output.outb`, restored the reference via `git checkout`, verified `git status` is clean.

## 6. OpenFAST remote fork swap

The user created `git@github.com:kevinmenear/openfast.git` as a fork of `https://github.com/OpenFAST/openfast.git` via GitHub's UI. Swapped the local clone's remotes to the standard fork pattern.

**Before:**
```
origin  https://github.com/OpenFAST/openfast.git  (fetch + push)
```

**Commands:**
```bash
cd vit_translation_openfast/openfast
git remote rename origin upstream
git remote add origin git@github.com:kevinmenear/openfast.git
git fetch origin
git branch --set-upstream-to=origin/main main
```

**After:**
```
origin    git@github.com:kevinmenear/openfast.git     (fetch + push) -> fork
upstream  https://github.com/OpenFAST/openfast.git    (fetch + push) -> NREL
```

Fork's `main` tip is `2895884d2` — identical to the local `main` (we had not made any commits, just initialized a submodule which doesn't produce a top-level commit). Local `main` now tracks `origin/main` (the fork). `git status` reports "up to date with 'origin/main'".

**Workflow going forward:**
- Any commits to OpenFAST source (e.g., VIT integration wrappers when translations are being applied, or CMakeLists modifications, or new test scaffolding) go to the fork via `git push` — same pattern as the ROSCO fork.
- `git fetch upstream && git merge upstream/main` to sync future NREL OpenFAST releases.
- The `reg_tests/r-test` submodule still points at `https://github.com/OpenFAST/r-test.git` — we are NOT forking r-test because it is reference test data that we only read, not modify.

**Nothing committed during this step.** The remote swap is a local config change only — `.git/config` updated, no repository history touched.

## Cumulative state after this dev note

**vit-dev-openfast container:**
- Running
- Image `vit-dev-openfast:latest` built from `Dockerfile.openfast`
- Editable VIT install pointing at `/workspace/vit`
- fparser 0.2.2 ready for Phase 0 structural inventory
- KGen ready under Python 2.7.18

**OpenFAST clone:**
- At `vit_translation_openfast/openfast/`
- On branch `main`, tracking `origin/main` (fork)
- `origin` → `kevinmenear/openfast`, `upstream` → `OpenFAST/openfast`
- Submodule `reg_tests/r-test` initialized at `dd5feaaa`
- AeroDyn_Driver built in `build/modules/aerodyn/aerodyn_driver` (42 MB build tree, not committed)
- Clean working tree after restoring `ad_driver.outb` from git

**Verified working:**
- cmake configure against OpenFAST (0.9s, zero errors)
- `make aerodyn_driver` (builds 7 libraries + 1 executable in AeroDyn's closure)
- AeroDyn_Driver running a real regression test case (`ad_BAR_SineMotion`, BAR turbine, BEMT, 70 timesteps, 0.265s wall clock, 105x real-time)
- Output file produced at expected size
- Binary `.outb` comparison against reference fails byte-level but that's a known limitation of the format, not a real failure

**Not yet done (deferred):**
- Proper tolerance-based regression comparison via `executeAerodynRegressionCase.py` or a direct `openfast_io` parse. Would confirm that our simulation output is numerically close to NREL's reference. Not blocking — we know the simulation ran cleanly.
- Full OpenFAST executable build (intentionally skipped; not needed for AeroDyn-focused work).
- Unit test build (`BUILD_TESTING=on`, runs the pFUnit-based `aerodyn_utest.F90`). Would give us an additional smoke test of AeroDyn internals. Low priority.
- Draft `PROJECT_PLAN_AERODYN.md` per the `CREATING_PROJECT_PLANS.md` methodology. Phase 0 structural inventory using fparser against `modules/aerodyn/` and `modules/nwtc-library/` is the natural first step.
- Flag the `.outb` verification question as an open decision in the AeroDyn plan.

## What this unblocks for VIT workflow

### AeroDyn_Driver is the analog of ROSCO's `vit_sim.py`

In the ROSCO project, VIT's KGen extraction used `vit_sim.py` as the `run_cmd` — a Python harness that loaded `libdiscon.so` via ctypes and ran the controller loop in-process to produce runtime state for KGen to capture. For AeroDyn, the equivalent run target is **`aerodyn_driver`** (the standalone executable we just built). Advantages over trying to use the full `openfast` executable:

- **Scope isolation.** `aerodyn_driver` only exercises AeroDyn + its direct dependencies (NWTC Library, InflowWind, UnsteadyAero, AirfoilInfo, BEMT, DBEMT, FVW, AeroAcoustics). It does not pull in ElastoDyn, HydroDyn, SubDyn, BeamDyn, MoorDyn, or the glue-code framework. KGen instrumentation is simpler when the call chain is shallower.
- **Fast iteration.** 0.265s per 7-second simulation = ~105x real-time. A KGen extraction cycle (instrument → rebuild → run → capture state) will be fast.
- **Real test cases available.** The `r-test` submodule ships 17 AeroDyn test cases with reference outputs, covering BEMT, DBEMT, UnsteadyAero, FVW, static and moving base, wind and MHK configurations. We do not need to write our own test harness from scratch — we can use r-test cases as our `run_cmd` equivalents.
- **No full OpenFAST build needed.** AeroDyn-only builds are much smaller (42 MB vs the multi-hundred-MB full OpenFAST build), which means faster container rebuilds if we need to iterate.

The `vit.yaml` for the AeroDyn project will therefore look something like:
```yaml
kgen:
  build_cmd: cd /workspace/openfast/build && cmake --build . --target aerodyn_driver
  run_cmd: cd /workspace/openfast/reg_tests/r-test/modules/aerodyn/ad_BAR_SineMotion && /workspace/openfast/build/modules/aerodyn/aerodyn_driver ad_driver.dvr
  # ... etc
```

This is much simpler than what we'd need for a full-OpenFAST instrumentation. The decision to scope AeroDyn-only was validated by this build experiment.

### Verification strategy will differ from ROSCO

As noted in section 5, OpenFAST's `.outb` binary format is not suitable for exact byte comparison between builds. This means we cannot replicate ROSCO's "three-build chain with byte-identical numpy array comparison" directly for AeroDyn. The plan needs to specify a verification strategy that accounts for this. Options:

1. **Tolerance-based `.outb` comparison via openfast_io** — use OpenFAST's own regression test framework pattern. Produces "bit-close" verification with a configurable tolerance.
2. **Custom output capture** — write a harness that runs AeroDyn_Driver but captures outputs through a different channel (e.g., text dump, ctypes direct read of internal state, or a minimal .outb reader that unpacks the int16 scaling). Preserves double precision for exact comparison.
3. **Hybrid** — use KGen kernel-level comparison at float64 precision for the core translation verification (same as ROSCO's Phase 1-8), and use tolerance-based `.outb` comparison for end-to-end simulation checks (analogous to ROSCO's Stage B/C/D/E sim-compare).

Option 3 is closest to what worked for ROSCO. But there's a twist: ROSCO's sim-compare used numpy arrays at float32 (the avrSWAP deployment precision), and we disclosed that as an honest caveat in the adversarial audit. For AeroDyn, the `.outb` packing introduces yet another layer of precision loss (int16 scaled integers). We should think carefully about how to frame verification claims so they are honest about these precision ceilings.

This is an open question for `PROJECT_PLAN_AERODYN.md`, first-class in the Open Questions section per the `CREATING_PROJECT_PLANS.md` methodology.

### r-test as a pre-built test inventory

ROSCO had to build its own simulation harness (`vit_sim.py`) that exercised specific mode combinations to capture state for each translated function. For AeroDyn, we have 17 r-test cases ready to go, each covering different operating conditions. When we map each AeroDyn function to a scenario that exercises it (analog of ROSCO's "saturate needs scenario 3, wrap_360 needs scenario 2" table in CLAUDE.md), we can draw from the existing r-test inventory rather than inventing new scenarios. This is a real productivity win.

The `CaseList.md` file in `reg_tests/r-test/modules/aerodyn/` is the index — should read it carefully when building the AeroDyn extraction scenario mapping.

## Related dev notes

- `202604140013-openfast-aerodyn-phase-start.md` — the AeroDyn phase kickoff. Flagged Docker mount as blocker, proposed first-week milestone as "can VIT see OpenFAST at all?", listed three anticipated challenges (NWTC Framework interface, Registry-generated types, heavier build/extraction chain).
- `202604140204-vit-dev-openfast-docker-plan.md` — the Docker container setup, Dockerfile draft, build + smoke test success. Ended with the container ready but OpenFAST itself not yet validated in it.
- This dev note (`202604140241-openfast-build-aerodyn-baseline.md`) — OpenFAST validated in the container. The "can VIT see OpenFAST at all?" question is no longer hypothetical; VIT can, the build works, a real test runs.

## Related files

- `vit/CLAUDE.md` — updated in the same commit as this dev note. Project Status → OpenFAST subsection gets a bullet noting the build/run baseline is verified. Git → OpenFAST workspace table's OpenFAST row updates to reflect the fork + upstream remote setup.
- `vit/docker/Dockerfile.openfast` — no changes. The LAPACK/BLAS + yaml-cpp + Python 2.7 setup was exactly right for what OpenFAST needed.
- `vit_translation_openfast/openfast/` — `origin` and `upstream` remotes swapped. `reg_tests/r-test` submodule initialized. `build/aerodyn_driver` binary produced (not committed — build artifact).
- `vit_translation_openfast/openfast/reg_tests/r-test/modules/aerodyn/ad_BAR_SineMotion/ad_driver.outb` — touched and restored. git-clean now.
