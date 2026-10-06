# Test your own MCP server

The quickest start is to generate a suite from your server's tools:

```bash
mcp-rig init "python -m my_mcp_server" --output tests/mcp/my-server.yaml
```

Or copy this suite into your project and replace the server command, tool
names, arguments, and expectations:

```yaml
server:
  command: python
  args: [-m, my_mcp_server]
  cwd: ../..
  env:
    APP_ENV: test

tests:
  - name: returns a known record
    call: get_record
    args: {record_id: 1}
    expect:
      is_error: false
      json_path:
        id: 1
      schema:
        type: object
        required: [id]

  - name: rejects a missing record
    call: get_record
    args: {record_id: 999}
    expect:
      is_error: true
      contains: not found
```

Run a quick protocol and tool-definition check first, then run the suite:

```bash
mcp-rig check "python -m my_mcp_server"
mcp-rig run tests/mcp/my-server.yaml
```

MCP Rig 0.1 launches local stdio servers and tests tools. It does not connect
to an already-running HTTP/SSE server and does not test resources or prompts.
Do not commit real credentials in `env`; inject secrets through your CI or
wrapper command.
