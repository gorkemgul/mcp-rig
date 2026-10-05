"""A committed side effect whose response is lost must be judged by server state, not by the retry's JSON.

The ledger fixture records every invocation and every created record in a file
outside the server process. Each test arms a one-shot fault so the first call
commits and then never answers, retries the same logical call explicitly, and
reads the ledger directly to count side effects.
"""

import json
import sys
from pathlib import Path

import pytest
from mcp import MCPError
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

from mcp_rig.assertions import check
from mcp_rig.client import CallOutcome, ServerSpec, connect
from mcp_rig.runner import CaseStatus, ErrorCategory, run_suite
from mcp_rig.spec import load_suite

LEDGER_SERVER = Path(__file__).parent / "fixtures" / "ledger_server.py"
LOST_RESPONSE_TIMEOUT_S = 0.5
RECORD_EXPECTATIONS = {
    "json_path": {"name": "invoice"},
    "schema": {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    },
}


@pytest.fixture
def ledger(tmp_path) -> Path:
    return tmp_path / "ledger.json"


@pytest.fixture
def ledger_spec(ledger) -> ServerSpec:
    return ServerSpec(command=sys.executable, args=[str(LEDGER_SERVER)], env={"MCP_RIG_LEDGER": str(ledger)})


def arm_lost_response(ledger: Path, mode: str) -> None:
    ledger.with_name(ledger.name + ".fault").write_text(mode, encoding="utf-8")


def read_ledger(ledger: Path) -> dict:
    return json.loads(ledger.read_text(encoding="utf-8"))


def mcp_error_codes(exc: BaseException) -> set[int]:
    if isinstance(exc, BaseExceptionGroup):
        return {code for nested in exc.exceptions for code in mcp_error_codes(nested)}
    return {exc.code} if isinstance(exc, MCPError) else set()


async def lose_response_then_retry(spec: ServerSpec, ledger: Path, mode: str, tool: str, args: dict) -> CallOutcome:
    """Run one call whose response is lost after commit, then retry the identical call explicitly."""
    arm_lost_response(ledger, mode)
    if mode == "disconnect":
        answered = False
        with pytest.raises(BaseException) as caught:
            async with connect(spec) as probe:
                await probe.call(tool, args)
                answered = True
        assert not answered
        assert CONNECTION_CLOSED in mcp_error_codes(caught.value)
        assert len(read_ledger(ledger)["invocations"]) == 1
        async with connect(spec) as probe:
            return await probe.call(tool, args)

    async with connect(spec) as probe:
        with pytest.raises(MCPError) as caught:
            await probe.call(tool, args, timeout_s=LOST_RESPONSE_TIMEOUT_S)
        assert caught.value.code == REQUEST_TIMEOUT
        assert len(read_ledger(ledger)["invocations"]) == 1
        return await probe.call(tool, args)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["disconnect", "hang"])
async def test_unprotected_retry_duplicates_side_effect_that_response_checks_miss(ledger_spec, ledger, mode):
    retry = await lose_response_then_retry(ledger_spec, ledger, mode, "create_record", {"name": "invoice"})

    assert check(RECORD_EXPECTATIONS, retry) == []
    state = read_ledger(ledger)
    assert [item["tool"] for item in state["invocations"]] == ["create_record", "create_record"]
    assert state["records"] == [{"id": 1, "name": "invoice"}, {"id": 2, "name": "invoice"}]
    assert retry.json() == state["records"][-1]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["disconnect", "hang"])
async def test_idempotent_retry_with_same_key_applies_side_effect_once(ledger_spec, ledger, mode):
    args = {"name": "invoice", "idempotency_key": "invoice-2026-001"}

    retry = await lose_response_then_retry(ledger_spec, ledger, mode, "create_record_idempotent", args)

    assert check(RECORD_EXPECTATIONS, retry) == []
    state = read_ledger(ledger)
    assert [item["idempotency_key"] for item in state["invocations"]] == ["invoice-2026-001"] * 2
    assert state["records"] == [{"id": 1, "name": "invoice"}]
    assert retry.json() == {"id": 1, "name": "invoice"}


