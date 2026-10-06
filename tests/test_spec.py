import re
from pathlib import Path

import pytest

from mcp_rig.spec import SpecError, load_suite


def write_suite(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "suite.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_string_server_and_defaults(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tests:
  - name: adds
    call: add
    args: {a: 1, b: 2}
    expect: {contains: "3"}
""",
        )
    )

    assert suite.path == tmp_path / "suite.yaml"
    assert suite.server.command == "python"
    assert suite.server.args == ["server.py"]
    assert suite.server.cwd == str(tmp_path.resolve())
    assert suite.cases[0].args == {"a": 1, "b": 2}


def test_tags_default_to_empty_immutable_sets(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tests:
  - name: pings
    call: ping
""",
        )
    )

    assert suite.tags == frozenset()
    assert suite.cases[0].tags == frozenset()


def test_parses_and_deduplicates_suite_and_case_tags(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tags: [playwright, playwright]
tests:
  - name: opens homepage
    call: browser_navigate
    tags: [smoke, browser, smoke]
""",
        )
    )

    assert suite.tags == frozenset({"playwright"})
    assert suite.cases[0].tags == frozenset({"smoke", "browser"})


@pytest.mark.parametrize(
    ("body", "location"),
    [
        (
            "server: python server.py\ntags: smoke\ntests: [{name: ping, call: ping}]",
            "suite.yaml: 'tags'",
        ),
        (
            "server: python server.py\ntags: null\ntests: [{name: ping, call: ping}]",
            "suite.yaml: 'tags'",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, tags: null}]",
            r"tests\[0\].*'tags'",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, tags: [smoke, 3]}]",
            r"tests\[0\].*'tags'",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, tags: [Smoke]}]",
            r"tests\[0\].*'tags'",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, tags: [-slow]}]",
            r"tests\[0\].*'tags'",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, tags: ['']}]",
            r"tests\[0\].*'tags'",
        ),
    ],
)
def test_rejects_invalid_tags_with_location(tmp_path, body, location):
    with pytest.raises(SpecError, match=location):
        load_suite(write_suite(tmp_path, body))


def test_accepts_snapshot_true_with_other_expectations(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tests:
  - name: user payload
    call: get_user
    expect: {snapshot: true, contains: Ada}
""",
        )
    )

    assert suite.cases[0].expect == {"snapshot": True, "contains": "Ada"}


@pytest.mark.parametrize("value", ["false", "null", "1", "'true'", "{}", "[]"])
def test_rejects_snapshot_values_other_than_boolean_true(tmp_path, value):
    body = f"""
server: python server.py
tests:
  - name: user payload
    call: get_user
    expect: {{snapshot: {value}}}
"""

    with pytest.raises(SpecError, match=r"tests\[0\].*'snapshot' must be true"):
        load_suite(write_suite(tmp_path, body))


def test_rejects_duplicate_snapshot_case_names(tmp_path):
    body = """
server: python server.py
tests:
  - {name: same, call: first, expect: {snapshot: true}}
  - {name: same, call: second, expect: {snapshot: true}}
"""

    with pytest.raises(SpecError, match="duplicate snapshot case name 'same'"):
        load_suite(write_suite(tmp_path, body))


def test_allows_duplicate_case_names_when_only_one_uses_snapshot(tmp_path):
    body = """
server: python server.py
tests:
  - {name: same, call: first, expect: {snapshot: true}}
  - {name: same, call: second}
"""

    suite = load_suite(write_suite(tmp_path, body))

    assert [case.name for case in suite.cases] == ["same", "same"]


def test_rejects_snapshot_suite_with_ambiguous_sidecar_owner(tmp_path):
    suite = write_suite(
        tmp_path,
        """
server: python server.py
tests:
  - {name: snapshots, call: echo, expect: {snapshot: true}}
""",
    )
    (tmp_path / "suite.yml").write_text("server: python other.py\ntests: []\n", encoding="utf-8")

    with pytest.raises(SpecError, match=r"suite\.yaml.*same snapshot sidecar"):
        load_suite(suite)


