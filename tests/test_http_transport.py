"""Remote servers over Streamable HTTP and SSE behave like stdio servers."""

import pytest
import yaml

import mcp_rig.cli as cli_module
from mcp_rig.cli import main
from mcp_rig.client import ServerSpec, connect
from mcp_rig.runner import CaseStatus, ErrorCategory, run_suite
from mcp_rig.spec import Case, Suite, load_suite


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_remote_server_lists_and_calls_tools(http_server, transport):
    spec = ServerSpec(url=http_server(transport), transport=transport)

    async with connect(spec) as probe:
        tools = await probe.list_tools()
        outcome = await probe.call("add", {"a": 2, "b": 3})

    assert {"add", "echo", "get_user"} <= {tool.name for tool in tools}
    assert outcome.text == "5"


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_headers_reach_the_server_and_rejections_name_the_http_status(http_server, tmp_path, transport):
    url = http_server(transport, token="s3cret")

    async with connect(ServerSpec(url=url, transport=transport, headers={"Authorization": "Bearer s3cret"})) as probe:
        assert (await probe.call("echo", {"text": "ok"})).text == "ok"

    rejected = await run_suite(Suite(tmp_path / "s.yaml", ServerSpec(url=url, transport=transport), [Case("a", "add")]))

    assert rejected.suite_error.category is ErrorCategory.SETUP
    assert rejected.suite_error.exception_type == "ConnectionError"
    assert rejected.suite_error.message == f"HTTP 401 Unauthorized from {url}"


@pytest.mark.anyio
async def test_yaml_suite_reads_header_secrets_from_the_environment(http_server, tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", "s3cret")
    suite_path = tmp_path / "remote.yaml"
    suite_path.write_text(
        yaml.safe_dump(
            {
                "server": {"url": http_server(token="s3cret"), "headers": {"Authorization": "Bearer ${FIXTURE_TOKEN}"}},
                "tests": [{"name": "adds", "call": "add", "args": {"a": 1, "b": 1}, "expect": {"contains": "2"}}],
            }
        ),
        encoding="utf-8",
    )

    result = await run_suite(load_suite(suite_path))

    assert [item.status for item in result.results] == [CaseStatus.PASSED]
    assert "s3cret" not in suite_path.read_text()


@pytest.mark.anyio
async def test_unreachable_remote_server_is_a_setup_error(tmp_path):
    suite = Suite(tmp_path / "s.yaml", ServerSpec(url="http://127.0.0.1:9/mcp"), [Case("never", "echo")])

    result = await run_suite(suite)

    assert result.suite_error.category is ErrorCategory.SETUP
    assert [item.status for item in result.results] == [CaseStatus.SKIPPED]


def test_check_accepts_a_url_and_headers(http_server, capsys):
    url = http_server(token="s3cret")

    assert main(["check", url, "--header", "Authorization: Bearer s3cret"]) == 0
    assert "3/3 checks passed" in capsys.readouterr().out

    assert main(["check", url]) == 2
    err = capsys.readouterr().err
    assert "could not run server" in err
    assert cli_module.SERVER_LOGS_HINT not in err


def test_run_omits_server_logs_hint_for_remote_suites(tmp_path, capsys):
    suite = tmp_path / "remote.yaml"
    suite.write_text("server: http://127.0.0.1:9/mcp\ntests:\n  - {name: a, call: add}\n", encoding="utf-8")

    assert main(["run", str(suite)]) == 2
    assert cli_module.SERVER_LOGS_HINT not in capsys.readouterr().err


def test_init_writes_header_references_instead_of_secrets(http_server, tmp_path, capsys, monkeypatch):
    url = http_server(token="s3cret")
    output = tmp_path / "remote.yaml"

    assert main(["init", url, "--header", "Authorization: Bearer s3cret", "--output", str(output)]) == 0

    text = output.read_text()
    assert "s3cret" not in text
    assert "${MCP_AUTHORIZATION}" in text
    assert "header Authorization reads ${MCP_AUTHORIZATION}" in capsys.readouterr().out
    monkeypatch.setenv("MCP_AUTHORIZATION", "Bearer s3cret")
    assert main(["run", str(output), "--case", "add"]) == 0


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["check", "http://127.0.0.1:9/mcp", "--header", "no-colon"], "expected NAME:VALUE"),
        (["check", "python server.py", "--header", "A: b"], "headers apply only to http(s) server URLs"),
    ],
)
def test_invalid_header_usage_is_a_usage_error(argv, message, capsys):
    assert main(argv) == 2
    assert message in capsys.readouterr().err
