"""
Project configuration for VIT.

Stores project-specific settings in a vit.yaml file:
- Source directory paths
- Build/run commands for KGen
- Kind aliases (e.g., ReKi → REAL(8))
- CMake target names
- KGen path
"""

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


CONFIG_FILENAME = "vit.yaml"

# Default config template
DEFAULT_CONFIG = {
    "project": {
        "name": "my_project",
        "source_dir": "src",
        "build_dir": "build",
    },
    "kgen": {
        "path": "kgen",
        "build_cmd": "cmake --build build",
        "run_cmd": "python run_simulation.py",
        "clean_cmd": "cmake --build build --target clean",
        "install_cmd": "",  # e.g., "cp build/libdiscon.so lib/libdiscon.so"
        "invocation": "auto",  # or KGen's rank:thread:calls (vit/extract.py capture_invocation)
        "timeout": 1200,  # seconds for KGen: its parse, the instrumented build and the run
    },
    "build_system": {
        "type": "cmake",  # or "make"
        "cmake_file": "CMakeLists.txt",
        # Optional: `vit integrate` finds the add_library target that lists the
        # source file, and uses this only when it cannot.
        "target_name": "",
    },
    "fortran": {
        # The project's own kind parameters, e.g. {"ReKi": "REAL(8)"}. None by
        # default: a guessed precision is worse than an unmapped one.
        "kind_aliases": {},
        "compiler": "gfortran",
        "flags": "",  # Extra Fortran flags for kernel compilation (e.g., -fdefault-real-8)
    },
    "verification": {
        "tolerance": 1e-14,
    },
    "translations": {},  # Tracks completed translations
}


@dataclass
class VitConfig:
    """Parsed VIT configuration."""
    project_name: str = "my_project"
    source_dir: str = "src"
    build_dir: str = "build"

    kgen_path: str = "kgen"
    build_cmd: str = "cmake --build build"
    run_cmd: str = "python run_simulation.py"
    clean_cmd: str = ""
    install_cmd: str = ""  # Post-build: copy library to where simulation loads it
    invocation: str = "auto"  # or KGen's rank:thread:calls (vit/extract.py capture_invocation)
    # Seconds for KGen: its parse, the instrumented build and the run, which,
    # with the calls spread over the run, goes to the end of the test case.
    kgen_timeout: int = 1200

    build_type: str = "cmake"
    cmake_file: str = "CMakeLists.txt"
    target_name: str = ""  # fallback when integrate cannot find the target

    kind_aliases: dict = field(default_factory=dict)
    fortran_compiler: str = "gfortran"
    cxx_compiler: str = "g++"
    fortran_runtime_libs: str = ""  # "" = derive from fortran_compiler
    fortran_flags: str = ""  # Extra Fortran flags for kernel compilation
    preprocessor_defines: list = field(default_factory=list)  # Preprocessor macros for KGen parsing (e.g., OPENFAST_DOUBLE_PRECISION)

    tolerance: float = 1e-14
    translations: dict = field(default_factory=dict)
    type_strategies: dict = field(default_factory=dict)  # type_name → "bind_c" | "view"
    allocatable_helpers: list = field(default_factory=list)  # allocation helper patterns
    assumed_size_arrays: dict = field(default_factory=dict)  # varname → size for assumed-size (*) arrays
    dll_persistence: dict = field(default_factory=dict)  # DLL SAVE persistence workaround config
    add_source_cmd: str = ""  # shell command to add kgen_utils.f90 to the build file,
                               # for a build system or source list the default cannot match
    shared_files: list = field(default_factory=list)  # Shared headers/sources to copy to kernel (paths relative to project root)
    # Line coverage per test case (`vit coverage run`). Paths are relative to
    # vit.yaml; commands may use {build_dir} and {project_dir}.
    coverage_file: str = "coverage.json"
    coverage_build_dir: str = "build-coverage"
    coverage_configure_cmd: str = ""  # "" = CMake with --coverage on every compiler and linker
    coverage_build_cmd: str = ""  # "" = cmake --build {build_dir}
    coverage_gcov: str = ""  # "" = the gcov matching the build's Fortran compiler
    coverage_cases: dict = field(default_factory=dict)  # name -> shell command

    config_dir: str = "."  # Directory containing vit.yaml
    # The YAML as loaded. save_config writes VIT's fields over it, so keys VIT
    # does not manage survive a rewrite.
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    def abs_path(self, relpath: str) -> str:
        """Resolve a path relative to the config directory."""
        return str(Path(self.config_dir) / relpath)

    @property
    def abs_source_dir(self) -> str:
        return self.abs_path(self.source_dir)

    @property
    def abs_build_dir(self) -> str:
        return self.abs_path(self.build_dir)

    @property
    def abs_cmake_file(self) -> str:
        return self.abs_path(self.cmake_file)


