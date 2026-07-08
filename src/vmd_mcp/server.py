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
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# --------------------------------------------------------------------------- #
# Configuration & VMD discovery
# --------------------------------------------------------------------------- #

MARKER = "@@VMDMCP@@"
MAX_OUTPUT_CHARS = 4000
MAX_TIMEOUT_SECONDS = 900
MIN_RENDER_DIMENSION = 64
MAX_RENDER_DIMENSION = 4096
ALLOW_ABSOLUTE_OUTPUTS_ENV = "VMD_MCP_ALLOW_ABSOLUTE_OUTPUTS"

ALLOWED_REPRESENTATIONS = frozenset(
    {
        "NewCartoon",
        "Cartoon",
        "Licorice",
        "VDW",
        "CPK",
        "Lines",
        "Bonds",
        "Trace",
        "Tube",
        "Surf",
        "QuickSurf",
    }
)
ALLOWED_COLORINGS = frozenset(
    {
        "Name",
        "Type",
        "Element",
        "ResName",
        "ResID",
        "Chain",
        "SegName",
        "Structure",
        "ColorID",
        "Beta",
        "Occupancy",
        "Mass",
        "Charge",
        "Index",
    }
)
ALLOWED_BACKGROUNDS = frozenset(
    {
        "white",
        "black",
        "gray",
        "silver",
        "blue",
        "red",
        "green",
        "orange",
        "yellow",
        "purple",
    }
)

READ_ONLY_TOOL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE_TOOL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)
ESCAPE_HATCH_TOOL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


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


def _tcl_braced(text: str) -> str:
    """Return *text* as a Tcl brace-group literal with command substitution disabled."""
    return f"{{{_tcl_quote(text)}}}"


class ValidationError(ValueError):
    """Raised when user-controlled tool input is outside the safe local policy."""


def _validation_error(exc: ValidationError) -> dict[str, Any]:
    return {"ok": False, "error": str(exc)}


def _validate_text(
    value: str, name: str, *, max_len: int = 1000, allow_newlines: bool = False
) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string.")
    if not value.strip():
        raise ValidationError(f"{name} must not be empty.")
    if len(value) > max_len:
        raise ValidationError(f"{name} is too long; maximum is {max_len} characters.")
    blocked = "\x00\r" if allow_newlines else "\x00\r\n"
    if any(ch in value for ch in blocked):
        raise ValidationError(f"{name} contains unsupported control characters.")
    return value


