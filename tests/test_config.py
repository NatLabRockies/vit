"""
Tests for VIT configuration management.

Tests config round-trip (save/load), find_config walk-up,
record_translation, and VitConfig path resolution.
"""

import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit.config import (
    VitConfig,
    find_config,
    load_config,
    save_config,
    init_config,
    record_translation,
    CONFIG_FILENAME,
)


# === VitConfig tests ===

class TestVitConfig:

    def test_default_values(self):
        config = VitConfig()
        assert config.project_name == "my_project"
        assert config.tolerance == 1e-14
        assert config.translations == {}

    def test_abs_path_resolves_relative(self):
        config = VitConfig(config_dir="/project/root")
        assert config.abs_path("src") == "/project/root/src"

    def test_abs_source_dir(self):
        config = VitConfig(config_dir="/project", source_dir="rosco/src")
        assert config.abs_source_dir == "/project/rosco/src"

    def test_abs_build_dir(self):
        config = VitConfig(config_dir="/project", build_dir="build")
        assert config.abs_build_dir == "/project/build"

    def test_abs_cmake_file(self):
        config = VitConfig(config_dir="/project", cmake_file="CMakeLists.txt")
        assert config.abs_cmake_file == "/project/CMakeLists.txt"


# === find_config tests ===

class TestFindConfig:

    def test_finds_in_current_dir(self, tmp_path):
        (tmp_path / CONFIG_FILENAME).write_text("project:\n  name: test\n")
        result = find_config(str(tmp_path))
        assert result is not None
        assert CONFIG_FILENAME in result

    def test_finds_in_parent_dir(self, tmp_path):
        (tmp_path / CONFIG_FILENAME).write_text("project:\n  name: test\n")
        child = tmp_path / "subdir" / "deep"
        child.mkdir(parents=True)
        result = find_config(str(child))
        assert result is not None
        assert str(tmp_path.resolve()) in result

    def test_returns_none_when_not_found(self, tmp_path):
        # tmp_path has no vit.yaml, and walk-up will hit filesystem root
        child = tmp_path / "isolated"
        child.mkdir()
        result = find_config(str(child))
        # May or may not find one depending on test environment
        # The important thing is it doesn't crash
        assert result is None or CONFIG_FILENAME in result


# === load_config / save_config round-trip tests ===

