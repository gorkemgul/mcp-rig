"""Side-effecting MCP server for lost-response retry tests.

State lives in the JSON file named by ``MCP_RIG_LEDGER`` (default:
``.mcp-rig-ledger.json`` in the working directory) so it survives server
restarts and can be inspected without calling a tool. A one-shot fault is armed
by writing ``disconnect`` or ``hang`` to ``<ledger>.fault``, or by calling
``arm_lost_response`` from a suite: the next create call consumes it, commits
its side effect, and then never delivers a response.
"""

import json
import os
from pathlib import Path

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

LEDGER = Path(os.environ.get("MCP_RIG_LEDGER", ".mcp-rig-ledger.json"))
FAULT = LEDGER.with_name(LEDGER.name + ".fault")

server = MCPServer("mcp-rig-ledger-fixture")


def _load() -> dict:
    if not LEDGER.exists():
        return {"records": [], "invocations": [], "idempotency": {}}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _save(state: dict) -> None:
    pending = LEDGER.with_name(LEDGER.name + ".tmp")
    with pending.open("w", encoding="utf-8") as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, LEDGER)


def _take_fault() -> str | None:
    try:
        mode = FAULT.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    FAULT.unlink()
    return mode


async def _lose_response(mode: str | None) -> None:
    if mode == "disconnect":
        os._exit(0)
    if mode == "hang":
        await anyio.sleep_forever()


@server.tool()
async def create_record(name: str, ctx: Context) -> dict:
    """Create a record. Not idempotent: every call creates a new record."""
    fault = _take_fault()
    state = _load()
    state["invocations"].append({"tool": "create_record", "name": name, "request_id": str(ctx.request_id)})
    record = {"id": len(state["records"]) + 1, "name": name}
    state["records"].append(record)
    _save(state)
    await _lose_response(fault)
    return record


@server.tool()
async def create_record_idempotent(name: str, idempotency_key: str, ctx: Context) -> dict:
    """Create a record once per idempotency key; retries with the same key return the original record."""
    fault = _take_fault()
    state = _load()
    state["invocations"].append(
        {
            "tool": "create_record_idempotent",
            "name": name,
            "request_id": str(ctx.request_id),
            "idempotency_key": idempotency_key,
        }
    )
    if idempotency_key in state["idempotency"]:
        record_id = state["idempotency"][idempotency_key]
        record = next(item for item in state["records"] if item["id"] == record_id)
    else:
        record = {"id": len(state["records"]) + 1, "name": name}
        state["records"].append(record)
        state["idempotency"][idempotency_key] = record["id"]
    _save(state)
    await _lose_response(fault)
    return record


@server.tool()
def arm_lost_response(mode: str) -> str:
    """Make the next create call commit and then lose its response ("disconnect" or "hang")."""
    if mode not in {"disconnect", "hang"}:
        raise ValueError("mode must be 'disconnect' or 'hang'")
    FAULT.write_text(mode, encoding="utf-8")
    return f"armed {mode}"


@server.tool()
def count_records(name: str) -> dict:
    """Count stored records and create invocations for one record name, read from the ledger file."""
    state = _load()
    return {
        "records": sum(item["name"] == name for item in state["records"]),
        "invocations": sum(item.get("name") == name for item in state["invocations"]),
    }


@server.tool()
def reset_records() -> str:
    """Delete the ledger file and any armed fault so the next suite starts empty."""
    LEDGER.unlink(missing_ok=True)
    FAULT.unlink(missing_ok=True)
    return "reset"


if __name__ == "__main__":
    server.run()
