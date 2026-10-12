"""The agent skill stays valid and matches what MCP Rig actually supports."""

import importlib.util
import re
import zipfile
from pathlib import Path

import pytest
import yaml

import mcp_rig
from mcp_rig.cli import main
from mcp_rig.lint import LINT_CODES
from mcp_rig.spec import KNOWN_EXPECT_KEYS, load_suite

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "mcp-rig"
SKILL_FILES = sorted(SKILL_DIR.rglob("*.md"))
COMMANDS = ("run", "check", "init", "coverage")


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no YAML frontmatter"
    return yaml.safe_load(match.group(1))


def command_options(command: str, capsys) -> set[str]:
    with pytest.raises(SystemExit):
        main([command, "--help"])
    return set(re.findall(r"--[a-z][a-z-]*", capsys.readouterr().out))


def code_blocks(language: str) -> list[str]:
    blocks = []
    for path in SKILL_FILES:
        blocks.extend(re.findall(rf"```{language}\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL))
    return blocks


def test_frontmatter_follows_the_skill_format():
    meta = frontmatter(SKILL_DIR / "SKILL.md")

    assert set(meta) == {"name", "description"}
    assert meta["name"] == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
    assert 0 < len(meta["description"]) <= 1024
    assert "Use when" in meta["description"]


def test_relative_links_point_at_files_in_the_skill():
    for path in SKILL_FILES:
        for target in re.findall(r"\]\(([^)#:]+)\)", path.read_text(encoding="utf-8")):
            assert (path.parent / target).is_file(), f"{path.name} links to missing {target}"


def test_every_command_option_the_skill_names_exists(capsys):
    options = {command: command_options(command, capsys) for command in COMMANDS}
    for path in SKILL_FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            for command, rest in re.findall(r"mcp-rig (run|check|init|coverage)\b(.*)", line):
                for option in re.findall(r"--[a-z][a-z-]*", rest):
                    assert option in options[command], f"{path.name}: `mcp-rig {command}` has no {option}"


def test_expectation_and_lint_tables_match_the_code():
    text = (SKILL_DIR / "references" / "suite-format.md").read_text(encoding="utf-8")
    expect_section = text.split("## Expectations", 1)[1].split("\n## ", 1)[0]
    named = set(re.findall(r"^\| `([a-z_]+)`", expect_section, re.MULTILINE))
    named |= set(re.findall(r"^\| `[a-z_]+` / `([a-z_]+)`", expect_section, re.MULTILINE))
    assert named == KNOWN_EXPECT_KEYS

    troubleshooting = (SKILL_DIR / "references" / "troubleshooting.md").read_text(encoding="utf-8")
    codes_line = troubleshooting.split("Codes:", 1)[1].split("\n\n", 1)[0]
    assert set(re.findall(r"`([a-z-]+)`", codes_line)) == LINT_CODES


def test_complete_suite_examples_load(tmp_path, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "example")
    suites = [block for block in code_blocks("yaml") if re.search(r"^tests:", block, re.MULTILINE)]
    assert suites, "the skill should show at least one complete suite"
    for index, block in enumerate(suites):
        path = tmp_path / f"example{index}.yaml"
        path.write_text(block, encoding="utf-8")
        load_suite(path)


def test_the_action_reference_names_the_current_version():
    for block in code_blocks("yaml"):
        for version in re.findall(r"gorkemgul/mcp-rig@v([0-9.]+)", block):
            assert version == mcp_rig.__version__


def test_package_script_builds_a_marketplace_zip(tmp_path):
    spec = importlib.util.spec_from_file_location("package_skill", ROOT / "scripts" / "package_skill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"

    names = module.package(first)
    module.package(second)

    assert first.read_bytes() == second.read_bytes()
    assert "mcp-rig/SKILL.md" in names
    assert all(name.startswith("mcp-rig/") for name in names)
    with zipfile.ZipFile(first) as archive:
        assert archive.read("mcp-rig/SKILL.md") == (SKILL_DIR / "SKILL.md").read_bytes()