def test_loads_mapping_server_and_resolves_relative_cwd(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server:
  command: python -u
  args: [server.py]
  env: {MODE: test}
  cwd: fixtures
tests:
  - name: pings
    call: ping
""",
        )
    )

    assert suite.server.command == "python"
    assert suite.server.args == ["-u", "server.py"]
    assert suite.server.env == {"MODE": "test"}
    assert suite.server.cwd == str((tmp_path / "fixtures").resolve())
    assert suite.cases[0].args == {}
    assert suite.cases[0].expect == {}


def test_case_timeout_defaults_to_thirty_seconds(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tests:
  - name: pings
    call: ping
""",
        )
    )

    assert suite.cases[0].timeout_s == 30.0


def test_case_timeout_accepts_positive_number(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tests:
  - name: pings quickly
    call: ping
    timeout_s: 1.25
""",
        )
    )

    assert suite.cases[0].timeout_s == 1.25


@pytest.mark.parametrize("value", ["true", "0", "-1", ".nan", ".inf", "-.inf", "fast", "null"])
def test_case_timeout_rejects_non_positive_or_non_finite_number(tmp_path, value):
    with pytest.raises(SpecError, match="'timeout_s' must be a positive number"):
        load_suite(
            write_suite(
                tmp_path,
                f"""
server: python server.py
tests:
  - name: pings
    call: ping
    timeout_s: {value}
""",
            )
        )


def test_loads_advanced_expectations(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            r"""
server: python server.py
tests:
  - name: validates user
    call: get_user
    expect:
      not_contains: [password, secret]
      matches: 'user #[0-9]+'
      max_latency_ms: 500.5
      json_path:
        user.name: Ada
        user.roles.0: admin
      schema:
        type: object
        required: [user]
""",
        )
    )

    assert suite.cases[0].expect == {
        "not_contains": ["password", "secret"],
        "matches": r"user #[0-9]+",
        "max_latency_ms": 500.5,
        "json_path": {"user.name": "Ada", "user.roles.0": "admin"},
        "schema": {"type": "object", "required": ["user"]},
    }


def test_loads_schema_with_local_reference(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
tests:
  - name: validates identifier
    call: get_user
    expect:
      schema:
        $defs:
          identifier: {type: integer}
        $ref: '#/$defs/identifier'
""",
        )
    )

    assert suite.cases[0].expect["schema"]["$ref"] == "#/$defs/identifier"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("- not-a-mapping", "top level must be a mapping"),
        ("tests: []", "'server'"),
        ("server: python server.py", "'tests' must be a non-empty list"),
        ("server: python server.py\ntests: []", "'tests' must be a non-empty list"),
        ("server: python server.py\ntests: [{call: ping}]", "'name'"),
        ("server: python server.py\ntests: [{name: ping}]", "'call'"),
        ("server: python server.py\ntests: [{name: ping, call: ping, args: []}]", "'args'"),
        ("server: python server.py\ntests: [{name: ping, call: ping, expect: {other: true}}]", "unknown expect keys"),
        ("server: python server.py\ntests: [{name: ping, call: ping, expect: {is_error: nope}}]", "'is_error'"),
        ("server: python server.py\ntests: [{name: ping, call: ping, expect: {contains: []}}]", "'contains'"),
        ("server: {command: true}\ntests: [{name: ping, call: ping}]", "server.command"),
        ("server: '\"\"'\ntests: [{name: ping, call: ping}]", "server command is empty"),
        ("server: {command: '\"\"'}\ntests: [{name: ping, call: ping}]", "server command is empty"),
        ("server: {command: python, args: [server.py, 3]}\ntests: [{name: ping, call: ping}]", "server.args"),
        ("server: {command: python, env: {PORT: 3}}\ntests: [{name: ping, call: ping}]", "server.env"),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {not_contains: []}}]",
            "'not_contains' must be a string or non-empty list of strings",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {not_contains: [ok, 3]}}]",
            "'not_contains' must be a string or non-empty list of strings",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {matches: 3}}]",
            "'matches' must be a string",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {matches: '[unclosed'}}]",
            "invalid 'matches' regular expression",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {max_latency_ms: true}}]",
            "'max_latency_ms' must be a positive number",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {max_latency_ms: 0}}]",
            "'max_latency_ms' must be a positive number",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {max_latency_ms: .nan}}]",
            "'max_latency_ms' must be a positive number",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {max_latency_ms: .inf}}]",
            "'max_latency_ms' must be a positive number",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {json_path: {}}}]",
            "'json_path' must be a non-empty mapping with non-empty string keys",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {json_path: {1: value}}}]",
            "'json_path' must be a non-empty mapping with non-empty string keys",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {json_path: {'': value}}}]",
            "'json_path' must be a non-empty mapping with non-empty string keys",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {schema: []}}]",
            "'schema' must be a mapping",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {schema: {type: not-a-json-type}}}]",
            "invalid 'schema'",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {schema: {$ref: '#/$defs/missing'}}}]",
            "invalid 'schema' reference",
        ),
        (
            "server: python server.py\ntests: [{name: ping, call: ping, expect: {schema: {$ref: 'https://example.invalid/schema'}}}]",
            "external 'schema' references are not supported",
        ),
        ("server: [unclosed", "invalid YAML"),
    ],
)
def test_rejects_invalid_suites(tmp_path, body, message):
    with pytest.raises(SpecError, match=message):
        load_suite(write_suite(tmp_path, body))


