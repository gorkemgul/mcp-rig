"""The composite GitHub Action passes inputs to the CLI without touching the caller's Python."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text())
STEPS = {step["name"]: step for step in ACTION["runs"]["steps"]}


def run_step(name: str, tmp_path: Path, **env: str) -> list[list[str]]:
    """Run one action step with a stub `mcp-rig` that records its arguments."""
    log = tmp_path / "calls.log"
    stub = tmp_path / "mcp-rig"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" >> {shlex.quote(str(log))}\nprintf "\\n" >> {log}\n')
    stub.chmod(0o755)
    subprocess.run(
        ["bash", "-c", STEPS[name]["run"]],
        env={**os.environ, "MCP_RIG": str(stub), **env},
        check=True,
    )
    if not log.exists():
        return []
    return [line.split("\0")[:-1] for line in log.read_text().splitlines()]


def test_action_is_composite_and_uses_no_third_party_actions():
    assert ACTION["runs"]["using"] == "composite"
    assert all("uses" not in step for step in ACTION["runs"]["steps"])
    assert ACTION["inputs"]["suites"]["required"] is True
    assert {"version", "python", "junit", "args", "check", "check-args"} <= set(ACTION["inputs"])


def test_inputs_reach_scripts_only_through_environment_variables():
    for step in ACTION["runs"]["steps"]:
        assert "${{" not in step["run"], step["name"]


def test_install_defaults_to_the_action_checkout_and_accepts_a_pinned_version():
    script = STEPS["Install MCP Rig"]["run"]

    assert 'requirement="$GITHUB_ACTION_PATH"' in script
    assert 'requirement="mcp-rig==$MCP_RIG_VERSION"' in script
    assert 'echo "bin=$venv/bin/mcp-rig" >> "$GITHUB_OUTPUT"' in script


@pytest.mark.parametrize(
    ("suites", "expected"),
    [
        ("tests/mcp/", ["tests/mcp/"]),
        ("a.yaml b.yaml", ["a.yaml", "b.yaml"]),
        ("a.yaml\nb.yaml\n", ["a.yaml", "b.yaml"]),
    ],
)
def test_run_step_splits_suites_on_spaces_and_newlines(tmp_path, suites, expected):
    calls = run_step("Run suites", tmp_path, MCP_RIG_SUITES=suites, MCP_RIG_JUNIT="", MCP_RIG_ARGS="")

    assert calls == [["run", *expected]]


def test_run_step_adds_junit_and_extra_arguments(tmp_path):
    calls = run_step(
        "Run suites",
        tmp_path,
        MCP_RIG_SUITES="tests/mcp/",
        MCP_RIG_JUNIT="results.xml",
        MCP_RIG_ARGS="--tag smoke --exclude-tag slow",
    )

    assert calls == [["run", "tests/mcp/", "--junit", "results.xml", "--tag", "smoke", "--exclude-tag", "slow"]]


def test_check_step_passes_the_server_command_as_one_argument(tmp_path):
    calls = run_step(
        "Check server",
        tmp_path,
        MCP_RIG_CHECK="python -m my_server --port 0",
        MCP_RIG_CHECK_ARGS="--strict",
    )

    assert calls == [["check", "python -m my_server --port 0", "--strict"]]
    assert STEPS["Check server"]["if"] == "inputs.check != ''"


def test_run_step_propagates_a_failing_exit_code(tmp_path):
    stub = tmp_path / "failing"
    stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    stub.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", STEPS["Run suites"]["run"]],
        env={**os.environ, "MCP_RIG": str(stub), "MCP_RIG_SUITES": "s.yaml", "MCP_RIG_JUNIT": "", "MCP_RIG_ARGS": ""},
        check=False,
    )

    assert result.returncode == 1
