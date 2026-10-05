import shlex
import sys
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml

import mcp_rig.cli as cli_module
from mcp_rig.cli import main
from mcp_rig.client import CallOutcome, ToolInfo


def write_suite(tmp_path, fixture_spec, cases, name="suite.yaml", tags=None):
    command = shlex.join([fixture_spec.command, *fixture_spec.args])
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    suite_tags = f"tags: [{', '.join(tags)}]\n" if tags else ""
    path.write_text(
        f"server: {command!r}\n{suite_tags}tests:\n{cases}",
        encoding="utf-8",
    )
    return path


def server_command(spec):
    return shlex.join([spec.command, *spec.args])


PASSING = """\
  - name: adds
    call: add
    args: {a: 1, b: 1}
    expect: {contains: "2"}
"""

FAILING = PASSING + """\
  - name: wrong
    call: echo
    args: {text: hi}
    expect: {contains: bye}
"""

FILTER_CASES = """\
  - name: opens homepage
    tags: [smoke]
    call: echo
    args: {text: homepage}
    expect: {contains: homepage}
  - name: takes screenshot
    tags: [slow]
    call: echo
    args: {text: screenshot}
    expect: {contains: screenshot}
  - name: opens profile
    tags: [smoke, flaky]
    call: echo
    args: {text: profile}
    expect: {contains: profile}
"""


def snapshot_cases(first: str = "first", second: str | None = None) -> str:
    cases = f"""\
  - name: first snapshot
    tags: [smoke]
    call: echo
    args: {{text: {first}}}
    expect: {{snapshot: true}}
"""
    if second is not None:
        cases += f"""\
  - name: second snapshot
    tags: [slow]
    call: echo
    args: {{text: {second}}}
    expect: {{snapshot: true}}
"""
    return cases


def test_run_filters_by_exact_case_name(tmp_path, fixture_spec, capsys):
    path = write_suite(tmp_path, fixture_spec, FILTER_CASES)

    assert main(["run", str(path), "--case", "opens homepage"]) == 0
    captured = capsys.readouterr()
    assert "✓ opens homepage" in captured.out
    assert "takes screenshot" not in captured.out
    assert "opens profile" not in captured.out
    assert "Selection: 1 selected, 2 filtered out" in captured.out


def test_run_case_globs_and_repeated_patterns_use_or(tmp_path, fixture_spec, capsys):
    path = write_suite(tmp_path, fixture_spec, FILTER_CASES)

    assert main(
        [
            "run",
            str(path),
            "--case",
            "opens*",
            "--case",
            "takes screenshot",
        ]
    ) == 0
    captured = capsys.readouterr()
    assert "✓ opens homepage" in captured.out
    assert "✓ takes screenshot" in captured.out
    assert "✓ opens profile" in captured.out
    assert "Selection: 3 selected, 0 filtered out" in captured.out


def test_run_repeated_tags_require_all_and_inherit_suite_tags(
    tmp_path, fixture_spec, capsys
):
    path = write_suite(
        tmp_path,
        fixture_spec,
        FILTER_CASES,
        tags=["playwright"],
    )

    assert main(
        ["run", str(path), "--tag", "playwright", "--tag", "smoke"]
    ) == 0
    captured = capsys.readouterr()
    assert "✓ opens homepage" in captured.out
    assert "✓ opens profile" in captured.out
    assert "takes screenshot" not in captured.out
    assert "Selection: 2 selected, 1 filtered out" in captured.out


def test_run_excludes_cases_matching_any_excluded_tag(tmp_path, fixture_spec, capsys):
    path = write_suite(tmp_path, fixture_spec, FILTER_CASES)

    assert main(
        ["run", str(path), "--exclude-tag", "slow", "--exclude-tag", "flaky"]
    ) == 0
    captured = capsys.readouterr()
    assert "✓ opens homepage" in captured.out
    assert "takes screenshot" not in captured.out
    assert "opens profile" not in captured.out
    assert "Selection: 1 selected, 2 filtered out" in captured.out


