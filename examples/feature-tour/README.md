# Complete feature tour

These deterministic suites exercise every YAML-suite feature against MCP Rig's
local fixture server. Run them from the repository root after installing the
development environment:

```bash
mcp-rig run examples/feature-tour/
```

## Suites

- `assertions.yaml` demonstrates every focused expectation: expected errors,
  text inclusion/exclusion, regular expressions, latency, JSON paths, JSON
  Schema, and a per-case timeout.
- `filtering.yaml` demonstrates suite and case tags.
- `diagnostics.yaml` emits a controlled server diagnostic for `--server-logs`.
- `snapshots.yaml` demonstrates full-response snapshots with its committed
  `snapshots.snap.yaml` baseline.
- `server-config.yaml` demonstrates structured `command`, `args`, `cwd`, and
  `env` server configuration.
- `state-and-retries.yaml` demonstrates `setup`, `teardown`, `retry`, and
  `verify` against a ledger fixture that commits a record and then loses its
  response. The unprotected tool creates a duplicate; the idempotent tool does not.

## Run and report commands

Run one suite, several suites, or the complete directory:

```bash
mcp-rig run examples/feature-tour/assertions.yaml
mcp-rig run examples/feature-tour/assertions.yaml examples/feature-tour/filtering.yaml
mcp-rig run examples/feature-tour/
```

Write CI-readable JUnit and expose server stderr while diagnosing a failure:

```bash
mcp-rig run examples/feature-tour/ --junit mcp-rig-results.xml
mcp-rig run examples/feature-tour/diagnostics.yaml --server-logs
```

Select cases by case-sensitive shell pattern and effective tags:

```bash
mcp-rig run examples/feature-tour/filtering.yaml --case "inherits*"
mcp-rig run examples/feature-tour/filtering.yaml --tag feature-tour --tag smoke
mcp-rig run examples/feature-tour/filtering.yaml --exclude-tag slow
```

Refresh snapshots intentionally, review the Git diff, and then verify them in
ordinary read-only mode:

```bash
mcp-rig run examples/feature-tour/snapshots.yaml --update-snapshots
git diff -- examples/feature-tour/snapshots.snap.yaml
mcp-rig run examples/feature-tour/snapshots.yaml
```

## Check commands

Check the server without a suite:

```bash
mcp-rig check "python tests/fixtures/fixture_server.py"
mcp-rig check "python tests/fixtures/fixture_server.py" --strict
mcp-rig check "python tests/fixtures/fixture_server.py" --probe-invalid-args
mcp-rig check "python tests/fixtures/fixture_server.py" --server-logs
```

The fixture intentionally contains an undocumented tool, so the `--strict`
example demonstrates a non-zero lint result. Use `--probe-invalid-args` only
against development/test servers because it calls tools with empty arguments.

Exit code `0` means success, `1` means completed assertions or strict checks
failed, and `2` means configuration, process, transport, timeout, or report
infrastructure failed.
