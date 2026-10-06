import json
import shlex
import sys
from pathlib import Path

import pytest

from mcp_rig.cli import main
from mcp_rig.client import ServerSpec, ToolInfo
from mcp_rig.coverage import ServerCoverage, called_tools, group_suites, measure, render_coverage
from mcp_rig.spec import Case, Step, Suite

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "fixture_server.py"


def suite(path, server, cases, setup=(), teardown=()):
    return Suite(Path(path), server, cases, setup=setup, teardown=teardown)


def test_called_tools_include_every_step_kind_once_in_first_seen_order():
    first = suite(
        "a.yaml",
        ServerSpec("srv"),
        [Case("one", "create", verify=(Step("count"),)), Case("two", "create")],
        setup=(Step("reset"),),
        teardown=(Step("cleanup"),),
    )
    second = suite("b.yaml", ServerSpec("srv"), [Case("three", "delete")])

    assert called_tools([first, second]) == ["reset", "create", "count", "cleanup", "delete"]


def test_suites_reaching_one_script_through_different_relative_paths_share_an_entry(tmp_path):
    (tmp_path / "server.py").write_text("", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    top = suite("top.yaml", ServerSpec("python", ["server.py"], cwd=str(tmp_path)), [Case("a", "add")])
    nested = suite(
        "nested/n.yaml", ServerSpec("python", ["../server.py"], cwd=str(tmp_path / "nested")), [Case("b", "echo")]
    )
    configured = suite(
        "c.yaml", ServerSpec("python", ["server.py"], env={"MODE": "x"}, cwd=str(tmp_path)), [Case("c", "add")]
    )
    remote = suite("r.yaml", ServerSpec(url="https://example.com/mcp"), [Case("d", "add")])

    groups = group_suites([top, nested, configured, remote])

    assert [[item.path.name for item in members] for _, members in groups] == [
        ["top.yaml", "n.yaml"],
        ["c.yaml"],
        ["r.yaml"],
    ]


@pytest.mark.anyio
async def test_measure_reports_covered_missing_and_unadvertised_tools_without_calling_them():
    listed = []

    async def list_tools(spec):
        listed.append(spec)
        return [ToolInfo(name, "", {}) for name in ("add", "echo", "get_user")]

    cases = [Case("adds", "add"), Case("typo", "ech0")]
    (result,) = await measure([suite("s.yaml", ServerSpec("srv"), cases)], list_tools)

    assert (result.covered, result.missing, result.unknown) == (["add"], ["echo", "get_user"], ["ech0"])
    assert round(result.percent) == 33
    assert len(listed) == 1


@pytest.mark.anyio
async def test_one_unreachable_server_does_not_hide_the_others():
    async def list_tools(spec):
        if spec.command == "broken":
            raise OSError("no such file")
        return [ToolInfo("add", "", {})]

    suites = [
        suite("a.yaml", ServerSpec("broken"), [Case("a", "add")]),
        suite("b.yaml", ServerSpec("ok"), [Case("b", "add")]),
    ]
    broken, ok = await measure(suites, list_tools)

    assert broken.error == "OSError: no such file"
    assert ok.percent == 100.0


def test_render_coverage_lists_gaps_and_a_total():
    results = [
        ServerCoverage("srv", [Path("a.yaml")], tools=["add", "echo"], called=["add", "typo"]),
        ServerCoverage("down", [Path("b.yaml")], error="OSError: refused"),
    ]

    text = render_coverage(results)

    assert "  1/2 tools covered (50%)" in text
    assert "  missing: echo" in text
    assert "  not advertised by the server: typo" in text
    assert "  ! could not list tools: OSError: refused" in text
    assert text.endswith("Total: 1/2 tools covered (50%); 1 server could not list tools")


def test_render_coverage_has_no_total_when_no_server_was_reachable():
    text = render_coverage([ServerCoverage("down", [Path("b.yaml")], error="OSError: refused")])

    assert text.endswith("Total: unavailable; no server could list its tools")
    assert "100%" not in text


def write_suite(tmp_path, name, calls):
    command = shlex.join([sys.executable, str(FIXTURE_SERVER)])
    cases = "".join(f"  - {{name: {call}, call: {call}}}\n" for call in calls)
    path = tmp_path / name
    path.write_text(f"server: {command!r}\ntests:\n{cases}", encoding="utf-8")
    return path


def test_coverage_cli_merges_suites_and_enforces_minimum(tmp_path, capsys):
    write_suite(tmp_path, "one.yaml", ["add", "echo"])
    write_suite(tmp_path, "two.yaml", ["get_user", "slow"])

    assert main(["coverage", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "4/8 tools covered (50%)" in out
    assert "missing: get_env, working_directory, write_stderr, undocumented" in out

    assert main(["coverage", str(tmp_path), "--min", "50"]) == 0
    capsys.readouterr()
    assert main(["coverage", str(tmp_path), "--min", "75"]) == 1
    assert "coverage 50% is below --min 75%" in capsys.readouterr().err


def test_coverage_cli_prints_json(tmp_path, capsys):
    write_suite(tmp_path, "one.yaml", ["add", "missing_tool"])

    assert main(["coverage", str(tmp_path), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    (server,) = report["servers"]
    assert (server["tools"], server["covered"], server["unknown"]) == (8, ["add"], ["missing_tool"])
    assert server["percent"] == 12.5
    assert report["errors"] == []


def test_coverage_cli_reports_configuration_and_connection_errors(tmp_path, capsys):
    write_suite(tmp_path, "good.yaml", ["add"])
    (tmp_path / "bad.yaml").write_text("tests: []\n", encoding="utf-8")
    (tmp_path / "down.yaml").write_text(
        "server: /definitely/missing/mcp-rig-server\ntests:\n  - {name: a, call: add}\n", encoding="utf-8"
    )

    assert main(["coverage", str(tmp_path)]) == 2

    captured = capsys.readouterr()
    assert "'server' must be a command string or mapping" in captured.err
    assert "could not list tools" in captured.out
    assert "1/8 tools covered" in captured.out


@pytest.mark.parametrize("value", ["-1", "101", "lots"])
def test_coverage_cli_rejects_invalid_minimums(value, capsys):
    with pytest.raises(SystemExit) as caught:
        main(["coverage", "suites/", "--min", value])

    assert caught.value.code == 2
