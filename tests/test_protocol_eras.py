"""MCP Rig works with servers that speak only one protocol era.

The 2026-07-28 revision has no ``initialize`` handshake; earlier revisions
start with it. The fixtures' ``MCP_RIG_FIXTURE_ERA`` setting makes them behave
like a server that knows only one era, so each test proves which path ran.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

from mcp_rig.cli import main
from mcp_rig.client import ServerSpec, connect
from mcp_rig.runner import CaseStatus, ErrorCategory, run_suite
from mcp_rig.spec import Case, Step, Suite

FIXTURES = Path(__file__).parent / "fixtures"
ERAS = ["legacy", "modern"]
TRANSPORTS = ["stdio", "streamable-http", "sse"]


def expected_versions(era: str) -> tuple[str, ...]:
    return HANDSHAKE_PROTOCOL_VERSIONS if era == "legacy" else MODERN_PROTOCOL_VERSIONS


def fixture_spec(http_server, transport: str, era: str, env: dict[str, str] | None = None) -> ServerSpec:
    if transport == "stdio":
        script = FIXTURES / ("ledger_server.py" if env else "fixture_server.py")
        return ServerSpec(sys.executable, [str(script)], env={"MCP_RIG_FIXTURE_ERA": era, **(env or {})})
    return ServerSpec(url=http_server(transport, era=era), transport=transport)


@pytest.mark.anyio
@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_each_era_negotiates_its_own_protocol_version(http_server, transport, era):
    async with connect(fixture_spec(http_server, transport, era)) as probe:
        tools = await probe.list_tools()
        outcome = await probe.call("add", {"a": 2, "b": 3})
        version = probe.protocol_version

    assert version in expected_versions(era)
    assert "add" in {tool.name for tool in tools}
    assert outcome.text == "5"


@pytest.mark.anyio
@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize(
    ("fault", "category"),
    [("drop_response", ErrorCategory.TIMEOUT), ("disconnect", ErrorCategory.TRANSPORT)],
)
async def test_faults_retry_and_verify_count_side_effects_in_each_era(tmp_path, era, fault, category):
    ledger = tmp_path / "ledger.json"
    spec = fixture_spec(None, "stdio", era, env={"MCP_RIG_LEDGER": str(ledger)})
    case = Case(
        "create",
        "create_record",
        {"name": "invoice"},
        timeout_s=0.5,
        retry_attempts=1,
        fault=fault,
        verify=(Step("count_records", {"name": "invoice"}, {"json_path": {"records": 2, "invocations": 2}}),),
    )

    result = await run_suite(Suite(tmp_path / "s.yaml", spec, [case]))

    item = result.results[0]
    assert item.status is CaseStatus.PASSED, item.failures or item.error
    assert [error.category for error in item.retried_errors] == [category]
    assert len(json.loads(ledger.read_text())["records"]) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("fault", ["drop_response", "disconnect"])
async def test_faults_work_over_streamable_http_in_each_era(http_server, tmp_path, era, fault):
    spec = fixture_spec(http_server, "streamable-http", era)
    cases = [
        Case("adds", "add", {"a": 2, "b": 3}, {"contains": "5"}, timeout_s=0.5, retry_attempts=1, fault=fault),
        Case("after", "echo", {"text": "ok"}),
    ]

    result = await run_suite(Suite(tmp_path / "s.yaml", spec, cases))

    assert [item.status for item in result.results] == [CaseStatus.PASSED, CaseStatus.PASSED]
    assert result.results[0].fault == fault


@pytest.mark.anyio
@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
async def test_a_timeout_can_continue_on_the_same_session_in_each_era(http_server, tmp_path, transport, era):
    spec = fixture_spec(http_server, transport, era)
    cases = [
        Case("slow", "slow", {"seconds": 2}, timeout_s=0.3),
        Case("after", "echo", {"text": "ok"}, {"contains": "ok"}),
    ]

    result = await run_suite(Suite(tmp_path / "s.yaml", spec, cases, after_timeout="continue"))

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.PASSED]
    assert result.results[0].error.category is ErrorCategory.TIMEOUT


@pytest.mark.parametrize("era", ERAS)
def test_cli_commands_work_in_each_era(http_server, tmp_path, capsys, era):
    url = http_server(era=era)
    suite_path = tmp_path / "suite.yaml"

    assert main(["check", url, "--ignore", "param-no-description"]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert header in {f"Protocol checks (MCP {version})" for version in expected_versions(era)}

    assert main(["init", url, "--output", str(suite_path)]) == 0
    generated = yaml.safe_load(suite_path.read_text())
    assert generated["server"] == {"url": url}

    case = {"name": "adds", "call": "add", "args": {"a": 1, "b": 1}, "expect": {"contains": "2"}}
    suite_path.write_text(yaml.safe_dump({"server": {"url": url}, "tests": [case]}))
    capsys.readouterr()
    assert main(["run", str(suite_path)]) == 0
    assert "1 passed, 0 failed" in capsys.readouterr().out

    assert main(["coverage", str(suite_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["servers"][0]["covered"] == ["add"]
