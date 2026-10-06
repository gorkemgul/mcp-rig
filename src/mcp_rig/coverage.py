"""Report which advertised tools no suite calls, without calling any tool."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_rig.client import ServerSpec, ToolInfo
from mcp_rig.spec import Suite


@dataclass(frozen=True)
class ServerCoverage:
    """Coverage of one server, merged across every suite that starts it."""

    server: str
    suites: list[Path]
    tools: list[str] = field(default_factory=list)
    called: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def covered(self) -> list[str]:
        return [name for name in self.tools if name in set(self.called)]

    @property
    def missing(self) -> list[str]:
        return [name for name in self.tools if name not in set(self.called)]

    @property
    def unknown(self) -> list[str]:
        """Tools that suites call but the server does not advertise."""
        return [name for name in self.called if name not in set(self.tools)]

    @property
    def percent(self) -> float:
        return 100.0 if not self.tools else 100.0 * len(self.covered) / len(self.tools)

    def to_json(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "suites": [display_path(path) for path in self.suites],
            "tools": len(self.tools),
            "covered": self.covered,
            "missing": self.missing,
            "unknown": self.unknown,
            "percent": round(self.percent, 1),
            "error": self.error,
        }


def server_label(spec: ServerSpec) -> str:
    if spec.url is not None:
        return spec.url if spec.transport == "streamable-http" else f"{spec.url} ({spec.transport})"
    label = shlex.join([_display_path(part, spec.cwd) for part in (spec.command, *spec.args)])
    if spec.env:
        label += f" [env: {', '.join(sorted(spec.env))}]"
    return label


def display_path(path: Path) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


def group_suites(suites: Sequence[Suite]) -> list[tuple[ServerSpec, list[Suite]]]:
    """Group suites that start the same server, in first-seen order.

    Command parts that name existing files are resolved, so suites in different
    directories that reach the same script through different relative paths share
    one entry. The working directory itself is not part of the identity.
    """
    groups: dict[tuple, tuple[ServerSpec, list[Suite]]] = {}
    for suite in suites:
        server = suite.server
        key = (
            tuple(_resolve(part, server.cwd) for part in (server.command, *server.args)),
            tuple(sorted((server.env or {}).items())),
            server.url,
            tuple(sorted(server.headers.items())),
            server.transport,
        )
        groups.setdefault(key, (server, []))[1].append(suite)
    return list(groups.values())


def _resolve(part: str, cwd: str | None) -> str:
    if not part or cwd is None or Path(part).is_absolute():
        return part
    candidate = Path(cwd) / part
    return str(candidate.resolve()) if candidate.exists() else part


def _display_path(part: str, cwd: str | None) -> str:
    resolved = _resolve(part, cwd)
    return display_path(Path(resolved)) if resolved != part else part


def called_tools(suites: Sequence[Suite]) -> list[str]:
    """Every tool a suite calls from a case, verify step, or setup/teardown step, in first-seen order."""
    names: dict[str, None] = {}
    for suite in suites:
        for step in suite.setup:
            names.setdefault(step.call)
        for case in suite.cases:
            names.setdefault(case.call)
            for step in case.verify:
                names.setdefault(step.call)
        for step in suite.teardown:
            names.setdefault(step.call)
    return list(names)


async def measure(
    suites: Sequence[Suite],
    list_tools: Callable[[ServerSpec], Any],
) -> list[ServerCoverage]:
    """List each distinct server's tools once and compare them with what its suites call."""
    results: list[ServerCoverage] = []
    for spec, group in group_suites(suites):
        label = server_label(spec)
        paths = [suite.path for suite in group]
        called = called_tools(group)
        try:
            tools: list[ToolInfo] = await list_tools(spec)
        except Exception as exc:  # noqa: BLE001 - one unreachable server must not hide the others
            results.append(ServerCoverage(label, paths, called=called, error=_describe(exc)))
            continue
        results.append(ServerCoverage(label, paths, tools=[tool.name for tool in tools], called=called))
    return results


def render_coverage(results: Sequence[ServerCoverage]) -> str:
    lines = ["MCP Rig coverage"]
    for item in results:
        lines.append("")
        lines.append(item.server)
        lines.append(f"  suites: {', '.join(display_path(path) for path in item.suites)}")
        if item.error is not None:
            lines.append(f"  ! could not list tools: {item.error}")
            continue
        lines.append(f"  {len(item.covered)}/{len(item.tools)} tools covered ({item.percent:.0f}%)")
        if item.missing:
            lines.append(f"  missing: {', '.join(item.missing)}")
        if item.unknown:
            lines.append(f"  not advertised by the server: {', '.join(item.unknown)}")
    measured = [item for item in results if item.error is None]
    covered = sum(len(item.covered) for item in measured)
    total = sum(len(item.tools) for item in measured)
    percent = 100.0 if not total else 100.0 * covered / total
    lines.extend(["", f"Total: {covered}/{total} tools covered ({percent:.0f}%)"])
    return "\n".join(lines)


def _describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"
