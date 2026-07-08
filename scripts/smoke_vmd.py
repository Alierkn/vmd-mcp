"""Run a small real-VMD smoke workflow against the bundled sample PDB.

Usage:
    uv run python scripts/smoke_vmd.py

The script exits non-zero if VMD is unavailable or any core tool fails.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a vmd-mcp real VMD smoke test.")
    parser.add_argument(
        "--structure",
        default=str(Path("examples") / "sample.pdb"),
        help="Structure file to load. Defaults to examples/sample.pdb.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Optional VMD_MCP_ROOT override. Defaults to a temporary directory.",
    )
    return parser.parse_args()


def _require_ok(name: str, result: dict[str, Any]) -> None:
    if not result.get("ok"):
        raise RuntimeError(f"{name} failed: {result}")


def main() -> None:
    args = _parse_args()
    output_root = args.output_root or tempfile.mkdtemp(prefix="vmd-mcp-smoke-")
    os.environ["VMD_MCP_ROOT"] = output_root

    from vmd_mcp import server

    structure = Path(args.structure).resolve()
    if not structure.exists():
        raise FileNotFoundError(f"Structure does not exist: {structure}")

    results = {
        "vmd_info": server.vmd_info(),
        "molecule_info": server.molecule_info(str(structure)),
        "count_atoms": server.count_atoms("all", str(structure)),
        "distance": server.distance(str(structure), "resid 1", "resid 2"),
        "contacts": server.contacts(str(structure), "resid 1", "resid 2", cutoff=5.0, max_pairs=5),
        "render_preset": server.render_preset(
            str(structure),
            output="sample.png",
            preset="atomistic_lines",
            selection="all",
            width=320,
            height=240,
        ),
    }

    for name, result in results.items():
        _require_ok(name, result)

    summary = {
        "ok": True,
        "vmd_version": results["vmd_info"].get("version"),
        "output_root": output_root,
        "numatoms": results["molecule_info"].get("info", {}).get("numatoms"),
        "count_all": results["count_atoms"].get("count"),
        "distance_mean": results["distance"].get("stats", {}).get("mean"),
        "contact_count": results["contacts"].get("count"),
        "render_path": results["render_preset"].get("image_path"),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
