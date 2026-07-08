<div align="center">

# 🔬 vmd-mcp

**Drive [VMD](https://www.ks.uiuc.edu/Research/vmd/) headlessly from any MCP client.**

Analyse structures & trajectories (RMSD, radius of gyration, SASA, atom selections) and
**ray-trace publication-quality images with no display** — all through the
[Model Context Protocol](https://modelcontextprotocol.io).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-server-6E56CF.svg)](https://modelcontextprotocol.io)
[![CI](https://github.com/Alierkn/vmd-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Alierkn/vmd-mcp/actions/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

</div>

---

## Why

VMD is the standard tool for molecular visualisation and trajectory analysis, but its power
lives behind a GUI and a Tcl console. `vmd-mcp` runs VMD in **text mode**, turns the common
analyses and rendering into clean MCP tools, and parses the results back into structured data —
so an LLM agent can measure an RMSD curve or render a labelled cartoon **without a display,
without you writing a line of Tcl.**

## Features

- **Headless analysis** — molecule summaries, atom-selection counts, RMSD, RMSF, radius of
  gyration, SASA, selection distances, and contact pairs.
- **Headless rendering** — ray-traces images with VMD's built-in Tachyon and saves PNG, no
  X11 / display required. Choose explicit render controls or curated presets.
- **MCP-native guidance** — resources expose capabilities, recent outputs, and example recipes;
  prompts help clients render molecules, analyze trajectories, and debug VMD failures.
- **Safer local defaults** — structure inputs must exist, render outputs stay under
  `VMD_MCP_ROOT` by default, high-risk tool inputs are bounded, and MCP tool annotations mark
  read-only, write, and escape-hatch behavior.
- **Hybrid tool design** — typed helpers for common jobs **plus** a generic `run_tcl` escape
  hatch that runs any VMD Tcl and returns bounded output plus your `@@VMDMCP@@`-tagged results.
- **Robust in non-interactive sessions** — works around VMD's `-eofexit`/stdin and
  `display resize` quirks that otherwise break headless runs (see [How it works](#how-it-works)).
- **Zero-config discovery** — finds VMD on `PATH` or in macOS `.app` bundles (override with `VMD_BIN`).

## Tools

| Category | Tool | Purpose |
|----------|------|---------|
| **Introspect** | `vmd_info` | VMD version + resolved launcher path |
| | `molecule_info` | Atoms, frames, protein/water/chain/residue counts |
| | `count_atoms` | Count atoms matching a VMD selection |
| **Analyse** | `radius_of_gyration` | Per-frame R_gyr + min/max/mean |
| | `rmsd` | Per-frame RMSD vs a reference frame (optional alignment) |
| | `rmsf` | Per-atom RMSF across a trajectory |
| | `sasa` | Per-frame solvent-accessible surface area |
| | `distance` | Center-to-center distance between two selections |
| | `contacts` | Atom-index contact pairs between selections |
| **Render** | `render_image` | Headless Tachyon ray-trace → PNG |
| | `render_preset` | Curated render presets for common figures |
| **Escape hatch** | `run_tcl` | Any VMD Tcl script (with marker parsing) |

## Resources & prompts

| Type | Name / URI | Purpose |
|------|------------|---------|
| Resource | `vmd://capabilities` | Tool categories, allowed render options, presets, safety defaults |
| Resource | `vmd://output` | Recent files below `VMD_MCP_ROOT` |
| Resource | `vmd://examples` | Compact example workflows |
| Prompt | `render_molecule` | Safe render workflow using `render_preset` |
| Prompt | `analyze_trajectory` | RMSD/RMSF/R_gyr/SASA trajectory workflow |
| Prompt | `debug_vmd_failure` | Triage path for path, selection, rendering, and Tcl errors |

## Requirements

- **VMD** installed and runnable (GUI build is fine; it is used in `-dispdev text` mode).
- **Python ≥ 3.10**.
- **PNG conversion:** macOS `sips` (built in) or ImageMagick. Without either, renders are
  saved as `.tga`.
- An MCP client (e.g. [Claude Code](https://claude.com/claude-code) or Claude Desktop).

## Install & run

```bash
# Run straight from GitHub with uv — no global install needed
uvx --from git+https://github.com/Alierkn/vmd-mcp vmd-mcp
```

<details>
<summary>Alternative: pip / pipx</summary>

```bash
pipx install git+https://github.com/Alierkn/vmd-mcp
# or
pip install git+https://github.com/Alierkn/vmd-mcp
vmd-mcp        # starts the stdio server
```
</details>

## Connect it to your MCP client

### Claude Code

```bash
claude mcp add vmd --scope user -- \
  uvx --from git+https://github.com/Alierkn/vmd-mcp vmd-mcp
```

Check: `claude mcp list` → `vmd: … ✔ Connected`.

### Claude Desktop

```jsonc
{
  "mcpServers": {
    "vmd": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Alierkn/vmd-mcp", "vmd-mcp"],
      "env": { "VMD_BIN": "/Applications/VMD2b1.app/Contents/MacOS/startup.command" }
    }
  }
}
```

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `VMD_BIN` | auto (`PATH`, then macOS `.app`) | Path to the VMD launcher |
| `VMD_MCP_ROOT` | `~/vmd-mcp/output` | Where rendered images / scratch are written |
| `VMD_MCP_ALLOW_ABSOLUTE_OUTPUTS` | unset / `0` | Set to `1` only if `render_image.output` should be allowed outside `VMD_MCP_ROOT` |

## Example prompts

> *"Load `bpti.pdb`, tell me how many residues and chains it has, then render a
> Structure-colored NewCartoon at 1200×900 and save it as `bpti.png`."*

> *"For `topol.tpr` + `md.xtc`, compute the CA-RMSD over the trajectory aligned to
> frame 0, and report min / max / mean."*

> *"Using `examples/sample.pdb`, run the bundled VMD smoke workflow and render
> `sample.png` with the `atomistic_lines` preset."*

## How it works

Each tool generates a small Tcl script and runs `vmd -dispdev text -e script.tcl`. Results are
emitted with a `@@VMDMCP@@ key=value` marker that the server parses into structured data.
Two headless-specific gotchas are handled for you:

- **`-eofexit` + non-tty stdin** would make VMD exit before a long render finishes — so the
  server relies on a trailing `quit` and `stdin=DEVNULL` instead.
- **`display resize`** is unreliable without a real display — so render resolution is set via
  the `-size W H` command-line flag.

Images are ray-traced with `render TachyonInternal` (built-in, no external renderer needed) and
converted TGA → PNG via macOS `sips`, ImageMagick `magick`, or ImageMagick `convert` when
available. If no converter is present, the `.tga` is kept.

## Safety model

`vmd-mcp` is a local automation server, not a remote multi-user service. Typed tools validate
paths, dimensions, timeouts, selections, render methods, and color modes before launching VMD.
`render_image` writes below `VMD_MCP_ROOT` unless `VMD_MCP_ALLOW_ABSOLUTE_OUTPUTS=1` is set.

The `run_tcl` tool intentionally remains an escape hatch for advanced VMD workflows. It is marked
as a destructive MCP tool, capped by timeout and output size, and should only be used with Tcl you
trust.

## Development

```bash
git clone https://github.com/Alierkn/vmd-mcp && cd vmd-mcp
uv sync --extra dev
uv run pytest        # VMD-dependent tests auto-skip if VMD is absent
RUN_VMD_INTEGRATION=1 uv run pytest -m integration
uv run python scripts/smoke_vmd.py
uv run ruff check .
uv build
uvx twine check dist/*
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Related

- [**gromacs-mcp**](https://github.com/Alierkn/gromacs-mcp) — companion MCP server that runs
  GROMACS simulations. Pair them: simulate with GROMACS, then analyse & visualise with VMD.

## License

[MIT](LICENSE) © Ali Erkan Ocaklı