def find_config(start_dir: str = ".") -> Optional[str]:
    """Walk up directory tree to find vit.yaml."""
    current = Path(start_dir).resolve()
    while True:
        config_path = current / CONFIG_FILENAME
        if config_path.exists():
            return str(config_path)
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_config(config_path: Optional[str] = None) -> VitConfig:
    """Load configuration from vit.yaml."""
    if config_path is None:
        config_path = find_config()

    if config_path is None:
        # Return defaults
        return VitConfig()

    if not HAS_YAML:
        # Fallback: parse as simple key-value
        return _load_config_fallback(config_path)

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    config_dir = str(Path(config_path).parent)

    project = data.get("project", {})
    kgen = data.get("kgen", {})
    build = data.get("build_system", {})
    fortran = data.get("fortran", {})
    verify = data.get("verification", {})
    coverage = data.get("coverage", {}) or {}

    return VitConfig(
        project_name=project.get("name", "my_project"),
        source_dir=project.get("source_dir", "src"),
        build_dir=project.get("build_dir", "build"),
        kgen_path=kgen.get("path", "kgen"),
        build_cmd=kgen.get("build_cmd", "cmake --build build"),
        run_cmd=kgen.get("run_cmd", "python run_simulation.py"),
        clean_cmd=kgen.get("clean_cmd", ""),
        install_cmd=kgen.get("install_cmd", ""),
        invocation=str(kgen.get("invocation", "auto")),
        kgen_timeout=int(kgen.get("timeout", 1200)),
        build_type=build.get("type", "cmake"),
        cmake_file=build.get("cmake_file", "CMakeLists.txt"),
        target_name=build.get("target_name", ""),
        kind_aliases=fortran.get("kind_aliases", {}),
        fortran_compiler=fortran.get("compiler", "gfortran"),
        cxx_compiler=fortran.get("cxx_compiler", "g++"),
        fortran_runtime_libs=fortran.get("runtime_libs", ""),
        fortran_flags=fortran.get("flags", ""),
        preprocessor_defines=fortran.get("preprocessor_defines", []),
        tolerance=verify.get("tolerance", 1e-14),
        translations=data.get("translations", {}),
        type_strategies=_parse_type_strategies(data.get("types", {})),
        allocatable_helpers=_parse_allocatable_helpers(data.get("allocatable", {})),
        assumed_size_arrays={str(k): int(v) for k, v in kgen.get("assumed_size_arrays", {}).items()},
        dll_persistence=kgen.get("dll_persistence", {}),
        add_source_cmd=kgen.get("add_source_cmd", ""),
        shared_files=data.get("shared_files", []),
        coverage_file=coverage.get("file", "coverage.json"),
        coverage_build_dir=coverage.get("build_dir", "build-coverage"),
        coverage_configure_cmd=coverage.get("configure_cmd", ""),
        coverage_build_cmd=coverage.get("build_cmd", ""),
        coverage_gcov=coverage.get("gcov", ""),
        coverage_cases={str(k): str(v) for k, v in (coverage.get("cases") or {}).items()},
        config_dir=config_dir,
        raw=data,
    )


