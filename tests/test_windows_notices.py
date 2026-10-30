import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path


def test_notice_inventory_records_installed_distribution_license(tmp_path):
    root = Path(__file__).resolve().parents[1]
    python_license = tmp_path / "python-license"
    python_license.write_text("test Python license")
    out = tmp_path / "notices"
    inventory = tmp_path / "inventory.json"
    result = subprocess.run([sys.executable, str(root / "scripts/windows/collect_notices.py"), "--output", str(out), "--inventory", str(inventory), "--python-license", str(python_license), "paho-mqtt"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    records = json.loads(inventory.read_text())
    assert records["distributions"]["paho-mqtt"]["version"] == importlib.metadata.version("paho-mqtt")
    notice = records["distributions"]["paho-mqtt"]["notices"][0]
    assert notice["sha256"] == hashlib.sha256((out / notice["file"]).read_bytes()).hexdigest()
    assert (out / notice["file"]).stat().st_size > 0


def test_collects_license_shipped_in_distribution_package_directory(tmp_path):
    import os

    root = Path(__file__).resolve().parents[1]
    site = tmp_path / "site"
    metadata = site / "fixture-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n")
    (metadata / "RECORD").write_text("fixture/license.txt,,\n")
    (site / "fixture").mkdir()
    (site / "fixture/license.txt").write_text("fixture license")
    python_license = tmp_path / "python-license"
    python_license.write_text("test Python license")
    result = subprocess.run([sys.executable, str(root / "scripts/windows/collect_notices.py"), "--output", str(tmp_path / "notices"), "--inventory", str(tmp_path / "inventory.json"), "--python-license", str(python_license), "fixture"], env={**os.environ, "PYTHONPATH": str(site)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "notices/fixture-0.txt").read_text() == "fixture license"


def test_supplemental_notice_rejects_unexpected_source_hash(monkeypatch):
    import importlib.util
    import io

    path = Path(__file__).resolve().parents[1] / "scripts/windows/collect_notices.py"
    spec = importlib.util.spec_from_file_location("collect_notices_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"unexpected"))
    try:
        module.fetch_verified("https://example.invalid/notice", "0" * 64)
    except ValueError as error:
        assert "SHA-256" in str(error)
    else:
        raise AssertionError("unexpected bytes were accepted")


def test_wmi_notice_rejects_unverified_metadata(monkeypatch):
    import importlib.util
    from types import SimpleNamespace

    path = Path(__file__).resolve().parents[1] / "scripts/windows/collect_notices.py"
    spec = importlib.util.spec_from_file_location("collect_notices_wmi_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "fetch_verified", lambda *args: b"mit text")
    dist = SimpleNamespace(version="1.5.1", read_text=lambda name: "Copyright Tim Golden and mit-license.php plus changes")
    try:
        module.supplemental_notices("WMI", dist)
    except ValueError as error:
        assert "metadata" in str(error).lower()
    else:
        raise AssertionError("unverified metadata was accepted")
