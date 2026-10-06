"""CLI messages say what went wrong and only suggest --server-logs when it can help."""

import logging
import shlex
import sys
from pathlib import Path

import pytest
import yaml

import mcp_rig
import mcp_rig.cli as cli_module
from mcp_rig.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "fixture_server.py"
STDOUT_NOISE = shlex.join([sys.executable, "-c", "print('hello')"])
ECHO_STDIN = "import sys\nfor line in sys.stdin: print(line, end='', flush=True)"
ECHOES_REQUESTS = shlex.join([sys.executable, "-c", ECHO_STDIN])


def test_version_prints_the_package_version(capsys):
    with pytest.raises(SystemExit) as exited:
        main(["--version"])

    assert exited.value.code == 0
    assert capsys.readouterr().out.strip() == f"mcp-rig {mcp_rig.__version__}"


def test_non_json_stdout_is_named_without_a_traceback(capsys):
    assert main(["check", STDOUT_NOISE]) == 2

    err = capsys.readouterr().err
    assert "the server wrote non-JSON to stdout: 'hello'" in err
    assert "log to stderr" in err
    assert "Traceback" not in err
    assert "ValidationError" not in err
    assert cli_module.SERVER_LOGS_HINT not in err


def test_a_command_that_is_not_an_mcp_server_is_named(capsys):
    assert main(["init", ECHOES_REQUESTS]) == 2

    err = capsys.readouterr().err
    assert "answered the MCP handshake with 'Method not found'" in err
    assert "check that it starts an MCP server" in err


def test_sdk_log_records_are_routed_away_unless_server_logs_is_given():
    # Without a handler of its own, the SDK's records would reach the terminal through
    # Python's last-resort handler; --server-logs lets them through.
    sdk = logging.getLogger("mcp")

    main(["check", STDOUT_NOISE])
    assert cli_module._SDK_LOGS in sdk.handlers
    assert not sdk.propagate

    main(["check", STDOUT_NOISE, "--server-logs"])
    assert cli_module._SDK_LOGS not in sdk.handlers
    assert sdk.propagate


def write_suite(path: Path, setup_expect: dict) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "server": shlex.join([sys.executable, str(FIXTURE)]),
                "setup": [{"call": "echo", "args": {"text": "ready"}, "expect": setup_expect}],
                "tests": [{"name": "adds", "call": "add", "args": {"a": 1, "b": 1}}],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_failed_setup_expectation_does_not_blame_the_server(tmp_path, capsys):
    path = write_suite(tmp_path / "suite.yaml", {"contains": "not in the response"})

    assert main(["run", str(path)]) == 2

    captured = capsys.readouterr()
    assert "StepFailed: setup[0] echo" in captured.out
    assert cli_module.SERVER_LOGS_HINT not in captured.err


def test_batch_output_shows_suite_paths_relative_to_the_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_suite(tmp_path / "suite.yaml", {"contains": "ready"})

    assert main(["run", "suite.yaml", "--case", "adds"]) == 0

    assert "MCP Rig — suite.yaml" in capsys.readouterr().out.splitlines()
