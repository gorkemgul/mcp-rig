import io
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_distribution_contents.py"

WHEEL_MEMBERS = (
    "mcp_rig/__init__.py",
    "mcp_rig-0.1.1.dist-info/METADATA",
    "mcp_rig-0.1.1.dist-info/WHEEL",
    "mcp_rig-0.1.1.dist-info/entry_points.txt",
    "mcp_rig-0.1.1.dist-info/licenses/LICENSE",
    "mcp_rig-0.1.1.dist-info/RECORD",
)

SDIST_MEMBERS = (
    "mcp_rig-0.1.1/LICENSE",
    "mcp_rig-0.1.1/PKG-INFO",
    "mcp_rig-0.1.1/README.md",
    "mcp_rig-0.1.1/pyproject.toml",
    "mcp_rig-0.1.1/src/mcp_rig/__init__.py",
)


def make_wheel(
    path: Path,
    members: tuple[str, ...] = WHEEL_MEMBERS,
    links: tuple[str, ...] = (),
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            archive.writestr(member, b"fixture")
        for member in links:
            info = zipfile.ZipInfo(member)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"AGENTS.md")


def make_sdist(
    path: Path,
    members: tuple[str, ...] = SDIST_MEMBERS,
    links: tuple[tuple[str, bytes], ...] = (),
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            content = b"fixture"
            info = tarfile.TarInfo(member)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for member, link_type in links:
            info = tarfile.TarInfo(member)
            info.type = link_type
            info.linkname = "AGENTS.md"
            archive.addfile(info)


def run_checker(*archives: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(archive) for archive in archives)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_minimal_wheel_and_sdist_are_accepted(tmp_path: Path) -> None:
    wheel = tmp_path / "mcp_rig-0.1.1-py3-none-any.whl"
    sdist = tmp_path / "mcp_rig-0.1.1.tar.gz"
    make_wheel(wheel)
    make_sdist(sdist)

    result = run_checker(wheel, sdist)

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"distribution contents accepted: {wheel}",
        f"distribution contents accepted: {sdist}",
    ]
    assert result.stderr == ""


def test_wheel_rejects_files_outside_package_and_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "mcp_rig-0.1.1-py3-none-any.whl"
    make_wheel(wheel, WHEEL_MEMBERS + ("docs/releasing.md", "AGENTS.md"))

    result = run_checker(wheel)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"unexpected files in {wheel}:",
        "- AGENTS.md",
        "- docs/releasing.md",
    ]


def test_sdist_rejects_repository_only_files(tmp_path: Path) -> None:
    sdist = tmp_path / "mcp_rig-0.1.1.tar.gz"
    make_sdist(
        sdist,
        SDIST_MEMBERS
        + (
            "mcp_rig-0.1.1/.superpowers/sdd/session.md",
            "mcp_rig-0.1.1/AGENTS.md",
            "mcp_rig-0.1.1/docs/releasing.md",
            "mcp_rig-0.1.1/graphify-out/graph.json",
        ),
    )

    result = run_checker(sdist)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"unexpected files in {sdist}:",
        "- .superpowers/sdd/session.md",
        "- AGENTS.md",
        "- docs/releasing.md",
        "- graphify-out/graph.json",
    ]


def test_wheel_rejects_foreign_metadata_tree(tmp_path: Path) -> None:
    wheel = tmp_path / "mcp_rig-0.1.1-py3-none-any.whl"
    foreign_members = tuple(
        member.replace("mcp_rig-0.1.1.dist-info", "mcp_rig-9.9.9.dist-info")
        for member in WHEEL_MEMBERS
    )
    make_wheel(wheel, foreign_members)

    result = run_checker(wheel)

    assert result.returncode == 1
    assert "mcp_rig-9.9.9.dist-info/METADATA" in result.stderr


def test_wheel_rejects_foreign_directory_entries(tmp_path: Path) -> None:
    wheel = tmp_path / "mcp_rig-0.1.1-py3-none-any.whl"
    make_wheel(wheel, WHEEL_MEMBERS + ("docs/",))

    result = run_checker(wheel)

    assert result.returncode == 1
    assert "- docs/" in result.stderr


def test_wheel_rejects_symlink_disguised_as_directory(tmp_path: Path) -> None:
    wheel = tmp_path / "mcp_rig-0.1.1-py3-none-any.whl"
    make_wheel(wheel, links=("mcp_rig/link/",))

    result = run_checker(wheel)

    assert result.returncode == 1
    assert "- mcp_rig/link/" in result.stderr


@pytest.mark.parametrize("link_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_sdist_rejects_links(tmp_path: Path, link_type: bytes) -> None:
    sdist = tmp_path / "mcp_rig-0.1.1.tar.gz"
    make_sdist(
        sdist,
        links=(("mcp_rig-0.1.1/src/mcp_rig/link.py", link_type),),
    )

    result = run_checker(sdist)

    assert result.returncode == 1
    assert "- src/mcp_rig/link.py" in result.stderr


@pytest.mark.parametrize(
    ("archive_name", "unsafe_member"),
    [
        ("mcp_rig-0.1.1-py3-none-any.whl", "mcp_rig/subdir\\payload.py"),
        ("mcp_rig-0.1.1.tar.gz", "mcp_rig-0.1.1/src/mcp_rig/subdir\\payload.py"),
    ],
)
def test_archives_reject_backslash_paths(
    tmp_path: Path, archive_name: str, unsafe_member: str
) -> None:
    archive = tmp_path / archive_name
    if archive_name.endswith(".whl"):
        make_wheel(archive, WHEEL_MEMBERS + (unsafe_member,))
    else:
        make_sdist(archive, SDIST_MEMBERS + (unsafe_member,))

    result = run_checker(archive)

    assert result.returncode == 1
    assert unsafe_member.split("/", 1)[-1] in result.stderr


@pytest.mark.parametrize(
    "archive_name", ["mcp_rig-0.1.1-py3-none-any.whl", "mcp_rig-0.1.1.tar.gz"]
)
def test_empty_archives_are_rejected(tmp_path: Path, archive_name: str) -> None:
    archive = tmp_path / archive_name
    if archive_name.endswith(".whl"):
        make_wheel(archive, ())
    else:
        make_sdist(archive, ())

    result = run_checker(archive)

    assert result.returncode == 1
    assert "missing required distribution content" in result.stderr
