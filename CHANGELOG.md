# Changelog

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
