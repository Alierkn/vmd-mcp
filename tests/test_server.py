"""Tests for vmd-mcp.

Pure-logic tests always run. Tests that actually invoke VMD are skipped
automatically when VMD is not installed (e.g. on CI).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

# Redirect the output root to a temp dir BEFORE importing the server, so the
# import-time mkdir does not touch the real home directory.
os.environ.setdefault("VMD_MCP_ROOT", tempfile.mkdtemp(prefix="vmd-mcp-test-"))

import pytest  # noqa: E402

from vmd_mcp import server  # noqa: E402

EXPECTED_TOOLS = {
    "vmd_info",
    "molecule_info",
    "count_atoms",
    "radius_of_gyration",
    "rmsd",
    "sasa",
    "render_image",
    "run_tcl",
}


def _vmd_available() -> bool:
    b = server.VMD_BIN
    return bool(shutil.which(b) or os.path.exists(b))


vmd_required = pytest.mark.skipif(not _vmd_available(), reason="VMD not installed")


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names >= EXPECTED_TOOLS, f"missing: {EXPECTED_TOOLS - names}"


def test_tcl_quote_escapes_braces():
    assert server._tcl_quote("a{b}c") == "a\\{b\\}c"
    assert server._tcl_quote("back\\slash") == "back\\\\slash"


def test_load_block_single_and_trajectory():
    one = server._load_block("/x/conf.pdb")
    assert "mol new" in one and "addfile" not in one
    two = server._load_block("/x/conf.gro", "/x/traj.xtc")
    assert "mol new" in two and "addfile" in two


def test_parse_markers():
    out = "noise\n@@VMDMCP@@ frame=0 rgyr=10.42\nmore\n@@VMDMCP@@ frame=1 rgyr=10.9\n"
    markers = server._parse_markers(out)
    assert markers == [
        {"frame": "0", "rgyr": "10.42"},
        {"frame": "1", "rgyr": "10.9"},
    ]


def test_parse_markers_ignores_unmarked():
    assert server._parse_markers("just some vmd banner text") == []


def test_first_float_helper():
    markers = [{"frame": "0"}, {"frame": "1", "rmsd": "2.5"}]
    assert server._f(markers, "rmsd") == 2.5
    assert server._f(markers, "missing") is None


@vmd_required
def test_vmd_info_runs():
    info = server.vmd_info()
    assert info["ok"] is True
    assert info["version"]


@vmd_required
def test_count_atoms_smoke():
    # Uses whatever sample the user has; skip cleanly if none is provided.
    sample = os.environ.get("VMD_TEST_PDB")
    if not sample or not os.path.exists(sample):
        pytest.skip("set VMD_TEST_PDB to a structure file to run this test")
    res = server.count_atoms("all", sample)
    assert res["ok"] and res["count"] > 0
