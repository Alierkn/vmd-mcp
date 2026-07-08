"""Optional integration tests that invoke the real VMD binary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vmd_mcp import server


def _vmd_available() -> bool:
    return bool(shutil.which(server.VMD_BIN) or os.path.exists(server.VMD_BIN))


integration_enabled = pytest.mark.skipif(
    os.environ.get("RUN_VMD_INTEGRATION") != "1" or not _vmd_available(),
    reason="set RUN_VMD_INTEGRATION=1 and install VMD to run integration tests",
)


@pytest.mark.integration
@integration_enabled
def test_smoke_script_runs_real_vmd(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    output_root = tmp_path / "output"

    proc = subprocess.run(
        [
            "python",
            str(repo_root / "scripts" / "smoke_vmd.py"),
            "--output-root",
            str(output_root),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["numatoms"] == 9
    assert payload["count_all"] == 9
    assert Path(payload["render_path"]).exists()
