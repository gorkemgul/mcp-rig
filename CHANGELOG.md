# Changelog

## 0.4.1

### Added

- Local servers can receive environment variables portably. `${NAME}` is
  filled in in the command, `args`, `env` and `cwd`, `${SUITE_DIR}` names the
  suite's directory, and `server.inherit_env` copies listed variables, such as
  proxy settings, when they are set. `check` and `init` accept
  `--env NAME[=VALUE]`. ([#54](https://github.com/gorkemgul/mcp-rig/issues/54))
- A server must start and answer the MCP handshake within
  `server.connect_timeout_s`, 120 s by default, or `--connect-timeout` on
  `check` and `init`. A server that never answers fails with
  `ConnectTimeoutError` instead of hanging.
  ([#65](https://github.com/gorkemgul/mcp-rig/issues/65))
- `check` names the negotiated protocol version, and MCP Rig is tested against
  servers that speak only the 2026-07-28 revision or only the earlier
  handshake revisions. ([#51](https://github.com/gorkemgul/mcp-rig/issues/51))
- `mcp-rig --version`.

### Fixed

- A local server that cannot start says why: command not found, a command that
  is not an MCP server, or a server that wrote non-JSON to stdout, quoting the
  first such line. These were reported as a bare `FileNotFoundError` or
  `Connection closed`. ([#56](https://github.com/gorkemgul/mcp-rig/issues/56))
- The `--server-logs` hint no longer follows errors that already explain
  themselves, such as a setup step that fails its expectation.
  ([#56](https://github.com/gorkemgul/mcp-rig/issues/56))
- MCP SDK log records and tracebacks no longer reach the terminal unless
  `--server-logs` is given. ([#56](https://github.com/gorkemgul/mcp-rig/issues/56))
- `coverage` no longer reports 100% when no server could list its tools.
  ([#56](https://github.com/gorkemgul/mcp-rig/issues/56))
- Batch output shows suite paths relative to the working directory, so the
  header no longer changes with `--case`, `--tag` or `--update-snapshots`.
  ([#56](https://github.com/gorkemgul/mcp-rig/issues/56))

### Changed

- The README explains what MCP Rig finds that response checks miss, compares it
  with MCP Inspector, the conformance suite and LLM eval tools, and starts with
  `uvx`.
- `${NAME}` in a local server's command, `args`, `env` or `cwd` is now
  replaced, and an unset variable is a configuration error. It used to be
  passed through literally.

## 0.4.0

### Added

- `mcp-rig coverage` lists the tools each server advertises that no suite
  calls, with `--min PERCENT` to fail CI below a threshold and `--json` for
  machine-readable output. It only lists tools and never calls one.
  ([#33](https://github.com/gorkemgul/mcp-rig/issues/33))
- A case can set `fault: drop_response` or `fault: disconnect` to lose its
  call's response after the server has handled it, without modifying the
  server. Only the first attempt is faulted, so `retry` and `verify` show
  whether a retry repeats the side effect. Reports and JUnit name the
  injected fault. ([#32](https://github.com/gorkemgul/mcp-rig/issues/32))

### Changed

- The README artwork shows `init`, fault injection, and `coverage`.

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
