"""Command-line entry point for MCP Rig."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio

from mcp_rig.batch import BatchResult, run_batch
from mcp_rig.checks import CheckResult, run_protocol_checks
from mcp_rig.client import ServerSpec, ToolInfo, connect
from mcp_rig.discovery import discover_suites
from mcp_rig.junit import write_batch_junit
from mcp_rig.lint import LintWarning, lint_tools
from mcp_rig.report import render_batch, render_batch_errors, render_check, render_suite
from mcp_rig.runner import CaseStatus, ErrorCategory
from mcp_rig.scaffold import scaffold_suite
from mcp_rig.selection import SelectionFilter, validate_tag
from mcp_rig.snapshots import SNAPSHOT_SUFFIX

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
SERVER_LOGS_HINT = "hint: the server may have exited; rerun with --server-logs to see its stderr"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-rig",
        description="Deterministic tests for MCP servers.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run a YAML tool suite")
    run_parser.add_argument(
        "targets",
        nargs="+",
        metavar="TARGET",
        help="YAML suite file or directory containing suites",
    )
    run_parser.add_argument(
        "--server-logs",
        action="store_true",
        help="show the MCP server's stderr",
    )
    run_parser.add_argument(
        "--junit",
        metavar="PATH",
        help="also write a JUnit XML report",
    )
    run_parser.add_argument(
        "--case",
        dest="case_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help="run cases whose names match this shell-style pattern; repeat for OR",
    )
    run_parser.add_argument(
        "--tag",
        dest="required_tags",
        action="append",
        default=[],
        type=_tag_arg,
        metavar="TAG",
        help="require this effective tag; repeat to require every tag",
    )
    run_parser.add_argument(
        "--exclude-tag",
        dest="excluded_tags",
        action="append",
        default=[],
        type=_tag_arg,
        metavar="TAG",
        help="exclude cases carrying this effective tag; repeat for OR",
    )
    run_parser.add_argument(
        "--update-snapshots",
        action="store_true",
        help="create, update, and prune snapshot sidecars",
    )
    check_parser = commands.add_parser(
        "check",
        help="run protocol checks and tool lint without a suite",
    )
    check_parser.add_argument(
        "server",
        help='server command, for example "python server.py"',
    )
    check_parser.add_argument(
        "--probe-invalid-args",
        action="store_true",
        help="call tools with missing required args; only use on development/test servers",
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when tool lint produces warnings",
    )
    check_parser.add_argument(
        "--server-logs",
        action="store_true",
        help="show the MCP server's stderr",
    )

    init_parser = commands.add_parser(
        "init",
        help="generate a starter suite from a server's tools",
    )
    init_parser.add_argument(
        "server",
        help='server command, for example "python server.py"',
    )
    init_parser.add_argument(
        "--output",
        metavar="PATH",
        help="write the suite to PATH instead of stdout",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing --output file",
    )
    init_parser.add_argument(
        "--server-logs",
        action="store_true",
        help="show the MCP server's stderr",
    )

    args = parser.parse_args(argv)
    color = sys.stdout.isatty()
    if args.command == "run":
        return _cmd_run(args, color=color)
    if args.command == "init":
        return _cmd_init(args)
    return _cmd_check(args, color=color)


def _cmd_run(args: argparse.Namespace, color: bool) -> int:
    selection = SelectionFilter(
        case_patterns=tuple(args.case_patterns),
        required_tags=frozenset(args.required_tags),
        excluded_tags=frozenset(args.excluded_tags),
    )
    discovery = discover_suites(args.targets)
    if args.junit and any(_same_path(path, args.junit) for path in discovery.paths):
        print("error: JUnit report path must differ from suite path", file=sys.stderr)
        return EXIT_USAGE
    if args.junit and any(
        _same_path(path.with_suffix(SNAPSHOT_SUFFIX), args.junit)
        for path in discovery.paths
    ):
        print(
            "error: JUnit report path must differ from suite snapshot path",
            file=sys.stderr,
        )
        return EXIT_USAGE

    result = anyio.run(
        run_batch,
        discovery,
        args.server_logs,
        selection,
        args.update_snapshots,
    )
    errors = render_batch_errors(result)
    if errors:
        for line in errors.splitlines():
            print(f"error: {line}", file=sys.stderr)

    print(_render_run(args.targets, result, color=color))
    if not args.server_logs and _server_may_have_exited(result):
        print(SERVER_LOGS_HINT, file=sys.stderr)
    if args.junit:
        try:
            write_batch_junit(args.junit, result)
        except OSError as exc:
            print(f"error: {args.junit}: could not write JUnit report: {_describe(exc)}", file=sys.stderr)
            return EXIT_USAGE
    if result.selection_active and result.selected_cases == 0:
        print("error: filters matched no test cases", file=sys.stderr)
        return EXIT_USAGE
    if result.has_errors:
        return EXIT_USAGE
    if result.has_failures:
        return EXIT_FAILED
    return EXIT_OK


def _server_may_have_exited(result: BatchResult) -> bool:
    for item in result.suites:
        if item.result is None:
            continue
        error = item.result.suite_error
        if error is not None and error.category is ErrorCategory.SETUP:
            return True
        if any(
            case.status is CaseStatus.ERROR and case.error.category is ErrorCategory.TRANSPORT
            for case in item.result.results
        ):
            return True
    return False


def _render_run(targets: list[str], result: BatchResult, color: bool) -> str:
    if (
        len(targets) == 1
        and not result.selection_active
        and not result.snapshot_update_active
        and not result.discovery_errors
        and len(result.suites) == 1
        and result.suites[0].result is not None
        and Path(targets[0]).is_file()
    ):
        return render_suite(targets[0], result.suites[0].result, color=color)
    return render_batch(result, color=color)


def _cmd_check(args: argparse.Namespace, color: bool) -> int:
    try:
        spec = ServerSpec.from_command_line(args.server)
        checks, warnings = anyio.run(
            _check,
            spec,
            args.probe_invalid_args,
            args.server_logs,
        )
    except Exception as exc:  # noqa: BLE001 - CLI converts infrastructure errors to exit 2
        print(f"error: could not run server: {_describe(exc)}", file=sys.stderr)
        if not args.server_logs:
            print(SERVER_LOGS_HINT, file=sys.stderr)
        return EXIT_USAGE

    print(render_check(checks, warnings, color=color))
    failed = not all(check.passed for check in checks) or (
        args.strict and bool(warnings)
    )
    return EXIT_FAILED if failed else EXIT_OK


def _cmd_init(args: argparse.Namespace) -> int:
    output = Path(args.output) if args.output else None
    if output is not None and output.exists() and not args.force:
        print(f"error: {output} already exists; use --force to overwrite it", file=sys.stderr)
        return EXIT_USAGE
    try:
        spec = ServerSpec.from_command_line(args.server)
        tools = anyio.run(_list_tools, spec, args.server_logs)
    except Exception as exc:  # noqa: BLE001 - CLI converts infrastructure errors to exit 2
        print(f"error: could not run server: {_describe(exc)}", file=sys.stderr)
        if not args.server_logs:
            print(SERVER_LOGS_HINT, file=sys.stderr)
        return EXIT_USAGE
    if not tools:
        print("error: the server lists no tools", file=sys.stderr)
        return EXIT_FAILED
    suite = scaffold_suite(spec, tools, suite_path=output)
    if output is None:
        sys.stdout.write(suite)
        return EXIT_OK
    try:
        output.write_text(suite, encoding="utf-8")
    except OSError as exc:
        print(f"error: {output}: could not write suite: {_describe(exc)}", file=sys.stderr)
        return EXIT_USAGE
    print(f"wrote {len(tools)} {'case' if len(tools) == 1 else 'cases'} to {output}")
    print(f"next: replace the placeholders, then run `mcp-rig run {output}`")
    return EXIT_OK


async def _list_tools(spec: ServerSpec, show_server_logs: bool) -> list[ToolInfo]:
    async with connect(spec, show_server_logs=show_server_logs) as probe:
        return await probe.list_tools()


async def _check(
    spec: ServerSpec,
    probe_invalid_args: bool,
    show_server_logs: bool,
) -> tuple[list[CheckResult], list[LintWarning]]:
    async with connect(spec, show_server_logs=show_server_logs) as probe:
        tools = await probe.list_tools()
        checks = await run_protocol_checks(
            probe,
            probe_invalid_args=probe_invalid_args,
        )
    return checks, lint_tools(tools)


def _describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _same_path(first: str | Path, second: str | Path) -> bool:
    try:
        return Path(first).samefile(second)
    except OSError:
        return Path(first).resolve() == Path(second).resolve()


def _tag_arg(value: str) -> str:
    try:
        return validate_tag(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
