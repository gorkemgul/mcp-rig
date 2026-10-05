"""JUnit XML output for structured MCP Rig suite results."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mcp_rig.batch import BatchFailure, BatchResult
from mcp_rig.runner import CaseStatus, InfrastructureError, SuiteResult


def write_junit(path: str | Path, suite_name: str, result: SuiteResult) -> None:
    root = ET.Element("testsuites")
    _append_suite(root, suite_name, result)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_batch_junit(path: str | Path, result: BatchResult) -> None:
    root = ET.Element("testsuites")
    for error in result.discovery_errors:
        _append_synthetic_error(
            root,
            suite_name=str(error.target),
            case_name="[target configuration]",
            category="configuration",
            exception_type=error.exception_type,
            message=error.message,
        )
    for item in result.suites:
        if item.result is not None:
            suite = _append_suite(root, str(item.path), item.result)
            if item.error is not None:
                _append_batch_error(suite, item.error)
        elif item.error is not None:
            _append_synthetic_error(
                root,
                suite_name=str(item.path),
                case_name=f"[suite {item.error.category}]",
                category=item.error.category,
                exception_type=item.error.exception_type,
                message=item.error.message,
            )

    children = root.findall("testsuite")
    for attribute in ("tests", "failures", "errors", "skipped"):
        root.set(attribute, str(sum(int(suite.attrib[attribute]) for suite in children)))
    root.set("time", f"{sum(float(suite.attrib['time']) for suite in children):.3f}")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _append_suite(root: ET.Element, suite_name: str, result: SuiteResult) -> ET.Element:
    suite = ET.SubElement(
        root,
        "testsuite",
        name=suite_name,
        tests=str(len(result.results) + (result.suite_error is not None)),
        failures=str(result.failed),
        errors=str(result.errors),
        skipped=str(result.skipped),
        time=_seconds(sum(item.elapsed_ms for item in result.results)),
    )
    for item in result.results:
        case = ET.SubElement(
            suite,
            "testcase",
            classname=suite_name,
            name=item.name,
            time=_seconds(item.elapsed_ms),
        )
        if item.retried_errors:
            _add_retry_properties(case, item.retried_errors)
        if item.status is CaseStatus.FAILED:
            assert item.failures
            failure = ET.SubElement(case, "failure", message=item.failures[0])
            failure.text = "\n".join(item.failures)
        elif item.status is CaseStatus.ERROR:
            assert item.error is not None
            _add_error(case, item.error)
        elif item.status is CaseStatus.SKIPPED:
            assert item.skip_reason is not None
            ET.SubElement(case, "skipped", message=item.skip_reason)
    if result.suite_error is not None:
        case = ET.SubElement(
            suite,
            "testcase",
            classname=suite_name,
            name=f"[suite {result.suite_error.category}]",
            time="0.000",
        )
        _add_error(case, result.suite_error)
    return suite


def _append_batch_error(suite: ET.Element, error: BatchFailure) -> None:
    suite.set("tests", str(int(suite.attrib["tests"]) + 1))
    suite.set("errors", str(int(suite.attrib["errors"]) + 1))
    case = ET.SubElement(
        suite,
        "testcase",
        classname=suite.attrib["name"],
        name=f"[suite {error.category}]",
        time="0.000",
    )
    element = ET.SubElement(
        case,
        "error",
        type=f"{error.category}.{error.exception_type}",
        message=error.message,
    )
    element.text = error.message


def _append_synthetic_error(
    root: ET.Element,
    suite_name: str,
    case_name: str,
    category: str,
    exception_type: str,
    message: str,
) -> None:
    suite = ET.SubElement(
        root,
        "testsuite",
        name=suite_name,
        tests="1",
        failures="0",
        errors="1",
        skipped="0",
        time="0.000",
    )
    case = ET.SubElement(
        suite,
        "testcase",
        classname=suite_name,
        name=case_name,
        time="0.000",
    )
    error = ET.SubElement(
        case,
        "error",
        type=f"{category}.{exception_type}",
        message=message,
    )
    error.text = message


def _add_error(case: ET.Element, error: InfrastructureError) -> None:
    element = ET.SubElement(
        case,
        "error",
        type=f"{error.category}.{error.exception_type}",
        message=error.message,
    )
    element.text = error.message


def _seconds(milliseconds: float) -> str:
    return f"{milliseconds / 1000:.3f}"


def _add_retry_properties(case: ET.Element, retried: tuple[InfrastructureError, ...]) -> None:
    properties = ET.SubElement(case, "properties")
    ET.SubElement(properties, "property", name="mcp-rig.attempts", value=str(len(retried) + 1))
    for attempt, error in enumerate(retried, start=1):
        ET.SubElement(
            properties,
            "property",
            name=f"mcp-rig.retried.{attempt}",
            value=f"{error.category}: {error.exception_type}: {error.message}",
        )
