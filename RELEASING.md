# Releasing

1. In a release PR, bump `version` in `pyproject.toml` and `__version__` in
   `src/mcp_rig/__init__.py`, update the GitHub Action references in the README
   and `skills/mcp-rig`, and add a `## X.Y.Z` section to `CHANGELOG.md`.
   CI fails if the project version has no changelog section.
2. After the PR is merged, tag the merge commit and push the tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

3. The `release` workflow checks the tag against `pyproject.toml` and creates a
   **draft** GitHub Release whose notes are the changelog section. The agent
   skill in `skills/mcp-rig` is attached as `mcp-rig-skill.zip`, ready to
   upload to skill marketplaces.
4. Review the draft and click **Publish release**. Publishing starts the
   `publish` workflow, which builds, verifies, and uploads the package to PyPI.

Publishing stays a manual step on purpose. GitHub does not let a release
created by a workflow token start another workflow, and the manual click is the
last review before an irreversible upload to PyPI.
