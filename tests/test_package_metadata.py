import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_project_metadata() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        return tomllib.load(pyproject_file)["project"]


def test_package_identity_and_python_support_stay_stable() -> None:
    project = load_project_metadata()

    assert project["name"] == "mcp-rig"
    assert project["version"] == "0.3.0"
    assert project["requires-python"] == ">=3.11"


def test_package_declares_complete_pypi_metadata() -> None:
    project = load_project_metadata()

    assert project["license-files"] == ["LICENSE"]
    assert set(project["keywords"]) >= {"mcp", "testing", "cli", "developer-tools"}
    assert set(project["classifiers"]) >= {
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    }
    assert project["urls"] == {
        "Repository": "https://github.com/gorkemgul/mcp-rig",
        "Issues": "https://github.com/gorkemgul/mcp-rig/issues",
    }


def test_readme_documents_end_user_installation() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "pipx install mcp-rig" in readme
    assert "pip install mcp-rig" in readme
    assert "PyPI publication arrives in a later increment" not in readme
