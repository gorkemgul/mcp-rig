"""Run discovered MCP Rig suites sequentially as one resilient batch."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from mcp_rig.discovery import DiscoveryError, DiscoveryResult
from mcp_rig.runner import CaseStatus, SuiteResult, run_suite
from mcp_rig.selection import SelectionFilter, select_suite
from mcp_rig.snapshots import (
    SnapshotChanges,
    SnapshotError,
    SnapshotSession,
    snapshot_path,
)
from mcp_rig.spec import load_suite


class BatchFailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    EXECUTION = "execution"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class BatchFailure:
    category: BatchFailureCategory
    exception_type: str
    message: str


@dataclass(frozen=True)
class SuiteRun:
    path: Path
    result: SuiteResult | None = None
    error: BatchFailure | None = None
    remote: bool = False


@dataclass(frozen=True)
class BatchResult:
    suites: list[SuiteRun]
    discovery_errors: list[DiscoveryError]
    selection_active: bool = False
    selected_cases: int = 0
    filtered_out_cases: int = 0
    snapshot_update_active: bool = False
    snapshot_changes: SnapshotChanges = field(default_factory=SnapshotChanges)

    @property
    def suite_passed(self) -> int:
        return sum(
            item.error is None and item.result is not None and item.result.ok
            for item in self.suites
        )

    @property
    def suite_failed(self) -> int:
        return sum(
            item.result is not None
            and item.error is None
            and item.result.failed > 0
            and item.result.errors == 0
            for item in self.suites
        )

    @property
    def suite_errors(self) -> int:
        return len(self.discovery_errors) + sum(
            item.error is not None
            or item.result is not None
            and item.result.errors > 0
            for item in self.suites
        )

    @property
    def case_passed(self) -> int:
        return sum(item.result.passed for item in self.suites if item.result is not None)

    @property
    def case_failed(self) -> int:
        return sum(item.result.failed for item in self.suites if item.result is not None)

    @property
    def case_errors(self) -> int:
        return sum(
            sum(case.status is CaseStatus.ERROR for case in item.result.results)
            for item in self.suites
            if item.result is not None
        )

    @property
    def case_skipped(self) -> int:
        return sum(item.result.skipped for item in self.suites if item.result is not None)

    @property
    def has_failures(self) -> bool:
        return self.case_failed > 0

    @property
    def has_errors(self) -> bool:
        return self.suite_errors > 0


async def run_batch(
    discovery: DiscoveryResult,
    show_server_logs: bool = False,
    selection: SelectionFilter | None = None,
    update_snapshots: bool = False,
) -> BatchResult:
    selection = selection or SelectionFilter()
    suites: list[SuiteRun] = []
    selected_cases = 0
    filtered_out_cases = 0
    snapshot_changes = SnapshotChanges()
    for path in discovery.paths:
        try:
            suite = load_suite(path)
        except Exception as exc:
            suites.append(
                SuiteRun(
                    path,
                    error=_failure(BatchFailureCategory.CONFIGURATION, exc),
                )
            )
            continue

        selected = select_suite(suite, selection)
        selected_cases += selected.selected
        filtered_out_cases += selected.filtered_out
        if not selected.suite.cases:
            continue

        declared_snapshot_names = [
            case.name for case in suite.cases if case.expect.get("snapshot") is True
        ]
        selected_uses_snapshots = any(
            case.expect.get("snapshot") is True for case in selected.suite.cases
        )
        snapshots = None
        try:
            cleans_existing_sidecar = (
                update_snapshots
                and not selection.active
                and snapshot_path(suite.path).exists()
            )
        except SnapshotError as exc:
            suites.append(
                SuiteRun(
                    path,
                    error=_failure(BatchFailureCategory.SNAPSHOT, exc),
                )
            )
            continue
        if selected_uses_snapshots or cleans_existing_sidecar:
            try:
                snapshots = SnapshotSession.open(
                    suite.path,
                    update=update_snapshots,
                )
            except SnapshotError as exc:
                suites.append(
                    SuiteRun(
                        path,
                        error=_failure(BatchFailureCategory.SNAPSHOT, exc),
                    )
                )
                continue

        try:
            if snapshots is None:
                result = await run_suite(
                    selected.suite,
                    show_server_logs=show_server_logs,
                )
            else:
                result = await run_suite(
                    selected.suite,
                    show_server_logs=show_server_logs,
                    snapshots=snapshots,
                )
        except SnapshotError as exc:
            suites.append(
                SuiteRun(
                    path,
                    error=_failure(BatchFailureCategory.SNAPSHOT, exc),
                )
            )
            continue
        except Exception as exc:
            suites.append(
                SuiteRun(
                    path,
                    error=_failure(BatchFailureCategory.EXECUTION, exc),
                )
            )
            continue

        snapshot_error = None
        if snapshots is not None:
            try:
                snapshot_changes += snapshots.finalize(
                    declared_snapshot_names,
                    prune=not selection.active and result.errors == 0,
                )
            except SnapshotError as exc:
                snapshot_error = _failure(BatchFailureCategory.SNAPSHOT, exc)
        suites.append(SuiteRun(path, result=result, error=snapshot_error, remote=suite.server.is_remote))

    return BatchResult(
        suites=suites,
        discovery_errors=discovery.errors,
        selection_active=selection.active,
        selected_cases=selected_cases,
        filtered_out_cases=filtered_out_cases,
        snapshot_update_active=update_snapshots,
        snapshot_changes=snapshot_changes,
    )


def _failure(category: BatchFailureCategory, exc: Exception) -> BatchFailure:
    return BatchFailure(
        category=category,
        exception_type=type(exc).__name__,
        message=str(exc) or type(exc).__name__,
    )
