import xml.etree.ElementTree as ET

import pytest

from mcp_rig.batch import BatchFailure, BatchFailureCategory, BatchResult, SuiteRun
from mcp_rig.discovery import DiscoveryError
from mcp_rig.junit import write_batch_junit, write_junit
from mcp_rig.runner import CaseResult, CaseStatus, ErrorCategory, InfrastructureError, SuiteResult


def test_write_junit_represents_every_status_and_suite_error(tmp_path):
    output = tmp_path / "results.xml"
    result = SuiteResult(
        [
            CaseResult(name="passes", status=CaseStatus.PASSED, elapsed_ms=1500.0),
            CaseResult(
                name="fails",
                status=CaseStatus.FAILED,
                elapsed_ms=250.0,
                failures=["first problem", "second problem"],
            ),
            CaseResult(
                name="errors",
                status=CaseStatus.ERROR,
                elapsed_ms=10.0,
                error=InfrastructureError(ErrorCategory.TRANSPORT, "BrokenPipeError", "connection lost"),
            ),
            CaseResult(name="skips", status=CaseStatus.SKIPPED, skip_reason="session unavailable"),
        ],
        suite_error=InfrastructureError(ErrorCategory.TEARDOWN, "RuntimeError", "close failed"),
    )

    write_junit(output, "suite.yaml", result)

    root = ET.parse(output).getroot()
    assert root.tag == "testsuites"
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.attrib == {
        "name": "suite.yaml",
        "tests": "5",
        "failures": "1",
        "errors": "2",
        "skipped": "1",
        "time": "1.760",
    }
    cases = suite.findall("testcase")
    assert [case.attrib["name"] for case in cases] == [
        "passes",
        "fails",
        "errors",
        "skips",
        "[suite teardown]",
    ]
    assert [case.attrib["time"] for case in cases] == ["1.500", "0.250", "0.010", "0.000", "0.000"]
    assert cases[0].find("failure") is None
    failure = cases[1].find("failure")
    assert failure is not None
    assert failure.attrib == {"message": "first problem"}
    assert failure.text == "first problem\nsecond problem"
    error = cases[2].find("error")
    assert error is not None
    assert error.attrib == {"type": "transport.BrokenPipeError", "message": "connection lost"}
    assert error.text == "connection lost"
    skipped = cases[3].find("skipped")
    assert skipped is not None
    assert skipped.attrib == {"message": "session unavailable"}
    suite_error = cases[4].find("error")
    assert suite_error is not None
    assert suite_error.attrib == {"type": "teardown.RuntimeError", "message": "close failed"}
    assert suite_error.text == "close failed"


def test_write_junit_escapes_xml_content(tmp_path):
    output = tmp_path / "escaped.xml"
    result = SuiteResult(
        [
            CaseResult(
                name='case <one> & "two"',
                status=CaseStatus.FAILED,
                failures=['expected <tag> & "value"'],
            )
        ]
    )

    write_junit(output, 'suite <one> & "two"', result)

    suite = ET.parse(output).getroot().find("testsuite")
    assert suite is not None
    case = suite.find("testcase")
    assert suite.attrib["name"] == 'suite <one> & "two"'
    assert case is not None
    assert case.attrib["name"] == 'case <one> & "two"'
    failure = case.find("failure")
    assert failure is not None
    assert failure.attrib["message"] == 'expected <tag> & "value"'
    assert failure.text == 'expected <tag> & "value"'


def test_write_junit_propagates_file_errors(tmp_path):
    with pytest.raises(OSError):
        write_junit(tmp_path / "missing" / "results.xml", "suite.yaml", SuiteResult([]))


def test_write_batch_junit_emits_ordered_suite_children_and_root_totals(tmp_path):
    output = tmp_path / "results.xml"
    first = SuiteResult(
        [CaseResult(name="passes", status=CaseStatus.PASSED, elapsed_ms=1500.0)]
    )
    second = SuiteResult(
        [
            CaseResult(
                name="fails",
                status=CaseStatus.FAILED,
                elapsed_ms=250.0,
                failures=["wrong value"],
            )
        ]
    )
    batch = BatchResult(
        [
            SuiteRun(tmp_path / "a.yaml", result=first),
            SuiteRun(tmp_path / "b.yaml", result=second),
        ],
        [],
    )

    write_batch_junit(output, batch)

    root = ET.parse(output).getroot()
    assert root.attrib == {
        "tests": "2",
        "failures": "1",
        "errors": "0",
        "skipped": "0",
        "time": "1.750",
    }
    suites = root.findall("testsuite")
    assert [suite.attrib["name"] for suite in suites] == [
        str(tmp_path / "a.yaml"),
        str(tmp_path / "b.yaml"),
    ]
    assert suites[0].find("testcase/failure") is None
    assert suites[1].find("testcase/failure").text == "wrong value"


