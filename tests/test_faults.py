"""Generic lost-response faults work against unmodified servers over stdio and HTTP."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mcp_rig.client import CallOutcome, ServerSpec
from mcp_rig.faults import FaultInjector
from mcp_rig.junit import write_junit
from mcp_rig.report import render_suite
from mcp_rig.runner import CaseResult, CaseStatus, ErrorCategory, SuiteResult, run_suite
from mcp_rig.spec import Case, SpecError, Step, Suite, load_suite

LEDGER_SERVER = Path(__file__).parent / "fixtures" / "ledger_server.py"


def ledger_suite(tmp_path, cases):
    ledger = tmp_path / "ledger.json"
    spec = ServerSpec(sys.executable, [str(LEDGER_SERVER)], env={"MCP_RIG_LEDGER": str(ledger)})
    return Suite(tmp_path / "suite.yaml", spec, cases), ledger


def records(ledger):
    return json.loads(ledger.read_text())["records"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fault", "category"),
    [("drop_response", ErrorCategory.TIMEOUT), ("disconnect", ErrorCategory.TRANSPORT)],
)
@pytest.mark.parametrize(
    ("tool", "extra_args", "expected_records"),
    [("create_record", {}, 2), ("create_record_idempotent", {"idempotency_key": "inv-1"}, 1)],
)
async def test_lost_response_and_retry_expose_the_side_effect_count_on_an_unmodified_server(
    tmp_path, fault, category, tool, extra_args, expected_records
):
    case = Case(
        "create",
        tool,
        {"name": "invoice", **extra_args},
        timeout_s=0.5,
        retry_attempts=1,
        fault=fault,
        verify=(Step("count_records", {"name": "invoice"}, {"json_path": {"invocations": 2}}),),
    )
    suite, ledger = ledger_suite(tmp_path, [case])

    result = await run_suite(suite)

    item = result.results[0]
    assert item.status is CaseStatus.PASSED, item.failures or item.error
    assert item.fault == fault
    assert [error.category for error in item.retried_errors] == [category]
    assert len(records(ledger)) == expected_records


@pytest.mark.anyio
async def test_fault_without_retry_leaves_one_committed_record_and_an_error(tmp_path):
    case = Case("create", "create_record", {"name": "invoice"}, timeout_s=0.5, fault="drop_response")
    suite, ledger = ledger_suite(tmp_path, [case, Case("later", "count_records", {"name": "invoice"})])

    result = await run_suite(suite)

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.SKIPPED]
    assert result.results[0].fault == "drop_response"
    assert len(records(ledger)) == 1


@pytest.mark.anyio
async def test_faults_apply_to_the_first_attempt_only(tmp_path):
    suite, ledger = ledger_suite(
        tmp_path,
        [
            Case("faulted", "create_record", {"name": "a"}, timeout_s=0.5, retry_attempts=1, fault="drop_response"),
            Case("plain", "create_record", {"name": "b"}),
        ],
    )

    result = await run_suite(suite)

    assert [item.attempts for item in result.results] == [2, 1]
    assert [item.fault for item in result.results] == ["drop_response", None]
    assert [record["name"] for record in records(ledger)] == ["a", "a", "b"]


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["drop_response", "disconnect"])
async def test_faults_work_over_streamable_http(http_server, tmp_path, fault):
    spec = ServerSpec(url=http_server())
    case = Case("adds", "add", {"a": 2, "b": 3}, {"contains": "5"}, timeout_s=0.5, retry_attempts=1, fault=fault)

    result = await run_suite(Suite(tmp_path / "s.yaml", spec, [case, Case("after", "echo", {"text": "ok"})]))

    assert [item.status for item in result.results] == [CaseStatus.PASSED, CaseStatus.PASSED]
    assert result.results[0].fault == fault
    assert result.results[0].attempts == 2


def test_injector_rejects_unknown_faults_and_disarms():
    injector = FaultInjector()
    with pytest.raises(ValueError, match="unknown fault"):
        injector.arm("explode")
    injector.arm("drop_response")
    injector.disarm()
    assert (injector.mode, injector.request_id) == (None, None)


def test_suite_rejects_unknown_fault(tmp_path):
    path = tmp_path / "suite.yaml"
    path.write_text("server: python s.py\ntests:\n  - {name: a, call: b, fault: explode}\n", encoding="utf-8")

    with pytest.raises(SpecError, match=re.escape("'fault' must be one of: drop_response, disconnect")):
        load_suite(path)


def test_reports_name_the_injected_fault(tmp_path):
    result = SuiteResult(
        [
            CaseResult(
                "create",
                CaseStatus.PASSED,
                elapsed_ms=1.0,
                outcome=CallOutcome(False, "", None, 1.0),
                fault="drop_response",
            )
        ]
    )
    path = tmp_path / "report.xml"

    write_junit(path, "suite", result)

    assert "    fault injected on attempt 1: drop_response" in render_suite("s.yaml", result).splitlines()
    properties = {p.get("name"): p.get("value") for p in ET.parse(path).getroot().iter("property")}
    assert properties == {"mcp-rig.fault": "drop_response", "mcp-rig.attempts": "1"}