def test_run_combines_case_include_and_exclude_filters(
    tmp_path, fixture_spec, capsys
):
    path = write_suite(
        tmp_path,
        fixture_spec,
        FILTER_CASES,
        tags=["playwright"],
    )

    assert main(
        [
            "run",
            str(path),
            "--case",
            "opens*",
            "--tag",
            "playwright",
            "--tag",
            "smoke",
            "--exclude-tag",
            "flaky",
        ]
    ) == 0
    captured = capsys.readouterr()
    assert "✓ opens homepage" in captured.out
    assert "takes screenshot" not in captured.out
    assert "opens profile" not in captured.out
    assert "Selection: 1 selected, 2 filtered out" in captured.out


def test_run_duplicate_cli_tags_use_set_semantics(tmp_path, fixture_spec, capsys):
    path = write_suite(
        tmp_path,
        fixture_spec,
        FILTER_CASES,
        tags=["playwright"],
    )

    assert main(
        ["run", str(path), "--tag", "playwright", "--tag", "playwright"]
    ) == 0
    captured = capsys.readouterr()
    assert "Selection: 3 selected, 0 filtered out" in captured.out


def test_snapshot_create_verify_and_mismatch_workflow(
    tmp_path,
    fixture_spec,
    capsys,
):
    suite = write_suite(tmp_path, fixture_spec, snapshot_cases())
    sidecar = tmp_path / "suite.snap.yaml"
    report = tmp_path / "results.xml"

    assert main(["run", str(suite)]) == 1
    captured = capsys.readouterr()
    assert "snapshot: missing entry for 'first snapshot'" in captured.out
    assert sidecar.exists() is False

    assert main(["run", str(suite), "--update-snapshots"]) == 0
    captured = capsys.readouterr()
    assert "Snapshots: 1 added, 0 updated, 0 unchanged, 0 removed" in captured.out
    assert sidecar.exists()

    assert main(["run", str(suite)]) == 0
    assert "✓ first snapshot" in capsys.readouterr().out
    before_mismatch = sidecar.read_bytes()
    suite.write_text(
        suite.read_text(encoding="utf-8").replace("text: first", "text: second"),
        encoding="utf-8",
    )

    assert main(["run", str(suite), "--junit", str(report)]) == 1
    captured = capsys.readouterr()
    assert "snapshot: mismatch for 'first snapshot'" in captured.out
    assert sidecar.read_bytes() == before_mismatch
    failure = ET.parse(report).getroot().find("testsuite/testcase/failure")
    assert failure is not None
    assert "--- expected" in failure.text
    assert "+++ actual" in failure.text


def test_filtered_snapshot_update_preserves_unselected_entry(
    tmp_path,
    fixture_spec,
    capsys,
):
    suite = write_suite(
        tmp_path,
        fixture_spec,
        snapshot_cases("old-smoke", "old-slow"),
    )
    sidecar = tmp_path / "suite.snap.yaml"
    assert main(["run", str(suite), "--update-snapshots"]) == 0
    capsys.readouterr()
    suite.write_text(
        suite.read_text(encoding="utf-8")
        .replace("old-smoke", "new-smoke")
        .replace("old-slow", "new-slow"),
        encoding="utf-8",
    )

    assert main(
        ["run", str(suite), "--tag", "smoke", "--update-snapshots"]
    ) == 0
    captured = capsys.readouterr()
    contents = sidecar.read_text(encoding="utf-8")
    assert "Selection: 1 selected, 1 filtered out" in captured.out
    assert "Snapshots: 0 added, 1 updated, 0 unchanged, 0 removed" in captured.out
    assert "new-smoke" in contents
    assert "old-slow" in contents
    assert "new-slow" not in contents


def test_full_snapshot_update_prunes_stale_entry(tmp_path, fixture_spec, capsys):
    suite = write_suite(tmp_path, fixture_spec, snapshot_cases("one", "two"))
    sidecar = tmp_path / "suite.snap.yaml"
    assert main(["run", str(suite), "--update-snapshots"]) == 0
    capsys.readouterr()
    write_suite(tmp_path, fixture_spec, snapshot_cases("one"))

    assert main(["run", str(suite), "--update-snapshots"]) == 0
    captured = capsys.readouterr()
    snapshots = yaml.safe_load(sidecar.read_text(encoding="utf-8"))["snapshots"]
    assert "Snapshots: 0 added, 0 updated, 1 unchanged, 1 removed" in captured.out
    assert list(snapshots) == ["first snapshot"]