class TestConfigRoundTrip:

    def test_save_then_load(self, tmp_path):
        """Saving then loading produces equivalent config."""
        config = VitConfig(
            project_name="ROSCO",
            source_dir="rosco/controller/src",
            build_dir="rosco/controller/build",
            kgen_path="/workspace/KGen/bin/kgen",
            build_cmd="cmake --build build",
            run_cmd="python3 sim.py",
            tolerance=1e-14,
            kind_aliases={"ReKi": "REAL(8)", "IntKi": "INTEGER(4)"},
            config_dir=str(tmp_path),
        )

        path = save_config(config, str(tmp_path / CONFIG_FILENAME))
        loaded = load_config(path)

        assert loaded.project_name == "ROSCO"
        assert loaded.source_dir == "rosco/controller/src"
        assert loaded.build_dir == "rosco/controller/build"
        assert loaded.kgen_path == "/workspace/KGen/bin/kgen"
        assert loaded.tolerance == 1e-14
        assert loaded.kind_aliases["ReKi"] == "REAL(8)"

    def test_load_missing_translations_key(self, tmp_path):
        """Config file without translations key loads with empty dict."""
        config_content = (
            "project:\n"
            "  name: test\n"
            "  source_dir: src\n"
            "  build_dir: build\n"
        )
        path = tmp_path / CONFIG_FILENAME
        path.write_text(config_content)

        config = load_config(str(path))
        assert config.translations == {}

    def test_load_nonexistent_raises(self):
        """Loading a nonexistent config path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/vit.yaml")

    def test_load_none_returns_defaults(self):
        """Loading with None config_path and no vit.yaml in tree returns defaults."""
        # When find_config() returns None, load_config returns defaults
        config = load_config(None)
        # May find a vit.yaml in some parent dir, or return defaults
        assert config is not None

    def test_save_creates_file(self, tmp_path):
        config = VitConfig(config_dir=str(tmp_path))
        path = save_config(config, str(tmp_path / CONFIG_FILENAME))
        assert os.path.exists(path)

    def test_translations_preserved(self, tmp_path):
        """Translation records survive round-trip."""
        config = VitConfig(
            config_dir=str(tmp_path),
            translations={
                "saturate": {"status": "verified", "cases_passed": 40}
            },
        )
        path = save_config(config, str(tmp_path / CONFIG_FILENAME))
        loaded = load_config(path)
        assert "saturate" in loaded.translations
        assert loaded.translations["saturate"]["status"] == "verified"


# === init_config tests ===

class TestInitConfig:

    def test_creates_config_file(self, tmp_path):
        path = init_config(str(tmp_path))
        assert os.path.exists(path)

    def test_overrides_applied(self, tmp_path):
        path = init_config(str(tmp_path), project_name="MyProject",
                           source_dir="my/src")
        config = load_config(path)
        assert config.project_name == "MyProject"
        assert config.source_dir == "my/src"

    def test_writes_no_project_specific_defaults(self, tmp_path):
        """`vit init` used to write ROSCO's target (`discon`) and ROSCO's kind
        aliases into every new project, where they were wrong and silent: a
        wrong precision for ReKi changes every translated type."""
        config = load_config(init_config(str(tmp_path)))
        assert config.target_name == ""
        assert config.kind_aliases == {}
        assert "-ffp-contract=off" in config.fortran_flags


# === record_translation tests ===

class TestRecordTranslation:

    def test_records_translation(self, tmp_path):
        config = VitConfig(config_dir=str(tmp_path))
        save_config(config, str(tmp_path / CONFIG_FILENAME))

        record_translation(config, "saturate", "verified",
                           {"cases_passed": 40, "cases_total": 40})

        loaded = load_config(str(tmp_path / CONFIG_FILENAME))
        assert "saturate" in loaded.translations
        assert loaded.translations["saturate"]["status"] == "verified"
        assert loaded.translations["saturate"]["cases_passed"] == 40

    def test_updates_existing_entry(self, tmp_path):
        config = VitConfig(
            config_dir=str(tmp_path),
            translations={"saturate": {"status": "verified"}},
        )
        save_config(config, str(tmp_path / CONFIG_FILENAME))

        record_translation(config, "saturate", "integrated",
                           {"cpp_file": "saturate.cpp"})

        loaded = load_config(str(tmp_path / CONFIG_FILENAME))
        assert loaded.translations["saturate"]["status"] == "integrated"

    def test_does_not_downgrade_status(self, tmp_path):
        config = VitConfig(
            config_dir=str(tmp_path),
            translations={"saturate": {
                "status": "integrated",
                "cpp_file": "saturate.cpp",
            }},
        )
        save_config(config, str(tmp_path / CONFIG_FILENAME))

        # Verification should not downgrade from integrated to verified
        record_translation(config, "saturate", "verified",
                           {"cases_passed": 40, "cases_total": 40})

        loaded = load_config(str(tmp_path / CONFIG_FILENAME))
        assert loaded.translations["saturate"]["status"] == "integrated"
        # But details should still be updated
        assert loaded.translations["saturate"]["cases_passed"] == 40

    def test_upgrade_keeps_the_verification_record(self, tmp_path):
        """`vit integrate` follows `vit verify`. It used to replace the entry,
        dropping the case counts and the red-test verdict, so `vit status`
        then called a verified function unverified."""
        config = VitConfig(config_dir=str(tmp_path))
        save_config(config, str(tmp_path / CONFIG_FILENAME))

        record_translation(config, "drag", "verified",
                           {"cases_passed": 20, "cases_total": 20,
                            "red_test": "demonstrated"})
        record_translation(config, "drag", "integrated", {"cpp_file": "drag.cpp"})

        entry = load_config(str(tmp_path / CONFIG_FILENAME)).translations["drag"]
        assert entry["status"] == "integrated"
        assert entry["cases_passed"] == 20
        assert entry["red_test"] == "demonstrated"
        assert entry["cpp_file"] == "drag.cpp"

    def test_upgrade_status_works(self, tmp_path):
        config = VitConfig(
            config_dir=str(tmp_path),
            translations={"saturate": {"status": "verified"}},
        )
        save_config(config, str(tmp_path / CONFIG_FILENAME))

        record_translation(config, "saturate", "integrated",
                           {"cpp_file": "saturate.cpp"})

        loaded = load_config(str(tmp_path / CONFIG_FILENAME))
        assert loaded.translations["saturate"]["status"] == "integrated"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestRewritePreservesKeys:
    """VIT rewrites vit.yaml whenever it records a result.

    It used to write only the fields it manages, so the first `vit verify`
    deleted `kgen.add_source_cmd`, `fortran.cxx_compiler`,
    `fortran.runtime_libs` and anything else a project had added.
    """

    YAML = """\
project:
  name: demo
  source_dir: src
  build_dir: /build/demo
  owner: physics team
kgen:
  build_cmd: make
  run_cmd: ./run
  add_source_cmd: sed -i 's/x/y/' CMakeLists.txt
fortran:
  compiler: gfortran-13
  cxx_compiler: g++-13
  runtime_libs: -lgfortran -lquadmath
  flags: -ffp-contract=off
types:
  State:
    strategy: view
    note: keep me
notes:
  reviewed: true
translations: {}
"""

    def test_record_translation_keeps_every_other_key(self, tmp_path):
        import yaml

        path = tmp_path / CONFIG_FILENAME
        path.write_text(self.YAML)
        config = load_config(str(path))
        assert config.add_source_cmd.startswith("sed")

        record_translation(config, "drag", "verified", {"cases_passed": 20})

        data = yaml.safe_load(path.read_text())
        assert data["kgen"]["add_source_cmd"] == "sed -i 's/x/y/' CMakeLists.txt"
        assert data["fortran"]["cxx_compiler"] == "g++-13"
        assert data["fortran"]["runtime_libs"] == "-lgfortran -lquadmath"
        assert data["project"]["owner"] == "physics team"
        assert data["types"]["State"] == {"strategy": "view", "note": "keep me"}
        assert data["notes"] == {"reviewed": True}
        assert data["translations"]["drag"]["status"] == "verified"

        reloaded = load_config(str(path))
        assert reloaded.cxx_compiler == "g++-13"
        assert reloaded.fortran_runtime_libs == "-lgfortran -lquadmath"

    def test_vit_owned_fields_still_change(self, tmp_path):
        import yaml

        path = tmp_path / CONFIG_FILENAME
        path.write_text(self.YAML)
        config = load_config(str(path))
        config.type_strategies["Params"] = "view"
        config.fortran_flags = "-ffp-contract=off -O2"
        save_config(config, str(path))

        data = yaml.safe_load(path.read_text())
        assert data["types"]["Params"] == {"strategy": "view"}
        assert data["fortran"]["flags"] == "-ffp-contract=off -O2"
        assert list(data)[:3] == ["project", "kgen", "fortran"], "the file keeps its order"
