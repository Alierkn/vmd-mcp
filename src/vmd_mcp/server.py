"""VMD MCP server.

Drives a local VMD (Visual Molecular Dynamics) installation headlessly so an
MCP client (Claude) can analyse structures/trajectories and produce rendered
images without a GUI.

How it works
------------
VMD is run in text mode (``vmd -dispdev text -e script.tcl``). Each
tool generates a small Tcl script, VMD executes it, and results are emitted on
stdout with a ``@@VMDMCP@@`` marker that this server parses back into structured
data. Images are ray-traced headlessly with VMD's built-in Tachyon renderer and
converted to PNG.

Design (mirrors the companion gromacs-mcp):
  * Hybrid tools  -> typed helpers for common analyses (molecule_info,
                     count_atoms, radius_of_gyration, rmsd, sasa, render_image)
                     PLUS a generic ``run_tcl`` escape hatch for anything else.
  * Python / FastMCP, stdio transport (runs locally next to vmd).
"""

from __future__ import annotations

import contextlib
import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- #
# Configuration & VMD discovery
# --------------------------------------------------------------------------- #

MARKER = "@@VMDMCP@@"
MAX_OUTPUT_CHARS = 4000


def _discover_vmd() -> str:
    """Locate a runnable VMD launcher. Order: $VMD_BIN, PATH, macOS .app bundles."""
    env = os.environ.get("VMD_BIN")
    if env:
        return env
    which = shutil.which("vmd")
    if which:
        return which
    # macOS .app bundles ship a startup.command wrapper that sets VMDDIR etc.
    for pat in (
        "/Applications/VMD*.app/Contents/MacOS/startup.command",
        str(Path.home() / "Applications/VMD*.app/Contents/MacOS/startup.command"),
    ):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]  # newest-sorting bundle
    return "vmd"


VMD_BIN = _discover_vmd()

# Base dir for rendered images / scratch. Overridable.
ROOT = Path(os.environ.get("VMD_MCP_ROOT", Path.home() / "vmd-mcp" / "output"))
ROOT.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("vmd")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _tcl_quote(text: str) -> str:
    """Wrap a value for safe literal use inside a Tcl ``{...}`` group by
    escaping the brace/backslash characters Tcl treats specially there."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _load_block(structure: str, trajectory: str | None = None) -> str:
    """Tcl that loads a structure and (optionally) appends a trajectory, leaving
    the new molecule as ``top`` and its id in ``$mol``."""
    lines = [f"set mol [mol new {{{_tcl_quote(structure)}}} waitfor all]"]
    if trajectory:
        lines.append(f"mol addfile {{{_tcl_quote(trajectory)}}} waitfor all molid $mol")
    return "\n".join(lines)


def _run_tcl(script: str, timeout: int = 300, vmd_args: list[str] | None = None) -> dict:
    """Execute a Tcl script in headless VMD; return combined output + parsed
    marker lines. ``vmd_args`` are extra VMD command-line flags (e.g.
    ``["-size", "1000", "800"]`` to set the render resolution)."""
    # Every generated script ends with `quit`, so we do NOT pass -eofexit:
    # under a non-tty stdin (as when launched by an MCP client) -eofexit makes
    # VMD exit on the immediate stdin EOF, aborting long scripts (e.g. a render)
    # midway. Relying on `quit` + stdin=DEVNULL runs the script to completion.
    if "quit" not in script:
        script = script.rstrip() + "\nquit\n"
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as fh:
        fh.write(script)
        script_path = fh.name
    cmd = [VMD_BIN, "-dispdev", "text", *(vmd_args or []), "-e", script_path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"VMD not found at '{VMD_BIN}'. Set VMD_BIN env var."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"VMD timed out after {timeout}s."}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(script_path)

    combined = (proc.stdout or "") + (proc.stderr or "")
    markers = _parse_markers(combined)
    result = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "markers": markers,
    }
    # Surface a Tcl error if one occurred (VMD prints it to stderr).
    err = [
        ln for ln in combined.splitlines() if ln.strip().lower().startswith(("error", "tcl error"))
    ]
    if err and not markers:
        result["error"] = err[0].strip()
    return result


def _parse_markers(output: str) -> list[dict]:
    """Extract ``@@VMDMCP@@ key=value key=value`` lines into dicts (values kept
    as strings; numeric coercion is left to the caller)."""
    out = []
    for ln in output.splitlines():
        if MARKER in ln:
            payload = ln.split(MARKER, 1)[1].strip()
            kv = {}
            for tok in payload.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k] = v
            if kv:
                out.append(kv)
    return out


def _f(markers: list[dict], key: str) -> float | None:
    """First float value for *key* across markers (or None)."""
    for m in markers:
        if key in m:
            try:
                return float(m[key])
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #


@mcp.tool()
def vmd_info() -> dict:
    """Return the VMD version/banner and the resolved launcher path. Use this
    first to confirm the server can reach a working VMD."""
    res = _run_tcl(
        f'puts "{MARKER} probe=ok"\nputs "{MARKER} version=[vmdinfo version]"', timeout=60
    )
    version = None
    for m in res.get("markers", []):
        if "version" in m:
            version = m["version"]
    return {
        "vmd_bin": VMD_BIN,
        "output_root": str(ROOT),
        "version": version,
        "ok": res.get("ok", False),
    }


@mcp.tool()
def molecule_info(structure: str, trajectory: str | None = None) -> dict:
    """Load a structure (and optional trajectory) and report a summary:
    total atoms, number of frames, protein/water/backbone atom counts, and the
    number of distinct chains and residues."""
    tcl = f"""
{_load_block(structure, trajectory)}
set nat [molinfo $mol get numatoms]
set nfr [molinfo $mol get numframes]
set prot [atomselect $mol "protein"]
set wat  [atomselect $mol "water"]
set ca   [atomselect $mol "protein and name CA"]
set allsel [atomselect $mol "all"]
set chains [lsort -unique [$allsel get chain]]
set resids [lsort -unique -integer [$prot get resid]]
puts "{MARKER} numatoms=$nat numframes=$nfr protein=[$prot num] water=[$wat num] ca=[$ca num] nchains=[llength $chains] nres=[llength $resids]"
quit
"""
    res = _run_tcl(tcl)
    m = res["markers"][0] if res.get("markers") else {}
    return {
        "ok": res["ok"] and bool(m),
        "structure": structure,
        "trajectory": trajectory,
        "info": {k: int(v) if v.lstrip("-").isdigit() else v for k, v in m.items()},
        "raw": res.get("error"),
    }


@mcp.tool()
def count_atoms(selection: str, structure: str, trajectory: str | None = None) -> dict:
    """Count atoms matching a VMD atom-selection expression
    (e.g. ``"protein and name CA"``, ``"resname LIG"``, ``"within 5 of protein"``)."""
    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol "{_tcl_quote(selection)}"]
puts "{MARKER} count=[$sel num]"
quit
"""
    res = _run_tcl(tcl)
    return {
        "ok": res["ok"],
        "selection": selection,
        "count": int(_f(res["markers"], "count") or 0) if res.get("markers") else None,
        "error": res.get("error"),
    }