def test_junit_keeps_case_results_and_adds_snapshot_persistence_error(tmp_path):
    output = tmp_path / "results.xml"
    path = tmp_path / "suite.yaml"
    batch = BatchResult(
        [
            SuiteRun(
                path,
                result=SuiteResult(
                    [CaseResult(name="passes", status=CaseStatus.PASSED, elapsed_ms=5)]
                ),
                error=BatchFailure(
                    BatchFailureCategory.SNAPSHOT,
                    "SnapshotError",
                    "could not write snapshot file",
                ),
            )
        ],
        [],
    )

    write_batch_junit(output, batch)

    root = ET.parse(output).getroot()
    suite = root.find("testsuite")
    assert root.attrib == {
        "tests": "2",
        "failures": "0",
        "errors": "1",
        "skipped": "0",
        "time": "0.005",
    }
    assert suite is not None
    assert suite.attrib == {
        "name": str(path),
        "tests": "2",
        "failures": "0",
        "errors": "1",
        "skipped": "0",
        "time": "0.005",
    }
    error = suite.find("testcase[@name='[suite snapshot]']/error")
    assert error is not None
    assert error.attrib == {
        "type": "snapshot.SnapshotError",
        "message": "could not write snapshot file",
    }


def test_snapshot_mismatch_is_ordinary_junit_failure_with_diff(tmp_path):
    output = tmp_path / "results.xml"
    failure_text = (
        "snapshot: mismatch for 'case'\n"
        "--- expected\n+++ actual\n@@ -1 +1 @@\n-old\n+new"
    )
    batch = BatchResult(
        [
            SuiteRun(
                tmp_path / "suite.yaml",
                result=SuiteResult(
                    [
                        CaseResult(
                            name="case",
                            status=CaseStatus.FAILED,
                            failures=[failure_text],
                        )
                    ]
                ),
            )
        ],
        [],
    )

    write_batch_junit(output, batch)

    failure = ET.parse(output).getroot().find("testsuite/testcase/failure")
    assert failure is not None
    assert failure.text == failure_text
    assert failure.find("error") is None


def test_snapshot_update_pass_has_no_junit_failure(tmp_path):
    output = tmp_path / "results.xml"
    batch = BatchResult(
        [
            SuiteRun(
                tmp_path / "suite.yaml",
                result=SuiteResult([CaseResult("case", CaseStatus.PASSED)]),
            )
        ],
        [],
        snapshot_update_active=True,
    )

    write_batch_junit(output, batch)

    case = ET.parse(output).getroot().find("testsuite/testcase")
    assert case is not None
    assert case.find("failure") is None
    assert case.find("error") is None


def test_filtered_batch_junit_contains_selected_cases_and_runtime_skips_only(
    tmp_path,
):
    output = tmp_path / "filtered.xml"
    result = SuiteResult(
        [
            CaseResult(name="selected", status=CaseStatus.PASSED),
            CaseResult(
                name="selected later",
                status=CaseStatus.SKIPPED,
                skip_reason="session unavailable",
            ),
        ]
    )
    batch = BatchResult(
        [SuiteRun(tmp_path / "suite.yaml", result=result)],
        [],
        selection_active=True,
        selected_cases=2,
        filtered_out_cases=1,
    )

    write_batch_junit(output, batch)

    root = ET.parse(output).getroot()
    assert root.attrib == {
        "tests": "2",
        "failures": "0",
        "errors": "0",
        "skipped": "1",
        "time": "0.000",
    }
    cases = root.findall("testsuite/testcase")
    assert [case.attrib["name"] for case in cases] == [
        "selected",
        "selected later",
    ]
    assert cases[0].find("skipped") is None
    assert cases[1].find("skipped").attrib == {"message": "session unavailable"}


