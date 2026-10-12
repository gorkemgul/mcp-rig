# Troubleshooting MCP Rig

Exit code `2` means MCP Rig could not run a server or suite. Read the first
error line; it usually names the cause. Add `--server-logs` to see the server's
stderr.

## The server will not start

| Error | Cause | Fix |
| --- | --- | --- |
| `command not found: X` | The command is not on `PATH` | Use the full path, or install the runtime |
| `the server wrote non-JSON to stdout: '...'` | The server printed or logged to stdout, which carries the protocol | Send logs and prints to stderr in the server |
| `answered the MCP handshake with 'Method not found'` | The command is not an MCP server | Check the command and its arguments |
| `ConnectTimeoutError: ... within 120 s` | The server never answered the handshake | Check with `--server-logs`; for slow first installs raise `server.connect_timeout_s` or `--connect-timeout` |
| `HTTP 401 Unauthorized from <url>` | A remote server rejected the request | Pass credentials with `--header` or `server.headers` |
| `MCPError: Connection closed` | The server exited during startup | Rerun with `--server-logs` to see why |

## Missing environment variables

A local server starts with a minimal environment (`HOME`, `PATH`, `USER`, ...),
not the shell's full environment. `npx` and `uvx` servers often need proxy or
registry settings, and many servers need API keys.

- Command line: `--env NAME=VALUE` sets a variable; `--env NAME` copies it from
  the current shell.
- Suite: `server.env: {API_TOKEN: "${API_TOKEN}"}` reads it when the suite
  loads; `server.inherit_env: [HTTPS_PROXY, NPM_CONFIG_USERCONFIG]` copies
  optional settings only when they are set.

## Failing cases

- Read the failure line under the case. `json_path X: missing` means the
  structured result has no such key; check whether the tool returns structured
  content or only text.
- An `is_error` case passes but `contains` cannot find the expected message:
  some SDKs replace an unexpected exception's message with a generic one, such
  as `Error executing tool X`. Raise the SDK's tool error type instead, for
  example `ToolError` from `mcp.server.mcpserver.exceptions` in the Python SDK,
  so the client sees the real message.
- A tool that reports errors as successful text (for example `Error: ...` with
  `isError: false`) makes `is_error: true` fail. Assert on the text with
  `contains` or `matches` instead, and consider fixing the server to set
  `isError`.
- Results that change every run (timestamps, generated IDs) break `snapshot`
  and exact `json_path` checks. Assert on stable fields, or use `matches` or
  `schema`.
- Cases that depend on earlier cases' state fail when filtered with `--case`.
  Prefer `setup` steps that create the state each case needs.

## Lint warnings from `check`

Codes: `no-description`, `short-description`, `invalid-schema`,
`param-no-description`, `similar-tools`, `retry-unsafe`.

`retry-unsafe` marks side-effecting tools that declare neither
`idempotentHint: true` nor an idempotency key argument. It trusts annotations;
confirm real behavior with `fault` and `verify`. Silence a reviewed warning
with `--ignore CODE` or `--ignore CODE:TOOL`.
