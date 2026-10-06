import shlex
import sys
from pathlib import Path

import pytest
import yaml

import mcp_rig.cli as cli_module
from mcp_rig.cli import main
from mcp_rig.client import ServerSpec, ToolInfo
from mcp_rig.scaffold import SIDE_EFFECT_TAG, placeholder_arguments, placeholder_value, scaffold_suite
from mcp_rig.spec import load_suite

FIXTURES = Path(__file__).parent / "fixtures"
CITY_SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def tool(name, description="Do something useful for the caller.", schema=None, annotations=None):
    return ToolInfo(name, description, schema or {"type": "object"}, annotations or {})


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, ""),
        ({"type": "integer"}, 0),
        ({"type": "number"}, 0),
        ({"type": "boolean"}, False),
        ({"type": "array", "items": {"type": "string"}}, []),
        ({"type": "null"}, None),
        ({"type": "string", "default": "utc"}, "utc"),
        ({"type": "string", "enum": ["fast", "slow"]}, "fast"),
        ({"const": 3}, 3),
        ({"type": ["null", "integer"]}, 0),
        ({"anyOf": [{"type": "null"}, {"type": "boolean"}]}, False),
        ({"oneOf": [{"type": "string", "enum": ["a"]}]}, "a"),
        (
            {"type": "object", "properties": {"x": {"type": "integer"}, "y": {}}, "required": ["x"]},
            {"x": 0},
        ),
        ({"$ref": "#/$defs/unknown"}, None),
        ("not a schema", None),
    ],
)
def test_placeholder_value_follows_the_schema(schema, expected):
    assert placeholder_value(schema) == expected


def test_placeholder_arguments_fill_only_required_parameters():
    schema = {
        "type": "object",
        "properties": {"city": {"type": "string"}, "units": {"type": "string", "default": "metric"}},
        "required": ["city"],
    }

    assert placeholder_arguments(schema) == {"city": ""}
    assert placeholder_arguments({"type": "object"}) == {}


def test_scaffolded_suite_loads_and_tags_side_effecting_tools(tmp_path):
    tools = [
        tool("get_weather", schema=CITY_SCHEMA),
        tool("create_record"),
        tool("purge", annotations={"destructiveHint": True}),
        tool("lookup", annotations={"readOnlyHint": True}),
        tool("weird: name", description=""),
    ]
    path = tmp_path / "suite.yaml"
    path.write_text(scaffold_suite(ServerSpec("python", ["server.py"]), tools, suite_path=path), encoding="utf-8")

    suite = load_suite(path)

    assert suite.server.command == "python"
    assert suite.server.args == ["server.py"]
    assert [case.call for case in suite.cases] == ["get_weather", "create_record", "purge", "lookup", "weird: name"]
    assert suite.cases[0].args == {"city": ""}
    assert [case.tags for case in suite.cases] == [
        frozenset(),
        frozenset({SIDE_EFFECT_TAG}),
        frozenset({SIDE_EFFECT_TAG}),
        frozenset(),
        frozenset(),
    ]
    assert all(case.expect == {} for case in suite.cases)


def test_scaffolded_suite_documents_each_case_with_a_short_description():
    long_description = "word " * 40
    text = scaffold_suite(ServerSpec("srv"), [tool("one", description=long_description), tool("two", description="")])

    comments = [line.strip() for line in text.splitlines() if line.strip().startswith("#")]
    assert comments[2].endswith("…") and len(comments[2]) <= 102
    assert comments.count("# TODO: replace placeholder arguments and add expectations.") == 2


def test_suite_in_another_directory_keeps_the_server_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "tests" / "mcp" / "suite.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(scaffold_suite(ServerSpec("python", ["server.py"]), [tool("ping")], suite_path=path))

    suite = load_suite(path)

    assert Path(suite.server.cwd) == tmp_path.resolve()
    assert "cwd: ../.." in path.read_text()


def fixture_command() -> str:
    return shlex.join([sys.executable, str(FIXTURES / "fixture_server.py")])


def test_init_writes_a_suite_that_runs(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "suite.yaml"

    assert main(["init", fixture_command(), "--output", str(output)]) == 0
    assert "wrote 8 cases" in capsys.readouterr().out

    assert main(["run", str(output), "--case", "add", "--case", "echo"]) == 0


def test_init_prints_to_stdout_without_output(capsys):
    assert main(["init", fixture_command()]) == 0

    suite = yaml.safe_load(capsys.readouterr().out)
    assert [case["call"] for case in suite["tests"]][:2] == ["add", "echo"]


def test_init_marks_ledger_create_tools_as_side_effecting(capsys):
    assert main(["init", shlex.join([sys.executable, str(FIXTURES / "ledger_server.py")])]) == 0

    cases = {case["call"]: case for case in yaml.safe_load(capsys.readouterr().out)["tests"]}
    assert cases["create_record"]["tags"] == [SIDE_EFFECT_TAG]
    assert cases["create_record_idempotent"]["tags"] == [SIDE_EFFECT_TAG]
    assert "tags" not in cases["count_records"]


def test_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    output = tmp_path / "suite.yaml"
    output.write_text("keep me", encoding="utf-8")

    assert main(["init", fixture_command(), "--output", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert output.read_text() == "keep me"

    assert main(["init", fixture_command(), "--output", str(output), "--force"]) == 0
    assert output.read_text().startswith("# Generated by `mcp-rig init`")


def test_init_reports_an_unstartable_server(capsys):
    assert main(["init", "/definitely/missing/mcp-rig-server"]) == 2

    err = capsys.readouterr().err
    assert "could not run server: ServerStartError: command not found" in err
    assert cli_module.SERVER_LOGS_HINT not in err


def test_init_fails_when_the_server_lists_no_tools(monkeypatch, capsys):
    async def no_tools(spec, show_server_logs):
        return []

    monkeypatch.setattr(cli_module, "_list_tools", no_tools)

    assert main(["init", "srv"]) == 1
    assert "lists no tools" in capsys.readouterr().err
