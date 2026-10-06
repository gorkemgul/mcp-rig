"""Run suite cases sequentially against one MCP server process."""

from __future__ import annotations

import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT

from mcp_rig.assertions import check
from mcp_rig.client import CallOutcome, Probe, ServerSpec, connect
from mcp_rig.faults import FaultInjector
from mcp_rig.snapshots import SnapshotSession
from mcp_rig.spec import Case, Step, Suite


class CaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class ErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    SETUP = "setup"
    TRANSPORT = "transport"
    TEARDOWN = "teardown"


@dataclass(frozen=True)
class InfrastructureError:
    category: ErrorCategory
    exception_type: str
    message: str


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: CaseStatus
    elapsed_ms: float = 0.0
    failures: list[str] = field(default_factory=list)
    outcome: CallOutcome | None = None
    error: InfrastructureError | None = None
    skip_reason: str | None = None
    retried_errors: tuple[InfrastructureError, ...] = ()
    fault: str | None = None

    @property
    def attempts(self) -> int:
        return len(self.retried_errors) + 1


@dataclass(frozen=True)
class SuiteResult:
    results: list[CaseResult]
    suite_error: InfrastructureError | None = None

    @property
    def passed(self) -> int:
        return sum(item.status is CaseStatus.PASSED for item in self.results)

    @property
    def failed(self) -> int:
        return sum(item.status is CaseStatus.FAILED for item in self.results)

    @property
    def errors(self) -> int:
        return sum(item.status is CaseStatus.ERROR for item in self.results) + (self.suite_error is not None)

    @property
    def skipped(self) -> int:
        return sum(item.status is CaseStatus.SKIPPED for item in self.results)

    @property
    def ok(self) -> bool:
        return bool(self.results) and self.passed == len(self.results) and self.suite_error is None


class _Session:
    """The suite's server connection, replaceable after the transport fails."""

    def __init__(self, spec: ServerSpec, show_server_logs: bool):
        self._spec = spec
        self._show_server_logs = show_server_logs
        self._stack: AsyncExitStack | None = None
        self._probe: Probe | None = None
        self.broken = False
        self.faults = FaultInjector()

    async def open(self) -> None:
        stack = AsyncExitStack()
        self._probe = await stack.enter_async_context(
            connect(self._spec, show_server_logs=self._show_server_logs, faults=self.faults)
        )
        self._stack = stack
        self.broken = False

    async def close(self, exc: BaseException | None = None) -> None:
        stack, self._stack, self._probe = self._stack, None, None
        if stack is not None:
            await stack.__aexit__(type(exc) if exc else None, exc, exc.__traceback__ if exc else None)

    async def reconnect(self) -> None:
        try:
            await self.close()
        except Exception:  # noqa: BLE001 - the failed session was already reported
            pass
        await self.open()

    async def call(
        self,
        name: str,
        args: dict[str, Any],
        timeout_s: float,
        fault: str | None = None,
    ) -> CallOutcome:
        if self.broken:
            await self.reconnect()
        assert self._probe is not None
        if fault is not None:
            self.faults.arm(fault)
        try:
            return await self._probe.call(name, args, timeout_s=timeout_s)
        except Exception as exc:
            if not _is_timeout_error(exc):
                self.broken = True
            raise
        finally:
            self.faults.disarm()


async def run_suite(
    suite: Suite,
    show_server_logs: bool = False,
    snapshots: SnapshotSession | None = None,
) -> SuiteResult:
    session = _Session(suite.server, show_server_logs)
    try:
        await session.open()
    except Exception as exc:
        return SuiteResult(
            _skipped_cases(suite.cases, "suite could not start"),
            suite_error=_normalize_error(exc, ErrorCategory.SETUP),
        )

    results: list[CaseResult] = []
    suite_error: InfrastructureError | None = None
    try:
        suite_error = await _run_steps(session, suite.setup, ErrorCategory.SETUP)
        if suite_error is not None:
            results = _skipped_cases(suite.cases, "suite setup failed")
        else:
            for index, case in enumerate(suite.cases):
                result = await _run_case(session, case, snapshots=snapshots, setup=suite.setup)
                results.append(result)
                if result.status is CaseStatus.ERROR and not _continues_after(suite, case, result):
                    reason = f"not run after infrastructure error in '{case.name}'"
                    results.extend(_skipped_cases(suite.cases[index + 1 :], reason))
                    break
            suite_error = await _run_steps(session, suite.teardown, ErrorCategory.TEARDOWN)
    except BaseException as exc:
        await session.close(exc)
        raise
    try:
        await session.close()
    except Exception as exc:
        if suite_error is None:
            suite_error = _normalize_error(exc, ErrorCategory.TEARDOWN)
    return SuiteResult(results, suite_error=suite_error)


