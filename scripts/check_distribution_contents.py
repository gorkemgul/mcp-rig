"""Reject repository-only files from MCP Rig distribution archives."""

from __future__ import annotations

import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def _is_safe_relative_path(member: str) -> bool:
    path = PurePosixPath(member)
    return bool(path.parts) and "\\" not in member and not path.is_absolute() and ".." not in path.parts


def _wheel_metadata_directory(archive_path: Path) -> str:
    filename_parts = archive_path.name.removesuffix(".whl").split("-")
    if len(filename_parts) < 5 or filename_parts[0] != "mcp_rig":
        raise ValueError(f"invalid MCP Rig wheel filename: {archive_path}")
    return f"{filename_parts[0]}-{filename_parts[1]}.dist-info"


def _is_allowed_wheel_path(member: str, metadata_directory: str) -> bool:
    parts = PurePosixPath(member).parts
    return bool(parts) and parts[0] in {"mcp_rig", metadata_directory}


def _wheel_extras(archive_path: Path) -> list[str]:
    metadata_directory = _wheel_metadata_directory(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()

    extras = []
    regular_files = set()
    for info in members:
        member = info.filename
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        is_directory = info.is_dir() and file_type in {0, stat.S_IFDIR}
        is_regular = not info.is_dir() and file_type in {0, stat.S_IFREG}
        if (
            not _is_safe_relative_path(member)
            or not _is_allowed_wheel_path(member, metadata_directory)
            or not (is_directory or is_regular)
        ):
            extras.append(member)
        elif is_regular:
            regular_files.add(member)

    if extras:
        return sorted(extras)

    required_metadata = {
        f"{metadata_directory}/METADATA",
        f"{metadata_directory}/RECORD",
        f"{metadata_directory}/WHEEL",
    }
    has_package_file = any(member.startswith("mcp_rig/") for member in regular_files)
    if not has_package_file or not required_metadata.issubset(regular_files):
        raise ValueError(f"missing required distribution content in {archive_path}")
    return sorted(extras)


def _sdist_extras(archive_path: Path) -> list[str]:
    expected_root = archive_path.name.removesuffix(".tar.gz")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()

    allowed_files = {"LICENSE", "PKG-INFO", "README.md", "pyproject.toml"}
    extras = []
    regular_files = set()
    for info in members:
        member = info.name
        path = PurePosixPath(member)
        relative = PurePosixPath(*path.parts[1:])
        is_package_path = relative.parts[:2] == ("src", "mcp_rig")
        is_allowed_file = relative.as_posix() in allowed_files or (
            is_package_path and len(relative.parts) > 2
        )
        is_allowed_directory = not relative.parts or relative.parts in {
            ("src",),
            ("src", "mcp_rig"),
        } or (is_package_path and len(relative.parts) > 2)
        if (
            not _is_safe_relative_path(member)
            or path.parts[0] != expected_root
            or (info.isfile() and not is_allowed_file)
            or (info.isdir() and not is_allowed_directory)
            or not (info.isfile() or info.isdir())
        ):
            extras.append(relative.as_posix())
        elif info.isfile():
            regular_files.add(relative.as_posix())

    if extras:
        return sorted(extras)

    has_package_file = any(member.startswith("src/mcp_rig/") for member in regular_files)
    if not has_package_file or not allowed_files.issubset(regular_files):
        raise ValueError(f"missing required distribution content in {archive_path}")
    return sorted(extras)


def unexpected_files(archive_path: Path) -> list[str]:
    if archive_path.name.endswith(".whl"):
        return _wheel_extras(archive_path)
    if archive_path.name.endswith(".tar.gz"):
        return _sdist_extras(archive_path)
    raise ValueError(f"unsupported distribution archive: {archive_path}")


def main(argv: list[str] | None = None) -> int:
    archive_names = sys.argv[1:] if argv is None else argv
    if not archive_names:
        print("usage: check_distribution_contents.py ARCHIVE [ARCHIVE ...]", file=sys.stderr)
        return 2

    failures: list[tuple[Path, list[str]]] = []
    for archive_name in archive_names:
        archive_path = Path(archive_name)
        try:
            extras = unexpected_files(archive_path)
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
            print(str(error), file=sys.stderr)
            return 1
        if extras:
            failures.append((archive_path, extras))
        else:
            print(f"distribution contents accepted: {archive_path}")

    if failures:
        for archive_path, extras in failures:
            print(f"unexpected files in {archive_path}:", file=sys.stderr)
            for extra in extras:
                print(f"- {extra}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
