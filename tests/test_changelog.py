import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "changelog_section.py"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PINNED_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
}

CHANGELOG = """# Changelog

## 0.2.0

### Added

- A feature.

## 0.1.1

### Fixed

- A fix.

## 0.1.0

"""


def load_module():
    spec = importlib.util.spec_from_file_location("changelog_section", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["0.2.0", "v0.2.0"])
def test_extracts_one_section_without_its_heading(version):
    notes = load_module().extract_section(CHANGELOG, version)

    assert notes == "### Added\n\n- A feature.\n"


def test_section_ends_at_the_next_version_heading_not_a_subheading():
    assert load_module().extract_section(CHANGELOG, "0.1.1") == "### Fixed\n\n- A fix.\n"


@pytest.mark.parametrize(
    ("version", "message"),
    [("0.3.0", "no '## 0.3.0' section"), ("0.1.0", "'## 0.1.0' is empty"), ("0.2", "no '## 0.2' section")],
)
def test_missing_or_empty_sections_are_rejected(version, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        load_module().extract_section(CHANGELOG, version)


def test_cli_prints_notes_and_fails_without_a_section(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "v0.2.0", "--changelog", str(changelog)],
        capture_output=True,
        text=True,
        check=False,
    )
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "9.9.9", "--changelog", str(changelog)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert (ok.returncode, ok.stdout, ok.stderr) == (0, "### Added\n\n- A feature.\n", "")
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert "no '## 9.9.9' section" in missing.stderr


def test_project_version_has_release_notes():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

    assert load_module().extract_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)


def test_release_workflow_drafts_a_release_from_version_tags_only():
    workflow = yaml.load(RELEASE_WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["draft"]
    steps = {step["name"]: step for step in job["steps"]}

    assert workflow["on"] == {"push": {"tags": ["v*.*.*"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "write"}
    assert steps["Check out release tag"]["with"] == {"persist-credentials": "false"}
    assert steps["Validate release tag"]["run"] == 'python scripts/check_release_tag.py "$RELEASE_TAG"'
    assert "scripts/changelog_section.py" in steps["Extract release notes"]["run"]
    assert "--draft" in steps["Create draft release"]["run"]
    assert {step["uses"] for step in job["steps"] if "uses" in step} == PINNED_ACTIONS


def test_release_workflow_has_no_publication_or_secret_path():
    raw = RELEASE_WORKFLOW.read_text().lower()

    for forbidden in ("secrets.", "pypi", "twine", "workflow_dispatch", "pull_request", "id-token"):
        assert forbidden not in raw
    for line in raw.splitlines():
        if "run:" in line:
            assert "${{" not in line
