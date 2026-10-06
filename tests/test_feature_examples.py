import os
import shlex
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp_rig.cli import main
from mcp_rig.spec import KNOWN_EXPECT_KEYS, load_suite

ROOT = Path(__file__).resolve().parents[1]
FEATURE_TOUR = ROOT / "examples" / "feature-tour"


def activate_test_python(monkeypatch) -> None:
    executable_directory = str(Path(sys.executable).parent)
    monkeypatch.setenv(
        "PATH",
        executable_directory + os.pathsep + os.environ.get("PATH", ""),
    )


def fixture_command() -> str:
    return shlex.join(
        [sys.executable, str(ROOT / "tests" / "fixtures" / "fixture_server.py")]
    )


def test_feature_tour_runs_end_to_end(capsys, monkeypatch) -> None:
    activate_test_python(monkeypatch)
    assert main(["run", str(FEATURE_TOUR)]) == 0

    output = capsys.readouterr().out
    assert "validates a complete user payload" in output
    assert "inherits suite and case tags" in output
    assert "captures a stable tool response" in output
    assert "reads configured environment" in output


def test_feature_tour_covers_the_complete_suite_contract() -> None:
    suite_paths = sorted(
        path
        for path in FEATURE_TOUR.glob("*.yaml")
        if not path.name.endswith(".snap.yaml")
    )
    suites = [load_suite(path) for path in suite_paths]
    cases = [case for suite in suites for case in suite.cases]

    covered_expectations = {
        key for case in cases for key in case.expect if key in KNOWN_EXPECT_KEYS
    }
    assert covered_expectations == KNOWN_EXPECT_KEYS
    assert any(case.timeout_s != 30 for case in cases)
    assert any(suite.tags for suite in suites)
    assert any(case.tags for case in cases)
    assert any(suite.setup and suite.teardown for suite in suites)
    assert any(case.verify for case in cases)
    assert any(case.retry_attempts for case in cases)
    assert {case.fault for case in cases} >= {"drop_response", "disconnect"}

    configured = next(suite for suite in suites if suite.path.name == "server-config.yaml")
    assert configured.server.args
    assert configured.server.cwd is not None
    assert configured.server.env == {"MCP_RIG_EXAMPLE": "configured"}


def test_feature_tour_filters_real_cases(capsys, monkeypatch) -> None:
    activate_test_python(monkeypatch)
    suite = FEATURE_TOUR / "filtering.yaml"

    assert main(["run", str(suite), "--tag", "smoke", "--exclude-tag", "slow"]) == 0

    output = capsys.readouterr().out
    assert "inherits suite and case tags" in output
    assert "slow case can be excluded" not in output
    assert "Selection: 1 selected, 1 filtered out" in output


def test_feature_tour_case_filter_and_junit_commands(tmp_path, capsys, monkeypatch) -> None:
    activate_test_python(monkeypatch)
    suite = FEATURE_TOUR / "filtering.yaml"
    report = tmp_path / "mcp-rig-results.xml"

    assert main(["run", str(suite), "--case", "inherits*"]) == 0
    output = capsys.readouterr().out
    assert "inherits suite and case tags" in output
    assert "slow case can be excluded" not in output

    assert main(["run", str(FEATURE_TOUR), "--junit", str(report)]) == 0
    capsys.readouterr()
    assert len(ET.parse(report).getroot().findall("testsuite")) >= 4


def test_feature_tour_snapshot_update_command_writes_a_fresh_baseline(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    activate_test_python(monkeypatch)
    copied_root = tmp_path / "repository"
    copied_tour = copied_root / "examples" / "feature-tour"
    copied_fixture = copied_root / "tests" / "fixtures" / "fixture_server.py"
    shutil.copytree(FEATURE_TOUR, copied_tour)
    copied_fixture.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "tests" / "fixtures" / "fixture_server.py", copied_fixture)
    shutil.copy2(ROOT / "tests" / "fixtures" / "eras.py", copied_fixture.parent)
    sidecar = copied_tour / "snapshots.snap.yaml"
    sidecar.unlink()

    assert main(
        ["run", str(copied_tour / "snapshots.yaml"), "--update-snapshots"]
    ) == 0

    assert sidecar.exists()
    assert "Snapshots: 1 added" in capsys.readouterr().out


def test_feature_tour_check_commands_cover_normal_strict_and_probe(capsys) -> None:
    command = fixture_command()

    assert main(["check", command]) == 0
    assert "3/3 checks passed" in capsys.readouterr().out

    assert main(["check", command, "--strict"]) == 1
    assert "lint warnings" in capsys.readouterr().out

    assert main(["check", command, "--probe-invalid-args"]) == 0
    assert "missing required args rejected" in capsys.readouterr().out


def test_feature_tour_check_server_logs_option_runs_with_real_stderr(capfd) -> None:
    command = fixture_command()

    assert main(["check", command, "--server-logs"]) == 0
    assert "3/3 checks passed" in capfd.readouterr().out


def test_feature_tour_server_logs_command_exposes_diagnostic(capfd, monkeypatch) -> None:
    activate_test_python(monkeypatch)
    suite = FEATURE_TOUR / "diagnostics.yaml"

    assert main(["run", str(suite), "--server-logs"]) == 0

    captured = capfd.readouterr()
    assert "mcp-rig-feature-tour-diagnostic" in captured.err