@pytest.mark.anyio
async def test_idempotency_is_keyed_by_tool_argument_not_jsonrpc_request_id(ledger_spec, ledger):
    retry = await lose_response_then_retry(
        ledger_spec, ledger, "hang", "create_record_idempotent", {"name": "invoice", "idempotency_key": "first"}
    )
    async with connect(ledger_spec) as probe:
        other = await probe.call("create_record_idempotent", {"name": "invoice", "idempotency_key": "second"})

    state = read_ledger(ledger)
    first_attempt, retry_attempt, _ = state["invocations"]
    assert first_attempt["request_id"] != retry_attempt["request_id"]
    assert retry.json() == {"id": 1, "name": "invoice"}
    assert other.json() == {"id": 2, "name": "invoice"}
    assert len(state["records"]) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "category"),
    [("hang", ErrorCategory.TIMEOUT), ("disconnect", ErrorCategory.TRANSPORT)],
)
async def test_yaml_suite_without_retry_skips_the_retry_case(ledger_spec, ledger, tmp_path, mode, category):
    suite_path = tmp_path / "retry.yaml"
    suite_path.write_text(
        f"""
server:
  command: {json.dumps(sys.executable)}
  args: [{json.dumps(str(LEDGER_SERVER))}]
  env: {{MCP_RIG_LEDGER: {json.dumps(str(ledger))}}}
tests:
  - name: create invoice
    call: create_record
    args: {{name: invoice}}
    timeout_s: {LOST_RESPONSE_TIMEOUT_S}
  - name: retry create invoice
    call: create_record
    args: {{name: invoice}}
""",
        encoding="utf-8",
    )
    arm_lost_response(ledger, mode)

    result = await run_suite(load_suite(suite_path))

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.SKIPPED]
    assert result.results[0].error.category is category
    assert result.results[1].skip_reason == "not run after infrastructure error in 'create invoice'"
    state = read_ledger(ledger)
    assert len(state["invocations"]) == 1
    assert state["records"] == [{"id": 1, "name": "invoice"}]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "tool", "extra_args", "records"),
    [
        ("disconnect", "create_record", "", 2),
        ("hang", "create_record", "", 2),
        ("disconnect", "create_record_idempotent", ", idempotency_key: inv-1", 1),
        ("hang", "create_record_idempotent", ", idempotency_key: inv-1", 1),
    ],
)
async def test_yaml_retry_and_verify_expose_the_side_effect_count(tmp_path, mode, tool, extra_args, records):
    ledger = tmp_path / "ledger.json"
    suite_path = tmp_path / "retry.yaml"
    suite_path.write_text(
        f"""
server:
  command: {json.dumps(sys.executable)}
  args: [{json.dumps(str(LEDGER_SERVER))}]
  env: {{MCP_RIG_LEDGER: {json.dumps(str(ledger))}}}
setup:
  - call: reset_records
tests:
  - name: arm
    call: arm_lost_response
    args: {{mode: {mode}}}
  - name: create invoice
    call: {tool}
    args: {{name: invoice{extra_args}}}
    timeout_s: {LOST_RESPONSE_TIMEOUT_S}
    retry: {{attempts: 1}}
    expect: {{json_path: {{name: invoice}}}}
    verify:
      - call: count_records
        args: {{name: invoice}}
        expect: {{json_path: {{records: 1, invocations: 2}}}}
""",
        encoding="utf-8",
    )

    result = await run_suite(load_suite(suite_path))

    item = result.results[1]
    assert item.attempts == 2
    assert item.retried_errors[0].category is (ErrorCategory.TIMEOUT if mode == "hang" else ErrorCategory.TRANSPORT)
    assert check(RECORD_EXPECTATIONS, item.outcome) == []
    if records == 1:
        assert item.status is CaseStatus.PASSED
    else:
        assert item.status is CaseStatus.FAILED
        assert item.failures == ["verify[0] count_records: json_path records: expected 1, got 2"]
    assert len(read_ledger(ledger)["records"]) == records