def _parse_type_strategies(types_section: dict) -> dict:
    """Parse the types: section of vit.yaml into a type_name → strategy dict.

    Example input:
        types:
          ControlParameters:
            strategy: view
          PerformanceData:
            strategy: view

    Returns: {"ControlParameters": "view", "PerformanceData": "view"}
    """
    strategies = {}
    for type_name, info in types_section.items():
        if isinstance(info, dict):
            strategy = info.get("strategy", "bind_c")
        elif isinstance(info, str):
            strategy = info
        else:
            strategy = "bind_c"
        strategies[type_name] = strategy
    return strategies


def _parse_allocatable_helpers(alloc_section: dict) -> list[dict]:
    """Parse the allocatable: section of vit.yaml into helper definitions.

    Example input:
        allocatable:
          helpers:
            - name: ParseAry
              array_arg: 3
              size_arg: 4

    Each helper describes a subroutine that allocates an array internally.
    The scanner generates a regex from these definitions:
    - name: the subroutine name to match in CALL statements
    - array_arg: which positional argument (1-indexed) is the array being allocated
    - size_arg: which positional argument is the size expression

    The scanner matches: CALL <name>(..., Var%field [at position array_arg],
    size_expr [at position size_arg], ...)

    Returns: list of {"name": str, "array_arg": int, "size_arg": int}
    """
    helpers = alloc_section.get("helpers", [])
    parsed = []
    for h in helpers:
        if isinstance(h, dict) and "name" in h:
            parsed.append({
                "name": h["name"],
                "array_arg": int(h.get("array_arg", 3)),
                "size_arg": int(h.get("size_arg", 4)),
            })
    return parsed


