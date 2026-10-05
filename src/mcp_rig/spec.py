"""Load and validate MCP Rig YAML suites."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from mcp_rig.client import ServerSpec
from mcp_rig.selection import validate_tag
from mcp_rig.snapshots import SnapshotError, snapshot_path

KNOWN_EXPECT_KEYS = {
    "is_error",
    "contains",
    "not_contains",
    "matches",
    "max_latency_ms",
    "json_path",
    "schema",
    "snapshot",
}
_MISSING = object()


class SpecError(ValueError):
    """Raised when a suite file cannot be loaded as valid configuration."""


AFTER_TIMEOUT_CHOICES = ("stop", "continue")


@dataclass(frozen=True)
class Step:
    """A tool call that supports a case or suite without being a test case itself."""

    call: str
    args: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 30.0


@dataclass(frozen=True)
class Case:
    name: str
    call: str
    args: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 30.0
    tags: frozenset[str] = field(default_factory=frozenset)
    verify: tuple[Step, ...] = ()
    retry_attempts: int = 0


@dataclass(frozen=True)
class Suite:
    path: Path
    server: ServerSpec
    cases: list[Case]
    tags: frozenset[str] = field(default_factory=frozenset)
    setup: tuple[Step, ...] = ()
    teardown: tuple[Step, ...] = ()
    after_timeout: str = "stop"


def load_suite(path: str | Path) -> Suite:
    suite_path = Path(path)
    try:
        text = suite_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"{suite_path}: could not read suite: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"{suite_path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{suite_path}: top level must be a mapping")

    server = _parse_server(data.get("server"), suite_path)
    raw_cases = data.get("tests")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise SpecError(f"{suite_path}: 'tests' must be a non-empty list")
    cases = [_parse_case(raw, index, suite_path) for index, raw in enumerate(raw_cases)]
    _validate_snapshot_cases(cases, suite_path)
    tags = _parse_tags(data.get("tags", _MISSING), f"{suite_path}")
    setup = _parse_steps(data.get("setup", _MISSING), f"{suite_path}: setup")
    teardown = _parse_steps(data.get("teardown", _MISSING), f"{suite_path}: teardown")
    after_timeout = data.get("after_timeout", "stop")
    if after_timeout not in AFTER_TIMEOUT_CHOICES:
        raise SpecError(f"{suite_path}: 'after_timeout' must be one of: {', '.join(AFTER_TIMEOUT_CHOICES)}")
    return Suite(
        path=suite_path,
        server=server,
        cases=cases,
        tags=tags,
        setup=setup,
        teardown=teardown,
        after_timeout=after_timeout,
    )


def _parse_server(raw: Any, path: Path) -> ServerSpec:
    if isinstance(raw, str) and raw.strip():
        try:
            spec = ServerSpec.from_command_line(raw)
        except ValueError as exc:
            raise SpecError(f"{path}: invalid 'server': {exc}") from exc
    elif isinstance(raw, dict):
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise SpecError(f"{path}: 'server.command' must be a non-empty string")
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise SpecError(f"{path}: 'server.args' must be a list of strings")
        env = raw.get("env")
        if env is not None and (
            not isinstance(env, dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items())
        ):
            raise SpecError(f"{path}: 'server.env' must map strings to strings")
        cwd = raw.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise SpecError(f"{path}: 'server.cwd' must be a string")
        try:
            spec = ServerSpec.from_command_line(command)
        except ValueError as exc:
            raise SpecError(f"{path}: invalid 'server.command': {exc}") from exc
        spec.args.extend(args)
        spec.env = env
        spec.cwd = cwd
    else:
        raise SpecError(f"{path}: 'server' must be a command string or mapping")

    if not spec.command:
        raise SpecError(f"{path}: invalid 'server': server command is empty")

    base = path.parent.resolve()
    if spec.cwd is None:
        spec.cwd = str(base)
    else:
        cwd_path = Path(spec.cwd)
        spec.cwd = str(cwd_path if cwd_path.is_absolute() else (base / cwd_path).resolve())
    return spec


def _parse_case(raw: Any, index: int, path: Path) -> Case:
    where = f"{path}: tests[{index}]"
    if not isinstance(raw, dict):
        raise SpecError(f"{where} must be a mapping")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SpecError(f"{where}: 'name' must be a non-empty string")
    call = raw.get("call")
    if not isinstance(call, str) or not call.strip():
        raise SpecError(f"{where} ({name}): 'call' must be a non-empty string")
    label = f"{where} ({name})"
    args, expect, timeout_s = _parse_call_fields(raw, label, allow_snapshot=True)
    verify = _parse_steps(raw.get("verify", _MISSING), f"{label}: verify")
    retry_attempts = _parse_retry(raw.get("retry", _MISSING), label)
    tags = _parse_tags(raw.get("tags", _MISSING), label)
    return Case(
        name=name,
        call=call,
        args=args,
        expect=expect,
        timeout_s=timeout_s,
        tags=tags,
        verify=verify,
        retry_attempts=retry_attempts,
    )


def _parse_steps(raw: Any, where: str) -> tuple[Step, ...]:
    if raw is _MISSING:
        return ()
    if not isinstance(raw, list) or not raw:
        raise SpecError(f"{where} must be a non-empty list")
    steps = []
    for index, item in enumerate(raw):
        label = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise SpecError(f"{label} must be a mapping")
        unknown = set(item) - {"call", "args", "expect", "timeout_s"}
        if unknown:
            raise SpecError(f"{label}: unknown keys: {', '.join(sorted(unknown))}")
        call = item.get("call")
        if not isinstance(call, str) or not call.strip():
            raise SpecError(f"{label}: 'call' must be a non-empty string")
        args, expect, timeout_s = _parse_call_fields(item, label, allow_snapshot=False)
        steps.append(Step(call=call, args=args, expect=expect, timeout_s=timeout_s))
    return tuple(steps)


def _parse_retry(raw: Any, where: str) -> int:
    if raw is _MISSING:
        return 0
    if not isinstance(raw, dict) or set(raw) != {"attempts"}:
        raise SpecError(f"{where}: 'retry' must be a mapping with only 'attempts'")
    attempts = raw["attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise SpecError(f"{where}: 'retry.attempts' must be a positive integer")
    return attempts


def _parse_call_fields(raw: dict[str, Any], label: str, allow_snapshot: bool) -> tuple[dict, dict, float]:
    args = raw.get("args", {})
    if not isinstance(args, dict):
        raise SpecError(f"{label}: 'args' must be a mapping")
    expect = raw.get("expect", {})
    if not isinstance(expect, dict):
        raise SpecError(f"{label}: 'expect' must be a mapping")
    timeout_s = raw.get("timeout_s", 30.0)
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise SpecError(f"{label}: 'timeout_s' must be a positive number")
    unknown = set(expect) - KNOWN_EXPECT_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise SpecError(f"{label}: unknown expect keys: {names}")
    if "is_error" in expect and not isinstance(expect["is_error"], bool):
        raise SpecError(f"{label}: 'is_error' must be a boolean")
    if not allow_snapshot and "snapshot" in expect:
        raise SpecError(f"{label}: 'snapshot' is only supported on test cases")
    if "snapshot" in expect and expect["snapshot"] is not True:
        raise SpecError(f"{label}: 'snapshot' must be true")
    if "contains" in expect:
        contains = expect["contains"]
        if not _is_string_or_non_empty_string_list(contains):
            raise SpecError(f"{label}: 'contains' must be a string or non-empty list of strings")
    if "not_contains" in expect:
        not_contains = expect["not_contains"]
        if not _is_string_or_non_empty_string_list(not_contains):
            raise SpecError(f"{label}: 'not_contains' must be a string or non-empty list of strings")
    if "matches" in expect:
        matches = expect["matches"]
        if not isinstance(matches, str):
            raise SpecError(f"{label}: 'matches' must be a string")
        try:
            re.compile(matches)
        except re.error as exc:
            raise SpecError(f"{label}: invalid 'matches' regular expression: {exc}") from exc
    if "max_latency_ms" in expect:
        limit = expect["max_latency_ms"]
        if (
            isinstance(limit, bool)
            or not isinstance(limit, (int, float))
            or not math.isfinite(limit)
            or limit <= 0
        ):
            raise SpecError(f"{label}: 'max_latency_ms' must be a positive number")
    if "json_path" in expect:
        paths = expect["json_path"]
        valid_paths = (
            isinstance(paths, dict)
            and bool(paths)
            and all(isinstance(key, str) and bool(key.strip()) for key in paths)
        )
        if not valid_paths:
            raise SpecError(f"{label}: 'json_path' must be a non-empty mapping with non-empty string keys")
    if "schema" in expect:
        schema = expect["schema"]
        if not isinstance(schema, dict):
            raise SpecError(f"{label}: 'schema' must be a mapping")
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise SpecError(f"{label}: invalid 'schema': {exc.message}") from exc
        _validate_schema_references(schema, label)
    return args, expect, float(timeout_s)


def _parse_tags(raw: Any, where: str) -> frozenset[str]:
    if raw is _MISSING:
        return frozenset()
    if not isinstance(raw, list):
        raise SpecError(f"{where}: 'tags' must be a list")
    try:
        return frozenset(validate_tag(value) for value in raw)
    except ValueError as exc:
        raise SpecError(f"{where}: 'tags' values {exc}") from exc


def _validate_snapshot_cases(cases: list[Case], path: Path) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.expect.get("snapshot") is not True:
            continue
        if case.name in seen:
            raise SpecError(f"{path}: duplicate snapshot case name {case.name!r}")
        seen.add(case.name)
    if seen:
        try:
            snapshot_path(path)
        except SnapshotError as exc:
            raise SpecError(str(exc)) from exc


def _is_string_or_non_empty_string_list(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)
    )


def _validate_schema_references(schema: dict[str, Any], label: str) -> None:
    root = Resource.from_contents(schema, default_specification=DRAFT202012)
    root_uri = root.id() or "urn:mcp-rig:inline-schema"
    registry = Registry().with_resource(root_uri, root).crawl()

    def visit(resource: Resource, resolver) -> None:
        resolver = resolver.in_subresource(resource)
        contents = resource.contents
        if isinstance(contents, dict) and "$ref" in contents:
            reference = contents["$ref"]
            if not reference.startswith("#"):
                raise SpecError(f"{label}: external 'schema' references are not supported")
            try:
                resolver.lookup(reference)
            except Unresolvable as exc:
                raise SpecError(f"{label}: invalid 'schema' reference: {reference}") from exc
        for child in resource.subresources():
            visit(child, resolver)

    visit(root, registry.resolver(root_uri))