# --------------------------------------------------------------------------- #
# Analyses
# --------------------------------------------------------------------------- #


@mcp.tool()
def radius_of_gyration(
    structure: str,
    selection: str = "protein",
    trajectory: str | None = None,
    mass_weighted: bool = True,
) -> dict:
    """Radius of gyration (nm-scale, in VMD's Angstrom units) of a selection,
    computed for every frame. Returns a per-frame series plus min/max/mean."""
    weight = "weight mass" if mass_weighted else ""
    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol "{_tcl_quote(selection)}"]
set n [molinfo $mol get numframes]
for {{set i 0}} {{$i < $n}} {{incr i}} {{
  $sel frame $i
  puts "{MARKER} frame=$i rgyr=[measure rgyr $sel {weight}]"
}}
quit
"""
    res = _run_tcl(tcl)
    series = [
        {"frame": int(m["frame"]), "rgyr": float(m["rgyr"])}
        for m in res.get("markers", [])
        if "rgyr" in m
    ]
    vals = [p["rgyr"] for p in series]
    stats = {"min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals)} if vals else {}
    return {
        "ok": res["ok"] and bool(series),
        "selection": selection,
        "n_frames": len(series),
        "series": series,
        "stats": stats,
        "error": res.get("error"),
    }


@mcp.tool()
def rmsd(
    structure: str,
    trajectory: str,
    selection: str = "protein and name CA",
    reference_frame: int = 0,
    align: bool = True,
) -> dict:
    """RMSD of a selection across a trajectory relative to a reference frame.

    With ``align=True`` each frame is first least-squares fitted onto the
    reference (rigid-body alignment) before measuring RMSD. Returns a per-frame
    series plus min/max/mean (Angstrom)."""
    fit = (
        """
  set M [measure fit $sel $ref]
  $all frame $i
  $all move $M"""
        if align
        else ""
    )
    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol "{_tcl_quote(selection)}"]
set all [atomselect $mol "all"]
set ref [atomselect $mol "{_tcl_quote(selection)}" frame {reference_frame}]
set n [molinfo $mol get numframes]
for {{set i 0}} {{$i < $n}} {{incr i}} {{
  $sel frame $i{fit}
  puts "{MARKER} frame=$i rmsd=[measure rmsd $sel $ref]"
}}
quit
"""
    res = _run_tcl(tcl)
    series = [
        {"frame": int(m["frame"]), "rmsd": float(m["rmsd"])}
        for m in res.get("markers", [])
        if "rmsd" in m
    ]
    vals = [p["rmsd"] for p in series]
    stats = {"min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals)} if vals else {}
    return {
        "ok": res["ok"] and bool(series),
        "selection": selection,
        "reference_frame": reference_frame,
        "aligned": align,
        "n_frames": len(series),
        "series": series,
        "stats": stats,
        "error": res.get("error"),
    }


@mcp.tool()
def sasa(
    structure: str,
    selection: str = "protein",
    srad: float = 1.4,
    trajectory: str | None = None,
) -> dict:
    """Solvent-accessible surface area (Angstrom^2) of a selection, per frame.
    ``srad`` is the solvent probe radius (1.4 A ~ water)."""
    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol "{_tcl_quote(selection)}"]
