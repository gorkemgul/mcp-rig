import sys
from pathlib import Path

import pytest
from mcp import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, REQUEST_TIMEOUT

from mcp_rig.client import Probe, ServerSpec, connect

FIXTURE_TOOLS = {
    "add",
    "echo",
    "get_user",
    "slow",
    "get_env",
    "working_directory",
    "write_stderr",
    "undocumented",
}


@pytest.mark.anyio
async def test_connect_lists_normalized_fixture_tools(fixture_spec):
    async with connect(fixture_spec) as probe:
        tools = await probe.list_tools()

    assert FIXTURE_TOOLS == {tool.name for tool in tools}
    undocumented = next(tool for tool in tools if tool.name == "undocumented")
    assert undocumented.description == ""
    assert undocumented.input_schema["type"] == "object"
    assert undocumented.annotations == {}
    get_user = next(tool for tool in tools if tool.name == "get_user")
    assert get_user.annotations == {"readOnlyHint": True}


@pytest.mark.anyio
async def test_connect_lists_tools_from_every_discovery_page():
    paginated_server = Path(__file__).parent / "fixtures" / "paginated_server.py"
    spec = ServerSpec(command=sys.executable, args=[str(paginated_server)])

    async with connect(spec) as probe:
        tools = await probe.list_tools()

    assert [tool.name for tool in tools] == ["first", "second"]


@pytest.mark.anyio
async def test_successful_call_preserves_text_structure_and_latency(fixture_spec):
    async with connect(fixture_spec) as probe:
        outcome = await probe.call("add", {"a": 2, "b": 3})

    assert outcome.is_error is False
    assert outcome.text == "5"
    assert outcome.structured == {"result": 5}
    assert outcome.latency_ms > 0


@pytest.mark.anyio
async def test_tool_error_is_a_normalized_outcome(fixture_spec):
    async with connect(fixture_spec) as probe:
        outcome = await probe.call("get_user", {"user_id": 99})

    assert outcome.is_error is True
    assert "user 99 not found" in outcome.text


@pytest.mark.anyio
async def test_protocol_error_is_a_normalized_outcome():
    class ProtocolErrorClient:
        async def call_tool(self, *args, **kwargs):
            raise MCPError(INVALID_PARAMS, "Unknown tool: missing")

    outcome = await Probe(ProtocolErrorClient()).call("missing")

    assert outcome.is_error is True
    assert outcome.text == "Unknown tool: missing"
    assert outcome.structured is None
    assert outcome.protocol_error_code == INVALID_PARAMS


@pytest.mark.anyio
@pytest.mark.parametrize("code", [CONNECTION_CLOSED, REQUEST_TIMEOUT])
async def test_infrastructure_mcp_errors_are_not_normalized(code):
    error = MCPError(code, "infrastructure failure")

    class FailingClient:
        async def call_tool(self, *args, **kwargs):
            raise error

    with pytest.raises(MCPError) as caught:
        await Probe(FailingClient()).call("echo")

    assert caught.value is error


@pytest.mark.anyio
async def test_explicit_environment_and_working_directory_reach_server(fixture_server_path, tmp_path):
    spec = ServerSpec(
        command=sys.executable,
        args=[str(fixture_server_path)],
        env={"MCP_RIG_TEST_VALUE": "visible"},
        cwd=str(tmp_path),
    )

    async with connect(spec) as probe:
        env_outcome = await probe.call("get_env", {"name": "MCP_RIG_TEST_VALUE"})
        cwd_outcome = await probe.call("working_directory")

    assert env_outcome.text == "visible"
    assert Path(cwd_outcome.text) == tmp_path


@pytest.mark.anyio
async def test_missing_executable_remains_an_infrastructure_error():
    spec = ServerSpec(command="/definitely/missing/mcp-rig-server")

    with pytest.raises(OSError):
        async with connect(spec):
            pass


@pytest.mark.anyio
async def test_omitted_arguments_are_sent_as_an_empty_mapping(fixture_spec):
    async with connect(fixture_spec) as probe:
        outcome = await probe.call("working_directory")

    assert outcome.is_error is False
    assert outcome.text


@pytest.mark.anyio
async def test_server_logs_are_hidden_by_default_and_opt_in(fixture_spec, capfd):
    hidden_message = "mcp-rig-hidden-server-log"
    async with connect(fixture_spec) as probe:
        await probe.call("write_stderr", {"message": hidden_message})

    assert hidden_message not in capfd.readouterr().err

    visible_message = "mcp-rig-visible-server-log"
    async with connect(fixture_spec, show_server_logs=True) as probe:
        await probe.call("write_stderr", {"message": visible_message})

    assert visible_message in capfd.readouterr().err
