"""Serve the stdio fixture's tools over Streamable HTTP or SSE on an ephemeral port.

Usage: http_server.py [streamable-http|sse] [port]

The port defaults to an ephemeral one. Prints ``PORT <number>`` once the socket
accepts connections. When
``MCP_RIG_REQUIRED_TOKEN`` is set, every HTTP request must carry
``Authorization: Bearer <token>`` or it is rejected with 401.
"""

import asyncio
import os
import socket
import sys

import uvicorn
from fixture_server import server
from starlette.responses import PlainTextResponse


def build_app(transport: str):
    app = server.sse_app() if transport == "sse" else server.streamable_http_app()
    token = os.environ.get("MCP_RIG_REQUIRED_TOKEN")
    if not token:
        return app
    expected = f"Bearer {token}".encode()

    async def require_token(scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != expected:
            await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
            return
        await app(scope, receive, send)

    return require_token


def main() -> None:
    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 0))
    sock.listen(128)
    print(f"PORT {sock.getsockname()[1]}", flush=True)
    config = uvicorn.Config(build_app(transport), log_level="warning")
    asyncio.run(uvicorn.Server(config).serve(sockets=[sock]))


if __name__ == "__main__":
    main()