set n [molinfo $mol get numframes]
for {{set i 0}} {{$i < $n}} {{incr i}} {{
  $sel frame $i
  molinfo $mol set frame $i
  puts "{MARKER} frame=$i sasa=[measure sasa {srad} $sel]"
}}
quit
"""
    res = _run_tcl(tcl)
    series = [
        {"frame": int(m["frame"]), "sasa": float(m["sasa"])}
        for m in res.get("markers", [])
        if "sasa" in m
    ]
    vals = [p["sasa"] for p in series]
    stats = {"min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals)} if vals else {}
    return {
        "ok": res["ok"] and bool(series),
        "selection": selection,
        "srad": srad,
        "n_frames": len(series),
        "series": series,
        "stats": stats,
        "error": res.get("error"),
    }


# --------------------------------------------------------------------------- #
# Headless rendering
# --------------------------------------------------------------------------- #


@mcp.tool()
def render_image(
    structure: str,
    output: str = "render.png",
    trajectory: str | None = None,
    selection: str = "all",
    representation: str = "NewCartoon",
    coloring: str = "Structure",
    frame: int = 0,
    width: int = 1000,
    height: int = 800,
    background: str = "white",
) -> dict:
    """Ray-trace a molecular image HEADLESSLY with VMD's built-in Tachyon and
    save it as PNG (no display needed).

    ``representation`` is any VMD draw method (NewCartoon, Licorice, VDW,
    Surf, ...); ``coloring`` is any VMD color method (Structure, Chain, Name,
    ResID, ...). The image is written under VMD_MCP_ROOT unless *output* is an
    absolute path. Returns the PNG path and dimensions."""
    out_png = Path(output).expanduser()
    if not out_png.is_absolute():
        out_png = ROOT / output
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tga = out_png.with_suffix(".tga")

    tcl = f"""
{_load_block(structure, trajectory)}
animate goto {frame}
mol delrep 0 $mol
mol representation {representation}
mol color {coloring}
mol selection {{{_tcl_quote(selection)}}}
mol addrep $mol
display projection Orthographic
axes location off
color Display Background {background}
display resetview
render TachyonInternal {{{_tcl_quote(str(tga))}}}
puts "{MARKER} rendered=ok"
quit
"""
    # Resolution is set via the -size command-line flag; the runtime
    # `display resize` command is unreliable under a headless (non-tty) session.
    res = _run_tcl(tcl, timeout=600, vmd_args=["-size", str(width), str(height)])
    if not tga.exists():
        return {
            "ok": False,
            "error": res.get("error", "Render produced no image."),
            "returncode": res.get("returncode"),
        }

    # Convert TGA -> PNG using macOS `sips` (fallback: leave TGA).
    png_ok = False
    if shutil.which("sips"):
        conv = subprocess.run(
            ["sips", "-s", "format", "png", str(tga), "--out", str(out_png)],
            capture_output=True,
            text=True,
        )
        png_ok = conv.returncode == 0 and out_png.exists()
    if png_ok:
        tga.unlink(missing_ok=True)
        final = out_png
    else:
        final = tga  # keep TGA if no converter available

    return {
        "ok": True,
        "image_path": str(final),
        "format": final.suffix.lstrip("."),
        "width": width,
        "height": height,
        "representation": representation,
        "coloring": coloring,
        "frame": frame,
    }


# --------------------------------------------------------------------------- #
# Generic escape hatch
# --------------------------------------------------------------------------- #


@mcp.tool()
def run_tcl(script: str, timeout: int = 300) -> dict:
    """Run an ARBITRARY VMD Tcl script headlessly and return its combined
    stdout/stderr plus any parsed ``@@VMDMCP@@`` marker lines.

    Emit results from your script with:  ``puts "@@VMDMCP@@ key=value ..."``.
    Use this for any analysis not covered by a dedicated tool (measure hbonds,
    measure contacts, custom per-residue loops, cluster analysis, ...).
    End the script with ``quit``."""
    res = _run_tcl(script, timeout=timeout)
    return res


def main() -> None:
    """Console-script entry point (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