def _continues_after(suite: Suite, case: Case, result: CaseResult) -> bool:
    assert result.error is not None
    after_timeout = case.after_timeout or suite.after_timeout
    return after_timeout == "continue" and result.error.category is ErrorCategory.TIMEOUT


async def _run_steps(
    session: _Session,
    steps: tuple[Step, ...],
    category: ErrorCategory,
) -> InfrastructureError | None:
    for index, step in enumerate(steps):
        where = f"{category}[{index}] {step.call}"
        try:
            outcome = await session.call(step.call, step.args, step.timeout_s)
        except Exception as exc:
            error = _normalize_error(exc, category)
            return InfrastructureError(category, error.exception_type, f"{where}: {error.message}")
        failures = check(step.expect, outcome)
        if failures:
            return InfrastructureError(category, "StepFailed", f"{where}: {'; '.join(failures)}")
    return None


async def _run_case(
    session: _Session,
    case: Case,
    snapshots: SnapshotSession | None = None,
    setup: tuple[Step, ...] = (),
) -> CaseResult:
    started = time.perf_counter()
    retried: list[InfrastructureError] = []
    injected: str | None = None
    while True:
        # Only the first attempt is faulted; retries and verify steps run normally.
        fault = case.fault if not retried else None
        try:
            outcome = await session.call(case.call, case.args, case.timeout_s, fault=fault)
            break
        except Exception as exc:
            error = _normalize_error(exc, ErrorCategory.TRANSPORT)
            if fault is not None and session.faults.injected:
                injected = fault
            if len(retried) >= case.retry_attempts:
                return _error_result(case, started, error, retried, fault=injected)
            retried.append(error)
            if case.retry_rerun_setup and session.broken:
                setup_error = await _run_steps(session, setup, ErrorCategory.SETUP)
                if setup_error is not None:
                    located = InfrastructureError(
                        setup_error.category,
                        setup_error.exception_type,
                        f"setup (after reconnect): {setup_error.message}",
                    )
                    return _error_result(case, started, located, retried, fault=injected)

    failures = check(case.expect, outcome)
    if snapshots is not None and case.expect.get("snapshot") is True:
        failures.extend(snapshots.evaluate(case.name, outcome))
    for index, step in enumerate(case.verify):
        where = f"verify[{index}] {step.call}"
        try:
            step_outcome = await session.call(step.call, step.args, step.timeout_s)
        except Exception as exc:
            error = _normalize_error(exc, ErrorCategory.TRANSPORT)
            located = InfrastructureError(error.category, error.exception_type, f"{where}: {error.message}")
            return _error_result(case, started, located, retried, outcome, fault=injected)
        failures.extend(f"{where}: {failure}" for failure in check(step.expect, step_outcome))
    return CaseResult(
        name=case.name,
        status=CaseStatus.FAILED if failures else CaseStatus.PASSED,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        failures=failures,
        outcome=outcome,
        retried_errors=tuple(retried),
        fault=injected,
    )


def _error_result(
    case: Case,
    started: float,
    error: InfrastructureError,
    retried: list[InfrastructureError],
    outcome: CallOutcome | None = None,
    fault: str | None = None,
) -> CaseResult:
    return CaseResult(
        name=case.name,
        status=CaseStatus.ERROR,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        outcome=outcome,
        error=error,
        retried_errors=tuple(retried),
        fault=fault,
    )


def _normalize_error(exc: BaseException, fallback: ErrorCategory) -> InfrastructureError:
    leaves = _exception_leaves(exc)
    selected = next((leaf for leaf in leaves if _is_timeout(leaf)), leaves[0])
    category = ErrorCategory.TIMEOUT if fallback is ErrorCategory.TRANSPORT and _is_timeout(selected) else fallback
    message = str(selected) or ("operation timed out" if _is_timeout(selected) else type(selected).__name__)
    return InfrastructureError(category=category, exception_type=type(selected).__name__, message=message)


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for nested in exc.exceptions for leaf in _exception_leaves(nested)]
    return [exc]


def _is_timeout_error(exc: BaseException) -> bool:
    return any(_is_timeout(leaf) for leaf in _exception_leaves(exc))


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or isinstance(exc, MCPError) and exc.code == REQUEST_TIMEOUT


def _skipped_cases(cases: list[Case], reason: str) -> list[CaseResult]:
    return [CaseResult(name=case.name, status=CaseStatus.SKIPPED, skip_reason=reason) for case in cases]
