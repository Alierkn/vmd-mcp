# Contributing to vmd-mcp

Thanks for your interest in improving `vmd-mcp`! 🎉

## Development setup

```bash
git clone https://github.com/Alierkn/vmd-mcp && cd vmd-mcp
uv sync --extra dev
uv run pre-commit install
```

## Before opening a PR

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pytest                # tests (VMD tests auto-skip without VMD)
RUN_VMD_INTEGRATION=1 uv run pytest -m integration
uv run python scripts/smoke_vmd.py
```

CI runs the same checks on Python 3.10–3.13.

## Adding a new analysis tool

1. Add an `@mcp.tool(annotations=...)`-decorated function in `src/vmd_mcp/server.py`
   using the closest existing annotation: read-only, write, or escape hatch.
2. Build its Tcl with the `_load_block()` helper and emit results with
   `puts "@@VMDMCP@@ key=value ..."`, then run it via `_run_tcl()`.
3. Validate user-controlled text, paths, numeric values, and allowlisted options
   before building Tcl.
4. Use `_tcl_braced()` for any user-controlled Tcl literal that is not intended
   to execute as Tcl.
5. Parse the markers back into structured data (`_parse_markers`, `_f`).
6. Write a clear docstring — the first paragraph becomes the tool description the
   LLM sees.
7. Add its name to `EXPECTED_TOOLS` in `tests/test_server.py`.

## Adding resources or prompts

- Register resources with `@mcp.resource(...)` and keep payloads compact enough
  for an MCP client to read inline.
- Register prompts with `@mcp.prompt(...)`; prompt text should guide an agent
  through safe tool order rather than duplicate README prose.
- Add names or URIs to `EXPECTED_RESOURCES` / `EXPECTED_PROMPTS` in
  `tests/test_server.py`.

## Headless gotchas (already handled — don't reintroduce them)

- **Do not pass `-eofexit`.** Under a non-tty stdin it makes VMD exit before long
  scripts finish. End every script with `quit` and run with `stdin=DEVNULL`.
- **Do not use the runtime `display resize` command** for render size — it is
  unreliable without a real display. Set resolution with the `-size W H`
  command-line flag via `_run_tcl(..., vmd_args=["-size", w, h])`.

## Design principles

- **Hybrid, not exhaustive.** Curated typed tools for common analyses; `run_tcl`
  covers everything else.
- **Structured out, not raw dumps.** Parse VMD output into clean fields.

## Reporting bugs

Open an [issue](https://github.com/Alierkn/vmd-mcp/issues) with your OS, VMD
version (`vmd_info`), the tool call, and the full result.

By contributing you agree your work is licensed under the [MIT License](LICENSE).
