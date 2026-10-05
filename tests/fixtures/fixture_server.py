"""Deterministic MCP server used by MCP Rig's stdio integration tests."""

import os
import sys

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

server = MCPServer("mcp-rig-fixture")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers and return their sum."""
    return a + b


@server.tool()
def echo(text: str) -> str:
    """Return the given text unchanged."""
    return text


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def get_user(user_id: int) -> dict:
    """Return a deterministic user or a readable tool error."""
    if user_id != 1:
        raise ToolError(f"user {user_id} not found")
    return {"id": 1, "name": "Ada", "roles": ["admin"]}


@server.tool()
async def slow(seconds: float) -> str:
    """Wait for the requested duration and return done."""
    await anyio.sleep(seconds)
    return "done"


@server.tool()
def get_env(name: str) -> str:
    """Return one environment variable from the server process."""
    return os.environ.get(name, "")


@server.tool()
def working_directory() -> str:
    """Return the server process working directory."""
    return os.getcwd()


@server.tool()
def write_stderr(message: str) -> str:
    """Write a controlled diagnostic to stderr for log-routing tests."""
    print(message, file=sys.stderr, flush=True)
    return "written"


@server.tool()
def undocumented(value: str) -> str:
    return value


if __name__ == "__main__":
    server.run()
