# MCP Rig suite format

A suite is one YAML file. Top-level keys: `server` (required), `tests`
(required, non-empty), and optionally `setup`, `teardown`, `tags`, and
`after_timeout`.

## A complete suite

```yaml
server:
  command: python
  args: [server.py]
  env:
    API_TOKEN: "${API_TOKEN}"

setup:
  - call: reset_records

teardown:
  - call: reset_records

tests:
  - name: adds two numbers
    call: add
    args: {a: 2, b: 3}
    expect:
      contains: "5"

  - name: unknown user is an error
    call: get_user
    args: {user_id: 42}
    expect:
      is_error: true
      contains: "not found"

  - name: order is created once despite a lost response
    call: create_order
    args: {sku: A1, idempotency_key: order-1}
    tags: [side-effect]
    fault: drop_response
    timeout_s: 2
    retry:
      attempts: 1
    verify:
      - call: count_orders
        expect:
          json_path: {count: 1}
```

## `server`

- A command string: `server: python server.py`
- A URL: `server: https://mcp.example.com/mcp`
- A mapping for a local server: `command`, `args`, `env`, `cwd`,
  `inherit_env`, `connect_timeout_s`
- A mapping for a remote server: `url`, `headers`, `transport`
  (`streamable-http` by default, or `sse`), `connect_timeout_s`

`${NAME}` is replaced with the environment variable `NAME` when the suite
loads; a missing variable is an error. `${SUITE_DIR}` is the suite file's
directory. `inherit_env: [NAME, ...]` copies variables that are set and skips
the rest; `inherit_env: true` copies the whole environment.
`connect_timeout_s` (default 120) limits how long the server may take to start
and answer the handshake.

## Cases (`tests`)

| Key | Meaning |
| --- | --- |
| `name` | Required, unique description |
| `call` | Required tool name |
| `args` | Tool arguments (mapping) |
| `expect` | Expectations, below |
| `timeout_s` | Call timeout in seconds, default 30 |
| `tags` | List of tags for `--tag` / `--exclude-tag` |
| `verify` | Steps run after the call to check state |
| `retry` | `attempts: N`, optional `rerun_setup: true` |
| `fault` | `drop_response` or `disconnect`, applied to the first attempt only |
| `after_timeout` | `stop` (default) or `continue` after a timeout |

A case expects a successful call unless it declares `is_error: true`.

## Expectations (`expect`)

| Key | Checks |
| --- | --- |
| `is_error` | `true` for a tool error or JSON-RPC error |
| `contains` / `not_contains` | A string or a list of strings in the text |
| `matches` | A Python regular expression found in the text |
| `json_path` | Exact values by dotted keys and list indexes, e.g. `{items.0.id: 7}` |
| `schema` | JSON Schema (Draft 2020-12) for the structured result |
| `max_latency_ms` | Inclusive response-time limit |
| `snapshot` | `true` to compare the whole result with `<suite>.snap.yaml` |

Create or refresh snapshots with `uvx mcp-rig run <suite> --update-snapshots`.

## Steps (`setup`, `teardown`, `verify`)

Each step has `call`, and optionally `args`, `expect`, and `timeout_s`. Steps
use the same expectations as cases except `snapshot`.

`setup` runs once before the first case and `teardown` once after the last, on
the same server session; they do not run around each case. A failing setup
step skips every case. Cases share state, so give each case its own data (for
example unique names) when they must not affect each other.
`retry.rerun_setup: true` runs `setup` again only after a reconnect.

## Retries and faults

MCP Rig never retries unless a case sets `retry.attempts`. Only infrastructure
errors (timeouts, closed connections) are retried, never assertion failures or
tool errors.

- `drop_response`: the server acts, the response is discarded, and the attempt
  times out after `timeout_s`, so keep it short (1–2 s). The session stays open.
- `disconnect`: the server acts, then the connection closes at once, so
  `timeout_s` does not delay it. The retry starts a fresh local server process
  or a new HTTP session, so only state stored outside the process survives.

Set the expected count in `verify` from the tool's contract: an unprotected tool
creates two records after a lost response, an idempotent one creates one.

## Filters and reports

- `--case PATTERN` selects cases whose whole name matches a shell-style
  pattern: `--case 'create*'`, not `--case create`. Repeat it for OR.
- `--tag TAG` requires a tag; `--exclude-tag TAG` skips cases with it.
- `--junit PATH` writes a JUnit XML report.
- `after_timeout: continue` at the top level or on a case keeps running on the
  same session after a timeout.
