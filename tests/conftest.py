import sys
from pathlib import Path

import pytest

from mcp_rig.client import ServerSpec


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fixture_server_path() -> Path:
    return Path(__file__).parent / "fixtures" / "fixture_server.py"


@pytest.fixture
def fixture_spec(fixture_server_path: Path) -> ServerSpec:
    return ServerSpec(command=sys.executable, args=[str(fixture_server_path)])


@pytest.fixture
def http_server(tmp_path):
    """Start the fixture tools over HTTP and return a factory that yields base URLs."""
    import os
    import subprocess

    processes = []

    def start(transport: str = "streamable-http", token: str | None = None, era: str | None = None) -> str:
        env = {**os.environ}
        env.pop("MCP_RIG_REQUIRED_TOKEN", None)
        env.pop("MCP_RIG_FIXTURE_ERA", None)
        if token is not None:
            env["MCP_RIG_REQUIRED_TOKEN"] = token
        if era is not None:
            env["MCP_RIG_FIXTURE_ERA"] = era
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "fixtures" / "http_server.py"), transport],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        processes.append(process)
        line = process.stdout.readline()
        assert line.startswith("PORT "), f"HTTP fixture did not start: {line!r}"
        path = "/sse" if transport == "sse" else "/mcp"
        return f"http://127.0.0.1:{line.split()[1]}{path}"

    yield start
    for process in processes:
        process.terminate()
        process.wait(timeout=10)
