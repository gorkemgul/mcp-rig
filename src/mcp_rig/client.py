"""Connect to MCP servers over stdio or HTTP and normalize tool results."""

from __future__ import annotations

import json
import logging
import math
import os
import shlex
import sys
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2
from mcp import Client, MCPError
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CONNECTION_CLOSED, METHOD_NOT_FOUND, REQUEST_TIMEOUT, TextContent

from mcp_rig.faults import FaultInjector, inject_faults

TRANSPORTS = ("streamable-http", "sse")
STDIO_LOGGER = "mcp.client.stdio"
# Long enough for npx or uvx to install a server on a cold CI cache, short enough
# that a server stuck before the handshake fails the job instead of holding it.
DEFAULT_CONNECT_TIMEOUT_S = 120.0


class ServerStartError(ConnectionError):
    """A local server command could not start, or what started does not speak MCP."""


class ConnectTimeoutError(ServerStartError):
    """The server did not finish the MCP handshake in time."""
HTTP_TIMEOUT = httpx2.Timeout(30.0, read=300.0)


@dataclass
class ServerSpec:
    """An MCP server: a local command launched over stdio, or a remote URL."""

    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    transport: str = "streamable-http"
    inherit_env: bool | tuple[str, ...] = False
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S

    @property
    def is_remote(self) -> bool:
        return self.url is not None

    def child_env(self) -> dict[str, str] | None:
        """Variables to add to the SDK's minimal default environment for a stdio server.

        ``inherit_env`` copies the whole current environment (``True``) or the named
        variables that are set; ``env`` is applied on top.
        """
        if self.inherit_env is True:
            inherited = dict(os.environ)
        else:
            inherited = {name: os.environ[name] for name in self.inherit_env or () if name in os.environ}
        if not inherited and self.env is None:
            return None
        return inherited | (self.env or {})

    @classmethod
    def from_command_line(cls, command_line: str) -> ServerSpec:
        parts = shlex.split(command_line)
        if not parts:
            raise ValueError("server command is empty")
        return cls(command=parts[0], args=parts[1:])

    @classmethod
    def from_target(
        cls,
        target: str,
        headers: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        inherit_env: tuple[str, ...] = (),
    ) -> ServerSpec:
        """Read a CLI server argument: an http(s) URL or a command line."""
        if is_url(target):
            if env or inherit_env:
                raise ValueError("--env applies only to server commands, not URLs")
            return cls(url=target.strip(), headers=dict(headers or {}))
        if headers:
            raise ValueError("headers apply only to http(s) server URLs")
        spec = cls.from_command_line(target)
        spec.env = dict(env) if env else None
        spec.inherit_env = tuple(inherit_env)
        return spec


def is_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))


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

    @property
    def protocol_version(self) -> str:
        """The MCP protocol version negotiated with the server."""
        return self._client.protocol_version

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
async def connect(
    spec: ServerSpec,
    show_server_logs: bool = False,
    faults: FaultInjector | None = None,
) -> AsyncIterator[Probe]:
    """Connect to one server and yield an initialized MCP Rig probe.

    With a fault injector, every message passes through a relay that can lose one
    tool response on purpose. Starting the server and the handshake share one
    deadline, ``spec.connect_timeout_s``; it is lifted once the server is connected.
    """
    connected = False
    with anyio.CancelScope(deadline=anyio.current_time() + spec.connect_timeout_s) as deadline:
        async with _connect(spec, show_server_logs, faults) as probe:
            deadline.deadline = math.inf
            connected = True
            yield probe
    if not connected and deadline.cancelled_caught:
        raise ConnectTimeoutError(
            f"the server did not answer the MCP handshake within {spec.connect_timeout_s:g} s; "
            "raise server.connect_timeout_s or --connect-timeout if it is still starting or installing"
        )


@asynccontextmanager
async def _connect(
    spec: ServerSpec,
    show_server_logs: bool,
    faults: FaultInjector | None,
) -> AsyncIterator[Probe]:
    if spec.is_remote:
        async with _connect_http(spec, faults) as probe:
            yield probe
        return
    params = StdioServerParameters(
        command=spec.command,
        args=spec.args,
        env=spec.child_env(),
        cwd=spec.cwd,
    )
    log_context = nullcontext(sys.stderr) if show_server_logs else open(os.devnull, "w", encoding="utf-8")
    with log_context as errlog:
        async with AsyncExitStack() as stack:
            transport = _with_faults(stdio_client(params, errlog=errlog), faults)
            stdout_noise = _StdoutNoise()
            logger = logging.getLogger(STDIO_LOGGER)
            logger.addHandler(stdout_noise)
            try:
                client = await stack.enter_async_context(Client(transport))
            except Exception as exc:
                explained = _explain_start_failure(exc, spec, stdout_noise.lines)
                if explained is None:
                    raise
                raise explained from exc
            finally:
                logger.removeHandler(stdout_noise)
            yield Probe(client)


class _StdoutNoise(logging.Handler):
    """Collect the lines a server wrote to stdout that were not JSON-RPC messages."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        exc = record.exc_info[1] if record.exc_info else None
        errors = getattr(exc, "errors", None)
        if callable(errors):
            for error in errors():
                if isinstance(error.get("input"), str):
                    self.lines.append(error["input"])
                    return


def _explain_start_failure(exc: BaseException, spec: ServerSpec, stdout_noise: list[str]) -> ServerStartError | None:
    """Turn an opaque startup failure into a message that says what to fix."""
    root = exc
    while isinstance(root, BaseExceptionGroup) and root.exceptions:
        root = root.exceptions[0]
    if isinstance(root, FileNotFoundError) and root.filename in (spec.command, None):
        return ServerStartError(f"command not found: {spec.command}")
    if stdout_noise:
        return ServerStartError(
            f"the server wrote non-JSON to stdout: {stdout_noise[0][:120]!r}; "
            "an MCP server must write only JSON-RPC messages to stdout and log to stderr"
        )
    if isinstance(root, MCPError) and root.code == METHOD_NOT_FOUND:
        return ServerStartError(
            "the command answered the MCP handshake with 'Method not found'; check that it starts an MCP server"
        )
    return None


@asynccontextmanager
async def _connect_http(spec: ServerSpec, faults: FaultInjector | None) -> AsyncIterator[Probe]:
    assert spec.url is not None
    http_errors: list[httpx2.Response] = []

    async def record_error(response: httpx2.Response) -> None:
        if response.status_code >= 400:
            http_errors.append(response)

    def http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            headers=headers,
            timeout=timeout or HTTP_TIMEOUT,
            auth=auth,
            event_hooks={"response": [record_error]},
        )

    async with AsyncExitStack() as stack:
        if spec.transport == "sse":
            transport = sse_client(spec.url, headers=spec.headers or None, httpx_client_factory=http_client)
        else:
            client = await stack.enter_async_context(http_client(headers=spec.headers))
            transport = streamable_http_client(spec.url, http_client=client)
        try:
            session = await stack.enter_async_context(Client(_with_faults(transport, faults)))
        except Exception as exc:
            if http_errors:
                response = http_errors[-1]
                raise ConnectionError(
                    f"HTTP {response.status_code} {response.reason_phrase} from {spec.url}"
                ) from exc
            raise
        yield Probe(session)


def _with_faults(transport: Any, faults: FaultInjector | None) -> Any:
    return transport if faults is None else inject_faults(transport, faults)
