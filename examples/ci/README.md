# GitHub Actions

## With the MCP Rig action

The action installs MCP Rig into its own virtual environment, so the Python on
`PATH`, and every package your server needs, stays untouched:

```yaml
name: MCP server tests

on: [push, pull_request]

permissions:
  contents: read

jobs:
  test-mcp-server:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: python -m pip install .
      - uses: gorkemgul/mcp-rig@v0.4.1
        with:
          check: python -m my_mcp_server
          check-args: --strict
          suites: tests/mcp/
          junit: mcp-rig-results.xml
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        if: always()
        with:
          name: mcp-rig-results
          path: mcp-rig-results.xml
```

| Input | Default | Purpose |
| --- | --- | --- |
| `suites` | required | Suite files or directories, separated by spaces or newlines |
| `junit` | none | Path for a JUnit XML report |
| `args` | none | Extra `mcp-rig run` arguments, such as `--tag smoke` |
| `check` | none | Server command for `mcp-rig check`, run before the suites |
| `check-args` | none | Extra `mcp-rig check` arguments, such as `--strict` |
| `version` | the action's version | MCP Rig version to install from PyPI |
| `python` | `python3` | Interpreter for MCP Rig's own environment (3.11 or newer) |

Suite and argument values are split on whitespace, so paths must not contain
spaces. Pin the action to a release tag or commit SHA, just like the other
actions in the workflow.

## Without the action

Install MCP Rig, run every suite recursively, and upload JUnit even when tests
fail:

```yaml
name: MCP server tests

on: [push, pull_request]

permissions:
  contents: read

jobs:
  test-mcp-server:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: python -m pip install . mcp-rig
      - run: mcp-rig check "python -m my_mcp_server" --strict
      - run: mcp-rig run tests/mcp/ --junit mcp-rig-results.xml
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        if: always()
        with:
          name: mcp-rig-results
          path: mcp-rig-results.xml
```

The install step installs both the checked-out server project and MCP Rig; add
your project's test extra when needed, for example `.[test]`. Commit snapshot
sidecars beside their suites; do not use `--update-snapshots` in CI.
