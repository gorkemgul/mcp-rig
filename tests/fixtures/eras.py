"""Restrict a fixture server to one MCP protocol era.

The SDK serves both eras on one connection: the 2026-07-28 era, which starts
with ``server/discover`` and has no ``initialize`` handshake, and the earlier
handshake era. Real servers often speak only one of them. Setting
``MCP_RIG_FIXTURE_ERA`` makes a fixture behave like such a server:

- ``legacy`` answers ``server/discover`` with "Method not found", as a server
  built before 2026-07-28 does, so clients fall back to ``initialize``.
- ``modern`` rejects ``initialize`` with "Unsupported protocol version", as a
  server that serves only 2026-07-28 does.

Unset, the fixture serves both eras.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import anyio
from mcp.shared.message import SessionMessage
from mcp_types import (
    METHOD_NOT_FOUND,
    UNSUPPORTED_PROTOCOL_VERSION,
    ErrorData,
    JSONRPCError,
    JSONRPCRequest,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from starlette.responses import JSONResponse

ERA_VARIABLE = "MCP_RIG_FIXTURE_ERA"
ERAS = ("legacy", "modern")


def requested_era() -> str | None:
    era = os.environ.get(ERA_VARIABLE) or None
    if era is not None and era not in ERAS:
        raise SystemExit(f"{ERA_VARIABLE} must be one of {', '.join(ERAS)}, not {era!r}")
    return era


def refusal(era: str | None, method: str) -> ErrorData | None:
    """The error this era's server returns for ``method``, or None to serve it."""
    if era == "legacy" and method == "server/discover":
        return ErrorData(code=METHOD_NOT_FOUND, message="Method not found")
    if era == "modern" and method == "initialize":
        return ErrorData(
            code=UNSUPPORTED_PROTOCOL_VERSION,
            message="Unsupported protocol version",
            data={"supported": list(MODERN_PROTOCOL_VERSIONS)},
        )
    return None


def restrict_streams(server: Any, era: str | None) -> None:
    """Filter requests for the stdio and SSE transports, which call the low-level ``run``."""
    if era is None:
        return
    lowlevel = server._lowlevel_server
    run = lowlevel.run

    async def restricted_run(read_stream: Any, write_stream: Any, *args: Any, **kwargs: Any) -> None:
        send, receive = anyio.create_memory_object_stream[Any](math.inf)

        async def filter_requests() -> None:
            async with send:
                async for message in read_stream:
                    request = message.message if isinstance(message, SessionMessage) else None
                    error = refusal(era, request.method) if isinstance(request, JSONRPCRequest) else None
                    if error is None:
                        await send.send(message)
                    else:
                        reply = JSONRPCError(jsonrpc="2.0", id=request.id, error=error)
                        await write_stream.send(SessionMessage(reply))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(filter_requests)
            await run(receive, write_stream, *args, **kwargs)
            tasks.cancel_scope.cancel()

    lowlevel.run = restricted_run


def restrict_http(app: Any, era: str | None) -> Any:
    """Filter requests for Streamable HTTP, whose session manager bypasses ``run``."""
    if era is None:
        return app

    async def restricted(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await app(scope, receive, send)
            return
        chunks: list[dict[str, Any]] = []
        while True:
            chunk = await receive()
            chunks.append(chunk)
            if chunk["type"] != "http.request" or not chunk.get("more_body"):
                break
        body = b"".join(chunk.get("body", b"") for chunk in chunks)
        try:
            request = json.loads(body)
        except ValueError:
            request = None
        error = refusal(era, request.get("method", "")) if isinstance(request, dict) else None
        if error is not None:
            reply = JSONRPCError(jsonrpc="2.0", id=request.get("id"), error=error)
            await JSONResponse(reply.model_dump(mode="json", exclude_none=True))(scope, receive, send)
            return
        replay = iter(chunks)

        async def replayed() -> dict[str, Any]:
            return next(replay, None) or await receive()

        await app(scope, replayed, send)

    return restricted
