"""Ensure the bundled ``gameStore.js`` matches modular parts (see ``scripts/build_gamestore.py``)."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_gamestore_bundle_matches_parts():
    script = REPO / "scripts" / "build_gamestore.py"
    assert script.is_file(), f"Missing {script}"
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
