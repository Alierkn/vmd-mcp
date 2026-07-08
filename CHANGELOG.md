# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- MCP tool annotations for read-only helpers, render writes, and the `run_tcl`
  escape hatch.
- ImageMagick `magick` / `convert` fallback for TGA to PNG conversion when
  macOS `sips` is unavailable.

### Changed
- Hardened typed tools with explicit validation for file inputs, selections,
  render options, dimensions, timeouts, and render output paths.
- `render_image` now writes under `VMD_MCP_ROOT` by default; absolute outputs
  require `VMD_MCP_ALLOW_ABSOLUTE_OUTPUTS=1`.
- `run_tcl` now returns bounded stdout/stderr output and rejects empty or overly
  large scripts.

### Security
- User-controlled Tcl literals are now passed through brace-group escaping to
  reduce accidental command execution in typed tools.

## [0.1.0] — 2026-07-07

### Added
- Initial release with 8 tools:
  - **Introspection:** `vmd_info`, `molecule_info`, `count_atoms`
  - **Analysis:** `radius_of_gyration`, `rmsd`, `sasa`
  - **Rendering:** `render_image` (headless Tachyon → PNG)
  - **Escape hatch:** `run_tcl`
- Headless VMD execution via text-mode Tcl with `@@VMDMCP@@` marker parsing into
  structured results.
- Automatic VMD discovery via `PATH` and macOS `.app` bundles, with `VMD_BIN` override.
- Robust handling of two headless pitfalls: dropped `-eofexit` (+ `stdin=DEVNULL`) so
  long renders complete, and resolution set via the `-size` flag instead of the
  unreliable `display resize` command.
- `src/` package layout, MIT license, CI (Python 3.10–3.13), ruff, pre-commit, tests.

[Unreleased]: https://github.com/Alierkn/vmd-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Alierkn/vmd-mcp/releases/tag/v0.1.0
