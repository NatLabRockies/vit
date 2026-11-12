"""The translation record keeps each function's latest verification.

A function's entry in vit.yaml used to hold one `status` that never went down,
so a later verification that failed, or could not discriminate, left an earlier
pass standing and `vit status` reported it with a tick. Now `status` is the
stage and never goes down, and `last_verify` is the latest result, replaced on
every run (dev note `202611111904`).

`vit verify` is driven with the kernel build, the run and the red test stubbed:
what is tested is what VIT records and reports, not KGen.
"""

from types import SimpleNamespace

import pytest

import vit.cli
from vit.config import CONFIG_FILENAME, VitConfig, load_config, record_translation, save_config
from vit.kernel_verify import VerifyResult

CPP = "drag.cpp"


@pytest.fixture(autouse=True)
def _no_telemetry(monkeypatch):
    monkeypatch.setenv("VIT_TELEMETRY", "0")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / CPP).write_text("double drag_c(double v) { return v; }\n")
    (tmp_path / "kernel" / "drag").mkdir(parents=True)
    config = VitConfig(config_dir=str(tmp_path), project_name="p")
    save_config(config, str(tmp_path / CONFIG_FILENAME))
    return tmp_path


def _reload(project):
    return load_config(str(project / CONFIG_FILENAME))


def _verify(project, monkeypatch, *, passed=20, failed=0, red="demonstrated", setup_ok=True):
    """Run `cmd_verify` on drag with the kernel's result chosen by the test."""
    monkeypatch.setattr(vit.cli, "find_function_in_file", lambda *a: SimpleNamespace(name="drag"))
    monkeypatch.setattr(vit.cli, "_build_registry", lambda *a: {})
    monkeypatch.setattr(vit.cli, "setup_kernel_cpp", lambda **k: {
        "success": setup_ok, "files_modified": [],
        "errors": [] if setup_ok else ["drag.cpp: error: expected ';'"]})
    monkeypatch.setattr(vit.cli, "run_kernel_verify", lambda *a, **k: VerifyResult(
        success=failed == 0, total_cases=passed + failed, passed_cases=passed,
        failed_cases=failed, skipped_cases=0, max_error=0.0, output="",
        details=[{"status": "FAILED", "line": "case 3"}] * failed))
    monkeypatch.setattr(vit.cli, "_run_red_test", lambda *a: {"verdict": red, "message": red})
    monkeypatch.setattr(vit.cli, "_kernel_coverage", lambda *a: None)
    args = SimpleNamespace(function_name="drag", cpp_file=CPP, file="physics.f90",
                           kernel_dir="kernel/drag", no_field_log=True,
                           no_kernel_coverage=True)
    return vit.cli.cmd_verify(args, _reload(project))


def _status(project, capsys):
    capsys.readouterr()
    rc = vit.cli.cmd_status(None, _reload(project))
    return rc, capsys.readouterr().out


def test_a_non_discriminating_result_after_a_pass_is_reported(project, monkeypatch, capsys):
    """It used to print `✓ drag: verified (12/20 cases)` and exit 0."""
    assert _verify(project, monkeypatch) == 0
    assert _verify(project, monkeypatch, passed=12, red="not_discriminating") == 1

    entry = _reload(project).translations["drag"]
    assert entry["status"] == "verified"
    assert entry["last_verify"]["result"] == "non_discriminating"
    assert entry["last_verify"]["cases_total"] == 12
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "✗ drag: verified — last verify NON_DISCRIMINATING" in out
    assert "✓" not in out


def test_a_failed_verification_is_recorded(project, monkeypatch, capsys):
    """A failure used to record nothing, so the earlier pass stood."""
    _verify(project, monkeypatch)
    assert _verify(project, monkeypatch, passed=12, failed=8) == 1

    latest = _reload(project).translations["drag"]["last_verify"]
    assert (latest["result"], latest["cases_passed"], latest["cases_failed"],
            latest["cases_total"]) == ("failed", 12, 8, 20)
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "✗ drag: verified — last verify FAILED, 12/20 cases" in out


def test_integration_survives_a_failure_and_the_failure_is_shown(project, monkeypatch, capsys):
    """The stage does not go down: the failing C++ is still in the build."""
    _verify(project, monkeypatch)
    config = _reload(project)
    record_translation(config, "drag", "integrated", {"cpp_file": CPP})
    _verify(project, monkeypatch, passed=19, failed=1)

    assert _reload(project).translations["drag"]["status"] == "integrated"
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "✗ drag: integrated — last verify FAILED, 19/20 cases" in out


def test_a_setup_failure_is_recorded(project, monkeypatch, capsys):
    _verify(project, monkeypatch)
    assert _verify(project, monkeypatch, setup_ok=False) == 1

    latest = _reload(project).translations["drag"]["last_verify"]
    assert latest["result"] == "setup_failed"
    assert latest["errors"] == ["drag.cpp: error: expected ';'"]
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "could not set up the kernel" in out


def test_a_first_verification_that_fails_is_listed(project, monkeypatch, capsys):
    _verify(project, monkeypatch, passed=0, failed=20)

    assert _reload(project).translations["drag"]["status"] == "translated"
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "✗ drag: translated — last verify FAILED, 0/20 cases" in out


def test_a_pass_is_for_the_cpp_that_was_verified(project, monkeypatch, capsys):
    _verify(project, monkeypatch)
    rc, out = _status(project, capsys)
    assert rc == 0
    assert "✓ drag: verified — verified 20/20 cases, red test demonstrated" in out

    (project / CPP).write_text("double drag_c(double v) { return 2 * v; }\n")
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "! drag: verified — drag.cpp has changed since it verified 20/20 cases" in out

    (project / CPP).unlink()
    rc, out = _status(project, capsys)
    assert rc == 1
    assert "drag.cpp is missing" in out