def test_full_snapshot_update_deletes_sidecar_after_last_declaration_is_removed(
    tmp_path,
    fixture_spec,
    capsys,
):
    suite = write_suite(tmp_path, fixture_spec, snapshot_cases())
    sidecar = tmp_path / "suite.snap.yaml"
    assert main(["run", str(suite), "--update-snapshots"]) == 0
    capsys.readouterr()
    write_suite(tmp_path, fixture_spec, PASSING)

    assert main(["run", str(suite), "--update-snapshots"]) == 0
    captured = capsys.readouterr()
    assert "Snapshots: 0 added, 0 updated, 0 unchanged, 1 removed" in captured.out
    assert sidecar.exists() is False


def test_malformed_snapshot_exits_two_without_traceback(
    tmp_path,
    fixture_spec,
    capsys,
):
    suite = write_suite(tmp_path, fixture_spec, snapshot_cases())
    (tmp_path / "suite.snap.yaml").write_text(
        "version: true\nsnapshots: {}\n",
        encoding="utf-8",
    )

    assert main(["run", str(suite)]) == 2
    captured = capsys.readouterr()
    assert "'version' must be the integer 1" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_snapshot_write_error_exits_two_and_keeps_completed_result(
    tmp_path,
    fixture_spec,
    capsys,
    monkeypatch,
):
    suite = write_suite(tmp_path, fixture_spec, snapshot_cases())

    def failing_replace(source, destination):
        raise OSError("replace denied")

    monkeypatch.setattr("mcp_rig.snapshots.os.replace", failing_replace)

    assert main(["run", str(suite), "--update-snapshots"]) == 2
    captured = capsys.readouterr()
    assert "✓ first snapshot" in captured.out
    assert "replace denied" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_snapshot_error_does_not_prevent_later_suite_execution(
    tmp_path,
    fixture_spec,
    capsys,
):
    invalid = write_suite(
        tmp_path,
        fixture_spec,
        snapshot_cases(),
        name="a-invalid.yaml",
    )
    (tmp_path / "a-invalid.snap.yaml").write_text("malformed", encoding="utf-8")
    valid = write_suite(tmp_path, fixture_spec, PASSING, name="b-valid.yaml")

    assert main(["run", str(invalid), str(valid)]) == 2
    captured = capsys.readouterr()
    assert "a-invalid.snap.yaml" in captured.err
    assert "✓ adds" in captured.out


def test_invalid_utf8_snapshot_exits_two_and_later_suite_runs(
    tmp_path,
    fixture_spec,
    capsys,
):
    invalid = write_suite(
        tmp_path,
        fixture_spec,
        snapshot_cases(),
        name="a-invalid.yaml",
    )
    (tmp_path / "a-invalid.snap.yaml").write_bytes(b"\xff\xfe")
    valid = write_suite(tmp_path, fixture_spec, PASSING, name="b-valid.yaml")

    assert main(["run", str(invalid), str(valid)]) == 2
    captured = capsys.readouterr()
    assert "a-invalid.snap.yaml" in captured.err
    assert "could not read snapshot file" in captured.err
    assert "✓ adds" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_no_snapshot_run_keeps_existing_output_without_snapshot_summary(
    tmp_path,
    fixture_spec,
    capsys,
):
    suite = write_suite(tmp_path, fixture_spec, PASSING)

    assert main(["run", str(suite)]) == 0
    captured = capsys.readouterr()
    assert "✓ adds" in captured.out
    assert "1 passed, 0 failed, 0 errors, 0 skipped" in captured.out
    assert "Snapshots:" not in captured.out


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--tag", "Smoke"), ("--exclude-tag", "browser tools")],
)
def test_run_rejects_invalid_cli_tags_before_discovery(
    tmp_path, capsys, flag, value
):
    missing = tmp_path / "missing.yaml"

    with pytest.raises(SystemExit) as raised:
        main(["run", str(missing), flag, value])

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert "must match [a-z0-9][a-z0-9_-]*" in captured.err
    assert "does not exist" not in captured.err