def _validate_existing_file(value: str, name: str) -> str:
    raw = _validate_text(value, name, max_len=4096)
    path = Path(raw).expanduser()
    if not path.exists():
        raise ValidationError(f"{name} does not exist: {raw}")
    if not path.is_file():
        raise ValidationError(f"{name} is not a file: {raw}")
    return str(path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_output_path(output: str) -> Path:
    raw = _validate_text(output, "output", max_len=4096)
    candidate = Path(raw)
    allow_absolute = os.environ.get(ALLOW_ABSOLUTE_OUTPUTS_ENV) == "1"

    if candidate.is_absolute() or raw.startswith("~"):
        if not allow_absolute:
            raise ValidationError(
                f"absolute output paths are disabled; write under VMD_MCP_ROOT or set {ALLOW_ABSOLUTE_OUTPUTS_ENV}=1"
            )
        return candidate.expanduser().resolve(strict=False)

    if any(part == ".." for part in candidate.parts):
        raise ValidationError("output must not contain '..' path traversal.")
    resolved = (ROOT / candidate).resolve(strict=False)
    root = ROOT.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise ValidationError("output must stay under VMD_MCP_ROOT.")
    return resolved


def _validate_selection(selection: str) -> str:
    return _validate_text(selection, "selection", max_len=1000)


def _validate_choice(value: str, name: str, allowed: frozenset[str]) -> str:
    raw = _validate_text(value, name, max_len=80)
    if raw not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValidationError(f"{name} must be one of: {choices}.")
    return raw


def _validate_int_range(
    value: int, name: str, *, min_value: int, max_value: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer.")
    if value < min_value:
        raise ValidationError(f"{name} must be >= {min_value}.")
    if max_value is not None and value > max_value:
        raise ValidationError(f"{name} must be <= {max_value}.")
    return value


def _validate_float_range(
    value: float,
    name: str,
    *,
    min_value: float,
    max_value: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValidationError(f"{name} must be a number.")
    number = float(value)
    if number < min_value or number > max_value:
        raise ValidationError(f"{name} must be between {min_value} and {max_value}.")
    return number


def _validate_timeout(timeout: int) -> int:
    return _validate_int_range(
        timeout,
        "timeout",
        min_value=1,
        max_value=MAX_TIMEOUT_SECONDS,
    )


def _has_quit_command(script: str) -> bool:
    for line in script.splitlines():
        stripped = line.strip().lower()
        if stripped == "quit" or stripped.startswith("quit "):
            return True
    return False


def _trim_output(output: str) -> tuple[str, bool]:
    if len(output) <= MAX_OUTPUT_CHARS:
        return output, False
    return output[-MAX_OUTPUT_CHARS:], True


def _coerce_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _load_block(structure: str, trajectory: str | None = None) -> str:
    """Tcl that loads a structure and (optionally) appends a trajectory, leaving
    the new molecule as ``top`` and its id in ``$mol``."""
    lines = [f"set mol [mol new {_tcl_braced(structure)} waitfor all]"]
    if trajectory:
        lines.append(f"mol addfile {_tcl_braced(trajectory)} waitfor all molid $mol")
    return "\n".join(lines)


def _run_tcl(
    script: str,
    timeout: int = 300,
    vmd_args: list[str] | None = None,
    *,
    include_output: bool = False,
) -> dict:
    """Execute a Tcl script in headless VMD; return combined output + parsed
    marker lines. ``vmd_args`` are extra VMD command-line flags (e.g.
    ``["-size", "1000", "800"]`` to set the render resolution)."""
    # Every generated script ends with `quit`, so we do NOT pass -eofexit:
    # under a non-tty stdin (as when launched by an MCP client) -eofexit makes
    # VMD exit on the immediate stdin EOF, aborting long scripts (e.g. a render)
    # midway. Relying on `quit` + stdin=DEVNULL runs the script to completion.
    try:
        timeout = _validate_timeout(timeout)
    except ValidationError as exc:
        return _validation_error(exc)

    if not _has_quit_command(script):
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
    except subprocess.TimeoutExpired as exc:
        result: dict[str, Any] = {"ok": False, "error": f"VMD timed out after {timeout}s."}
        if include_output:
            combined_timeout = _coerce_output_text(exc.stdout) + _coerce_output_text(exc.stderr)
            result["output"], result["output_truncated"] = _trim_output(combined_timeout)
        return result
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
    if include_output:
        result["output"], result["output_truncated"] = _trim_output(combined)
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


@mcp.tool(annotations=READ_ONLY_TOOL)
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


@mcp.tool(annotations=READ_ONLY_TOOL)
def molecule_info(structure: str, trajectory: str | None = None) -> dict:
    """Load a structure (and optional trajectory) and report a summary:
    total atoms, number of frames, protein/water/backbone atom counts, and the
    number of distinct chains and residues."""
    try:
        structure = _validate_existing_file(structure, "structure")
        trajectory = _validate_existing_file(trajectory, "trajectory") if trajectory else None
    except ValidationError as exc:
        return _validation_error(exc)

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


@mcp.tool(annotations=READ_ONLY_TOOL)
def count_atoms(selection: str, structure: str, trajectory: str | None = None) -> dict:
    """Count atoms matching a VMD atom-selection expression
    (e.g. ``"protein and name CA"``, ``"resname LIG"``, ``"within 5 of protein"``)."""
    try:
        structure = _validate_existing_file(structure, "structure")
        trajectory = _validate_existing_file(trajectory, "trajectory") if trajectory else None
        selection = _validate_selection(selection)
    except ValidationError as exc:
        return _validation_error(exc)

    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol {_tcl_braced(selection)}]
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


@mcp.tool(annotations=READ_ONLY_TOOL)
def radius_of_gyration(
    structure: str,
    selection: str = "protein",
    trajectory: str | None = None,
    mass_weighted: bool = True,
) -> dict:
    """Radius of gyration (nm-scale, in VMD's Angstrom units) of a selection,
    computed for every frame. Returns a per-frame series plus min/max/mean."""
    try:
        structure = _validate_existing_file(structure, "structure")
        trajectory = _validate_existing_file(trajectory, "trajectory") if trajectory else None
        selection = _validate_selection(selection)
    except ValidationError as exc:
        return _validation_error(exc)

    weight = "weight mass" if mass_weighted else ""
    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol {_tcl_braced(selection)}]
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


@mcp.tool(annotations=READ_ONLY_TOOL)
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
    try:
        structure = _validate_existing_file(structure, "structure")
        trajectory = _validate_existing_file(trajectory, "trajectory")
        selection = _validate_selection(selection)
        reference_frame = _validate_int_range(reference_frame, "reference_frame", min_value=0)
    except ValidationError as exc:
        return _validation_error(exc)

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
set sel [atomselect $mol {_tcl_braced(selection)}]
set all [atomselect $mol "all"]
set ref [atomselect $mol {_tcl_braced(selection)} frame {reference_frame}]
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


@mcp.tool(annotations=READ_ONLY_TOOL)
def sasa(
    structure: str,
    selection: str = "protein",
    srad: float = 1.4,
    trajectory: str | None = None,
) -> dict:
    """Solvent-accessible surface area (Angstrom^2) of a selection, per frame.
    ``srad`` is the solvent probe radius (1.4 A ~ water)."""
    try:
        structure = _validate_existing_file(structure, "structure")
        trajectory = _validate_existing_file(trajectory, "trajectory") if trajectory else None
        selection = _validate_selection(selection)
        srad = _validate_float_range(srad, "srad", min_value=0.1, max_value=10.0)
    except ValidationError as exc:
        return _validation_error(exc)

    tcl = f"""
{_load_block(structure, trajectory)}
set sel [atomselect $mol {_tcl_braced(selection)}]
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


@mcp.tool(annotations=WRITE_TOOL)
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

    ``representation`` is one of the supported VMD draw methods; ``coloring`` is
    one of the supported VMD color methods. The image is written under
    VMD_MCP_ROOT by default. Absolute output paths are rejected unless
    VMD_MCP_ALLOW_ABSOLUTE_OUTPUTS=1 is set."""
    try:
        structure = _validate_existing_file(structure, "structure")
        trajectory = _validate_existing_file(trajectory, "trajectory") if trajectory else None
        selection = _validate_selection(selection)
        representation = _validate_choice(
            representation,
            "representation",
            ALLOWED_REPRESENTATIONS,
        )
        coloring = _validate_choice(coloring, "coloring", ALLOWED_COLORINGS)
        background = _validate_choice(background, "background", ALLOWED_BACKGROUNDS)
        frame = _validate_int_range(frame, "frame", min_value=0)
        width = _validate_int_range(
            width,
            "width",
            min_value=MIN_RENDER_DIMENSION,
            max_value=MAX_RENDER_DIMENSION,
        )
        height = _validate_int_range(
            height,
            "height",
            min_value=MIN_RENDER_DIMENSION,
            max_value=MAX_RENDER_DIMENSION,
        )
        out_png = _resolve_output_path(output)
    except ValidationError as exc:
        return _validation_error(exc)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    tga = out_png.with_suffix(".tga")

    tcl = f"""
{_load_block(structure, trajectory)}
animate goto {frame}
mol delrep 0 $mol
mol representation {representation}
mol color {coloring}
mol selection {_tcl_braced(selection)}
mol addrep $mol
display projection Orthographic
axes location off
color Display Background {background}
display resetview
render TachyonInternal {_tcl_braced(str(tga))}
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

    # Convert TGA -> PNG using macOS `sips`, then ImageMagick if available.
    png_ok = False
    if shutil.which("sips"):
        conv = subprocess.run(
            ["sips", "-s", "format", "png", str(tga), "--out", str(out_png)],
            capture_output=True,
            text=True,
        )
        png_ok = conv.returncode == 0 and out_png.exists()
    if not png_ok and shutil.which("magick"):
        conv = subprocess.run(
            ["magick", str(tga), str(out_png)],
            capture_output=True,
            text=True,
        )
        png_ok = conv.returncode == 0 and out_png.exists()
    if not png_ok and shutil.which("convert"):
        conv = subprocess.run(
            ["convert", str(tga), str(out_png)],
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


@mcp.tool(annotations=ESCAPE_HATCH_TOOL)
def run_tcl(script: str, timeout: int = 300) -> dict:
    """Run an ARBITRARY VMD Tcl script headlessly and return its combined
    stdout/stderr plus any parsed ``@@VMDMCP@@`` marker lines.

    Emit results from your script with:  ``puts "@@VMDMCP@@ key=value ..."``.
    Use this for any analysis not covered by a dedicated tool (measure hbonds,
    measure contacts, custom per-residue loops, cluster analysis, ...).
    End the script with ``quit``."""
    try:
        script = _validate_text(script, "script", max_len=20000, allow_newlines=True)
    except ValidationError as exc:
        return _validation_error(exc)

    res = _run_tcl(script, timeout=timeout, include_output=True)
    return res


def main() -> None:
    """Console-script entry point (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
