"""A server that never finishes the handshake fails after a deadline instead of hanging."""

import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

import mcp_rig.cli as cli_module
from mcp_rig.cli import main
from mcp_rig.client import DEFAULT_CONNECT_TIMEOUT_S, ServerSpec
from mcp_rig.runner import CaseStatus, ErrorCategory, run_suite
from mcp_rig.spec import SpecError, load_suite

FIXTURE = Path(__file__).parent / "fixtures" / "fixture_server.py"
SILENT_SERVER = shlex.join([sys.executable, "-c", "import sys; sys.stdin.read()"])


def write_suite(tmp_path: Path, server: object, tests: list | None = None) -> Path:
    path = tmp_path / "suite.yaml"
    tests = tests or [{"name": "adds", "call": "add", "args": {"a": 1, "b": 1}}]
    path.write_text(yaml.safe_dump({"server": server, "tests": tests}), encoding="utf-8")
    return path


def test_check_gives_up_on_a_silent_server(capsys):
    started = time.monotonic()

    assert main(["check", SILENT_SERVER, "--connect-timeout", "1"]) == 2

    assert time.monotonic() - started < 15
    err = capsys.readouterr().err
    assert "ConnectTimeoutError: the server did not answer the MCP handshake within 1 s" in err
    assert "raise server.connect_timeout_s or --connect-timeout" in err
    assert cli_module.SERVER_LOGS_HINT in err


@pytest.mark.anyio
async def test_a_suite_reports_the_timeout_as_a_setup_error(tmp_path):
    path = write_suite(tmp_path, {"command": SILENT_SERVER, "connect_timeout_s": 0.5})

    result = await run_suite(load_suite(path))

    assert result.suite_error.category is ErrorCategory.SETUP
    assert result.suite_error.exception_type == "ConnectTimeoutError"
    assert [item.status for item in result.results] == [CaseStatus.SKIPPED]


@pytest.mark.anyio
async def test_the_deadline_ends_once_the_server_is_connected(tmp_path):
    # The session outlives the connect timeout, and a slow call still completes.
    path = write_suite(
        tmp_path,
        {"command": shlex.join([sys.executable, str(FIXTURE)]), "connect_timeout_s": 2},
        [{"name": "slow", "call": "slow", "args": {"seconds": 2.5}, "timeout_s": 10, "expect": {"contains": "done"}}],
    )

    result = await run_suite(load_suite(path))

    assert result.suite_error is None
    assert [item.status for item in result.results] == [CaseStatus.PASSED]


@pytest.fixture
def unresponsive_http_url():
    """A TCP port that accepts connections and never sends a byte."""
    code = (
        "import socket, sys, time\n"
        "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(8)\n"
        "print(s.getsockname()[1], flush=True)\n"
        "held = []\n"
        "while True: held.append(s.accept())\n"
    )
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    port = int(process.stdout.readline())
    yield f"http://127.0.0.1:{port}/mcp"
    process.terminate()
    process.wait(timeout=10)


def test_a_remote_server_that_never_answers_times_out(unresponsive_http_url, capsys):
    assert main(["check", unresponsive_http_url, "--connect-timeout", "1"]) == 2

    assert "did not answer the MCP handshake within 1 s" in capsys.readouterr().err


def test_the_default_allows_slow_installs():
    assert ServerSpec("python").connect_timeout_s == DEFAULT_CONNECT_TIMEOUT_S == 120


@pytest.mark.parametrize("server", [{"command": "python"}, {"url": "http://127.0.0.1:1/mcp"}])
def test_connect_timeout_applies_to_local_and_remote_servers(tmp_path, server):
    assert load_suite(write_suite(tmp_path, {**server, "connect_timeout_s": 5})).server.connect_timeout_s == 5


@pytest.mark.parametrize("value", [0, -1, "10", True, float("inf")])
def test_connect_timeout_must_be_a_positive_number(tmp_path, value):
    with pytest.raises(SpecError, match="'server.connect_timeout_s' must be a positive number of seconds"):
        load_suite(write_suite(tmp_path, {"command": "python", "connect_timeout_s": value}))


@pytest.mark.parametrize("value", ["0", "-2", "soon", "nan"])
def test_connect_timeout_option_rejects_invalid_values(capsys, value):
    with pytest.raises(SystemExit) as exited:
        main(["check", "python server.py", "--connect-timeout", value])

    assert exited.value.code == 2
    assert "--connect-timeout" in capsys.readouterr().err