def test_missing_file_is_a_spec_error(tmp_path):
    with pytest.raises(SpecError, match="could not read"):
        load_suite(tmp_path / "missing.yaml")


def test_loads_verify_retry_setup_teardown_and_after_timeout(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
after_timeout: continue
setup:
  - call: reset
teardown:
  - call: reset
    args: {hard: true}
    timeout_s: 2
tests:
  - name: creates
    call: create
    args: {name: a}
    retry: {attempts: 2}
    verify:
      - call: count
        args: {name: a}
        expect: {json_path: {records: 1}}
""",
        )
    )

    case = suite.cases[0]
    assert suite.after_timeout == "continue"
    assert [step.call for step in suite.setup] == ["reset"]
    assert suite.teardown[0].args == {"hard": True}
    assert suite.teardown[0].timeout_s == 2.0
    assert case.retry_attempts == 2
    assert case.verify[0].call == "count"
    assert case.verify[0].expect == {"json_path": {"records": 1}}


def test_supporting_steps_and_retry_default_to_off(tmp_path):
    suite = load_suite(write_suite(tmp_path, "server: python server.py\ntests:\n  - {name: a, call: add}\n"))

    assert (suite.setup, suite.teardown, suite.after_timeout) == ((), (), "stop")
    assert (suite.cases[0].verify, suite.cases[0].retry_attempts) == ((), 0)


CASE_PREFIX = "server: python server.py\ntests:\n  - name: a\n    call: add\n"
RETRY_SHAPE = "'retry' must be a mapping with 'attempts' and optional 'rerun_setup'"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (CASE_PREFIX + "    retry: 1\n", RETRY_SHAPE),
        (CASE_PREFIX + "    retry: {attempts: 1, delay: 2}\n", RETRY_SHAPE),
        (CASE_PREFIX + "    retry: {attempts: 0}\n", "'retry.attempts' must be a positive integer"),
        (CASE_PREFIX + "    retry: {attempts: true}\n", "'retry.attempts' must be a positive integer"),
        (CASE_PREFIX + "    verify: []\n", "(a): verify must be a non-empty list"),
        (CASE_PREFIX + "    verify: [count]\n", "(a): verify[0] must be a mapping"),
        (CASE_PREFIX + "    verify: [{args: {}}]\n", "verify[0]: 'call' must be a non-empty string"),
        (CASE_PREFIX + "    verify: [{call: c, name: x}]\n", "verify[0]: unknown keys: name"),
        (CASE_PREFIX + "    verify: [{call: c, expect: {snapshot: true}}]\n", "only supported on test cases"),
        (CASE_PREFIX + "    verify: [{call: c, expect: {nope: 1}}]\n", "verify[0]: unknown expect keys: nope"),
        (CASE_PREFIX + "    verify: [{call: c, timeout_s: 0}]\n", "verify[0]: 'timeout_s' must be a positive number"),
        ("setup: {call: reset}\n" + CASE_PREFIX, "setup must be a non-empty list"),
        ("teardown: [{call: ''}]\n" + CASE_PREFIX, "teardown[0]: 'call' must be a non-empty string"),
        ("after_timeout: retry\n" + CASE_PREFIX, "'after_timeout' must be one of: stop, continue"),
    ],
)
def test_rejects_invalid_supporting_steps_and_retry(tmp_path, body, message):
    with pytest.raises(SpecError, match=re.escape(message)):
        load_suite(write_suite(tmp_path, body))


def test_loads_rerun_setup_and_case_after_timeout(tmp_path):
    suite = load_suite(
        write_suite(
            tmp_path,
            """
server: python server.py
setup:
  - call: reset
tests:
  - name: a
    call: create
    retry: {attempts: 1, rerun_setup: true}
    after_timeout: continue
  - {name: b, call: echo}
""",
        )
    )

    first, second = suite.cases
    assert (first.retry_attempts, first.retry_rerun_setup, first.after_timeout) == (1, True, "continue")
    assert (second.retry_rerun_setup, second.after_timeout) == (False, None)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (CASE_PREFIX + "    retry: {attempts: 1, rerun_setup: yes please}\n", "'retry.rerun_setup' must be a boolean"),
        (CASE_PREFIX + "    retry: {attempts: 1, rerun_setup: true}\n", "but the suite has no setup"),
        (CASE_PREFIX + "    after_timeout: retry\n", "(a): 'after_timeout' must be one of: stop, continue"),
    ],
)
def test_rejects_invalid_rerun_setup_and_case_after_timeout(tmp_path, body, message):
    with pytest.raises(SpecError, match=re.escape(message)):
        load_suite(write_suite(tmp_path, body))


def test_loads_remote_server_from_url_string_or_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "abc")
    short = load_suite(write_suite(tmp_path, "server: https://mcp.example.com/mcp\ntests:\n  - {name: a, call: b}\n"))
    full = load_suite(
        write_suite(
            tmp_path,
            """
server:
  url: https://mcp.example.com/${MCP_TOKEN}/sse
  transport: sse
  headers:
    Authorization: "Bearer ${MCP_TOKEN}"
tests:
  - {name: a, call: b}
""",
        )
    )

    assert (short.server.url, short.server.transport, short.server.headers) == (
        "https://mcp.example.com/mcp",
        "streamable-http",
        {},
    )
    assert short.server.is_remote and short.server.cwd is None
    assert full.server.url == "https://mcp.example.com/abc/sse"
    assert full.server.transport == "sse"
    assert full.server.headers == {"Authorization": "Bearer abc"}


@pytest.mark.parametrize(
    ("server", "message"),
    [
        ("{url: https://x/mcp, command: python}", "'server.url' cannot be combined with command"),
        ("{url: ftp://x}", "'server.url' must be an http:// or https:// URL"),
        ("{url: https://x/mcp, headers: [a]}", "'server.headers' must map strings to strings"),
        ("{url: https://x/mcp, transport: websocket}", "'server.transport' must be one of: streamable-http, sse"),
        (
            "{url: https://x/mcp, headers: {Authorization: 'Bearer ${MCP_RIG_UNSET_VAR}'}}",
            "environment variable MCP_RIG_UNSET_VAR is not set",
        ),
    ],
)
def test_rejects_invalid_remote_servers(tmp_path, monkeypatch, server, message):
    monkeypatch.delenv("MCP_RIG_UNSET_VAR", raising=False)

    with pytest.raises(SpecError, match=re.escape(message)):
        load_suite(write_suite(tmp_path, f"server: {server}\ntests:\n  - {{name: a, call: b}}\n"))
