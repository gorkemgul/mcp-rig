"""Runner behavior for verify steps, opt-in retries, setup/teardown, and after_timeout."""

from contextlib import asynccontextmanager

import pytest
from mcp import MCPError
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

from mcp_rig.client import CallOutcome, ServerSpec
from mcp_rig.runner import CaseStatus, ErrorCategory, run_suite
from mcp_rig.spec import Case, Step, Suite

TIMEOUT = MCPError(REQUEST_TIMEOUT, "timed out")
CLOSED = MCPError(CONNECTION_CLOSED, "Connection closed")


class ScriptedServer:
    """Fake connections that replay scripted results and record every call per connection."""

    def __init__(self, script: dict[str, list]):
        self.script = {name: list(results) for name, results in script.items()}
        self.calls: list[tuple[int, str]] = []
        self.connections = 0

    def install(self, monkeypatch) -> None:
        server = self

        class FakeProbe:
            def __init__(self, connection: int):
                self.connection = connection

            async def call(self, name, args, timeout_s):
                server.calls.append((self.connection, name))
                queue = server.script.get(name, [])
                result = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else "ok")
                if isinstance(result, BaseException):
                    raise result
                return CallOutcome(False, result, None, 1.0)

        @asynccontextmanager
        async def fake_connect(spec, show_server_logs=False):
            server.connections += 1
            yield FakeProbe(server.connections)

        monkeypatch.setattr("mcp_rig.runner.connect", fake_connect)


def make_suite(tmp_path, cases, **options) -> Suite:
    return Suite(path=tmp_path / "suite.yaml", server=ServerSpec("unused"), cases=cases, **options)


@pytest.mark.anyio
async def test_verify_failures_are_prefixed_and_reported_with_case_failures(monkeypatch, tmp_path):
    server = ScriptedServer({"create": ["created"], "count": ["2"]})
    server.install(monkeypatch)
    case = Case(
        "creates",
        "create",
        expect={"contains": "missing"},
        verify=(Step("count", expect={"contains": "1"}),),
    )

    result = await run_suite(make_suite(tmp_path, [case]))

    item = result.results[0]
    assert item.status is CaseStatus.FAILED
    assert item.failures == [
        "contains: 'missing' not found in 'created'",
        "verify[0] count: contains: '1' not found in '2'",
    ]
    assert item.outcome.text == "created"
    assert [name for _, name in server.calls] == ["create", "count"]


@pytest.mark.anyio
async def test_verify_infrastructure_error_makes_the_case_an_error(monkeypatch, tmp_path):
    ScriptedServer({"count": [TIMEOUT]}).install(monkeypatch)
    cases = [Case("creates", "create", verify=(Step("count"),)), Case("later", "echo")]

    result = await run_suite(make_suite(tmp_path, cases))

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.SKIPPED]
    assert result.results[0].error.category is ErrorCategory.TIMEOUT
    assert result.results[0].error.message == "verify[0] count: timed out"


@pytest.mark.anyio
async def test_retry_after_timeout_reuses_the_session(monkeypatch, tmp_path):
    server = ScriptedServer({"create": [TIMEOUT, "created"]})
    server.install(monkeypatch)

    result = await run_suite(make_suite(tmp_path, [Case("creates", "create", retry_attempts=1)]))

    item = result.results[0]
    assert item.status is CaseStatus.PASSED
    assert item.attempts == 2
    assert item.retried_errors[0].category is ErrorCategory.TIMEOUT
    assert server.calls == [(1, "create"), (1, "create")]


@pytest.mark.anyio
async def test_retry_after_transport_error_reconnects_before_the_identical_call(monkeypatch, tmp_path):
    server = ScriptedServer({"create": [CLOSED, "created"]})
    server.install(monkeypatch)
    cases = [Case("creates", "create", retry_attempts=1), Case("later", "echo")]

    result = await run_suite(make_suite(tmp_path, cases))

    assert [item.status for item in result.results] == [CaseStatus.PASSED, CaseStatus.PASSED]
    assert result.results[0].retried_errors[0].category is ErrorCategory.TRANSPORT
    assert server.calls == [(1, "create"), (2, "create"), (2, "echo")]


@pytest.mark.anyio
async def test_exhausted_retries_report_the_last_error_and_every_earlier_one(monkeypatch, tmp_path):
    server = ScriptedServer({"create": [TIMEOUT, CLOSED, TIMEOUT]})
    server.install(monkeypatch)

    result = await run_suite(make_suite(tmp_path, [Case("creates", "create", retry_attempts=2)]))

    item = result.results[0]
    assert item.status is CaseStatus.ERROR
    assert item.error.category is ErrorCategory.TIMEOUT
    assert [error.category for error in item.retried_errors] == [ErrorCategory.TIMEOUT, ErrorCategory.TRANSPORT]
    assert item.attempts == 3
    assert server.connections == 2


@pytest.mark.anyio
async def test_assertion_failures_and_tool_errors_are_never_retried(monkeypatch, tmp_path):
    server = ScriptedServer({"create": ["unexpected"]})
    server.install(monkeypatch)

    result = await run_suite(
        make_suite(tmp_path, [Case("creates", "create", expect={"contains": "ok"}, retry_attempts=3)])
    )

    assert result.results[0].status is CaseStatus.FAILED
    assert result.results[0].attempts == 1
    assert len(server.calls) == 1


