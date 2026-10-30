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
    result = subprocess.run([sys.executable, str(root / "scripts/windows/collect_notices.py"), "--output", str(out), "--inventory", str(inventory), "--python-license", str(python_license), "psutil"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    records = json.loads(inventory.read_text())
    assert records["distributions"]["psutil"]["version"] == importlib.metadata.version("psutil")
    notice = records["distributions"]["psutil"]["notices"][0]
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


def test_paho_supplement_includes_both_upstream_license_texts(monkeypatch):
    import importlib.util
    import io
    import tarfile
    from types import SimpleNamespace

    path = Path(__file__).resolve().parents[1] / "scripts/windows/collect_notices.py"
    spec = importlib.util.spec_from_file_location("collect_notices_paho_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    content = {"paho/edl-v10": b"edl terms", "paho/epl-v20": b"epl terms"}
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, data in content.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    terms = tuple((name, hashlib.sha256(data).hexdigest()) for name, data in content.items())
    monkeypatch.setattr(module, "PAHO_TERMS", terms)
    monkeypatch.setattr(module, "fetch_verified", lambda *args: archive.getvalue())
    notices = module.supplemental_notices("paho-mqtt", SimpleNamespace(version="2.1.0"))
    assert [notice[0] for notice in notices] == list(content.values())


def test_paho_terms_keep_the_filenames_referenced_by_wheel_notice(tmp_path, monkeypatch):
    import importlib.util
    from types import SimpleNamespace

    path = Path(__file__).resolve().parents[1] / "scripts/windows/collect_notices.py"
    spec = importlib.util.spec_from_file_location("collect_notices_names_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wheel_notice = tmp_path / "LICENSE.txt"
    wheel_notice.write_text("See epl-v20 and edl-v10")
    python_notice = tmp_path / "python.txt"
    python_notice.write_text("Python")
    dist = SimpleNamespace(version="2.1.0", files=[Path("LICENSE.txt")], locate_file=lambda path: wheel_notice)
    monkeypatch.setattr(module.importlib.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(module, "supplemental_notices", lambda name, dist: [(b"edl", "https://example.invalid/sdist#paho/edl-v10", "a" * 64), (b"epl", "https://example.invalid/sdist#paho/epl-v20", "a" * 64)])
    monkeypatch.setattr(sys, "argv", [str(path), "--output", str(tmp_path / "out"), "--inventory", str(tmp_path / "inventory"), "--python-license", str(python_notice), "paho-mqtt"])
    module.main()
    assert (tmp_path / "out/edl-v10").read_bytes() == b"edl"
    assert (tmp_path / "out/epl-v20").read_bytes() == b"epl"


def test_notice_collection_does_not_copy_python_files_from_license_directories(tmp_path):
    import os

    root = Path(__file__).resolve().parents[1]
    site = tmp_path / "site"
    metadata = site / "fixture-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n")
    (metadata / "RECORD").write_text("fixture/licenses/LICENSE,,\nfixture/licenses/helper.py,,\n")
    folder = site / "fixture/licenses"
    folder.mkdir(parents=True)
    (folder / "LICENSE").write_text("fixture license")
    (folder / "helper.py").write_text("private = 'not a license'\n")
    python_license = tmp_path / "python-license"
    python_license.write_text("test Python license")
    result = subprocess.run([sys.executable, str(root / "scripts/windows/collect_notices.py"), "--output", str(tmp_path / "notices"), "--inventory", str(tmp_path / "inventory.json"), "--python-license", str(python_license), "fixture"], env={**os.environ, "PYTHONPATH": str(site)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    records = json.loads((tmp_path / "inventory.json").read_text())
    assert len(records["distributions"]["fixture"]["notices"]) == 1


def test_notice_collection_uses_reported_distributions_and_checks_versions(tmp_path):
    root = Path(__file__).resolve().parents[1]
    python_license = tmp_path / "python-license"
    python_license.write_text("test Python license")
    report = tmp_path / "report.xml"
    version = importlib.metadata.version("psutil")
    report.write_text(f'<nuitka-compilation-report completion="yes"><distributions><distribution name="psutil" version="{version}"/></distributions></nuitka-compilation-report>')
    command = [sys.executable, str(root / "scripts/windows/collect_notices.py"), "--output", str(tmp_path / "notices"), "--inventory", str(tmp_path / "inventory.json"), "--python-license", str(python_license), "--report", str(report)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "inventory.json").read_text())["distributions"]["psutil"]["version"] == version
    report.write_text('<nuitka-compilation-report completion="yes"><distributions><distribution name="psutil" version="0.0.0"/></distributions></nuitka-compilation-report>')
    command[command.index(str(tmp_path / "notices"))] = str(tmp_path / "notices-bad")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0
    assert "version" in result.stderr.lower()


def test_exact_cpython_binary_license_adds_versioned_native_notices(tmp_path):
    root = Path(__file__).resolve().parents[1]
    inputs = root / 'release-notices/windows-x64-cpython-3.13.17'
    out = tmp_path / 'notices'
    inventory = tmp_path / 'inventory.json'
    result = subprocess.run([sys.executable, str(root / 'scripts/windows/collect_notices.py'), '--output', str(out), '--inventory', str(inventory), '--python-license', str(inputs / 'LICENSE.cpython-binary.txt'), 'psutil'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for name in ('openssl-3.5.9-LICENSE.txt', 'xz-5.2.5-COPYING', 'cpython-3.13.17-expat-COPYING'):
        assert (out / name).read_bytes() == (inputs / name).read_bytes()
        assert json.loads(inventory.read_text())['native_notices'][name] == hashlib.sha256((out / name).read_bytes()).hexdigest()
