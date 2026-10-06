"""Render MCP Rig suite results for humans."""

from __future__ import annotations

import os
from pathlib import Path

from mcp_rig.batch import BatchResult
from mcp_rig.checks import CheckResult
from mcp_rig.lint import LintWarning
from mcp_rig.runner import CaseStatus, SuiteResult

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _display(path: Path) -> str:
    """Show a suite path relative to the working directory, as a user would type it."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


def render_suite(title: str, result: SuiteResult, color: bool = False) -> str:
    lines = [f"MCP Rig — {title}", ""]
    for item in result.results:
        if item.status is CaseStatus.PASSED:
            lines.append(f"{_paint('✓', GREEN, color)} {item.name} ({item.elapsed_ms:.0f} ms)")
        elif item.status is CaseStatus.FAILED:
            lines.append(f"{_paint('✗', RED, color)} {item.name} ({item.elapsed_ms:.0f} ms)")
            lines.extend(f"    {failure}" for failure in item.failures)
        elif item.status is CaseStatus.ERROR:
            assert item.error is not None
            lines.append(f"{_paint('!', RED, color)} {item.name} ({item.elapsed_ms:.0f} ms)")
            lines.append(f"    {item.error.category}: {item.error.exception_type}: {item.error.message}")
        else:
            assert item.skip_reason is not None
            lines.append(f"- {item.name}")
            lines.append(f"    {item.skip_reason}")
        if item.fault is not None:
            lines.append(f"    fault injected on attempt 1: {item.fault}")
        lines.extend(
            f"    retried after attempt {attempt}: {error.category}: {error.exception_type}: {error.message}"
            for attempt, error in enumerate(item.retried_errors, start=1)
        )
    if result.suite_error is not None:
        error = result.suite_error
        lines.append(
            f"{_paint('!', RED, color)} suite {error.category}: {error.exception_type}: {error.message}"
        )
    error_label = "error" if result.errors == 1 else "errors"
    lines.extend(
        [
            "",
            f"{result.passed} passed, {result.failed} failed, "
            f"{result.errors} {error_label}, {result.skipped} skipped",
        ]
    )
    return "\n".join(lines)


def render_batch_errors(result: BatchResult) -> str:
    lines = [
        f"! target {error.target}: {error.exception_type}: {error.message}"
        for error in result.discovery_errors
    ]
    lines.extend(
        f"! suite {item.path} {item.error.category}: "
        f"{item.error.exception_type}: {item.error.message}"
        for item in result.suites
        if item.error is not None
    )
    return "\n".join(lines)


def render_batch(result: BatchResult, color: bool = False) -> str:
    if (
        not result.discovery_errors
        and len(result.suites) == 1
        and result.suites[0].result is not None
    ):
        item = result.suites[0]
        rendered = render_suite(_display(item.path), item.result, color=color)
        if not result.selection_active and not result.snapshot_update_active:
            return rendered
        body, summary = rendered.rsplit("\n", maxsplit=1)
        detail_lines = []
        if result.selection_active:
            detail_lines.append(_render_selection(result))
        if result.snapshot_update_active:
            detail_lines.append(_render_snapshots(result))
        return "\n".join([body, *detail_lines, summary])

    sections = [
        render_suite(_display(item.path), item.result, color=color)
        for item in result.suites
        if item.result is not None
    ]
    summary_lines = []
    if result.selection_active:
        summary_lines.append(_render_selection(result))
    if result.snapshot_update_active:
        summary_lines.append(_render_snapshots(result))
    summary_lines.extend(
        [
            f"Suites: {result.suite_passed} passed, {result.suite_failed} failed, "
            f"{result.suite_errors} {_label(result.suite_errors, 'error')}",
            f"Cases: {result.case_passed} passed, {result.case_failed} failed, "
            f"{result.case_errors} {_label(result.case_errors, 'error')}, "
            f"{result.case_skipped} skipped",
        ]
    )
    summary = "\n".join(summary_lines)
    sections.append(summary)
    return "\n\n".join(sections)


def _render_selection(result: BatchResult) -> str:
    return (
        f"Selection: {result.selected_cases} selected, "
        f"{result.filtered_out_cases} filtered out"
    )


def _render_snapshots(result: BatchResult) -> str:
    changes = result.snapshot_changes
    return (
        f"Snapshots: {changes.added} added, {changes.updated} updated, "
        f"{changes.unchanged} unchanged, {changes.removed} removed"
    )


def _label(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"


def render_check(
    checks: list[CheckResult],
    warnings: list[LintWarning],
    color: bool = False,
    ignored: int = 0,
    protocol_version: str | None = None,
) -> str:
    """Render protocol checks and tool-definition warnings for a human."""
    lines = [f"Protocol checks (MCP {protocol_version})" if protocol_version else "Protocol checks"]
    for check in checks:
        mark = _paint("✓", GREEN, color) if check.passed else _paint("✗", RED, color)
        lines.append(f"  {mark} {check.name}")
        if check.detail:
            lines.append(f"      {check.detail}")

    lines.append("Lint")
    if warnings:
        for warning in warnings:
            mark = _paint("⚠", YELLOW, color)
            lines.append(f"  {mark} {warning.tool} [{warning.code}] {warning.message}")
    else:
        lines.append("  no warnings")

    passed = sum(check.passed for check in checks)
    warning_label = "warning" if len(warnings) == 1 else "warnings"
    summary = f"{passed}/{len(checks)} checks passed, {len(warnings)} lint {warning_label}"
    if ignored:
        summary += f" ({ignored} ignored)"
    lines.append(summary)
    return "\n".join(lines)
