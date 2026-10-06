import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release_tag.py"
PYPROJECT = ROOT / "pyproject.toml"


def load_release_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_tag", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expected_tag_comes_from_project_version() -> None:
    release = load_release_module()

    assert release.expected_tag(PYPROJECT) == "v0.4.0"


def test_matching_release_tag_is_accepted() -> None:
    release = load_release_module()

    release.validate_release_tag("v0.4.0", PYPROJECT)


@pytest.mark.parametrize("tag", ["0.4.0", "v0.3.0", "", "release-v0.4.0"])
def test_nonmatching_release_tags_are_rejected(tag: str) -> None:
    release = load_release_module()

    with pytest.raises(ValueError, match=r"expected v0\.4\.0"):
        release.validate_release_tag(tag, PYPROJECT)


def test_cli_accepts_matching_release_tag() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v0.4.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "release tag accepted: v0.4.0"
    assert result.stderr == ""


def test_cli_reports_expected_and_received_tags_on_mismatch() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v0.3.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip() == "release tag mismatch: expected v0.4.0, received v0.3.0"


def test_supported_python_matrix_lints_release_scripts() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    with workflow_path.open() as workflow_file:
        workflow = yaml.load(workflow_file, Loader=yaml.BaseLoader)

    lint_step = next(step for step in workflow["jobs"]["test"]["steps"] if step["name"] == "Lint")
    assert lint_step["run"] == "ruff check src tests scripts"
