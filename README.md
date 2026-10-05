![MCP Rig — Test your MCP servers. YAML suites connect to an MCP server and produce verified test results.](docs/assets/banner.png)

[![CI](https://github.com/gorkemgul/mcp-rig/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gorkemgul/mcp-rig/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-b8a0ff)](https://github.com/gorkemgul/mcp-rig/blob/main/LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/mcp-rig?logo=pypi&logoColor=white&color=b8a0ff)](https://pypi.org/project/mcp-rig/)
[![Download statistics pending](https://img.shields.io/badge/downloads-awaiting%20stats-91e4ef)](https://pypi.org/project/mcp-rig/)
[![Python versions](https://img.shields.io/pypi/pyversions/mcp-rig?logo=python&logoColor=white&color=91e4ef)](https://pypi.org/project/mcp-rig/)
[![Open issues](https://img.shields.io/github/issues/gorkemgul/mcp-rig?color=b8a0ff)](https://github.com/gorkemgul/mcp-rig/issues?q=is%3Aissue%20is%3Aopen)
[![GitHub stars](https://img.shields.io/github/stars/gorkemgul/mcp-rig?style=flat&color=91e4ef&logo=github&logoColor=white)](https://github.com/gorkemgul/mcp-rig/stargazers)

# MCP Rig

Deterministic, CI-friendly testing for Model Context Protocol servers.

MCP Rig currently launches local MCP servers over stdio and runs declarative
tool suites in YAML.

## CLI in action

Run a YAML suite, select tests by tag, and export a JUnit report for CI:

![Terminal demo of MCP Rig running three passing tests, selecting one test with the smoke tag, and exporting a JUnit report.](docs/assets/cli-demo.gif)

The demo runs the repository's local fixture server. Try it from a development
checkout after completing the [development setup](#development-setup):

```bash
mcp-rig run examples/fixture.yaml
mcp-rig run examples/feature-tour/filtering.yaml --tag smoke
mcp-rig run examples/fixture.yaml --junit results.xml
```

## Installation

Install MCP Rig as an isolated command-line tool with
[pipx](https://pipx.pypa.io/):

```bash
pipx install mcp-rig
```

Or install it into the active Python environment with pip:

```bash
pip install mcp-rig
```

## Development setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
source .venv/bin/activate
```

## Run a suite

```bash
mcp-rig run examples/fixture.yaml
mcp-rig run examples/fixture.yaml --junit results.xml
```

Run several suites by passing more files or a directory. Directories are
searched recursively for `.yaml` and `.yml` files:

```bash
mcp-rig run tests/mcp/smoke.yaml tests/mcp/regression.yaml
mcp-rig run tests/mcp/ --junit results.xml
```

MCP Rig resolves and deduplicates suite paths, then runs them sequentially in
deterministic path order. A configuration or infrastructure error in one suite
does not prevent later suites from running. Batch runs print per-suite results
followed by aggregate suite and case counts. A JUnit file contains one
`<testsuite>` for every suite or invalid target beneath a shared `<testsuites>`
root.

### Filter cases and tags

Suites and individual cases can declare lowercase tags. Suite tags are
inherited by every case, and case tags are added to that inherited set:

```yaml
server: npx @playwright/mcp@latest
tags: [playwright]

tests:
  - name: opens homepage
    tags: [smoke, browser]
    call: browser_navigate
    args:
      url: https://example.com

  - name: captures screenshot
    tags: [slow]
    call: browser_take_screenshot
```

Select cases with case-sensitive shell-style name patterns or effective tags:

```bash
mcp-rig run suites/ --case "opens*"
mcp-rig run suites/ --tag playwright --tag smoke
mcp-rig run suites/ --exclude-tag slow
mcp-rig run suites/ --case "opens*" --tag smoke --exclude-tag flaky
```

Repeated `--case` patterns use OR semantics. Repeated `--tag` options use AND
semantics, so the case must carry every requested tag. A case is filtered out
when it carries any repeated `--exclude-tag` value. Name, included-tag, and
excluded-tag filters combine with AND semantics.

Filtered runs report selection separately from execution, for example
`3 selected, 7 filtered out`. Filtered-out cases are not counted as skipped
and are absent from JUnit; skipped remains reserved for selected cases that
could not run after an infrastructure error. If no cases match, MCP Rig does
not start a server, writes an empty report when `--junit` is requested, and
exits with code `2`.

### Snapshot complete tool responses

Use `snapshot: true` to compare the complete normalized tool result while still
combining it with focused expectations:

```yaml
server: npx @playwright/mcp@latest

tests:
  - name: opens homepage
    call: browser_navigate
    args:
      url: https://example.com
    expect:
      snapshot: true
      contains: Example Domain
```

For a suite named `browser.yaml` or `browser.yml`, MCP Rig stores snapshots in
`browser.snap.yaml` beside the suite. Structured content is stored as stable,
readable YAML; otherwise the complete text response is stored. Error state is
included, while latency is deliberately excluded.

An ordinary run never writes files. The first run therefore fails with a
missing-snapshot assertion. Create or intentionally refresh snapshots with:

```bash
mcp-rig run suites/ --update-snapshots
```

Review the generated `.snap.yaml` Git diff, then commit it with the suite. Later
ordinary runs fail when the response changes and include a unified diff in the
terminal and JUnit report. Snapshot differences return exit code `1`; malformed
or unwritable snapshot files return exit code `2`.

Snapshot updates compose with `--case`, `--tag`, and `--exclude-tag`. A filtered
update changes only selected cases and preserves every unselected entry. A
complete, unfiltered successful update also removes stale entries for cases
that no longer declare snapshots.

See the [real-world server examples](https://github.com/gorkemgul/mcp-rig/tree/main/examples) for
pinned suites that exercise Playwright MCP, the MCP Everything reference server, and the Time
MCP server. External examples are kept out of the main CI path and run in a separate manual and
weekly smoke workflow.

The [complete feature tour](https://github.com/gorkemgul/mcp-rig/tree/main/examples/feature-tour)
provides runnable local examples for every expectation, snapshots, tags and filters, server
configuration, batch runs, JUnit, diagnostics, and `check`. Start with the
[custom-server template](https://github.com/gorkemgul/mcp-rig/tree/main/examples/custom-server-template)
when testing your own stdio server, and use the
[GitHub Actions example](https://github.com/gorkemgul/mcp-rig/tree/main/examples/ci) for CI.

A suite names the stdio server command and the tool calls to verify:

```yaml
server:
  command: python
  args: [path/to/server.py]

tests:
  - name: adds two numbers
    call: add
    args: {a: 2, b: 3}
    expect:
      contains: "5"

  - name: missing user returns an error
    call: get_user
    args: {user_id: 42}
    expect:
      is_error: true
      contains: "not found"
```

Each case expects a successful tool call unless it declares
`is_error: true`. Supported expectations are:

- `contains` / `not_contains`: require or forbid one string or a list of strings.
- `matches`: search response text with a Python regular expression.
- `max_latency_ms`: enforce an inclusive response-time limit.
- `json_path`: compare exact values through dotted mapping keys and numeric list indexes.
- `schema`: validate structured results with JSON Schema Draft 2020-12;
  document-local `#...` references are supported, while external references
  are rejected to keep evaluation offline.
- `snapshot`: compare the complete normalized result with the suite's committed
  `.snap.yaml` sidecar; the value must be `true`.

`is_error: true` accepts both an MCP tool result marked as an error and a
JSON-RPC protocol error returned for that tool call. Timeouts and closed
connections remain infrastructure errors.

JSON checks prefer MCP structured content and otherwise parse response text as
JSON. Each case may set a positive, finite `timeout_s`; the default is 30
seconds. A timeout is an infrastructure error, aborts further calls on the
shared session, and marks later cases as skipped.

Terminal and JUnit reports distinguish four states:

- passed: the call completed and every expectation matched;
- failed: the call completed but one or more expectations did not match;
- error: MCP Rig could not execute the call or suite reliably; and
- skipped: the case was not started after an infrastructure error.

`--junit PATH` writes CI-readable XML without creating missing parent
directories. Invalid targets and suite configuration are represented as
synthetic JUnit errors. Exit codes are `0` for success, `1` for assertion
failures only, and `2` for any configuration, infrastructure, or report-writing
error; code `2` takes precedence when a batch contains both kinds of failure.
Add `--server-logs` to expose every suite server's stderr while diagnosing
startup or tool behavior.

### Retries of side-effecting tools

MCP Rig never retries a call. A timeout or closed connection makes that case an
error and skips the rest of the suite, so a YAML suite cannot express "the
response was lost, now retry." This matters for tools with side effects: a tool
can commit and then lose its response, and a retry's JSON can pass `schema`,
`json_path`, and `snapshot` checks while the operation has happened twice.

Cover this scenario in a Python integration test that checks server state
directly. `tests/test_lost_response_retry.py` is a worked example built on a
fixture whose state lives in a file:

```python
arm_lost_response(ledger, "disconnect")  # the next call commits, then the server exits
with pytest.raises(BaseException):
    async with connect(spec) as probe:
        await probe.call("create_record_idempotent", args)
async with connect(spec) as probe:       # an explicit retry of the same logical call
    retry = await probe.call("create_record_idempotent", args)

assert check({"schema": record_schema}, retry) == []  # the response looks fine
assert len(read_ledger(ledger)["records"]) == 1        # the side effect happened once
```

Set the expected state from the tool's contract. An unprotected tool should
produce two records. A tool that accepts an `idempotency_key` argument should
produce one. That key belongs to the tool's arguments and is not the JSON-RPC
request ID, which changes with every attempt.

## Check a server without a suite

Run protocol checks and inspect tool-definition quality without writing YAML:

```bash
mcp-rig check "python path/to/server.py"
```

The command verifies that the server lists tools, returns an MCP tool error for
an unknown tool, and remains responsive after the negative call. It also warns
about missing or short descriptions, invalid input schemas, undocumented
parameters, and tool descriptions that are likely to be confused with each
other.

Lint warnings are advisory by default. Use `--strict` to make them fail CI:

```bash
mcp-rig check "python path/to/server.py" --strict
```

`--probe-invalid-args` calls every tool that declares required parameters with
an empty argument object and checks that the call is rejected. Use this option
only with development or test servers: a server that does not enforce its
declared schema could execute the tool body.

```bash
mcp-rig check "python path/to/server.py" --probe-invalid-args
```

Server stderr is hidden by default. Add `--server-logs` while diagnosing the
server:

```bash
mcp-rig check "python path/to/server.py" --server-logs
```

For `check`, exit code `0` means all protocol checks passed, `1` means a
protocol check failed or strict lint found warnings, and `2` means the command,
server process, connection, or teardown failed.

This release supports local stdio servers and tools only.

Repository CI tests Python 3.11 through 3.13 and validates both wheel and
source distributions without publishing them.
