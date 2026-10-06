"""Command-line entry point for MCP Rig."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import anyio

from mcp_rig.batch import BatchResult, run_batch
from mcp_rig.checks import CheckResult, run_protocol_checks
from mcp_rig.client import ServerSpec, ToolInfo, connect, is_url
from mcp_rig.coverage import measure, render_coverage
from mcp_rig.discovery import discover_suites
from mcp_rig.junit import write_batch_junit
from mcp_rig.lint import LintWarning, filter_warnings, lint_tools, parse_ignore_pattern
from mcp_rig.report import render_batch, render_batch_errors, render_check, render_suite
from mcp_rig.runner import CaseStatus, ErrorCategory
from mcp_rig.scaffold import header_variables, scaffold_suite
from mcp_rig.selection import SelectionFilter, validate_tag
from mcp_rig.snapshots import SNAPSHOT_SUFFIX
from mcp_rig.spec import SpecError, load_suite

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
        help='server command such as "python server.py", or an http(s) URL',
    )
    _add_header_option(check_parser)
    _add_env_option(check_parser)
    check_parser.add_argument(
        "--probe-invalid-args",
        action="store_true",
        help="call tools with missing required args; only use on development/test servers",
    )
    check_parser.add_argument(
        "--ignore",
        dest="ignore_patterns",
        action="append",
        default=[],
        type=_ignore_arg,
        metavar="CODE[:TOOL]",
        help="ignore a lint code, or a code for one tool; repeatable",
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
        help='server command such as "python server.py", or an http(s) URL',
    )
    _add_header_option(init_parser)
    _add_env_option(init_parser)
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

    coverage_parser = commands.add_parser(
        "coverage",
        help="report advertised tools that no suite calls",
    )
    coverage_parser.add_argument(
        "targets",
        nargs="+",
        metavar="TARGET",
        help="YAML suite file or directory containing suites",
    )
    coverage_parser.add_argument(
        "--min",
        dest="minimum",
        type=_percent_arg,
        metavar="PERCENT",
        help="exit 1 when any server's coverage is below PERCENT",
    )
    coverage_parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON",
    )
    coverage_parser.add_argument(
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
    if args.command == "coverage":
        return _cmd_coverage(args)
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
        if item.result is None or item.remote:
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
    spec = _server_from_args(args)
    if spec is None:
        return EXIT_USAGE
    try:
        checks, warnings, protocol_version = anyio.run(
            _check,
            spec,
            args.probe_invalid_args,
            args.server_logs,
        )
    except Exception as exc:  # noqa: BLE001 - CLI converts infrastructure errors to exit 2
        print(f"error: could not run server: {_describe(exc)}", file=sys.stderr)
        if not args.server_logs and not is_url(args.server):
            print(SERVER_LOGS_HINT, file=sys.stderr)
        return EXIT_USAGE

    filtered = filter_warnings(warnings, args.ignore_patterns)
    print(
        render_check(
            checks,
            filtered.kept,
            color=color,
            ignored=filtered.ignored,
            protocol_version=protocol_version,
        )
    )
    for pattern in filtered.unused_patterns:
        print(f"warning: --ignore {pattern} matched no lint warning", file=sys.stderr)
    failed = not all(check.passed for check in checks) or (
        args.strict and bool(filtered.kept)
    )
    return EXIT_FAILED if failed else EXIT_OK


def _cmd_init(args: argparse.Namespace) -> int:
    output = Path(args.output) if args.output else None
    if output is not None and output.exists() and not args.force:
        print(f"error: {output} already exists; use --force to overwrite it", file=sys.stderr)
        return EXIT_USAGE
    spec = _server_from_args(args)
    if spec is None:
        return EXIT_USAGE
    try:
        tools = anyio.run(_list_tools, spec, args.server_logs)
    except Exception as exc:  # noqa: BLE001 - CLI converts infrastructure errors to exit 2
        print(f"error: could not run server: {_describe(exc)}", file=sys.stderr)
        if not args.server_logs and not is_url(args.server):
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
    for name, variable in header_variables(spec).items():
        print(f"header {name} reads ${{{variable}}}; set it before running the suite")
    for name in spec.env or {}:
        print(f"env {name} reads ${{{name}}}; set it before running the suite")
    print(f"next: replace the placeholders, then run `mcp-rig run {output}`")
    return EXIT_OK


def _cmd_coverage(args: argparse.Namespace) -> int:
    discovery = discover_suites(args.targets)
    errors = [f"{error.target}: {error.message}" for error in discovery.errors]
    suites = []
    for path in discovery.paths:
        try:
            suites.append(load_suite(path))
        except SpecError as exc:
            errors.append(str(exc))

    async def list_tools(spec: ServerSpec) -> list[ToolInfo]:
        return await _list_tools(spec, args.server_logs)

    results = anyio.run(measure, suites, list_tools) if suites else []
    below = [
        item for item in results
        if item.error is None and args.minimum is not None and item.percent < args.minimum
    ]
    if args.json:
        print(json.dumps({"servers": [item.to_json() for item in results], "errors": errors}, indent=2))
    else:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        if results:
            print(render_coverage(results))
        for item in below:
            print(
                f"error: {item.server} coverage {item.percent:.0f}% is below --min {args.minimum:g}%",
                file=sys.stderr,
            )
    if errors or any(item.error is not None for item in results) or not results:
        return EXIT_USAGE
    return EXIT_FAILED if below else EXIT_OK


def _percent_arg(value: str) -> float:
    try:
        percent = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not 0 <= percent <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return percent


async def _list_tools(spec: ServerSpec, show_server_logs: bool) -> list[ToolInfo]:
    async with connect(spec, show_server_logs=show_server_logs) as probe:
        return await probe.list_tools()


async def _check(
    spec: ServerSpec,
    probe_invalid_args: bool,
    show_server_logs: bool,
) -> tuple[list[CheckResult], list[LintWarning], str]:
    async with connect(spec, show_server_logs=show_server_logs) as probe:
        tools = await probe.list_tools()
        checks = await run_protocol_checks(
            probe,
            probe_invalid_args=probe_invalid_args,
        )
        protocol_version = probe.protocol_version
    return checks, lint_tools(tools), protocol_version


def _server_from_args(args: argparse.Namespace) -> ServerSpec | None:
    try:
        env, inherit_env = _parse_env(args.env)
        return ServerSpec.from_target(args.server, _parse_headers(args.headers), env, inherit_env)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _add_header_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--header",
        dest="headers",
        action="append",
        default=[],
        metavar="NAME:VALUE",
        help="HTTP header for a URL server, for example 'Authorization: Bearer $TOKEN'; repeatable",
    )


def _add_env_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env",
        dest="env",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="environment variable for a server command; NAME alone copies it from this shell; repeatable",
    )


def _parse_env(values: list[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Split --env options into explicit values and names to copy from the current environment."""
    env: dict[str, str] = {}
    inherited: list[str] = []
    for value in values:
        name, separator, content = value.partition("=")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"invalid --env {value!r}; expected NAME or NAME=VALUE")
        if separator:
            env[name] = content
        elif name not in inherited:
            inherited.append(name)
    return env, tuple(inherited)


def _parse_headers(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition(":")
        if not separator or not name.strip():
            raise ValueError(f"invalid --header {value!r}; expected NAME:VALUE")
        headers[name.strip()] = content.strip()
    return headers


def _describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _same_path(first: str | Path, second: str | Path) -> bool:
    try:
        return Path(first).samefile(second)
    except OSError:
        return Path(first).resolve() == Path(second).resolve()


def _ignore_arg(value: str) -> str:
    try:
        return parse_ignore_pattern(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _tag_arg(value: str) -> str:
    try:
        return validate_tag(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
