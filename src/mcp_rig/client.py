"""Connect to MCP servers over stdio and normalize tool results."""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, MCPError
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT, TextContent


@dataclass
class ServerSpec:
    """A local command that launches an MCP server over stdio."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None

    @classmethod
    def from_command_line(cls, command_line: str) -> ServerSpec:
        parts = shlex.split(command_line)
        if not parts:
            raise ValueError("server command is empty")
        return cls(command=parts[0], args=parts[1:])


@dataclass(frozen=True)
class ToolInfo:
    """Tool metadata used by MCP Rig without exposing SDK model types."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallOutcome:
    """Normalized result of one MCP tool call."""

    is_error: bool
    text: str
    structured: Any
    latency_ms: float
    protocol_error_code: int | None = None

    def json(self) -> Any:
        if self.structured is not None:
            return self.structured
        return json.loads(self.text)


class Probe:
    """MCP Rig's narrow interface over a connected SDK client."""

    def __init__(self, client: Client):
        self._client = client

    async def list_tools(self) -> list[ToolInfo]:
        tools: list[ToolInfo] = []
        cursor: str | None = None
        while True:
            listed = await self._client.list_tools(cursor=cursor)
            tools.extend(
                ToolInfo(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema),
                    annotations=(
                        tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else {}
                    ),
                )
                for tool in listed.tools
            )
            if listed.next_cursor is None:
                return tools
            cursor = listed.next_cursor

    async def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        timeout_s: float = 30.0,
    ) -> CallOutcome:
        started = time.perf_counter()
        try:
            result = await self._client.call_tool(
                name,
                arguments=args or {},
                read_timeout_seconds=timeout_s,
            )
        except MCPError as exc:
            if exc.code in {CONNECTION_CLOSED, REQUEST_TIMEOUT}:
                raise
            return CallOutcome(
                is_error=True,
                text=exc.message,
                structured=exc.data,
                latency_ms=(time.perf_counter() - started) * 1000,
                protocol_error_code=exc.code,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
        return CallOutcome(
            is_error=bool(result.is_error),
            text=text,
            structured=result.structured_content,
            latency_ms=latency_ms,
        )


@asynccontextmanager
async def connect(spec: ServerSpec, show_server_logs: bool = False) -> AsyncIterator[Probe]:
    """Start one stdio server and yield an initialized MCP Rig probe."""
    params = StdioServerParameters(
        command=spec.command,
        args=spec.args,
        env=spec.env,
        cwd=spec.cwd,
    )
    log_context = nullcontext(sys.stderr) if show_server_logs else open(os.devnull, "w", encoding="utf-8")
    with log_context as errlog:
        transport = stdio_client(params, errlog=errlog)
        async with Client(transport) as client:
            yield Probe(client)
