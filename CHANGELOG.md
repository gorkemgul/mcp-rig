# Changelog

## 0.3.0

### Added

- Remote servers over Streamable HTTP and SSE: `server.url`, optional
  `headers` with `${ENV_VAR}` interpolation, and `transport: sse`. `check` and
  `init` accept a URL and `--header`. Rejected connections report their HTTP
  status, for example `HTTP 401 Unauthorized`.
  ([#31](https://github.com/gorkemgul/mcp-rig/issues/31))
- `mcp-rig check --ignore CODE[:TOOL]` silences lint warnings everywhere or
  for one tool, so a heuristic false positive no longer blocks `--strict`.
  ([#34](https://github.com/gorkemgul/mcp-rig/issues/34))
- `retry.rerun_setup` runs the suite's setup steps again after a reconnect.
  ([#35](https://github.com/gorkemgul/mcp-rig/issues/35))
- A case can override the suite's `after_timeout` with its own value.
  ([#36](https://github.com/gorkemgul/mcp-rig/issues/36))

### Changed

- `httpx2` is now a declared dependency.

## 0.2.0

### Added

- `mcp-rig init` generates a starter suite from a server's tools: one case per
  tool, typed placeholder arguments from the input schema, and a `side-effect`
  tag on tools that look state-changing.
  ([#27](https://github.com/gorkemgul/mcp-rig/issues/27))
- A composite GitHub Action runs MCP Rig suites in one step:
  `uses: gorkemgul/mcp-rig@v0.2.0`. It installs MCP Rig into its own
  environment so the server's Python stays untouched.
  ([#28](https://github.com/gorkemgul/mcp-rig/issues/28))
- Pushing a `vX.Y.Z` tag creates a draft GitHub Release from the matching
  `CHANGELOG.md` section, and CI checks that every version has release notes.
  ([#30](https://github.com/gorkemgul/mcp-rig/issues/30))

### Changed

- The README now explains how MCP Rig differs from MCP Inspector and starts
  with a quick start. ([#29](https://github.com/gorkemgul/mcp-rig/issues/29))

## 0.1.1

### Added

- Suites can check server state after a call with per-case `verify` steps.
  ([#17](https://github.com/gorkemgul/mcp-rig/issues/17))
- Cases can opt in to retrying their identical call after an infrastructure
  error with `retry.attempts`. After a timeout the retry reuses the session;
  after a closed connection a fresh server is started. Retried attempts appear
  in terminal output and in JUnit testcase properties.
  ([#18](https://github.com/gorkemgul/mcp-rig/issues/18))
- Suites can declare `setup` and `teardown` steps. They are not counted as
  test cases. ([#19](https://github.com/gorkemgul/mcp-rig/issues/19))
- `after_timeout: continue` keeps a suite running on the same session after a
  timeout. ([#21](https://github.com/gorkemgul/mcp-rig/issues/21))
- `mcp-rig check` warns with `retry-unsafe` about side-effecting tools that
  have no idempotency key or `idempotentHint`.
  ([#20](https://github.com/gorkemgul/mcp-rig/issues/20))
- Integration tests and a feature-tour suite cover a side-effecting tool that
  commits and then loses its response. They show that a retry's response can
  pass schema checks while the operation is applied twice.
  ([#16](https://github.com/gorkemgul/mcp-rig/issues/16))
- A `Makefile` runs lint, tests, examples, and `check` through `.venv/bin`.

### Fixed

- A server that exits during startup was reported only as
  `MCPError: Connection closed`. `run` and `check` now point to
  `--server-logs`. ([#22](https://github.com/gorkemgul/mcp-rig/issues/22))

Existing suites behave as before: MCP Rig does not retry, and a suite stops
after any infrastructure error unless it opts in.

## 0.1.0

Initial release.
