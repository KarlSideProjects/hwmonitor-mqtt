import hashlib
import json
import subprocess
import sys
from pathlib import Path


def test_dist_inventory_hashes_exact_relative_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    dist = tmp_path / "dist"
    (dist / "runtime").mkdir(parents=True)
    (dist / "runtime/component.dll").write_bytes(b"component")
    output = tmp_path / "inventory.json"
    result = subprocess.run([sys.executable, str(root / "scripts/windows/inventory_sender_dist.py"), "--dist", str(dist), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    inventory = json.loads(output.read_text())
    assert inventory == [{"path": "runtime/component.dll", "size": 9, "sha256": hashlib.sha256(b"component").hexdigest()}]