def test_run_zero_match_writes_empty_junit_without_starting_server(
    tmp_path, fixture_spec, fixture_server_path, capsys
):
    marker = tmp_path / "server-started"
    wrapper = tmp_path / "marker_server.py"
    wrapper.write_text(
        "from pathlib import Path\n"
        "import runpy\n"
        f"Path({str(marker)!r}).write_text('started', encoding='utf-8')\n"
        f"runpy.run_path({str(fixture_server_path)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    marker_spec = type(fixture_spec)(fixture_spec.command, [str(wrapper)])
    path = write_suite(
        tmp_path,
        marker_spec,
        FILTER_CASES,
        tags=["playwright"],
    )
    report = tmp_path / "results.xml"

    assert main(
        [
            "run",
            str(path),
            "--tag",
            "smoke",
            "--exclude-tag",
            "smoke",
            "--junit",
            str(report),
        ]
    ) == 2
    captured = capsys.readouterr()
    root = ET.parse(report).getroot()
    assert "error: filters matched no test cases" in captured.err
    assert "Selection: 0 selected, 3 filtered out" in captured.out
    assert root.attrib["tests"] == "0"
    assert root.findall("testsuite") == []
    assert marker.exists() is False


def test_run_filters_entire_suite_without_hiding_selected_suite(
    tmp_path, fixture_spec, capsys
):
    filtered = write_suite(
        tmp_path,
        fixture_spec,
        PASSING.replace("name: adds", "name: filtered case") + "    tags: [slow]\n",
        "a-filtered.yaml",
    )
    selected = write_suite(
        tmp_path,
        fixture_spec,
        PASSING.replace("name: adds", "name: selected case") + "    tags: [smoke]\n",
        "b-selected.yaml",
    )

    assert main(["run", str(filtered), str(selected), "--tag", "smoke"]) == 0
    captured = capsys.readouterr()
    assert "filtered case" not in captured.out
    assert "✓ selected case" in captured.out
    assert "Selection: 1 selected, 1 filtered out" in captured.out


def test_run_filtered_batch_keeps_malformed_suite_visible(
    tmp_path, fixture_spec, capsys
):
    invalid = tmp_path / "a-invalid.yaml"
    invalid.write_text("server: python server.py\ntests: []\n", encoding="utf-8")
    valid = write_suite(
        tmp_path,
        fixture_spec,
        PASSING.replace("name: adds", "name: selected case") + "    tags: [smoke]\n",
        "b-valid.yaml",
    )

    assert main(["run", str(invalid), str(valid), "--tag", "smoke"]) == 2
    captured = capsys.readouterr()
    assert "a-invalid.yaml" in captured.err
    assert "✓ selected case" in captured.out
    assert "Selection: 1 selected, 0 filtered out" in captured.out


def test_run_passing_suite_exits_zero(tmp_path, fixture_spec, capsys):
    code = main(["run", str(write_suite(tmp_path, fixture_spec, PASSING))])

    captured = capsys.readouterr()
    assert code == 0
    assert "✓ adds" in captured.out
    assert "1 passed, 0 failed" in captured.out
    assert "\033[" not in captured.out


def test_run_failing_suite_exits_one_and_reports_all_cases(tmp_path, fixture_spec, capsys):
    code = main(["run", str(write_suite(tmp_path, fixture_spec, FAILING))])

    captured = capsys.readouterr()
    assert code == 1
    assert "✓ adds" in captured.out
    assert "✗ wrong" in captured.out
    assert "1 passed, 1 failed" in captured.out


def test_run_invalid_or_missing_suite_exits_two(tmp_path, capsys):
    missing = tmp_path / "missing.yaml"

    assert main(["run", str(missing)]) == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "Traceback" not in captured.err


def test_run_unstartable_server_exits_two(tmp_path, capsys):
    path = tmp_path / "suite.yaml"
    path.write_text(
        "server: /definitely/missing/mcp-rig-server\n"
        "tests:\n  - {name: never, call: echo}\n",
        encoding="utf-8",
    )

    assert main(["run", str(path)]) == 2
    captured = capsys.readouterr()
    assert "! suite setup:" in captured.out
    assert "- never" in captured.out
    assert "suite could not start" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_server_logs_are_hidden_by_default_and_visible_with_flag(tmp_path, fixture_spec, capfd):
    cases = """\
  - name: writes log
    call: write_stderr
    args: {message: mcp-rig-cli-server-log}
    expect: {contains: written}
"""
    path = write_suite(tmp_path, fixture_spec, cases)

    assert main(["run", str(path)]) == 0
    assert "mcp-rig-cli-server-log" not in capfd.readouterr().err
    assert main(["run", str(path), "--server-logs"]) == 0
    assert "mcp-rig-cli-server-log" in capfd.readouterr().err


def test_run_advanced_expectations_through_public_cli(tmp_path, fixture_spec, capsys):
    cases = """\
  - name: validates user payload
    call: get_user
    args: {user_id: 1}
    expect:
      is_error: false
      contains: Ada
      not_contains: password
      matches: Ada
      max_latency_ms: 5000
      json_path:
        name: Ada
        roles.0: admin
      schema:
        type: object
        required: [id, name, roles]
        properties:
          id: {type: integer}
          name: {type: string}
          roles: {type: array}
"""
    path = write_suite(tmp_path, fixture_spec, cases)

    assert main(["run", str(path)]) == 0
    captured = capsys.readouterr()
    assert "✓ validates user payload" in captured.out
    assert "1 passed, 0 failed" in captured.out


def test_run_timeout_exits_two_and_reports_skipped_cases(tmp_path, fixture_spec, capsys):
    cases = """\
  - name: too slow
    call: slow
    args: {seconds: 0.2}
    timeout_s: 0.01
  - name: never runs
    call: echo
    args: {text: after}
"""
    path = write_suite(tmp_path, fixture_spec, cases)

    assert main(["run", str(path)]) == 2
    captured = capsys.readouterr()
    assert "! too slow" in captured.out
    assert "- never runs" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_run_writes_passing_junit_report(tmp_path, fixture_spec, capsys):
    suite_path = write_suite(tmp_path, fixture_spec, PASSING)
    report_path = tmp_path / "results.xml"

    assert main(["run", str(suite_path), "--junit", str(report_path)]) == 0
    captured = capsys.readouterr()
    suite = ET.parse(report_path).getroot().find("testsuite")
    assert suite is not None
    assert suite.attrib["failures"] == "0"
    assert suite.attrib["errors"] == "0"
    assert "✓ adds" in captured.out


def test_run_writes_failing_junit_report_and_exits_one(tmp_path, fixture_spec, capsys):
    suite_path = write_suite(tmp_path, fixture_spec, FAILING)
    report_path = tmp_path / "results.xml"

    assert main(["run", str(suite_path), "--junit", str(report_path)]) == 1
    captured = capsys.readouterr()
    suite = ET.parse(report_path).getroot().find("testsuite")
    assert suite is not None
    failure = suite.findall("testcase")[1].find("failure")
    assert failure is not None
    assert "contains: 'bye' not found in 'hi'" in failure.text
    assert "✗ wrong" in captured.out


def test_run_writes_timeout_and_skipped_junit_results(tmp_path, fixture_spec):
    cases = """\
  - name: too slow
    call: slow
    args: {seconds: 0.2}
    timeout_s: 0.01
  - name: never runs
    call: echo
    args: {text: after}
"""
    suite_path = write_suite(tmp_path, fixture_spec, cases)
    report_path = tmp_path / "results.xml"

    assert main(["run", str(suite_path), "--junit", str(report_path)]) == 2
    suite = ET.parse(report_path).getroot().find("testsuite")
    assert suite is not None
    assert suite.attrib["errors"] == "1"
    assert suite.attrib["skipped"] == "1"
    first, second = suite.findall("testcase")
    assert first.find("error") is not None
    assert second.find("skipped") is not None


def test_run_writes_setup_error_junit_report(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        "server: /definitely/missing/mcp-rig-server\n"
        "tests:\n  - {name: never, call: echo}\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "results.xml"

    assert main(["run", str(suite_path), "--junit", str(report_path)]) == 2
    suite = ET.parse(report_path).getroot().find("testsuite")
    assert suite is not None
    cases = suite.findall("testcase")
    assert [case.attrib["name"] for case in cases] == ["never", "[suite setup]"]
    assert cases[0].find("skipped") is not None
    assert cases[1].find("error") is not None


def test_invalid_configuration_writes_synthetic_junit_report(tmp_path, capsys):
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("server: python server.py\ntests: []\n", encoding="utf-8")
    report_path = tmp_path / "results.xml"

    assert main(["run", str(invalid), "--junit", str(report_path)]) == 2
    captured = capsys.readouterr()
    suite = ET.parse(report_path).getroot().find("testsuite")
    assert suite is not None
    case = suite.find("testcase")
    assert case is not None
    assert case.attrib["name"] == "[suite configuration]"
    assert case.find("error") is not None
    assert "error:" in captured.err


def test_unwritable_junit_path_exits_two_after_printing_terminal_result(tmp_path, fixture_spec, capsys):
    suite_path = write_suite(tmp_path, fixture_spec, PASSING)
    report_path = tmp_path / "missing" / "results.xml"

    assert main(["run", str(suite_path), "--junit", str(report_path)]) == 2
    captured = capsys.readouterr()
    assert "✓ adds" in captured.out
    assert "could not write JUnit report" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_junit_path_cannot_overwrite_suite_file(tmp_path, capsys):
    suite_path = tmp_path / "suite.yaml"
    original = (
        "server: /definitely/missing/mcp-rig-server\n"
        "tests:\n  - {name: never, call: echo}\n"
    )
    suite_path.write_text(original, encoding="utf-8")

    assert main(["run", str(suite_path), "--junit", str(suite_path)]) == 2
    captured = capsys.readouterr()
    assert suite_path.read_text(encoding="utf-8") == original
    assert "JUnit report path must differ from suite path" in captured.err
    assert "could not run server" not in captured.err


@pytest.mark.parametrize("sidecar_exists", [False, True])
def test_junit_path_cannot_overwrite_suite_snapshot_sidecar(
    tmp_path,
    fixture_spec,
    capsys,
    sidecar_exists,
):
    suite_path = write_suite(tmp_path, fixture_spec, PASSING)
    sidecar = tmp_path / "suite.snap.yaml"
    original = b"version: 1\nsnapshots: {}\n"
    if sidecar_exists:
        sidecar.write_bytes(original)

    assert main(["run", str(suite_path), "--junit", str(sidecar)]) == 2
    captured = capsys.readouterr()
    if sidecar_exists:
        assert sidecar.read_bytes() == original
    else:
        assert sidecar.exists() is False
    assert "JUnit report path must differ from suite snapshot path" in captured.err
    assert "✓ adds" not in captured.out


def test_run_accepts_multiple_files_and_recursive_directory_targets(
    tmp_path, fixture_spec, capsys
):
    suites = tmp_path / "suites"
    direct = write_suite(
        suites,
        fixture_spec,
        PASSING.replace("name: adds", "name: direct case"),
        "z.yaml",
    )
    nested = write_suite(
        suites,
        fixture_spec,
        PASSING.replace("name: adds", "name: nested case"),
        "nested/a.yml",
    )

    assert main(["run", str(direct), str(suites)]) == 0
    captured = capsys.readouterr()
    assert captured.out.index(str(nested.resolve())) < captured.out.index(
        str(direct.resolve())
    )
    assert "✓ nested case" in captured.out
    assert "✓ direct case" in captured.out
    assert "Suites: 2 passed, 0 failed, 0 errors" in captured.out


def test_run_continues_after_invalid_suite_and_returns_two(
    tmp_path, fixture_spec, capsys
):
    invalid = tmp_path / "a-invalid.yaml"
    invalid.write_text("server: python server.py\ntests: []\n", encoding="utf-8")
    valid = write_suite(
        tmp_path,
        fixture_spec,
        PASSING.replace("name: adds", "name: runs after invalid"),
        "b-valid.yaml",
    )

    assert main(["run", str(invalid), str(valid)]) == 2
    captured = capsys.readouterr()
    assert "a-invalid.yaml" in captured.err
    assert "'tests' must be a non-empty list" in captured.err
    assert "✓ runs after invalid" in captured.out
    assert "Suites: 1 passed, 0 failed, 1 error" in captured.out


def test_run_mixed_assertion_and_infrastructure_failures_returns_two(
    tmp_path, fixture_spec, capsys
):
    failing = write_suite(tmp_path, fixture_spec, FAILING, "a-failing.yaml")
    broken = tmp_path / "b-broken.yaml"
    broken.write_text(
        "server: /definitely/missing/mcp-rig-server\n"
        "tests:\n  - {name: never, call: echo}\n",
        encoding="utf-8",
    )
    passing = write_suite(
        tmp_path,
        fixture_spec,
        PASSING.replace("name: adds", "name: still runs"),
        "c-passing.yaml",
    )

    assert main(["run", str(failing), str(broken), str(passing)]) == 2
    captured = capsys.readouterr()
    assert "✗ wrong" in captured.out
    assert "! suite setup:" in captured.out
    assert "✓ still runs" in captured.out
    assert "Suites: 1 passed, 1 failed, 1 error" in captured.out


def test_run_writes_synthetic_junit_for_missing_or_invalid_targets(
    tmp_path, capsys
):
    missing = tmp_path / "missing.yaml"
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("server: python server.py\ntests: []\n", encoding="utf-8")
    report = tmp_path / "results.xml"

    assert main(
        ["run", str(missing), str(invalid), "--junit", str(report)]
    ) == 2
    capsys.readouterr()
    suites = ET.parse(report).getroot().findall("testsuite")
    assert [suite.find("testcase").attrib["name"] for suite in suites] == [
        "[target configuration]",
        "[suite configuration]",
    ]


def test_junit_path_cannot_overwrite_suite_discovered_inside_directory(
    tmp_path, capsys
):
    suites = tmp_path / "suites"
    suites.mkdir()
    report = suites / "report.yaml"
    original = (
        "server: /definitely/missing/mcp-rig-server\n"
        "tests:\n  - {name: never, call: echo}\n"
    )
    report.write_text(original, encoding="utf-8")

    assert main(["run", str(suites), "--junit", str(report)]) == 2
    captured = capsys.readouterr()
    assert report.read_text(encoding="utf-8") == original
    assert "JUnit report path must differ from suite path" in captured.err
    assert "suite setup" not in captured.out


def test_server_logs_flag_applies_to_every_suite(tmp_path, fixture_spec, capfd):
    cases = """\
  - name: writes log
    call: write_stderr
    args: {message: LOG_MESSAGE}
    expect: {contains: written}
"""
    write_suite(
        tmp_path,
        fixture_spec,
        cases.replace("LOG_MESSAGE", "first-batch-log"),
        "a.yaml",
    )
    write_suite(
        tmp_path,
        fixture_spec,
        cases.replace("LOG_MESSAGE", "second-batch-log"),
        "b.yaml",
    )

    assert main(["run", str(tmp_path)]) == 0
    assert "batch-log" not in capfd.readouterr().err
    assert main(["run", str(tmp_path), "--server-logs"]) == 0
    captured = capfd.readouterr()
    assert "first-batch-log" in captured.err
    assert "second-batch-log" in captured.err


def test_run_single_file_output_remains_unchanged(
    tmp_path, fixture_spec, capsys, monkeypatch
):
    write_suite(tmp_path, fixture_spec, PASSING)
    monkeypatch.chdir(tmp_path)

    assert main(["run", "suite.yaml"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0] == "MCP Rig — suite.yaml"
    assert "✓ adds (" in captured.out
    assert lines[-1] == "1 passed, 0 failed, 0 errors, 0 skipped"
    assert "Suites:" not in captured.out


def test_invalid_advanced_expectation_fails_before_server_startup(tmp_path, capsys):
    path = tmp_path / "suite.yaml"
    path.write_text(
        "server: /definitely/missing/mcp-rig-server\n"
        "tests:\n"
        "  - name: never\n"
        "    call: echo\n"
        "    expect:\n"
        "      matches: '[unclosed'\n",
        encoding="utf-8",
    )

    assert main(["run", str(path)]) == 2
    captured = capsys.readouterr()
    assert "invalid 'matches' regular expression" in captured.err
    assert "could not run server" not in captured.err
    assert "Traceback" not in captured.err


def test_check_passes_with_lint_warnings(fixture_spec, capsys):
    code = main(["check", server_command(fixture_spec)])

    captured = capsys.readouterr()
    assert code == 0
    assert "✓ lists tools" in captured.out
    assert "✓ unknown tool returns an error" in captured.out
    assert "✓ server alive after bad calls" in captured.out
    assert "undocumented [no-description]" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_check_strict_fails_on_lint_warnings(fixture_spec, capsys):
    code = main(["check", server_command(fixture_spec), "--strict"])

    captured = capsys.readouterr()
    assert code == 1
    assert "3/3 checks passed" in captured.out
    assert "undocumented [no-description]" in captured.out


def test_check_probe_invalid_args_reports_required_argument_checks(fixture_spec, capsys):
    code = main(
        [
            "check",
            server_command(fixture_spec),
            "--probe-invalid-args",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "✓ add: missing required args rejected" in captured.out
    assert "✓ get_user: missing required args rejected" in captured.out


def test_check_empty_server_command_exits_two_without_traceback(capsys):
    code = main(["check", ""])

    captured = capsys.readouterr()
    assert code == 2
    assert "error:" in captured.err
    assert "server command is empty" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_check_unstartable_server_exits_two_without_traceback(capsys):
    code = main(["check", "/definitely/missing/mcp-rig-server"])

    captured = capsys.readouterr()
    assert code == 2
    assert "error:" in captured.err
    assert "could not run server" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_check_server_logs_are_hidden_by_default_and_visible_with_flag(
    fixture_spec,
    capfd,
):
    wrapper = Path(__file__).parent / "fixtures" / "stderr_server.py"
    command = shlex.join([fixture_spec.command, str(wrapper)])

    assert main(["check", command]) == 0
    assert "mcp-rig-check-server-log" not in capfd.readouterr().err

    assert main(["check", command, "--server-logs"]) == 0
    assert "mcp-rig-check-server-log" in capfd.readouterr().err


@pytest.mark.parametrize(("strict", "expected_code"), [(False, 0), (True, 1)])
def test_check_handles_valid_boolean_property_schema(
    monkeypatch,
    capsys,
    strict,
    expected_code,
):
    class BooleanSchemaProbe:
        async def list_tools(self):
            return [
                ToolInfo(
                    "boolean_property",
                    "Return a value accepted by the schema.",
                    {"type": "object", "properties": {"value": True}},
                )
            ]

        async def call(self, name, args=None, timeout_s=30.0):
            return CallOutcome(True, "unknown tool", None, 1.0)

    @asynccontextmanager
    async def fake_connect(spec, show_server_logs=False):
        yield BooleanSchemaProbe()

    monkeypatch.setattr(cli_module, "connect", fake_connect)
    argv = ["check", "fixture-server"]
    if strict:
        argv.append("--strict")

    code = main(argv)

    captured = capsys.readouterr()
    assert code == expected_code
    assert "3/3 checks passed, 1 lint warning" in captured.out
    assert "boolean_property [param-no-description]" in captured.out
    assert "error:" not in captured.err


def write_crashing_server_suite(tmp_path):
    crash = tmp_path / "crash.py"
    crash.write_text("import sys\nprint('ImportError: incompatible mcp', file=sys.stderr)\nsys.exit(1)\n")
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        f"server: {shlex.join([sys.executable, str(crash)])}\ntests:\n  - {{name: a, call: add}}\n",
        encoding="utf-8",
    )
    return suite


def test_run_hints_at_server_logs_when_the_server_exits_during_startup(tmp_path, capfd):
    code = main(["run", str(write_crashing_server_suite(tmp_path))])

    captured = capfd.readouterr()
    assert code == 2
    assert "suite setup" in captured.out
    assert cli_module.SERVER_LOGS_HINT in captured.err
    assert "incompatible mcp" not in captured.err


def test_run_omits_server_logs_hint_when_logs_are_visible(tmp_path, capfd):
    code = main(["run", str(write_crashing_server_suite(tmp_path)), "--server-logs"])

    captured = capfd.readouterr()
    assert code == 2
    assert "incompatible mcp" in captured.err
    assert cli_module.SERVER_LOGS_HINT not in captured.err


def test_run_omits_server_logs_hint_after_a_healthy_run(tmp_path, fixture_spec, capsys):
    suite = write_suite(tmp_path, fixture_spec, PASSING)

    assert main(["run", str(suite)]) == 0
    assert cli_module.SERVER_LOGS_HINT not in capsys.readouterr().err


def test_check_unstartable_server_hints_at_server_logs(capsys):
    assert main(["check", "/definitely/missing/mcp-rig-server"]) == 2
    assert cli_module.SERVER_LOGS_HINT in capsys.readouterr().err