def test_filtered_zero_suite_batch_writes_valid_empty_junit(tmp_path):
    output = tmp_path / "empty.xml"
    batch = BatchResult(
        [],
        [],
        selection_active=True,
        selected_cases=0,
        filtered_out_cases=4,
    )

    write_batch_junit(output, batch)

    root = ET.parse(output).getroot()
    assert root.attrib == {
        "tests": "0",
        "failures": "0",
        "errors": "0",
        "skipped": "0",
        "time": "0.000",
    }
    assert root.findall("testsuite") == []


def test_zero_selected_batch_retains_configuration_error_in_junit(tmp_path):
    output = tmp_path / "error.xml"
    invalid = tmp_path / "invalid.yaml"
    batch = BatchResult(
        [
            SuiteRun(
                invalid,
                error=BatchFailure(
                    BatchFailureCategory.CONFIGURATION,
                    "SpecError",
                    "invalid suite",
                ),
            )
        ],
        [],
        selection_active=True,
        selected_cases=0,
        filtered_out_cases=0,
    )

    write_batch_junit(output, batch)

    root = ET.parse(output).getroot()
    assert root.attrib["tests"] == "1"
    assert root.attrib["errors"] == "1"
    case = root.find("testsuite/testcase")
    assert case is not None
    assert case.attrib["name"] == "[suite configuration]"


def test_write_batch_junit_adds_synthetic_discovery_parse_and_execution_errors(
    tmp_path,
):
    output = tmp_path / "results.xml"
    missing = tmp_path / "missing.yaml"
    invalid = tmp_path / "invalid.yaml"
    crashed = tmp_path / "crashed.yaml"
    batch = BatchResult(
        [
            SuiteRun(
                invalid,
                error=BatchFailure(
                    BatchFailureCategory.CONFIGURATION,
                    "SpecError",
                    "invalid suite",
                ),
            ),
            SuiteRun(
                crashed,
                error=BatchFailure(
                    BatchFailureCategory.EXECUTION,
                    "RuntimeError",
                    "runner crashed",
                ),
            ),
        ],
        [DiscoveryError(missing, "FileNotFoundError", "missing")],
    )

    write_batch_junit(output, batch)

    root = ET.parse(output).getroot()
    assert root.attrib == {
        "tests": "3",
        "failures": "0",
        "errors": "3",
        "skipped": "0",
        "time": "0.000",
    }
    suites = root.findall("testsuite")
    assert [suite.attrib["name"] for suite in suites] == [
        str(missing),
        str(invalid),
        str(crashed),
    ]
    cases = [suite.find("testcase") for suite in suites]
    assert [case.attrib["name"] for case in cases] == [
        "[target configuration]",
        "[suite configuration]",
        "[suite execution]",
    ]
    errors = [case.find("error") for case in cases]
    assert [error.attrib for error in errors] == [
        {"type": "configuration.FileNotFoundError", "message": "missing"},
        {"type": "configuration.SpecError", "message": "invalid suite"},
        {"type": "execution.RuntimeError", "message": "runner crashed"},
    ]


def test_write_junit_compatibility_wrapper_keeps_existing_document(tmp_path):
    output = tmp_path / "results.xml"
    result = SuiteResult(
        [CaseResult(name="passes", status=CaseStatus.PASSED, elapsed_ms=10.0)]
    )

    write_junit(output, "suite.yaml", result)

    root = ET.parse(output).getroot()
    assert root.attrib == {}
    assert [suite.attrib["name"] for suite in root.findall("testsuite")] == [
        "suite.yaml"
    ]


def test_write_junit_records_retry_attempts_as_testcase_properties(tmp_path):
    retried = InfrastructureError(ErrorCategory.TIMEOUT, "MCPError", "timed out")
    result = SuiteResult(
        [
            CaseResult(name="retried", status=CaseStatus.PASSED, elapsed_ms=1.0, retried_errors=(retried,)),
            CaseResult(name="plain", status=CaseStatus.PASSED, elapsed_ms=1.0),
        ]
    )
    path = tmp_path / "report.xml"

    write_junit(path, "suite", result)

    retried_case, plain_case = ET.parse(path).getroot().iter("testcase")
    assert {item.get("name"): item.get("value") for item in retried_case.iter("property")} == {
        "mcp-rig.attempts": "2",
        "mcp-rig.retried.1": "timeout: MCPError: timed out",
    }
    assert plain_case.find("properties") is None
