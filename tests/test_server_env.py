"""Stdio servers receive the environment a suite or command line asks for."""

import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

from mcp_rig.cli import main
from mcp_rig.client import ServerSpec, connect
from mcp_rig.runner import CaseStatus, run_suite
from mcp_rig.spec import SpecError, load_suite

FIXTURE = Path(__file__).parent / "fixtures" / "fixture_server.py"


def write_suite(tmp_path: Path, server: object, tests: list | None = None) -> Path:
    path = tmp_path / "suite.yaml"
    tests = tests or [{"name": "adds", "call": "add", "args": {"a": 1, "b": 1}}]
    path.write_text(yaml.safe_dump({"server": server, "tests": tests}), encoding="utf-8")
    return path


def env_case(name: str, expected: str) -> dict:
    return {"name": f"reads {name}", "call": "get_env", "args": {"name": name}, "expect": {"matches": f"^{expected}$"}}


def test_suite_references_are_filled_in_command_args_env_and_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_PYTHON", sys.executable)
    monkeypatch.setenv("RIG_TOKEN", "s3cret")
    (tmp_path / "work").mkdir()
    path = write_suite(
        tmp_path,
        {
            "command": "${RIG_PYTHON}",
            "args": ["${SUITE_DIR}/server.py", "--token=${RIG_TOKEN}"],
            "env": {"TOKEN": "${RIG_TOKEN}", "DATA": "${SUITE_DIR}/data.json"},
            "cwd": "${SUITE_DIR}/work",
        },
    )

    server = load_suite(path).server

    suite_dir = str(tmp_path.resolve())
    assert server.command == sys.executable
    assert server.args == [f"{suite_dir}/server.py", "--token=s3cret"]
    assert server.env == {"TOKEN": "s3cret", "DATA": f"{suite_dir}/data.json"}
    assert server.cwd == f"{suite_dir}/work"


def test_command_string_references_are_filled_after_splitting(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_FLAG", "two words")
    path = write_suite(tmp_path, "python ${SUITE_DIR}/server.py --name ${RIG_FLAG}")

    server = load_suite(path).server

    assert server.args == [f"{tmp_path.resolve()}/server.py", "--name", "two words"]


@pytest.mark.parametrize(
    ("server", "where"),
    [
        ({"command": "python", "env": {"TOKEN": "${RIG_UNSET}"}}, "server.env.TOKEN"),
        ({"command": "python", "args": ["${RIG_UNSET}"]}, "server.args[0]"),
        ({"command": "python", "cwd": "${RIG_UNSET}"}, "server.cwd"),
        ("python ${RIG_UNSET}", "server"),
    ],
)
def test_unset_references_name_the_field(tmp_path, monkeypatch, server, where):
    monkeypatch.delenv("RIG_UNSET", raising=False)

    message = f"'{where}' uses ${{RIG_UNSET}}, but environment variable RIG_UNSET is not set"
    with pytest.raises(SpecError, match=re.escape(message)):
        load_suite(write_suite(tmp_path, server))


@pytest.mark.parametrize("value", ["yes", ["BAD-NAME"], [1], {"A": "b"}])
def test_inherit_env_must_be_a_boolean_or_names(tmp_path, value):
    with pytest.raises(SpecError, match="'server.inherit_env' must be true, false, or a list of variable names"):
        load_suite(write_suite(tmp_path, {"command": "python", "inherit_env": value}))


def test_inherit_env_is_rejected_for_remote_servers(tmp_path):
    with pytest.raises(SpecError, match="cannot be combined with inherit_env"):
        load_suite(write_suite(tmp_path, {"url": "http://127.0.0.1:1/mcp", "inherit_env": True}))


def test_child_env_merges_inherited_variables_under_explicit_ones(monkeypatch):
    monkeypatch.setenv("RIG_A", "from-shell")
    monkeypatch.setenv("RIG_B", "from-shell")
    monkeypatch.delenv("RIG_MISSING", raising=False)

    assert ServerSpec("python").child_env() is None
    assert ServerSpec("python", env={}).child_env() == {}
    named = ServerSpec("python", env={"RIG_B": "explicit"}, inherit_env=("RIG_A", "RIG_B", "RIG_MISSING"))
    assert named.child_env() == {"RIG_A": "from-shell", "RIG_B": "explicit"}
    everything = ServerSpec("python", inherit_env=True).child_env()
    assert everything["RIG_A"] == "from-shell"
    assert "PATH" in everything


@pytest.mark.anyio
async def test_a_suite_passes_explicit_and_inherited_variables_to_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_PROXY", "http://proxy.test:3128")
    monkeypatch.setenv("RIG_SECRET", "s3cret")
    monkeypatch.setenv("RIG_NOT_LISTED", "hidden")
    path = write_suite(
        tmp_path,
        {
            "command": sys.executable,
            "args": [str(FIXTURE)],
            "env": {"API_TOKEN": "${RIG_SECRET}"},
            "inherit_env": ["RIG_PROXY"],
        },
        [
            env_case("API_TOKEN", "s3cret"),
            env_case("RIG_PROXY", "http://proxy.test:3128"),
            env_case("RIG_NOT_LISTED", ""),
        ],
    )

    result = await run_suite(load_suite(path))

    assert [item.status for item in result.results] == [CaseStatus.PASSED] * 3, [r.failures for r in result.results]


@pytest.mark.anyio
async def test_cli_env_values_and_names_reach_a_server_command(monkeypatch):
    monkeypatch.setenv("RIG_PROXY", "http://proxy.test:3128")
    spec = ServerSpec.from_target(
        shlex.join([sys.executable, str(FIXTURE)]),
        env={"API_TOKEN": "s3cret"},
        inherit_env=("RIG_PROXY",),
    )

    async with connect(spec) as probe:
        token = await probe.call("get_env", {"name": "API_TOKEN"})
        proxy = await probe.call("get_env", {"name": "RIG_PROXY"})

    assert (token.text, proxy.text) == ("s3cret", "http://proxy.test:3128")


def test_init_writes_env_references_instead_of_values(tmp_path, capsys):
    output = tmp_path / "suite.yaml"
    command = shlex.join([sys.executable, str(FIXTURE)])

    assert main(["init", command, "--env", "API_TOKEN=s3cret", "--env", "HTTPS_PROXY", "--output", str(output)]) == 0

    text = output.read_text()
    assert "s3cret" not in text
    server = yaml.safe_load(text)["server"]
    assert server["env"] == {"API_TOKEN": "${API_TOKEN}"}
    assert server["inherit_env"] == ["HTTPS_PROXY"]
    assert "env API_TOKEN reads ${API_TOKEN}; set it before running the suite" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["check", "python s.py", "--env", "BAD-NAME=x"], "invalid --env 'BAD-NAME=x'; expected NAME or NAME=VALUE"),
        (["check", "http://127.0.0.1:1/mcp", "--env", "TOKEN=x"], "--env applies only to server commands, not URLs"),
        (["init", "http://127.0.0.1:1/mcp", "--env", "TOKEN"], "--env applies only to server commands, not URLs"),
    ],
)
def test_invalid_env_options_are_usage_errors(capsys, argv, message):
    assert main(argv) == 2
    assert message in capsys.readouterr().err
