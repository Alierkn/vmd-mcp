# Release Checklist

Use this checklist for tagged releases.

## Before Publishing

1. Update the version in `pyproject.toml` and `src/vmd_mcp/__init__.py`.
2. Move `CHANGELOG.md` entries from `[Unreleased]` into the release version.
3. Run:

   ```bash
   uv lock --check
   uv run ruff check .
   uv run ruff format --check .
   uv run pytest
   uv build
   uvx twine check dist/*
   ```

4. Push a release PR and confirm GitHub Actions is green.

## Publishing

1. Merge the release PR into `main`.
2. Configure PyPI Trusted Publishing before the first publish:
   - Project name: `vmd-mcp`
   - Owner/repo: `Alierkn/vmd-mcp`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. Create and publish a GitHub release tagged `vX.Y.Z`.

Manual `workflow_dispatch` runs build and metadata checks only. PyPI publishing
only runs for a published GitHub release event.
