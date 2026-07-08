"""Tests for vmd-mcp.

Pure-logic tests always run. Tests that actually invoke VMD are skipped
automatically when VMD is not installed (e.g. on CI).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

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
    "rmsf",
    "sasa",
    "distance",
    "contacts",
    "render_image",
    "render_preset",
    "run_tcl",
}

EXPECTED_RESOURCES = {
    "vmd://capabilities",
    "vmd://output",
    "vmd://examples",
}

EXPECTED_PROMPTS = {
    "render_molecule",
    "analyze_trajectory",
    "debug_vmd_failure",
}


def _sample_structure(tmp_path: Path) -> Path:
    structure = tmp_path / "sample.pdb"
    structure.write_text(
        "\n".join(
            [
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N",
                "ATOM      2  CA  ALA A   1       1.000   0.000   0.000  1.00  0.00           C",
                "TER",
                "END",
                "",
            ]
        )
    )
    return structure


def _vmd_available() -> bool:
    b = server.VMD_BIN
    return bool(shutil.which(b) or os.path.exists(b))


vmd_required = pytest.mark.skipif(not _vmd_available(), reason="VMD not installed")


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names >= EXPECTED_TOOLS, f"missing: {EXPECTED_TOOLS - names}"


def test_tool_annotations_classify_risk():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}

    assert tools["vmd_info"].annotations.readOnlyHint is True
    assert tools["vmd_info"].annotations.destructiveHint is False
    assert tools["rmsf"].annotations.readOnlyHint is True
    assert tools["distance"].annotations.readOnlyHint is True
    assert tools["contacts"].annotations.readOnlyHint is True
    assert tools["count_atoms"].annotations.readOnlyHint is True
    assert tools["render_image"].annotations.readOnlyHint is False
    assert tools["render_image"].annotations.destructiveHint is True
    assert tools["render_preset"].annotations.destructiveHint is True
    assert tools["run_tcl"].annotations.readOnlyHint is False
    assert tools["run_tcl"].annotations.destructiveHint is True


def test_resources_registered_and_readable():
    resources = asyncio.run(server.mcp.list_resources())
    uris = {str(resource.uri) for resource in resources}

    assert uris >= EXPECTED_RESOURCES

    content = list(asyncio.run(server.mcp.read_resource("vmd://capabilities")))[0].content
    capabilities = json.loads(content)
    assert capabilities["server"] == "vmd-mcp"
    assert "rmsf" in capabilities["tools"]["analysis"]
    assert "publication_cartoon" in capabilities["render_presets"]


def test_output_resource_lists_root_files(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    (tmp_path / "renders").mkdir()
    (tmp_path / "renders" / "sample.png").write_bytes(b"fake")

    content = list(asyncio.run(server.mcp.read_resource("vmd://output")))[0].content
    payload = json.loads(content)

    assert payload["root"] == str(tmp_path)
    assert payload["files"][0]["relative_path"] == "renders/sample.png"


def test_prompts_registered_and_renderable():
    prompts = asyncio.run(server.mcp.list_prompts())
    names = {prompt.name for prompt in prompts}

    assert names >= EXPECTED_PROMPTS

    prompt = asyncio.run(
        server.mcp.get_prompt(
            "render_molecule",
            {"structure_path": "examples/sample.pdb", "output": "sample.png"},
        )
    )
    assert "render_preset" in prompt.messages[0].content.text


def test_tcl_quote_escapes_braces():
    assert server._tcl_quote("a{b}c") == "a\\{b\\}c"
    assert server._tcl_quote("back\\slash") == "back\\\\slash"


def test_tcl_braced_disables_command_substitution_shape():
    assert server._tcl_braced('all"; exec touch /tmp/pwn; #') == '{all"; exec touch /tmp/pwn; #}'
    assert server._tcl_braced("a{b}c") == "{a\\{b\\}c}"


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


def test_count_atoms_braces_user_selection(monkeypatch, tmp_path):
    structure = _sample_structure(tmp_path)
    captured = {}

    def fake_run_tcl(script, *args, **kwargs):
        captured["script"] = script
        return {"ok": True, "markers": [{"count": "7"}]}

    monkeypatch.setattr(server, "_run_tcl", fake_run_tcl)

    res = server.count_atoms('all"]; exec touch /tmp/pwn; #', str(structure))

    assert res == {
        "ok": True,
        "selection": 'all"]; exec touch /tmp/pwn; #',
        "count": 7,
        "error": None,
    }
    selection_line = next(
        line for line in captured["script"].splitlines() if line.startswith("set sel [atomselect")
    )
    assert selection_line == 'set sel [atomselect $mol {all"]; exec touch /tmp/pwn; #}]'


def test_rejects_missing_structure_before_vmd():
    res = server.count_atoms("all", "/definitely/not/here.pdb")

    assert res["ok"] is False
    assert "structure does not exist" in res["error"]


def test_resolve_output_rejects_absolute_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv(server.ALLOW_ABSOLUTE_OUTPUTS_ENV, raising=False)

    with pytest.raises(server.ValidationError, match="absolute output paths are disabled"):
        server._resolve_output_path(str(tmp_path / "out.png"))


def test_resolve_output_allows_absolute_with_env(monkeypatch, tmp_path):
    monkeypatch.setenv(server.ALLOW_ABSOLUTE_OUTPUTS_ENV, "1")

    assert server._resolve_output_path(str(tmp_path / "out.png")) == (tmp_path / "out.png").resolve(
        strict=False
    )


def test_resolve_output_rejects_relative_traversal():
    with pytest.raises(server.ValidationError, match="path traversal"):
        server._resolve_output_path("../outside.png")


def test_resolve_output_keeps_relative_paths_under_root():
    resolved = server._resolve_output_path("images/out.png")

    assert resolved == (server.ROOT / "images" / "out.png").resolve(strict=False)


def test_render_validation_rejects_unsafe_options(tmp_path):
    structure = _sample_structure(tmp_path)

    res = server.render_image(str(structure), output="safe.png", representation="Licorice; exec")

    assert res["ok"] is False
    assert "representation must be one of" in res["error"]


def test_render_validation_rejects_bad_dimensions(tmp_path):
    structure = _sample_structure(tmp_path)

    res = server.render_image(str(structure), output="safe.png", width=10)

    assert res["ok"] is False
    assert "width must be >=" in res["error"]


def test_render_preset_uses_curated_defaults(monkeypatch, tmp_path):
    structure = _sample_structure(tmp_path)
    captured = {}

    def fake_render_image(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "image_path": "/tmp/render.png"}

    monkeypatch.setattr(server, "render_image", fake_render_image)

    res = server.render_preset(str(structure), output="preset.png", preset="ligand_detail")

    assert res["ok"] is True
    assert res["preset"] == "ligand_detail"
    assert captured["representation"] == "Licorice"
    assert captured["coloring"] == "Name"
    assert captured["selection"] == "not water"


def test_render_preset_rejects_unknown_preset(tmp_path):
    structure = _sample_structure(tmp_path)

    res = server.render_preset(str(structure), preset="poster_mode")

    assert res["ok"] is False
    assert "preset must be one of" in res["error"]


def test_rmsf_parses_atom_series(monkeypatch, tmp_path):
    structure = _sample_structure(tmp_path)
    trajectory = tmp_path / "traj.dcd"
    trajectory.write_text("placeholder")

    def fake_run_tcl(script, *args, **kwargs):
        assert "measure rmsf" in script
        return {
            "ok": True,
            "markers": [
                {
                    "atom": "0",
                    "index": "1",
                    "name": "CA",
                    "resid": "1",
                    "resname": "ALA",
                    "chain": "A",
                    "rmsf": "0.25",
                },
                {
                    "atom": "1",
                    "index": "2",
                    "name": "CB",
                    "resid": "1",
                    "resname": "ALA",
                    "chain": "NA",
                    "rmsf": "0.75",
                },
            ],
        }

    monkeypatch.setattr(server, "_run_tcl", fake_run_tcl)

    res = server.rmsf(str(structure), str(trajectory), selection="protein")

    assert res["ok"] is True
    assert res["n_atoms"] == 2
    assert res["atoms"][1]["chain"] is None
    assert res["stats"] == {"min": 0.25, "max": 0.75, "mean": 0.5}


def test_distance_parses_series(monkeypatch, tmp_path):
    structure = _sample_structure(tmp_path)
    captured = {}

    def fake_run_tcl(script, *args, **kwargs):
        captured["script"] = script
        return {
            "ok": True,
            "markers": [
                {"frame": "0", "distance": "3.500000", "n1": "1", "n2": "1"},
                {"frame": "1", "distance": "4.500000", "n1": "1", "n2": "1"},
            ],
        }

    monkeypatch.setattr(server, "_run_tcl", fake_run_tcl)

    res = server.distance(str(structure), "name N", "name CA", all_frames=True)

    assert res["ok"] is True
    assert "set start 0" in captured["script"]
    assert res["n_frames"] == 2
    assert res["stats"]["mean"] == 4.0


def test_contacts_clips_pair_list(monkeypatch, tmp_path):
    structure = _sample_structure(tmp_path)

    def fake_run_tcl(script, *args, **kwargs):
        assert "measure contacts 4.0" in script
        return {
            "ok": True,
            "markers": [
                {"frame": "0", "count": "3", "clipped": "1", "n1": "2", "n2": "2"},
                {"pair": "0", "atom1": "1", "atom2": "2"},
                {"pair": "1", "atom1": "1", "atom2": "3"},
            ],
        }

    monkeypatch.setattr(server, "_run_tcl", fake_run_tcl)

    res = server.contacts(str(structure), "all", "all", max_pairs=2)

    assert res["ok"] is True
    assert res["count"] == 3
    assert res["pairs_clipped"] is True
    assert res["pairs"] == [{"atom1": 1, "atom2": 2}, {"atom1": 1, "atom2": 3}]


def test_run_tcl_rejects_timeout_over_cap():
    res = server.run_tcl("puts hi", timeout=server.MAX_TIMEOUT_SECONDS + 1)

    assert res["ok"] is False
    assert "timeout must be <=" in res["error"]


def test_run_tcl_rejects_empty_script():
    res = server.run_tcl("   ")

    assert res["ok"] is False
    assert "script must not be empty" in res["error"]


def test_trim_output_caps_to_tail():
    output, truncated = server._trim_output("a" * (server.MAX_OUTPUT_CHARS + 3))

    assert truncated is True
    assert output == "a" * server.MAX_OUTPUT_CHARS


def test_coerce_output_text_handles_timeout_bytes():
    assert server._coerce_output_text(None) == ""
    assert server._coerce_output_text("plain") == "plain"
    assert server._coerce_output_text(b"binary-\xff") == "binary-\ufffd"


def test_quit_detection_ignores_plain_text_mentions():
    assert server._has_quit_command('puts "quit"') is False
    assert server._has_quit_command("  quit  ") is True
    assert server._has_quit_command("quit -force") is True


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
