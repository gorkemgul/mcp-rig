"""Keep Hatch's generated source distribution on the explicit allowlist."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        gitignore = str(Path(self.root, ".gitignore"))
        build_data["force_include"].pop(gitignore, None)
