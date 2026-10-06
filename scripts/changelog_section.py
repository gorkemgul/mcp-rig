from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path


def extract_section(changelog: str, version: str) -> str:
    """Return the body of the `## <version>` section, without its heading."""
    version = version.removeprefix("v")
    lines = changelog.splitlines()
    heading = f"## {version}"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise ValueError(f"changelog has no '{heading}' section") from None
    end = next(
        (index for index in range(start + 1, len(lines)) if re.match(r"## (?!#)", lines[index])),
        len(lines),
    )
    body = "\n".join(lines[start + 1 : end]).strip()
    if not body:
        raise ValueError(f"changelog section '{heading}' is empty")
    return body + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print one version's release notes from CHANGELOG.md")
    parser.add_argument("version", help="version or release tag, for example 0.2.0 or v0.2.0")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args(argv)

    try:
        notes = extract_section(args.changelog.read_text(encoding="utf-8"), args.version)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
