# Releasing MCP Rig

MCP Rig publishes to PyPI from a stable GitHub Release through Trusted
Publishing. The supported path stores no PyPI password or API token.

## One-time setup

Create a GitHub environment named `pypi` in the repository settings. Require
manual approval before deployment to that environment.

In the PyPI account's Publishing settings, register a pending GitHub Trusted
Publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `mcp-rig` |
| GitHub owner | `gorkemgul` |
| Repository | `mcp-rig` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

A pending publisher does not reserve the project name. Complete setup shortly
before the first release and confirm that `mcp-rig` is still available.

## Preflight

From a clean checkout of `main` with the development environment activated:

```bash
python -m pip install build twine
python scripts/check_release_tag.py v0.1.0
ruff check src tests scripts
pytest -q
python -m pip check
python -m build
python scripts/check_distribution_contents.py dist/*
python -m twine check dist/*
```

Confirm that `dist/` contains only:

- `mcp_rig-0.1.0-py3-none-any.whl`
- `mcp_rig-0.1.0.tar.gz`

The wheel contains only the `mcp_rig` package and its distribution metadata.
The source distribution contains only `src/mcp_rig`, `pyproject.toml`,
`README.md`, `LICENSE`, and the generated `PKG-INFO`. Repository-only content
such as `AGENTS.md`, `.superpowers/`, `docs/`, `graphify-out/`, tests, and build
artifacts must never appear in either archive.

## Publish `v0.1.0`

Creating the release is the real publication trigger. Do not continue without
explicit publication approval.

1. Create a stable GitHub Release targeting `main` with the new tag `v0.1.0`.
2. Do not mark it as a draft or prerelease.
3. Publish the GitHub Release.
4. Open the `publish` Actions run and verify that the build job succeeds.
5. Review and approve the waiting deployment to the `pypi` environment.
6. Wait for the publish job to complete successfully.
7. Verify <https://pypi.org/project/mcp-rig/> and install from PyPI in a fresh
   environment before announcing the release.

PyPI versions are immutable. Never attempt to overwrite `0.1.0`; corrections
must use a new version. A failed run after files reach PyPI still counts as a
published version.
