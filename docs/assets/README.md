# README artwork

The banner and terminal demo use black and white foundations, a subtle purple
background tint, and restrained cyan highlights. The animation uses output captured
from the local CLI, including its measured timings; it types commands and reveals
that captured output at a readable pace.

Each asset name carries the version whose features it shows, for example
`banner-v0.3.0.png`. The README always points at the newest set. Older sets stay in
this directory as a record of earlier releases, but nothing links to them.

To render a set for the version in `pyproject.toml`, install Pillow in an artwork
environment and run from a development checkout with MCP Rig installed:

```bash
python -m pip install Pillow
python scripts/render_readme_assets.py
```

The renderer uses the checkout's `.venv/bin/python` for CLI commands when available,
otherwise its own interpreter. It uses Menlo and Arial on macOS, or DejaVu Sans
and DejaVu Sans Mono on Linux. All demo commands must succeed. The demo checks that
`init` generated eight cases, that the fault suite has two passing tests, and that
coverage ends with its total. It writes the generated `suite.yaml` to the
repository root and removes it afterwards.

| Version | Banner | Terminal demo | Poster | Demo shows |
| --- | --- | --- | --- | --- |
| 0.4.0 (current) | `banner-v0.4.0.png` | `cli-demo-v0.4.0.gif` | `cli-demo-poster-v0.4.0.png` | `init`, `fault` with retries, and `coverage` |
| 0.3.0 | `banner-v0.3.0.png` | `cli-demo-v0.3.0.gif` | `cli-demo-poster-v0.3.0.png` | `init`, a suite run, and `check` against a remote HTTP server |
| 0.1.1 | `banner-v0.1.1.png` | `cli-demo-v0.1.1.gif` | `cli-demo-poster-v0.1.1.png` | A suite run, tag selection, and JUnit export |

The banner has transparent rounded corners. The poster is the demo's last frame,
for previews and environments without animation.

The README uses repository-relative image paths, which work in local previews
and on GitHub. PyPI needs absolute public image URLs to display these assets.

CI uses GitHub's native workflow badge for `ci.yml` on `main`. PyPI version,
Python versions, open issues, and stars are live badges. MIT is the repository's
declared license.

The package was first published on 2026-09-29. PyPI Stats and Pepy did not yet
have download statistics when checked that day, so the README temporarily shows
`downloads: awaiting stats` rather than an error or an unverified zero. Once
`https://pypistats.org/api/packages/mcp-rig/recent` returns download data, replace
the temporary badge with:

```markdown
[![Monthly downloads](https://img.shields.io/pypi/dm/mcp-rig?label=downloads&color=91e4ef)](https://pypistats.org/packages/mcp-rig)
```
