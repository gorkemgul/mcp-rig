---
name: mcp-rig
description: Test MCP (Model Context Protocol) servers with MCP Rig, a CLI that runs YAML test suites against stdio and HTTP servers and works in CI. Use when building, changing, or reviewing an MCP server or its tools; when asked to add tests or CI for an MCP server; when checking whether a side-effecting tool is safe to retry; or when an MCP server will not connect.
---

# Testing MCP servers with MCP Rig

MCP Rig starts an MCP server, calls its tools, and checks the results. Suites
are YAML files that live next to the server's code and run the same way locally
and in CI. Run it with `uvx`, so nothing has to be installed first:

```bash
uvx mcp-rig --version   # needs 0.4.1 or later
```

The server argument is either a command line in quotes, such as
`"python server.py"` or `"npx -y @scope/server"`, or an `http(s)://` URL. Use
the command the server will also be started with in CI, such as `python` from
the project's environment, not an absolute path that only exists on one
machine; `init` copies it into the suite.

## Workflow

Follow these steps in order. Run each command, read its output, and fix what
it reports before moving on.

1. **Health check.** `uvx mcp-rig check "<server>"` connects, lists tools,
   checks protocol behavior, and lints tool definitions. Exit code `0` means the
   protocol checks passed; `1` means a check failed (or `--strict` lint found
   warnings); `2` means the server could not be started or reached. For `2`,
   read the error and see [troubleshooting](references/troubleshooting.md).
   Lint warnings point at tool descriptions and schemas worth improving in the
   server itself.

2. **Generate a suite.** Create the folder first, then let `init` write one
   case per tool with typed placeholder arguments:

   ```bash
   mkdir -p tests/mcp
   uvx mcp-rig init "<server>" --output tests/mcp/<name>.yaml
   ```

   Cases for tools that look state-changing are tagged `side-effect`.

3. **Make the cases real.** Replace every placeholder with arguments that
   exercise the tool, and add expectations (`contains`, `json_path`, `schema`,
   `is_error`, ...). A case that only checks "the call succeeded" proves little.
   Add negative cases with `expect: {is_error: true}` for invalid input. Keys and
   examples are in [suite format](references/suite-format.md).

   Keep tests away from real state: point the server at test data through
   `server.env`, for example a database or file path under `${SUITE_DIR}`.

   Do not run cases tagged `side-effect` until their arguments are deliberate:
   empty placeholders can act on real data. Use
   `uvx mcp-rig run <suite> --exclude-tag side-effect` until then.

4. **Run.** `uvx mcp-rig run tests/mcp/`. Exit `0` passed, `1` a case failed,
   `2` a suite could not run. Fix the server or the expectation, whichever is
   wrong; do not loosen an expectation just to make it pass.

5. **Check state, not just responses, for side-effecting tools.** Add `verify`
   steps that read back what the call changed, and `setup`/`teardown` steps that
   reset state. Then test retries: `fault: drop_response` loses the response
   after the server has acted, and `retry: {attempts: 1}` repeats the call. The
   `verify` step shows whether the effect happened once or twice. If a tool
   should be safe to retry, it needs an idempotency key argument or must be
   idempotent by nature.

   When a tool turns out to be unsafe, do not change its arguments or behavior
   on your own: that changes its contract for every client. Write the case to
   expect the current count, so the suite documents it, and report the finding
   with a proposed fix, such as an optional `idempotency_key` argument.

6. **Find gaps.** `uvx mcp-rig coverage tests/mcp/` lists tools that no case or
   step calls. Add cases for them.

7. **Run in CI.** Install the server's dependencies so the suite's server
   command works on the runner, then add one step:

   ```yaml
   - uses: gorkemgul/mcp-rig@v0.4.1
     with:
       suites: tests/mcp/
       junit: mcp-rig-results.xml
   ```

## Rules

- Point `check --probe-invalid-args`, `fault`, and side-effecting cases only at
  development or test servers, never at production data.
- If the server has test-only tools that change its behavior (for example to
  simulate failures), reset them in `teardown` so later cases are unaffected.
- Never put secrets in a suite. Use `${NAME}` references in `server.env`,
  `server.headers`, or `server.url`, and `--env`/`--header` on the command line.
- A local server starts with a minimal environment. If it needs proxy, registry,
  or API settings, pass them with `server.env`, `server.inherit_env`, or
  `--env` (see [troubleshooting](references/troubleshooting.md)).
- Keep suites deterministic: avoid asserting on timestamps, random IDs, or live
  network data; assert on stable fields with `json_path` or `schema` instead.
- Add `--server-logs` when diagnosing a failure to see the server's stderr.
