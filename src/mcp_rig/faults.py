"""Lose a tool call's response on purpose, without changing the server.

A relay sits between the MCP client and its transport. When a fault is armed,
the relay notes the id of the next `tools/call` request, forwards that request so
the server executes it, and then intercepts the server's response:

- `drop_response` discards the response; the client times out and the session
  stays usable.
- `disconnect` discards the response and ends the connection; the client sees a
  closed connection.

Faults are one-shot: the relay disarms itself after intercepting one response.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import anyio
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCError, JSONRPCRequest, JSONRPCResponse

FAULTS = ("drop_response", "disconnect")


class FaultInjector:
    """Shared switch between the runner and the relay of each connection."""

    def __init__(self) -> None:
        self.mode: str | None = None
        self.request_id: Any = None
        self.injected = False

    def arm(self, mode: str) -> None:
        if mode not in FAULTS:
            raise ValueError(f"unknown fault {mode!r}")
        self.mode, self.request_id, self.injected = mode, None, False

    def disarm(self) -> None:
        self.mode, self.request_id = None, None

    def _note_request(self, message: Any) -> None:
        if (
            self.mode is not None
            and self.request_id is None
            and isinstance(message, SessionMessage)
            and isinstance(message.message, JSONRPCRequest)
            and message.message.method == "tools/call"
        ):
            self.request_id = message.message.id

    def _intercept(self, message: Any) -> str | None:
        """Return the fault to apply to this server message, or None to deliver it."""
        if (
            self.request_id is not None
            and isinstance(message, SessionMessage)
            and isinstance(message.message, JSONRPCResponse | JSONRPCError)
            and message.message.id == self.request_id
        ):
            mode = self.mode
            self.injected = True
            self.disarm()
            return mode
        return None


@asynccontextmanager
async def inject_faults(
    transport: AbstractAsyncContextManager[tuple[Any, Any]],
    injector: FaultInjector,
) -> AsyncIterator[tuple[Any, Any]]:
    """Wrap a transport so the injector can drop or cut off one tool response."""
    async with transport as (server_read, server_write):
        to_client_send, to_client_receive = anyio.create_memory_object_stream[Any](math.inf)
        from_client_send, from_client_receive = anyio.create_memory_object_stream[Any](math.inf)

        async def forward_requests() -> None:
            async with from_client_receive:
                async for message in from_client_receive:
                    injector._note_request(message)
                    await server_write.send(message)

        async def forward_responses() -> None:
            async with to_client_send:
                async for message in server_read:
                    fault = injector._intercept(message)
                    if fault == "disconnect":
                        return
                    if fault is None:
                        await to_client_send.send(message)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(forward_requests)
            tasks.start_soon(forward_responses)
            try:
                yield to_client_receive, from_client_send
            finally:
                tasks.cancel_scope.cancel()