@pytest.mark.anyio
async def test_setup_and_teardown_wrap_cases_without_counting_as_cases(monkeypatch, tmp_path):
    server = ScriptedServer({})
    server.install(monkeypatch)
    suite = make_suite(
        tmp_path,
        [Case("first", "echo"), Case("second", "echo")],
        setup=(Step("reset"),),
        teardown=(Step("cleanup"),),
    )

    result = await run_suite(suite)

    assert [name for _, name in server.calls] == ["reset", "echo", "echo", "cleanup"]
    assert (result.passed, result.errors, result.ok) == (2, 0, True)


@pytest.mark.anyio
async def test_failed_setup_skips_cases_and_teardown(monkeypatch, tmp_path):
    server = ScriptedServer({"reset": ["refused"]})
    server.install(monkeypatch)
    suite = make_suite(
        tmp_path,
        [Case("first", "echo")],
        setup=(Step("reset", expect={"contains": "reset"}),),
        teardown=(Step("cleanup"),),
    )

    result = await run_suite(suite)

    assert [item.status for item in result.results] == [CaseStatus.SKIPPED]
    assert result.results[0].skip_reason == "suite setup failed"
    assert result.suite_error.category is ErrorCategory.SETUP
    assert result.suite_error.exception_type == "StepFailed"
    assert result.suite_error.message == "setup[0] reset: contains: 'reset' not found in 'refused'"
    assert [name for _, name in server.calls] == ["reset"]


@pytest.mark.anyio
async def test_teardown_reconnects_after_a_transport_error_and_reports_its_failure(monkeypatch, tmp_path):
    server = ScriptedServer({"echo": [CLOSED], "cleanup": [TIMEOUT]})
    server.install(monkeypatch)
    suite = make_suite(tmp_path, [Case("breaks", "echo"), Case("skipped", "echo")], teardown=(Step("cleanup"),))

    result = await run_suite(suite)

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.SKIPPED]
    assert server.calls == [(1, "echo"), (2, "cleanup")]
    assert result.suite_error.category is ErrorCategory.TEARDOWN
    assert result.suite_error.message == "teardown[0] cleanup: timed out"


@pytest.mark.anyio
async def test_after_timeout_continue_runs_later_cases_on_the_same_session(monkeypatch, tmp_path):
    server = ScriptedServer({"slow": [TIMEOUT]})
    server.install(monkeypatch)
    suite = make_suite(tmp_path, [Case("slow", "slow"), Case("later", "echo")], after_timeout="continue")

    result = await run_suite(suite)

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.PASSED]
    assert result.results[0].error.category is ErrorCategory.TIMEOUT
    assert server.calls == [(1, "slow"), (1, "echo")]


@pytest.mark.anyio
async def test_after_timeout_continue_still_stops_after_a_transport_error(monkeypatch, tmp_path):
    ScriptedServer({"breaks": [CLOSED]}).install(monkeypatch)
    suite = make_suite(tmp_path, [Case("breaks", "breaks"), Case("later", "echo")], after_timeout="continue")

    result = await run_suite(suite)

    assert [item.status for item in result.results] == [CaseStatus.ERROR, CaseStatus.SKIPPED]


@pytest.mark.anyio
async def test_rerun_setup_restores_state_on_the_new_connection_before_the_retry(monkeypatch, tmp_path):
    server = ScriptedServer({"create": [CLOSED, "created"]})
    server.install(monkeypatch)
    case = Case("creates", "create", retry_attempts=1, retry_rerun_setup=True)

    result = await run_suite(make_suite(tmp_path, [case], setup=(Step("reset"),)))

    assert result.results[0].status is CaseStatus.PASSED
    assert server.calls == [(1, "reset"), (1, "create"), (2, "reset"), (2, "create")]


@pytest.mark.anyio
async def test_rerun_setup_is_skipped_after_a_timeout_because_the_session_survives(monkeypatch, tmp_path):
    server = ScriptedServer({"create": [TIMEOUT, "created"]})
    server.install(monkeypatch)
    case = Case("creates", "create", retry_attempts=1, retry_rerun_setup=True)

    await run_suite(make_suite(tmp_path, [case], setup=(Step("reset"),)))

    assert server.calls == [(1, "reset"), (1, "create"), (1, "create")]


@pytest.mark.anyio
async def test_failed_setup_rerun_makes_the_case_an_error(monkeypatch, tmp_path):
    server = ScriptedServer({"create": [CLOSED, "created"], "reset": ["ok", "refused"]})
    server.install(monkeypatch)
    case = Case("creates", "create", retry_attempts=1, retry_rerun_setup=True)
    setup = (Step("reset", expect={"contains": "ok"}),)

    result = await run_suite(make_suite(tmp_path, [case, Case("later", "echo")], setup=setup))

    item = result.results[0]
    assert [r.status for r in result.results] == [CaseStatus.ERROR, CaseStatus.SKIPPED]
    assert item.error.category is ErrorCategory.SETUP
    assert item.error.message == "setup (after reconnect): setup[0] reset: contains: 'ok' not found in 'refused'"
    assert [name for _, name in server.calls] == ["reset", "create", "reset"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("suite_setting", "case_setting", "expected"),
    [
        ("stop", "continue", [CaseStatus.ERROR, CaseStatus.PASSED]),
        ("continue", "stop", [CaseStatus.ERROR, CaseStatus.SKIPPED]),
        ("continue", None, [CaseStatus.ERROR, CaseStatus.PASSED]),
    ],
)
async def test_case_after_timeout_overrides_the_suite_setting(
    monkeypatch, tmp_path, suite_setting, case_setting, expected
):
    ScriptedServer({"slow": [TIMEOUT]}).install(monkeypatch)
    cases = [Case("slow", "slow", after_timeout=case_setting), Case("later", "echo")]

    result = await run_suite(make_suite(tmp_path, cases, after_timeout=suite_setting))

    assert [item.status for item in result.results] == expected
