"""
Project configuration for VIT.

Stores project-specific settings in a vit.yaml file:
- Source directory paths
- Build/run commands for KGen
- Kind aliases (e.g., ReKi → REAL(8))
- CMake target names
- KGen path
"""

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
        "invocation": "0:0:1-20",  # rank:thread:start-end
    },
    "build_system": {
        "type": "cmake",  # or "make"
        "cmake_file": "CMakeLists.txt",
        "target_name": "discon",
    },
    "fortran": {
        "kind_aliases": {
            "ReKi": "REAL(8)",
            "DbKi": "REAL(8)",
            "SiKi": "REAL(4)",
            "IntKi": "INTEGER(4)",
        },
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
    invocation: str = "0:0:1-20"  # KGen invocation: rank:thread:start-end

    build_type: str = "cmake"
    cmake_file: str = "CMakeLists.txt"
    target_name: str = "discon"

    kind_aliases: dict = field(default_factory=lambda: {
        "ReKi": "REAL(8)",
        "DbKi": "REAL(8)",
        "SiKi": "REAL(4)",
        "IntKi": "INTEGER(4)",
    })
    fortran_compiler: str = "gfortran"
    fortran_flags: str = ""  # Extra Fortran flags for kernel compilation

    tolerance: float = 1e-14
    translations: dict = field(default_factory=dict)

    config_dir: str = "."  # Directory containing vit.yaml

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

    return VitConfig(
        project_name=project.get("name", "my_project"),
        source_dir=project.get("source_dir", "src"),
        build_dir=project.get("build_dir", "build"),
        kgen_path=kgen.get("path", "kgen"),
        build_cmd=kgen.get("build_cmd", "cmake --build build"),
        run_cmd=kgen.get("run_cmd", "python run_simulation.py"),
        clean_cmd=kgen.get("clean_cmd", ""),
        install_cmd=kgen.get("install_cmd", ""),
        invocation=kgen.get("invocation", "0:0:1-20"),
        build_type=build.get("type", "cmake"),
        cmake_file=build.get("cmake_file", "CMakeLists.txt"),
        target_name=build.get("target_name", "discon"),
        kind_aliases=fortran.get("kind_aliases", DEFAULT_CONFIG["fortran"]["kind_aliases"]),
        fortran_compiler=fortran.get("compiler", "gfortran"),
        fortran_flags=fortran.get("flags", ""),
        tolerance=verify.get("tolerance", 1e-14),
        translations=data.get("translations", {}),
        config_dir=config_dir,
    )


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
        },
        "build_system": {
            "type": config.build_type,
            "cmake_file": config.cmake_file,
            "target_name": config.target_name,
        },
        "fortran": {
            "kind_aliases": config.kind_aliases,
            "compiler": config.fortran_compiler,
            "flags": config.fortran_flags,
        },
        "verification": {
            "tolerance": config.tolerance,
        },
        "translations": config.translations,
    }

    if HAS_YAML:
        with open(config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    else:
        _save_config_fallback(data, config_path)

    return config_path


def init_config(project_dir: str = ".", **overrides) -> str:
    """
    Initialize a new vit.yaml in the given directory.

    Returns the path to the created config file.
    """
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
        config.translations[function_name] = {
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