def test_the_record_is_found_from_another_directory(project, monkeypatch, capsys):
    """The C++ path is recorded relative to vit.yaml, not to where verify ran."""
    _verify(project, monkeypatch)
    assert _reload(project).translations["drag"]["last_verify"]["cpp_file"] == CPP
    monkeypatch.chdir(project / "kernel")
    rc, _ = _status(project, capsys)
    assert rc == 0


def _integrate(project, monkeypatch, capsys):
    monkeypatch.setattr(vit.cli, "find_function_in_file",
                        lambda *a: SimpleNamespace(name="drag", source_file=str(project / "physics.f90")))
    monkeypatch.setattr(vit.cli, "_build_registry", lambda *a: {})
    monkeypatch.setattr(vit.cli, "generate_integration_plan",
                        lambda **k: SimpleNamespace(to_markdown=lambda: "plan"))
    monkeypatch.setattr(vit.cli, "apply_integration_plan",
                        lambda plan, dry_run=False: {"success": True, "applied": [], "errors": []})
    monkeypatch.setattr(vit.cli, "_sync_shared_files", lambda *a: [])
    args = SimpleNamespace(function_name="drag", cpp_file=CPP, file="physics.f90",
                           apply=True, dry_run=False, reverse_copy=False,
                           copy_arrays=None, auto_allocate=False)
    capsys.readouterr()
    assert vit.cli.cmd_integrate(args, _reload(project)) == 0
    return (_reload(project).translations["drag"]["integrated_after"],
            capsys.readouterr().out)


def test_integrate_says_what_it_follows(project, monkeypatch, capsys):
    after, out = _integrate(project, monkeypatch, capsys)
    assert after == "none" and "has no verification recorded" in out

    _verify(project, monkeypatch)
    after, out = _integrate(project, monkeypatch, capsys)
    assert after == "passed" and "⚠" not in out

    _verify(project, monkeypatch, passed=12, failed=8)
    after, out = _integrate(project, monkeypatch, capsys)
    assert after == "failed" and "The latest vit verify of drag was FAILED" in out

    _verify(project, monkeypatch)
    (project / CPP).write_text("double drag_c(double v) { return 2 * v; }\n")
    after, out = _integrate(project, monkeypatch, capsys)
    assert after == "changed" and "is not the C++ that passed vit verify" in out


@pytest.mark.parametrize("entry, line, ok", [
    ({"status": "integrated", "cases_passed": 20, "cases_total": 20, "red_test": "demonstrated"},
     "✓ f: integrated — verified 20/20 cases (recorded before VIT fingerprinted the C++)", True),
    ({"status": "verified", "cases_passed": 12, "cases_total": 20, "red_test": "not_discriminating"},
     "✗ f: verified — last verify NON_DISCRIMINATING", False),
    ({"status": "non_discriminating", "cases_passed": 20, "red_test": "not_constructed"},
     "✗ f: translated — last verify NON_DISCRIMINATING: no deliberately wrong translation", False),
    ({"status": "integrated", "cpp_file": "f.cpp", "verified": True},
     "? f: integrated — no verification recorded", True),
    ({"status": "integrated", "cpp_file": "f.cpp", "verification": "simulation"},
     "? f: integrated — no kernel verification recorded", True),
])
def test_entries_written_before_last_verify(entry, line, ok, capsys):
    """Read as they are; the campaigns' records are all of the `?` kind."""
    rc = vit.cli.cmd_status(None, VitConfig(project_name="p", translations={"f": entry}))
    assert line in capsys.readouterr().out
    assert rc == (0 if ok else 1)


def test_a_new_result_replaces_the_old_top_level_fields(project, monkeypatch):
    config = _reload(project)
    config.translations["drag"] = {"status": "integrated", "cpp_file": CPP,
                                   "cases_passed": 20, "cases_total": 20,
                                   "red_test": "demonstrated"}
    save_config(config)
    _verify(project, monkeypatch, passed=12, failed=8)

    entry = _reload(project).translations["drag"]
    assert set(entry) == {"status", "cpp_file", "last_verify"}
    assert entry["status"] == "integrated"


PHYSICS = """\
module physics
  implicit none
contains
  real(8) function sat(x)
    real(8), intent(in) :: x
    sat = min(x, 1.0d0)
  end function sat
  real(8) function lim(x)
    real(8), intent(in) :: x
    lim = max(x, 0.0d0)
  end function lim
  real(8) function drag(v)
    real(8), intent(in) :: v
    drag = sat(v) + lim(v)
  end function drag
end module physics
"""


def test_translate_offers_only_integrated_callees(tmp_path, monkeypatch, capsys):
    """Only integration puts a callee's `_c` entry point into the build. The
    kernel bridges every callee, so C++ calling a merely verified one passes
    `vit verify` and then fails to link."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "physics.f90").write_text(PHYSICS)
    config = VitConfig(config_dir=str(tmp_path), project_name="p", source_dir=".",
                       translations={
                           "sat": {"status": "integrated", "cpp_file": "sat.cpp"},
                           "lim": {"status": "verified", "last_verify": {"result": "passed"}},
                       })
    save_config(config, str(tmp_path / CONFIG_FILENAME))
    args = SimpleNamespace(function_name="drag", file="physics.f90", output=None, module=None)
    assert vit.cli.cmd_translate(args, load_config(str(tmp_path / CONFIG_FILENAME))) == 0

    out = capsys.readouterr().out
    assert "sat_c(" in out
    assert "lim_c(" not in out