def save_config(config: VitConfig, config_path: Optional[str] = None) -> str:
    """Save configuration to vit.yaml."""
    if config_path is None:
        config_path = os.path.join(config.config_dir, CONFIG_FILENAME)

    data = {
        "project": {
            "name": config.project_name,
            "source_dir": config.source_dir,
            "build_dir": config.build_dir,
        },
        "kgen": {
            "path": config.kgen_path,
            "build_cmd": config.build_cmd,
            "run_cmd": config.run_cmd,
            "clean_cmd": config.clean_cmd,
            "install_cmd": config.install_cmd,
            "invocation": config.invocation,
            **({"timeout": config.kgen_timeout} if config.kgen_timeout != 1200 else {}),
            **({"assumed_size_arrays": config.assumed_size_arrays} if config.assumed_size_arrays else {}),
            **({"dll_persistence": config.dll_persistence} if config.dll_persistence else {}),
            **({"add_source_cmd": config.add_source_cmd} if config.add_source_cmd else {}),
        },
        "build_system": {
            "type": config.build_type,
            "cmake_file": config.cmake_file,
            "target_name": config.target_name,
        },
        "fortran": {
            "kind_aliases": config.kind_aliases,
            "compiler": config.fortran_compiler,
            **({"cxx_compiler": config.cxx_compiler} if config.cxx_compiler != "g++" else {}),
            **({"runtime_libs": config.fortran_runtime_libs} if config.fortran_runtime_libs else {}),
            "flags": config.fortran_flags,
            **({"preprocessor_defines": config.preprocessor_defines} if config.preprocessor_defines else {}),
        },
        "verification": {
            "tolerance": config.tolerance,
        },
        "translations": config.translations,
    }

    # Only include types section if strategies exist
    if config.type_strategies:
        data["types"] = {
            name: {"strategy": strategy}
            for name, strategy in config.type_strategies.items()
        }

    if config.allocatable_helpers:
        data["allocatable"] = {"helpers": config.allocatable_helpers}

    if config.shared_files:
        data["shared_files"] = config.shared_files

    coverage = {key: value for key, value, default in (
        ("file", config.coverage_file, "coverage.json"),
        ("build_dir", config.coverage_build_dir, "build-coverage"),
        ("configure_cmd", config.coverage_configure_cmd, ""),
        ("build_cmd", config.coverage_build_cmd, ""),
        ("gcov", config.coverage_gcov, ""),
        ("cases", config.coverage_cases, {}),
    ) if value != default}
    if coverage:
        data["coverage"] = coverage

    data = _over_raw(config.raw, data)

    if HAS_YAML:
        with open(config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    else:
        _save_config_fallback(data, config_path)

    return config_path


def _over_raw(raw: dict, managed: dict) -> dict:
    """VIT's fields written over the YAML as loaded.

    VIT rewrites vit.yaml whenever it records a result. Writing only the
    fields it manages dropped every other key the first time `vit verify` or
    `vit integrate` ran, including documented ones (`kgen.add_source_cmd`,
    `fortran.cxx_compiler`, `fortran.runtime_libs`). Unknown sections and
    unknown keys within a section now survive, in their original order; within
    `types`, each type keeps its other keys. A field VIT manages is replaced
    whole, so `translations` and the dictionaries inside a section are exactly
    what VIT holds. Comments are still lost: the file is re-serialised.
    """
    out = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    for key, value in managed.items():
        old = out.get(key)
        if key == "types" and isinstance(old, dict) and isinstance(value, dict):
            out[key] = {**old, **{name: {**(old.get(name) or {}), **info}
                                  if isinstance(old.get(name), dict) else info
                                  for name, info in value.items()}}
        elif key != "translations" and isinstance(old, dict) and isinstance(value, dict):
            out[key] = {**old, **value}
        else:
            out[key] = value
    return out


def init_config(project_dir: str = ".", **overrides) -> str:
    """
    Initialize a new vit.yaml in the given directory.

    Returns the path to the created config file.
    """
    # Verification is bit-for-bit, so FMA contraction must be off in the
    # kernels as it must be in the project's own build.
    overrides.setdefault("fortran_flags", "-ffp-contract=off")
    config = VitConfig(config_dir=project_dir, **overrides)
    return save_config(config, os.path.join(project_dir, CONFIG_FILENAME))


_STATUS_RANK = {
    "blocked": 0,
    "scaffold": 1,
    "translated": 2,
    "verified": 3,
    "integrated": 4,
}


def record_translation(config: VitConfig, function_name: str,
                       status: str, details: dict) -> None:
    """Record a completed translation in the config.

    Will not downgrade status (e.g., "integrated" will not be
    overwritten by "verified", since integration implies verification).
    """
    existing = config.translations.get(function_name, {})
    existing_status = existing.get("status", "")
    existing_rank = _STATUS_RANK.get(existing_status, -1)
    new_rank = _STATUS_RANK.get(status, -1)

    if new_rank < existing_rank:
        # Don't downgrade — just update details without changing status
        existing.update(details)
        config.translations[function_name] = existing
    else:
        # Keep what earlier stages recorded. Integration follows verification,
        # and replacing the entry wholesale discarded the verification result,
        # so `vit status` reported a verified function as unverified.
        config.translations[function_name] = {
            **existing,
            "status": status,
            **details,
        }
    save_config(config)


def _load_config_fallback(config_path: str) -> VitConfig:
    """Simple fallback config parser when PyYAML isn't available."""
    # Just return defaults with the right config_dir
    return VitConfig(config_dir=str(Path(config_path).parent))


def _save_config_fallback(data: dict, config_path: str) -> None:
    """Simple fallback config writer."""
    lines = ["# VIT Configuration (install PyYAML for full YAML support)"]

    def _write_dict(d, prefix=""):
        for k, v in d.items():
            if isinstance(v, dict):
                lines.append(f"{prefix}{k}:")
                _write_dict(v, prefix + "  ")
            else:
                lines.append(f"{prefix}{k}: {v}")

    _write_dict(data)
    Path(config_path).write_text("\n".join(lines))