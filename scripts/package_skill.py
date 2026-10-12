"""Package the agent skill as a zip whose root folder is the skill's name.

Usage: package_skill.py OUTPUT_ZIP

Skill marketplaces expect a zip with a SKILL.md inside a folder named after the
skill. Entries are sorted and timestamped at a fixed date so the same sources
always produce the same archive.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "mcp-rig"
FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def package(output: Path, skill_dir: Path = SKILL_DIR) -> list[str]:
    files = sorted(path for path in skill_dir.rglob("*") if path.is_file())
    names = [f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}" for path in files]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in zip(files, names, strict=True):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return names


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: package_skill.py OUTPUT_ZIP", file=sys.stderr)
        return 2
    for name in package(Path(argv[0])):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
